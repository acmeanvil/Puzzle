"""Write a CalculiX .inp deck from a study and its solver-neutral mesh.

Units are SI throughout (m, kg, s, N, Pa, K). Feature values the writer reads:

| Feature | Values |
| --- | --- |
| fixed | none |
| displacement | any of x, y, z (length) |
| force | any of x, y, z (total force over the selection) |
| pressure | p (positive pushes into the surface) |
| temperature | T |
| heat_flux | q (positive into the body) |
| convection | h (W/(m^2 K)), T_inf |

Solve step settings: `modes` (int, modal, default 10); `time_step` and `end_time`
(transient).
"""

from __future__ import annotations

import re

import numpy as np

from puzzle.adapters.base import refuse_inputs
from puzzle.adapters.common import (
    BODY_LOADS,
    HOLDING,
    PRETENSION,
    STRUCTURAL,
    THERMAL,
    FaceLookup,
    InputError,
    body_load,
    check_contacts,
    globalize,
    initial_temperature,
    nodal_traction,
    remote_load,
    remote_values,
    rigid_motion,
    section_cuts,
    surface_area,
    surface_blocks,
    thermal_tables,
    time_scheme,
)
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Feature, Material, Project, SolveStep, Study

ROOM_K = 293.15

# Puzzle element -> (CalculiX type, node reorder from Gmsh order or None).
# Gmsh numbers the tet10 edge nodes (0-1, 1-2, 2-0, 0-3, 2-3, 1-3); CalculiX ends with (1-3, 2-3).
ELEMENTS = {
    "tet4": ("C3D4", None),
    "tet10": ("C3D10", [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "hex8": ("C3D8", None),
}
# Share of a uniform traction carried by each node of a surface element.
NODE_SHARES = {
    "tri3": [1 / 3] * 3,
    "tri6": [0, 0, 0, 1 / 3, 1 / 3, 1 / 3],
    "quad4": [1 / 4] * 4,
}


def reference_temperature(comp: Component) -> float:
    """Where thermal strain is zero: the structural physics' reference_temperature, in K."""
    solid = next((ph for ph in comp.physics if ph.type == "solid_mechanics"), None)
    value = solid.settings.get("reference_temperature") if solid is not None else None
    if value is None:
        return ROOM_K
    return value.to_si() if hasattr(value, "to_si") else float(value)


def set_name(prefix: str, name: str) -> str:
    """A CalculiX set name: letters, digits and underscores, upper case."""
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').upper()}"


def write_inp(project: Project, study: Study, mesh: Mesh, inputs=()) -> str:
    comp = project.get(study.component, Component)
    refuse_inputs(inputs, "CalculiX", {("load", "temperature"), ("prestress", "stress")})
    # A temperature field from a thermal study, as a thermal load on structural steps.
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    check_contacts(comp, CONTACTS, "CalculiX")
    touching = [c for c in comp.contacts if c.type in ("frictionless", "frictional")]
    if touching and any(
        s.type != "stationary" or not s.solves("solid_mechanics") or len(set(s.physics)) > 1
        for s in study.solve_steps()
    ):
        raise InputError(
            "CalculiX contact pairs are written for static structural steps only so far",
            code="unsupported",
        )
    volume_blocks = [b for b in mesh.blocks if b.dim == 3]
    for b in volume_blocks:
        if b.element not in ELEMENTS:
            raise InputError(f"CalculiX adapter does not support {b.element} elements yet")
    faces = FaceLookup.build(volume_blocks)
    out: list[str] = ["*HEADING", f"Puzzle study: {study.name}"]

    out.append("*NODE, NSET=N_ALL")
    out += [
        # CalculiX reads at most 20 characters per field: "%.15g" can give 21 (e.g.
        # -8.67361737988404e-19 from OCC round-off) and the value is silently cut.
        # 13 significant digits fit in 19.
        f"{n}, {x:.13g}, {y:.13g}, {z:.13g}"
        for n, (x, y, z) in zip(mesh.node_ids, mesh.coords, strict=True)
    ]

    cuts = _pretension_cuts(comp, mesh)  # bolt sections: elements moved onto twins
    for part in comp.parts:
        for b in mesh.groups[part.name].blocks(mesh):
            if b.dim != 3:
                continue
            ccx_type, order = ELEMENTS[b.element]
            conn = b.connectivity[:, order] if order else b.connectivity
            for cut in cuts.values():
                rows = np.flatnonzero(np.isin(b.ids, list(cut.moved)))
                if len(rows):
                    conn = conn.copy()
                    conn[rows] = np.vectorize(lambda n, c=cut: c.doubles.get(int(n), n))(conn[rows])
            out.append(f"*ELEMENT, TYPE={ccx_type}, ELSET={set_name('E', part.name)}")
            out += [_row([eid, *nodes]) for eid, nodes in zip(b.ids, conn, strict=True)]

    for group in mesh.groups.values():
        out.append(f"*NSET, NSET={set_name('N', group.name)}")
        out += _chunks(group.node_ids)
        if group.dim == 3 and group.name not in {p.name for p in comp.parts}:
            ids = np.concatenate([b.ids for b in group.blocks(mesh) if b.dim == 3])
            out.append(f"*ELSET, ELSET={set_name('E', group.name)}")
            out += _chunks(ids)

    out += _materials(project, comp, mesh, expansion=thermal_load is not None)
    out += _amplitudes(project, comp)
    out += _transforms(project, comp, mesh)
    rigid = _rigid_nodes(comp, mesh)
    out += _rigid_bodies(rigid)
    pretension = _pretension_nodes(comp, mesh)
    out += _pretension_sections(mesh, cuts)
    used = len(pretension) + sum(len(c.doubles) for c in cuts.values())
    out += _springs(comp, mesh, int(mesh.node_ids.max()) + 1 + 2 * len(rigid) + used)
    out += _contacts(project, comp, mesh, faces)

    solve_steps = study.solve_steps()
    if not solve_steps:
        raise InputError(f"study '{study.name}' has no solve steps")
    physics_types = {p.type for p in comp.physics}
    if thermal_load is not None:
        # Thermal strain is alpha (T - T_initial): start at the reference temperature.
        out += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"N_ALL, {reference_temperature(comp)}"]
    elif "heat_transfer" in physics_types:
        heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
        out += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"N_ALL, {initial_temperature(heat)}"]
    yields = plastic(project, comp)
    prestress = _prestress_steps(project, study, inputs)
    applied = _pretension_steps(comp, solve_steps)
    for number, step in enumerate(prestress, start=1):
        # Large-deformation static step: its stress stiffens the steps after it.
        lines = _step(number, step, comp, mesh, faces, physics_types, thermal_load, yields, project)
        first = lines[0] if "NLGEOM" in lines[0] else lines[0] + ", NLGEOM"
        out += [first, *lines[1:]]
    preloads = _preload_steps(solve_steps)
    for number, step in enumerate(solve_steps, start=len(prestress) + 1):
        index = number - len(prestress)  # the study's own step number (Feature.steps)
        lines = _step(
            number,
            step,
            comp,
            mesh,
            faces,
            physics_types,
            thermal_load,
            yields,
            project,
            index=index,
            pretension=(pretension, applied),
        )
        perturbs = prestress or index in preloads.values()  # of the stressed state
        if perturbs and "PERTURBATION" not in lines[0]:
            lines = [lines[0] + ", PERTURBATION", *lines[1:]]
        if index in preloads and "NLGEOM" not in lines[0]:  # a buckling step's preload
            lines = [lines[0].replace("*STEP", "*STEP, NLGEOM", 1), *lines[1:]]
        out += lines
    return "\n".join(out) + "\n"


