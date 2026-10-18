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

from puzzle.adapters.common import (
    STRUCTURAL,
    THERMAL,
    FaceLookup,
    InputError,
    initial_temperature,
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


def set_name(prefix: str, name: str) -> str:
    """A CalculiX set name: letters, digits and underscores, upper case."""
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').upper()}"


def write_inp(project: Project, study: Study, mesh: Mesh) -> str:
    comp = project.get(study.component, Component)
    volume_blocks = [b for b in mesh.blocks if b.dim == 3]
    for b in volume_blocks:
        if b.element not in ELEMENTS:
            raise InputError(f"CalculiX adapter does not support {b.element} elements yet")
    faces = FaceLookup.build(volume_blocks)
    out: list[str] = ["*HEADING", f"Puzzle study: {study.name}"]

    out.append("*NODE, NSET=N_ALL")
    out += [
        f"{n}, {x:.15g}, {y:.15g}, {z:.15g}"
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

    out += _materials(project, comp, mesh)

    solve_steps = study.solve_steps()
    if not solve_steps:
        raise InputError(f"study '{study.name}' has no solve steps")
    physics_types = {p.type for p in comp.physics}
    if "heat_transfer" in physics_types:
        heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
        out += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"N_ALL, {initial_temperature(heat)}"]
    for number, step in enumerate(solve_steps, start=1):
        out += _step(number, step, comp, mesh, faces, physics_types)
    return "\n".join(out) + "\n"


def _materials(project: Project, comp: Component, mesh: Mesh) -> list[str]:
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
        out += [f"*MATERIAL, NAME={name}"] + _material_cards(material, comp)
        out.append(f"*SOLID SECTION, ELSET={set_name('E', assignment.selection)}, MATERIAL={name}")
    if not comp.materials:
        raise InputError(f"component '{comp.name}' has no material assignments")
    return out


def _material_cards(m: Material, comp: Component) -> list[str]:
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
    if "thermal_expansion" in comp.couplings and "thermal_expansion" in p:
        table = m.tables.get("thermal_expansion")
        ref = table.reference_temperature if table and table.reference_temperature else ROOM_K
        cards += [f"*EXPANSION, ZERO={ref:.10g}", f"{p['thermal_expansion']:.10g}"]
    return cards


def _step(
    number: int, step: SolveStep, comp: Component, mesh: Mesh, faces: FaceLookup, physics_types
) -> list[str]:
    solved = set(step.physics) or physics_types
    op = ", OP=NEW" if number > 1 else ""
    settings = step.settings

    def setting(key: str) -> float:
        if key not in settings:
            raise InputError(f"{step.type} step needs a '{key}' setting")
        value = settings[key]
        return value.to_si() if hasattr(value, "to_si") else float(value)

    if solved == {"solid_mechanics"}:
        procedure = {
            "stationary": ["*STATIC"],
            "modal": ["*FREQUENCY", str(int(settings.get("modes", 10)))],
            "transient": ["*DYNAMIC"] + [f"{setting('time_step'):.10g}, {setting('end_time'):.10g}"]
            if step.type == "transient"
            else [],
        }.get(step.type)
        outputs = ["*NODE FILE", "U"] + ([] if step.type == "modal" else ["*EL FILE", "S, E"])
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
    out = ["*STEP, INC=1000000" if step.type == "transient" else "*STEP"] + procedure
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
