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
    STRUCTURAL,
    THERMAL,
    FaceLookup,
    InputError,
    check_contacts,
    initial_temperature,
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

    for part in comp.parts:
        for b in mesh.groups[part.name].blocks(mesh):
            if b.dim != 3:
                continue
            ccx_type, order = ELEMENTS[b.element]
            conn = b.connectivity[:, order] if order else b.connectivity
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
    for number, step in enumerate(prestress, start=1):
        # Large-deformation static step: its stress stiffens the steps after it.
        lines = _step(number, step, comp, mesh, faces, physics_types, thermal_load, yields)
        first = lines[0] if "NLGEOM" in lines[0] else lines[0] + ", NLGEOM"
        out += [first, *lines[1:]]
    for number, step in enumerate(solve_steps, start=len(prestress) + 1):
        lines = _step(number, step, comp, mesh, faces, physics_types, thermal_load, yields)
        if prestress:  # a perturbation of the stressed state, not of the unloaded one
            lines = [lines[0] + ", PERTURBATION", *lines[1:]]
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
) -> list[str]:
    solved = set(step.physics) or physics_types
    # Plasticity is nonlinear like contact: the load goes in by increments, each kept.
    contact = any(c.type in ("frictionless", "frictional") for c in comp.contacts) or (
        yields and step.type == "stationary" and "solid_mechanics" in solved
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
            "transient": ["*DYNAMIC"] + [f"{setting('time_step'):.10g}, {setting('end_time'):.10g}"]
            if step.type == "transient"
            else [],
        }.get(step.type)
        # Reaction forces (RF) let a study sum the support or contact loads.
        outputs = (
            ["*NODE FILE", "U"]
            if step.type == "modal"
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
    if procedure is None:
        raise InputError(
            f"CalculiX adapter does not support {step.type} steps for {sorted(solved)} yet"
        )

    # CalculiX stops after 100 increments per step unless told otherwise.
    head = "*STEP, NLGEOM, INC=10000" if nlgeom else "*STEP"
    out = ["*STEP, INC=1000000" if step.type == "transient" else head] + procedure
    if "heat_transfer" in solved:
        # Temperature tables make heat transfer nonlinear; CalculiX's default field
        # tolerances (0.5% residual, 1% correction) stop short. Linear cases converge at once.
        out += ["*CONTROLS, PARAMETERS=FIELD", "1e-7, 1e-7, 0., 0., 0.02, 1e-5, 1e-3, 1e-8"]
    wanted = (STRUCTURAL if "solid_mechanics" in solved else set()) | (
        THERMAL if "heat_transfer" in solved else set()
    )
    features = [
        f for ph in comp.physics if ph.type in solved for f in ph.features if f.type in wanted
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in ("fixed", "displacement")]
    out += _features(features, mesh, faces, op)
    if thermal_load is not None and solved == {"solid_mechanics"} and step.type != "modal":
        out.append("*TEMPERATURE")
        out += [
            f"{n}, {t:.10g}"
            for n, t in zip(thermal_load.ids, thermal_load.values[:, 0], strict=True)
        ]
    return out + outputs + ["*END STEP"]


def _features(features: list[Feature], mesh: Mesh, faces: FaceLookup, op: str) -> list[str]:
    boundary, cload, dload, dflux, film = [], [], [], [], []
    for f in features:
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
        elif f.type == "heat_flux":
            dflux += [f"{e}, S{n}, {v['q']:.10g}" for e, n in _element_faces(group, mesh, faces)]
        elif f.type == "convection":
            film += [
                f"{e}, F{n}, {v['T_inf']:.10g}, {v['h']:.10g}"
                for e, n in _element_faces(group, mesh, faces)
            ]
    out: list[str] = []
    for card, rows in (
        ("*BOUNDARY", boundary),
        ("*CLOAD", cload),
        ("*DLOAD", dload),
        ("*DFLUX", dflux),
        ("*FILM", film),
    ):
        if rows:
            out += [card + op] + rows
    return out


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