def prestress_step_count(project: Project, study: Study, inputs=()) -> int:
    return len(_prestress_steps(project, study, inputs))


def _prestress_steps(project: Project, study: Study, inputs) -> list[SolveStep]:
    """The source study's static steps, replayed before a prestressed study's own.

    CalculiX takes prestress as the stress state of a preceding NLGEOM step, not as a
    field, so the source study is rebuilt from its definition: it must be a static
    structural study on the same component (same mesh, same supports and loads).
    """
    steps: list[SolveStep] = []
    for i in inputs:
        if i.role != "prestress":
            continue
        source = project.get(i.study, Study)
        solves = source.solve_steps()
        if source.component != study.component or [s.type for s in solves] != ["stationary"]:
            raise InputError(
                f"prestress from '{source.name}': CalculiX replays a single static step on "
                "the same component as the prestressed study",
                code="unsupported",
            )
        steps.append(solves[0].model_copy(update={"physics": ["solid_mechanics"]}))
    return steps


def _materials(project: Project, comp: Component, mesh: Mesh, expansion: bool = False) -> list[str]:
    out: list[str] = []
    for i, assignment in enumerate(comp.materials, start=1):
        material = project.get(assignment.material, Material)
        group = mesh.groups.get(assignment.selection)
        if group is None or group.dim != 3:
            raise InputError(
                f"material '{material.name}' must be assigned to bodies, "
                f"not '{assignment.selection}'"
            )
        name = f"M{i}"
        out += [f"*MATERIAL, NAME={name}"] + _material_cards(material, comp, expansion)
        out.append(f"*SOLID SECTION, ELSET={set_name('E', assignment.selection)}, MATERIAL={name}")
    if not comp.materials:
        raise InputError(f"component '{comp.name}' has no material assignments")
    return out


def _material_cards(m: Material, comp: Component, expansion: bool = False) -> list[str]:
    p = {k: q.to_si() for k, q in m.properties.items()}
    types = {ph.type for ph in comp.physics}
    cards: list[str] = []

    def need(*keys: str) -> list[float]:
        missing = [k for k in keys if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        return [p[k] for k in keys]

    if "solid_mechanics" in types:
        e, nu = need("youngs_modulus", "poissons_ratio")
        cards += ["*ELASTIC", f"{e:.10g}, {nu:.10g}"]
        if m.models.plasticity is not None:  # von Mises, isotropic hardening (ADR 0020)
            cards += _plastic_card(m)
        if "density" in p:
            cards += ["*DENSITY", f"{p['density']:.10g}"]
    if "heat_transfer" in types:
        tables = thermal_tables(m, comp)

        def card(name: str, prop: str, required: bool = False) -> list[str]:
            # A table is one "value, temperature" line per point; CalculiX interpolates.
            if prop in tables:
                return [name, *(f"{v:.10g}, {t:.10g}" for t, v in tables[prop])]
            if prop in p:
                return [name, f"{p[prop]:.10g}"]
            if required:
                need(prop)
            return []

        cards += card("*CONDUCTIVITY", "thermal_conductivity", required=True)
        cards += card("*SPECIFIC HEAT", "specific_heat")
        if "*DENSITY" not in cards:
            cards += card("*DENSITY", "density")
    if expansion and "thermal_expansion" not in p:
        raise InputError(f"a temperature load needs thermal_expansion on material '{m.name}'")
    if ("thermal_expansion" in comp.couplings or expansion) and "thermal_expansion" in p:
        table = m.tables.get("thermal_expansion")
        ref = table.reference_temperature if table and table.reference_temperature else ROOM_K
        cards += [f"*EXPANSION, ZERO={ref:.10g}", f"{p['thermal_expansion']:.10g}"]
    return cards


def _plastic_card(m: Material) -> list[str]:
    """*PLASTIC: "true stress, equivalent plastic strain[, temperature]" lines, the
    curves in rising temperature (CalculiX interpolates between them)."""
    curves = sorted(m.models.plasticity.curves, key=lambda c: c.temperature or 0.0)
    if len(curves) > 1 and any(c.temperature is None for c in curves):
        raise InputError(f"material '{m.name}': give every hardening curve its temperature")
    lines = ["*PLASTIC"]
    for c in curves:
        temperature = "" if c.temperature is None else f", {c.temperature:.10g}"
        lines += [f"{s:.10g}, {e:.10g}{temperature}" for e, s in c.points]
    return lines


def plastic(project: Project, comp: Component) -> bool:
    """Whether any of the component's materials yields: its static steps are stepped."""
    return any(
        isinstance(m := project.objects.get(a.material), Material) and m.models.plasticity
        for a in comp.materials
    )


def _step(
    number: int,
    step: SolveStep,
    comp: Component,
    mesh: Mesh,
    faces: FaceLookup,
    physics_types,
    thermal_load=None,
    yields: bool = False,
    project: Project | None = None,
    index: int | None = None,
    pretension=({}, {}),
) -> list[str]:
    """One *STEP; `index` is the study's solve step number, for features applying in some
    steps only, and `pretension` (nodes, step applied) locks a bolt after its step."""
    solved = set(step.physics) or physics_types
    # Plasticity is nonlinear like contact: the load goes in by increments, each kept.
    lifting = any(f.type == "compression_only_support" for ph in comp.physics for f in ph.features)
    contact = any(c.type in ("frictionless", "frictional") for c in comp.contacts) or (
        (yields or lifting) and step.type == "stationary" and "solid_mechanics" in solved
    )
    nlgeom = bool(step.settings.get("geometric_nonlinearity", False))
    if nlgeom and (solved != {"solid_mechanics"} or step.type != "stationary"):
        raise InputError(
            "geometric_nonlinearity applies to static structural steps", code="unsupported"
        )
    # Nonlinear steps (contact, large deflection) go in at most 1/n of the load at a time;
    # each increment's results are kept, so a load-deflection curve can be drawn.
    increments = int(step.settings.get("increments", 10 if (contact or nlgeom) else 1))
    first = float(step.settings.get("initial_increment", 1 / increments))  # fraction of load
    op = ", OP=NEW" if number > 1 else ""
    settings = step.settings

    def setting(key: str) -> float:
        if key not in settings:
            raise InputError(f"{step.type} step needs a '{key}' setting")
        value = settings[key]
        return value.to_si() if hasattr(value, "to_si") else float(value)

    if solved == {"solid_mechanics"}:
        procedure = {
            # Contact is nonlinear: start with a tenth of the load, then let CalculiX step.
            "stationary": (
                [
                    "*STATIC",
                    f"{first:.6g}, 1.0, {1e-4 / increments:.3g}, {1 / increments:.6g}",
                ]
                if contact or nlgeom
                else ["*STATIC"]
            ),
            "modal": ["*FREQUENCY", str(int(settings.get("modes", 10)))],
            # ADR 0028: load factors on this step's loads, and their mode shapes.
            "buckling": ["*BUCKLE", str(int(settings.get("modes", 4)))],
            "transient": ["*DYNAMIC"] + [f"{setting('time_step'):.10g}, {setting('end_time'):.10g}"]
            if step.type == "transient"
            else [],
        }.get(step.type)
        # Reaction forces (RF) let a study sum the support or contact loads.
        outputs = (
            ["*NODE FILE", "U"]
            if step.type in ("modal", "buckling")
            else ["*NODE FILE", "U, RF", "*EL FILE", "S, E" + (", PEEQ" if yields else "")]
        )
    elif solved == {"heat_transfer"}:
        if step.type == "transient" and time_scheme(step)[0] != "backward_euler":
            raise InputError(
                f"CalculiX steps heat transfer by backward Euler only, not {time_scheme(step)[0]}",
                code="unsupported",
            )
        procedure = {
            "stationary": ["*HEAT TRANSFER, STEADY STATE"],
            # DIRECT keeps the given increment instead of CalculiX's automatic stepping.
            "transient": ["*HEAT TRANSFER, DIRECT"]
            + [f"{setting('time_step'):.10g}, {setting('end_time'):.10g}"]
            if step.type == "transient"
            else [],
        }.get(step.type)
        outputs = ["*NODE FILE", "NT", "*EL FILE", "HFL"]
    elif solved == {"solid_mechanics", "heat_transfer"} and "thermal_expansion" in comp.couplings:
        procedure = (
            ["*COUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE"]
            if step.type == "stationary"
            else None
        )
        outputs = ["*NODE FILE", "U, NT", "*EL FILE", "S, E, HFL"]
    else:
        raise InputError(
            f"CalculiX can't solve {sorted(solved)} together in one step; set the step's physics, "
            "or add the thermal_expansion coupling for a coupled thermal-structural step"
        )
    if step.type == "buckling" and "shift" in settings:
        raise InputError(
            "CalculiX's *BUCKLE finds the smallest load factors and takes no shift; "
            "remove it, or use Code_Aster",
            code="unsupported",
        )
    if procedure is None:
        raise InputError(
            f"CalculiX adapter does not support {step.type} steps for {sorted(solved)} yet"
        )

    # CalculiX stops after 100 increments per step unless told otherwise.
    # Gap elements (compression-only supports) are switched only in a nonlinear step:
    # without NLGEOM CalculiX solved them once, closed both ways.
    gapped = lifting and step.type == "stationary" and "solid_mechanics" in solved
    head = "*STEP, NLGEOM, INC=10000" if nlgeom or gapped else "*STEP"
    out = ["*STEP, INC=1000000" if step.type == "transient" else head] + procedure
    if "heat_transfer" in solved:
        # Temperature tables make heat transfer nonlinear; CalculiX's default field
        # tolerances (0.5% residual, 1% correction) stop short. Linear cases converge at once.
        out += ["*CONTROLS, PARAMETERS=FIELD", "1e-7, 1e-7, 0., 0., 0.02, 1e-5, 1e-3, 1e-8"]
    wanted = (STRUCTURAL if "solid_mechanics" in solved else set()) | (
        THERMAL if "heat_transfer" in solved else set()
    )
    features = [
        f
        for ph in comp.physics
        if ph.type in solved
        for f in ph.features
        if f.type in wanted and f.active(index)
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in HOLDING]
    if project is not None:  # Cartesian forces and full displacements in global axes
        features = globalize(project, features)
    rigid = _rigid_nodes(comp, mesh)  # the same ids the model section gave them
    nodes, applied = pretension
    extra: dict[str, list[str]] = {}
    if "solid_mechanics" in solved and any(
        f.type in ("elastic_support", "compression_only_support")
        for ph in comp.physics
        for f in ph.features
    ):
        extra["*BOUNDARY"] = ["N_SPRING_GROUND, 1, 3, 0."]  # the springs' grounds
    locked: list[str] = []
    if "solid_mechanics" in solved and step.type != "modal":
        for f in [f for ph in comp.physics for f in ph.features if f.type in PRETENSION]:
            node, first = nodes[id(f)], applied.get(id(f))
            if index is None or index == first:  # applied: a force, or a length change
                if f.type == "bolt_pretension":
                    force = -f.values["F"].to_si()  # tension pulls the sides together
                    extra.setdefault("*CLOAD", []).append(f"{node}, 1, {force:.10g}")
                else:
                    d = -f.values["d"].to_si()  # an adjustment shortens the bolt
                    extra.setdefault("*BOUNDARY", []).append(f"{node}, 1, 1, {d:.10g}")
            elif first is not None and index > first:  # held at the length it reached
                locked.append(f"{node}, 1, 1")
    features = [f for f in features if f.type not in PRETENSION]
    out += _features(features, mesh, faces, op, _amplitude_names(comp), rigid, extra)
    if op:  # a card not written again would keep the step before's loads: clear it
        resets = (["*BOUNDARY", "*CLOAD", "*DLOAD"] if "solid_mechanics" in solved else []) + (
            ["*BOUNDARY", "*DFLUX", "*FILM"] if "heat_transfer" in solved else []
        )
        for card in dict.fromkeys(resets):
            if not any(line.startswith(card) for line in out):
                out.append(card + op)
    if locked:  # the pretension force went with the reset; the lock holds the length
        out += ["*BOUNDARY, FIXED", *locked]
    if thermal_load is not None and solved == {"solid_mechanics"} and step.type != "modal":
        out.append("*TEMPERATURE")
        out += [
            f"{n}, {t:.10g}"
            for n, t in zip(thermal_load.ids, thermal_load.values[:, 0], strict=True)
        ]
    return out + outputs + ["*END STEP"]


def _preload_steps(steps) -> dict[int, int]:
    """Preload step -> its buckling step (ADR 0028), for buckling steps with `preload`:
    the static step just before is solved with NLGEOM and the buckling step perturbs
    it, so its stress is held and only the buckling step's loads are scaled. (A plain
    *BUCKLE after *STATIC ignored the preload.)"""
    out = {}
    for i, step in enumerate(steps, start=1):
        if step.type != "buckling" or not step.settings.get("preload", False):
            continue
        before = steps[i - 2] if i > 1 else None
        if before is None or before.type != "stationary":
            raise InputError(
                "a buckling step with a preload needs a static step just before it",
                code="unsupported",
            )
        out[i - 1] = i
    return out


def _amplitude_names(comp: Component) -> dict:
    """Amplitude id -> its card name, in the order the component first uses them."""
    ids = dict.fromkeys(f.amplitude for p in comp.physics for f in p.features if f.amplitude)
    return {a: f"A{i}" for i, a in enumerate(ids, start=1)}


def _amplitudes(project: Project, comp: Component) -> list[str]:
    """*AMPLITUDE cards (ADR 0037): factor against step time, linear between points."""
    from puzzle.model.schema import Amplitude

    out = []
    for amplitude_id, name in _amplitude_names(comp).items():
        a = project.get(amplitude_id, Amplitude)
        out.append(f"*AMPLITUDE, NAME={name}")
        out += [f"{t:.10g}, {v:.10g}" for t, v in a.points]
    return out


def _rigid_nodes(comp: Component, mesh: Mesh) -> dict:
    """Each rigid remote force's reference and rotation nodes (new ids past the mesh's)
    at its point: id(feature) -> (selection, ref, rot, point)."""
    from puzzle.adapters.common import remote_load

    out, next_id = {}, int(mesh.node_ids.max()) + 1
    for ph in comp.physics:
        for f in ph.features:
            if f.type != "remote_force_rigid":
                continue
            group = mesh.groups.get(f.selection)
            if group is None:
                raise InputError(f"{f.type} on '{f.selection}': no such selection in the mesh")
            force, moment, point = remote_values(f)
            if point is None:
                point = remote_load(group, mesh, force, moment).centroid
            out[id(f)] = (f.selection, next_id, next_id + 1, point)
            next_id += 2
    return out


def _pretension_nodes(comp: Component, mesh: Mesh) -> dict:
    """Each bolt pretension's reference node (ADR 0037), numbered after the rigid-body
    nodes: id(feature) -> node id."""
    first = int(mesh.node_ids.max()) + 1 + 2 * len(_rigid_nodes(comp, mesh))
    features = [f for ph in comp.physics for f in ph.features if f.type in PRETENSION]
    return {id(f): first + i for i, f in enumerate(features)}


def _pretension_steps(comp: Component, steps) -> dict:
    """The solve step (1, 2, ...) each pretension is applied in: the first static or
    transient structural step it is active in. It is locked in the steps after."""
    out = {}
    for f in [f for ph in comp.physics for f in ph.features if f.type in PRETENSION]:
        for i, s in enumerate(steps, start=1):
            if s.solves("solid_mechanics") and s.type not in ("modal", "buckling") and f.active(i):
                out[id(f)] = i
                break
    return out


def _pretension_cuts(comp: Component, mesh: Mesh) -> dict:
    """id(feature) -> its SectionCut; twins are numbered after the reference nodes."""
    refs = _pretension_nodes(comp, mesh)
    return section_cuts(comp, mesh, refs)


def _pretension_sections(mesh: Mesh, cuts: dict) -> list[str]:
    """Each section's reference node and twins, tied by u_twin - u = n u_ref: the
    reference node's first dof is the section's opening along n, and the force on it
    is the force across the section (ADR 0037). CalculiX's *PRE-TENSION SECTION was
    tried first: it moves only the elements with a face on the section, so on tets the
    elements touching it at an edge kept the sides joined (316 N of 10 kN)."""
    out = []
    for cut in cuts.values():
        out += ["*NODE", f"{cut.ref}, " + ", ".join(f"{c:.13g}" for c in cut.centre)]
        xyz = mesh.node_coords(np.array(list(cut.doubles)))
        out += [
            f"{twin}, " + ", ".join(f"{c:.13g}" for c in p)
            for twin, p in zip(cut.doubles.values(), xyz, strict=True)
        ]
        out.append("*EQUATION")
        for node, twin in cut.doubles.items():
            for d in (1, 2, 3):
                n = float(cut.normal[d - 1])
                terms = [f"{twin}, {d}, 1.", f"{node}, {d}, -1."]
                if abs(n) > 1e-12:
                    terms.append(f"{cut.ref}, 1, {-n:.13g}")
                out += [str(len(terms)), ", ".join(terms)]
    return out


def _springs(comp: Component, mesh: Mesh, first_node: int) -> list[str]:
    """Elastic supports (ADR 0037) as SPRINGA elements to grounded nodes: one along each
    face node's normal (kn × its area) and, with kt, two along its tangents. Springs of
    equal stiffness share an element set (CalculiX takes one stiffness per set)."""
    from puzzle.adapters.common import support_nodes, support_stiffness

    node, elem = first_node, int(max(b.ids.max() for b in mesh.blocks)) + 1
    size = float(np.linalg.norm(np.ptp(mesh.coords, axis=0)))
    nodes, bins, gaps = [], {}, {}
    for ph in comp.physics:
        for f in ph.features:
            if f.type not in ("elastic_support", "compression_only_support"):
                continue
            group = mesh.groups.get(f.selection)
            if group is None:
                raise InputError(f"elastic support on '{f.selection}': no such selection")
            kn, kt = support_stiffness(f)
            ids, areas, normals = support_nodes(group, mesh)
            xyz = mesh.node_coords(ids)
            if f.type == "compression_only_support":  # a gap per node, closed when pressed
                for nid, a, n, p in zip(ids, areas, normals, xyz, strict=True):
                    ground = p + 1e-3 * size * n
                    nodes.append(f"{node}, " + ", ".join(f"{c:.13g}" for c in ground))
                    key = (float(f"{kn * a:.9g}"), *(round(float(c), 9) for c in n))
                    gaps.setdefault(key, []).append(f"{elem}, {nid}, {node}")
                    node, elem = node + 1, elem + 1
                continue
            for nid, a, n, p in zip(ids, areas, normals, xyz, strict=True):
                t1 = np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0])
                t1 /= np.linalg.norm(t1)
                t2 = np.cross(n, t1)
                for k, d in ((kn, n), (kt, t1), (kt, t2)):
                    if k <= 0:
                        continue
                    ground = p + 1e-3 * size * d
                    nodes.append(f"{node}, " + ", ".join(f"{c:.13g}" for c in ground))
                    key = float(f"{k * a:.9g}")
                    bins.setdefault(key, []).append(f"{elem}, {nid}, {node}")
                    node, elem = node + 1, elem + 1
    if not nodes:
        return []
    out = ["*NODE", *nodes, "*NSET, NSET=N_SPRING_GROUND"]
    out += _chunks(np.arange(first_node, node))
    for i, (stiffness, rows) in enumerate(sorted(bins.items()), start=1):
        out += [f"*ELEMENT, TYPE=SPRINGA, ELSET=E_SPRING{i}", *rows]
        # Always a real with an exponent: a bare integer reads as a DOF number.
        out += [f"*SPRING, ELSET=E_SPRING{i}", "", f"{stiffness:.10e}"]
    for i, ((stiffness, *n), rows) in enumerate(sorted(gaps.items()), start=1):
        # GAPUNI: closed (stiffness k) once the face node has moved its clearance (0)
        # along the gap direction, the face's outward normal, into the foundation.
        out += [f"*ELEMENT, TYPE=GAPUNI, ELSET=E_GAP{i}", *rows]
        out += [
            f"*GAP, ELSET=E_GAP{i}",
            f"0., {n[0]:.9g}, {n[1]:.9g}, {n[2]:.9g}, , {stiffness:.10e}",
        ]
    return out  # the grounds are held in every step (_step), where OP=NEW can't drop them


def _transforms(project: Project, comp: Component, mesh: Mesh) -> list[str]:
    """*TRANSFORM for displacements still in local axes (cylindrical, or some components
    only), so their *BOUNDARY dofs are local (ADR 0037). A node in such a set takes no
    other load: CalculiX would read that load in the local axes too."""
    from puzzle.adapters.common import local_axes

    features = [f for ph in comp.physics for f in ph.features]
    local = [f for f in globalize(project, features) if f.axes is not None]
    out, transformed = [], set()
    for f in local:
        if f.type != "displacement":
            raise InputError(f"{f.type} in local axes isn't written yet (on {f.selection})")
        axes = local_axes(project, f)
        if axes.type == "cartesian":
            r = axes.rotation()
            a, b, kind = r[:, 0], r[:, 1], "R"
        else:
            o = np.asarray(axes.origin, float)
            a, b, kind = o, o + np.asarray(axes.axis, float), "C"
        nums = ", ".join(f"{c:.13g}" for c in (*a, *b))
        out += [f"*TRANSFORM, NSET={set_name('N', f.selection)}, TYPE={kind}", nums]
        transformed |= set(mesh.groups[f.selection].node_ids.tolist())
    for f in features:
        shared = (
            f.axes is None
            and f.type in ("displacement", "force", "remote_force", "remote_displacement")
            and f.selection in mesh.groups
            and transformed & set(mesh.groups[f.selection].node_ids.tolist())
        )
        if shared:
            raise InputError(
                f"{f.type} on '{f.selection}' shares nodes with a displacement in local "
                "axes; CalculiX would read it in those axes",
                code="unsupported",
            )
    return out


def _rigid_bodies(rigid: dict) -> list[str]:
    """*RIGID BODY: the face's nodes follow the reference node (translations) and the
    rotation node (whose displacements are the rotations)."""
    out = []
    for selection, ref, rot, point in rigid.values():
        xyz = ", ".join(f"{c:.13g}" for c in point)
        out += ["*NODE", f"{ref}, {xyz}", f"{rot}, {xyz}"]
        out.append(f"*RIGID BODY, NSET={set_name('N', selection)}, REF NODE={ref}, ROT NODE={rot}")
    return out


def _features(
    features: list[Feature],
    mesh: Mesh,
    faces: FaceLookup,
    op: str,
    amplitudes=None,
    rigid=None,
    extra=None,
) -> list[str]:
    """The step's load and boundary cards; a feature following an amplitude goes in a
    card of its own with AMPLITUDE=. `extra`: rows for helper nodes (card -> rows)."""
    amplitudes = amplitudes or {}
    cards: dict[tuple[str, str | None], list[str]] = {
        (card, None): list(rows) for card, rows in (extra or {}).items()
    }
    for f in features:
        amp = amplitudes.get(f.amplitude) if f.amplitude else None
        rows = _feature_rows(f, mesh, faces, (rigid or {}).get(id(f)))
        for card, lines in rows.items():
            cards.setdefault((card, amp), []).extend(lines)
    out: list[str] = []
    order = ("*BOUNDARY", "*CLOAD", "*DLOAD", "*DFLUX", "*FILM")
    for (card, amp), lines in sorted(
        cards.items(), key=lambda k: (order.index(k[0][0]), k[0][1] or "")
    ):
        if lines:
            out += [card + op + (f", AMPLITUDE={amp}" if amp else "")] + lines
    return out


def _feature_rows(f: Feature, mesh: Mesh, faces: FaceLookup, rigid=None) -> dict[str, list[str]]:
    boundary, cload, dload, dflux, film = [], [], [], [], []
    group = mesh.groups.get(f.selection)
    if group is None:
        raise InputError(f"{f.type} on '{f.selection}': no such selection in the mesh")
    nset = set_name("N", f.selection)
    v = {k: q.to_si() for k, q in f.values.items()}
    if f.type == "fixed":
        boundary.append(f"{nset}, 1, 3, 0.")
    elif f.type == "displacement":
        boundary += [
            f"{nset}, {d}, {d}, {v[axis]:.10g}"
            for d, axis in ((1, "x"), (2, "y"), (3, "z"))
            if axis in v
        ]
    elif f.type == "temperature":
        boundary.append(f"{nset}, 11, 11, {v['T']:.10g}")
    elif f.type == "force":
        cload += _nodal_forces(group, mesh, [v.get(a, 0.0) for a in "xyz"])
    elif f.type == "pressure":
        dload += [f"{e}, P{n}, {v['p']:.10g}" for e, n in _element_faces(group, mesh, faces)]
    elif f.type == "remote_force":  # an RBE3-equivalent traction (ADR 0037)
        load = remote_load(group, mesh, *remote_values(f))
        for node, force in sorted(nodal_traction(group, mesh, load.at).items()):
            cload += [f"{node}, {d + 1}, {force[d]:.10g}" for d in range(3) if force[d]]
    elif f.type == "remote_force_rigid" and rigid is not None:  # on the rigid body's nodes
        force, moment, _ = remote_values(f)
        _, ref, rot, _ = rigid
        cload += [f"{ref}, {d + 1}, {force[d]:.10g}" for d in range(3) if force[d]]
        cload += [f"{rot}, {d + 1}, {moment[d]:.10g}" for d in range(3) if moment[d]]
    elif f.type == "remote_displacement":  # the face moves rigidly, every component
        motion = rigid_motion(f, group, mesh)
        u = motion.at(mesh.node_coords(group.node_ids))
        boundary += [
            f"{n}, {d + 1}, {d + 1}, {u[i, d]:.10g}"
            for i, n in enumerate(group.node_ids)
            for d in range(3)
        ]
    elif f.type in BODY_LOADS:  # per volume, on the body's elements (ADR 0037)
        load, elset = body_load(f), set_name("E", f.selection)
        if load.uniform is not None:
            b = np.asarray(load.uniform)
            size = float(np.linalg.norm(b))
            if size:
                d = b / size
                dload.append(f"{elset}, GRAV, {size:.10g}, {d[0]:.10g}, {d[1]:.10g}, {d[2]:.10g}")
        elif load.omega:
            c, n = load.centre, load.axis
            dload.append(
                f"{elset}, CENTRIF, {load.omega**2:.10g}, {c[0]:.10g}, {c[1]:.10g}, "
                f"{c[2]:.10g}, {n[0]:.10g}, {n[1]:.10g}, {n[2]:.10g}"
            )
    elif f.type == "heat_flux":
        dflux += [f"{e}, S{n}, {v['q']:.10g}" for e, n in _element_faces(group, mesh, faces)]
    elif f.type == "convection":
        film += [
            f"{e}, F{n}, {v['T_inf']:.10g}, {v['h']:.10g}"
            for e, n in _element_faces(group, mesh, faces)
        ]
    return {
        "*BOUNDARY": boundary,
        "*CLOAD": cload,
        "*DLOAD": dload,
        "*DFLUX": dflux,
        "*FILM": film,
    }


CONTACTS = {"bonded_nonconformal", "frictionless", "frictional"}


def _contacts(project: Project, comp: Component, mesh: Mesh, faces: FaceLookup) -> list[str]:
    """Model-level contact definitions; the source is the slave side, the target the master.

    - A nonconformal bond is a *TIE: slave nodes follow the master faces.
    - Frictionless and frictional contact are surface-to-surface *CONTACT PAIRs with a
      linear penalty: K = 50 E / h (stiffest material, slave face size), so the
      overclosure stays near 2% of an element-sized strain. Friction adds *FRICTION with
      a stick slope of the same order.
    """
    out: list[str] = []
    for i, c in enumerate(comp.contacts, start=1):
        if c.capability == "bonded_conformal":
            continue
        names, slave_faces = [], []
        for role, selection in (("SLAVE", c.source), ("MASTER", c.target)):
            group = mesh.groups.get(selection)
            if group is None:
                raise InputError(f"contact '{c.name}': no selection '{selection}' in the mesh")
            name = set_name(f"S{i}{role[0]}", selection)
            hits = _element_faces(group, mesh, faces)
            out.append(f"*SURFACE, NAME={name}, TYPE=ELEMENT")
            out += [f"{e}, S{n}" for e, n in hits]
            names.append(name)
            if role == "SLAVE":
                slave_faces = hits
                slave_area = surface_area(group, mesh)
        if c.capability == "bonded_nonconformal":
            out += [f"*TIE, NAME={set_name('T', c.name)}", ", ".join(names)]
            continue
        h = (slave_area / max(len(slave_faces), 1)) ** 0.5
        # The softer body governs the local compliance; a rigid-ish platen must not
        # stiffen the penalty (it made rim contact chatter in the Belleville check).
        # Factor 50 keeps overclosure ~2% of an element strain; bending-dominated line
        # contact (disc springs, rims) converges only with less, as Ansys advises 0.01-0.1
        # of its default there: contact.extensions["calculix"]["penalty_factor"].
        factor = float(c.extensions.get("calculix", {}).get("penalty_factor", 50.0))
        soft = min(_modulus(project, comp, c.source), _modulus(project, comp, c.target))
        stiffness = factor * soft / h
        interaction = set_name("I", c.name)
        out += [
            f"*CONTACT PAIR, INTERACTION={interaction}, TYPE=SURFACE TO SURFACE",
            ", ".join(names),
            f"*SURFACE INTERACTION, NAME={interaction}",
            "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR",
            f"{stiffness:.6g}",
        ]
        if c.type == "frictional":
            out += ["*FRICTION", f"{c.friction:.10g}, {stiffness:.6g}"]
    return out


def _modulus(project: Project, comp: Component, selection: str) -> float:
    """Young's modulus of the material on the part that `selection` belongs to."""
    part = selection.split("/", 1)[0]
    for a in comp.materials:
        if a.selection == part or a.selection.startswith(part + "/"):
            props = project.get(a.material, Material).properties
            if "youngs_modulus" in props:
                return props["youngs_modulus"].to_si()
    raise InputError(f"contact on '{selection}' needs a youngs_modulus on its part's material")


def _element_faces(group, mesh: Mesh, faces: FaceLookup) -> list[tuple[int, int]]:
    return [hit for b in surface_blocks(group, mesh) for hit in faces.of(b)]


def _nodal_forces(group, mesh: Mesh, total: list[float]) -> list[str]:
    """Spread a total force as a uniform traction, consistently over the selection's faces."""
    if group.dim != 2:
        share = np.asarray(total) / len(group.node_ids)
        return [
            f"{n}, {d}, {share[d - 1]:.10g}"
            for n in group.node_ids
            for d in (1, 2, 3)
            if share[d - 1]
        ]
    weights: dict[int, float] = {}
    for b in surface_blocks(group, mesh):
        if b.element not in NODE_SHARES:
            raise InputError(f"force on {b.element} faces is not supported yet")
        xyz = mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        areas = 0.5 * np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)
        if b.element == "quad4":
            q = mesh.node_coords(b.connectivity[:, [0, 2, 3]].ravel()).reshape(-1, 3, 3)
            areas = areas + 0.5 * np.linalg.norm(
                np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0]), axis=1
            )
        for nodes, area in zip(b.connectivity, areas, strict=True):
            for node, share in zip(nodes, NODE_SHARES[b.element], strict=True):
                if share:
                    weights[int(node)] = weights.get(int(node), 0.0) + area * share
    area_total = sum(weights.values())
    rows = []
    for node, w in sorted(weights.items()):
        for d in (1, 2, 3):
            if total[d - 1]:
                rows.append(f"{node}, {d}, {total[d - 1] * w / area_total:.10g}")
    return rows


def _row(values) -> str:
    """One element line; CalculiX continues long element lines on the next line."""
    values = [str(int(v)) for v in values]
    head, tail = values[:16], values[16:]
    return ", ".join(head) + ("," + "\n" + ", ".join(tail) if tail else "")


def _chunks(ids, per_line: int = 16) -> list[str]:
    ids = [str(int(i)) for i in ids]
    return [", ".join(ids[i : i + per_line]) + "," for i in range(0, len(ids), per_line)]
