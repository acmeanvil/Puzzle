"""Write a Code_Aster command file (.comm), run description (.export) and MED mesh.

Results come back as comma-separated tables (IMPR_TABLE), which need no MED or HDF5
reader: nodal displacement, stress (SIGM_NOEU) or temperature, plus frequencies for
modal steps. Feature values follow the other adapters: displacement x/y/z, force
x/y/z (total, applied as a uniform traction), pressure p (positive pushes in),
temperature T, heat_flux q (positive into the body), convection h and T_inf. SI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    HEAT_SOURCES,
    HOLDING,
    PRETENSION,
    REMOTE,
    STRUCTURAL,
    THERMAL,
    VECTOR_LOADS,
    InputError,
    as_planar,
    body_forces,
    body_load,
    check_contacts,
    check_dynamics,
    cylindrical_expressions,
    fastener_selections,
    feature_vector,
    frequency_list,
    globalize,
    initial_temperature,
    load_area,
    local_axes,
    pressure_forces,
    rayleigh,
    remote_load,
    remote_values,
    rigid_motion,
    section_cuts,
    support_nodes,
    support_stiffness,
    surface_area,
    surface_blocks,
    thermal_tables,
    time_scheme,
    time_settings,
    traction_forces,
)
from puzzle.mesh.med import safe_names, write_med
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model.schema import Component, Feature, Material, Project, Study

JOB = "job"
ROOM_K = 293.15
TABLES = {
    "displacement": 81,
    "stress": 82,
    "temperature": 83,
    "frequency": 84,
    "reaction_force": 85,
    "plastic_strain": 86,
    "plastic_strain_equivalent": 87,
    "buckling": 88,
    "beam_forces": 89,
    "displacement_imag": 91,  # a harmonic response's imaginary part (ADR 0041)
    "contact": 92,  # CONT_NOEU on the slave nodes (ADR 0056)
}
FORMAT_R = "1PE18.10"


CONTACTS = {"bonded_nonconformal", "frictionless", "frictional"}


TEMPERATURE_UNIT = 90  # the transferred temperature table


TRACK_TITLE = "PZTRK{}"  # a SUIVI_DDL column's title, read back by the progress parser
_EVAL = {"max": "MAX", "min": "MIN", "mean": "MOY"}


POINT_GROUP = "PZTP{}"  # the one-node group of a tracked point
MAX_COLUMNS = 4  # SUIVI_DDL's limit


def followed(trackers: list) -> list:
    """The trackers Code_Aster follows in its Newton table (SUIVI_DDL, ADR 0054): a
    displacement component over a selection (maximum, minimum or mean), a point's
    displacement, or a selection's reaction (FORC_NODA's mean there, which the parser
    multiplies by the node count: SUIVI_DDL has no sum). A total (a magnitude) at a
    point or of a reaction is followed as its three components. SUIVI_DDL takes four
    columns at most, filled in the probes' order. Others are left to the finished
    result."""

    def kind(t) -> bool:
        if t.what not in ("x", "y", "z", "total"):
            return False
        if t.field == "reaction_force":
            return bool(t.selection) and t.reduce == "sum"
        if t.field != "displacement":
            return False
        if t.selection:
            return t.what != "total" and t.reduce in _EVAL
        return t.points is not None

    out, columns = [], 0
    for t in trackers:
        need = 3 if t.what == "total" else 1
        if kind(t) and columns + need <= MAX_COLUMNS:
            out.append(t)
            columns += need
    return out


def tracked_columns(tracked: list) -> list[tuple[str, object, str]]:
    """(SUIVI_DDL title, tracker, component) per column: one per component followed."""
    return [
        (TRACK_TITLE.format(i) + (c.upper() if t.what == "total" else ""), t, c)
        for i, t in enumerate(tracked, start=1)
        for c in (("x", "y", "z") if t.what == "total" else (t.what,))
    ]


def _suivi(tracked: list, names: dict[str, str]) -> str:
    if not tracked:
        return ""
    index = {id(t): i for i, t in enumerate(tracked, start=1)}
    entries = []
    for title, t, c in tracked_columns(tracked):
        group = names[t.selection] if t.selection else POINT_GROUP.format(index[id(t)])
        field, how = (
            ("FORC_NODA", "MOY") if t.reduce == "sum" else ("DEPL", _EVAL.get(t.reduce, "MAX"))
        )
        entries.append(
            f"_F(NOM_CHAM='{field}', NOM_CMP=('D{c.upper()}',), GROUP_NO='{group}', "
            f"EVAL_CHAM='{how}', TITRE=('{title}',))"
        )
    return f"SUIVI_DDL=({', '.join(entries)},), "


def _point_groups(tracked: list, mesh) -> list[str]:
    """One-node groups for tracked points: the nodes within a hair of each point
    (Code_Aster's sphere needs a radius above zero)."""
    import numpy as np

    xyz = np.asarray(mesh.coords, dtype=float)
    tol = 1e-6 * float(np.linalg.norm(xyz.max(axis=0) - xyz.min(axis=0)) or 1.0)
    out = []
    for i, t in enumerate(tracked, start=1):
        if t.selection is None:
            x, y, z = (float(v) for v in t.points[0])
            out.append(
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F("
                f"NOM='{POINT_GROUP.format(i)}', OPTION='ENV_SPHERE', POINT=({x!r}, {y!r}, {z!r}), "
                f"RAYON={tol!r}, PRECISION={tol!r}))"
            )
    return out


def _refuse_in_2d(comp: Component) -> None:
    """What Code_Aster's 2D models don't take here yet (ADR 0048), refused by name."""
    if comp.connectors or comp.beam_sections:
        raise InputError("fasteners and beams are not written in 2D models", code="unsupported")
    for c in comp.contacts:
        if c.capability != "bonded_conformal":
            raise InputError(
                f"contact '{c.name}' ({c.capability}) is not written in 2D models yet",
                code="unsupported",
            )
    unwritten = REMOTE | PRETENSION | {"elastic_support", "compression_only_support"}
    for ph in comp.physics:
        for f in ph.features:
            if f.type in unwritten:
                raise InputError(f"{f.type} is not written in 2D models yet", code="unsupported")


def _whole_part_marker(mesh: Mesh, comp: Component, workdir: Path) -> None:
    """What the reader multiplies Code_Aster's 2D reactions by, so totals mean the whole
    part (ADR 0048 decision 6): AXIS gives them per radian (2 pi), C_PLAN per unit
    thickness (each part's thickness on its nodes). Plane strain is per metre already.
    Measured in tests/integration/test_planar_totals.py."""
    from puzzle.adapters.common import plane_thickness

    if comp.dimension == "2d_axisymmetric":
        marker = {"scale": 2 * np.pi}
    elif comp.planar_model == "plane_stress":
        parts = [g for g in mesh.groups.values() if "/" not in g.name and g.dim == 2]
        marker = {"parts": [{"t": plane_thickness(comp, g.name), "nodes": g.node_ids.tolist()}
                            for g in parts]}  # fmt: skip
    else:
        return
    (workdir / f"{JOB}.totals.json").write_text(json.dumps(marker))


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    comp = project.get(study.component, Component)
    as_planar(mesh, comp)  # ADR 0048: a 2D model's faces are regions, not shells
    if mesh.planar:
        _refuse_in_2d(comp)
        _whole_part_marker(mesh, comp, workdir)
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    check_contacts(comp, CONTACTS, "Code_Aster")
    steps = study.solve_steps()
    # A preloaded buckling step comes with its static step (run_study joins them).
    preloaded = (
        len(steps) == 2
        and steps[0].type == "stationary"
        and steps[1].type == "buckling"
        and bool(steps[1].settings.get("preload", False))
    )
    if len(steps) != 1 and not preloaded:
        raise InputError("the Code_Aster adapter runs studies with exactly one solve step for now")
    step = steps[-1]
    solved = set(step.physics) or {p.type for p in comp.physics}
    if solved not in ({"solid_mechanics"}, {"heat_transfer"}):
        raise InputError(
            "the Code_Aster adapter solves solid_mechanics or heat_transfer alone for now, "
            f"not {sorted(solved)}: set the step's physics to one of them (study > steps > "
            "physics). For thermal stress, solve heat in its own study and pass its "
            "temperature to a structural study as a transfer (role load)."
        )
    thermal = solved == {"heat_transfer"}
    allowed = (
        ("stationary", "transient")
        if thermal
        else ("stationary", "modal", "buckling", "transient", "frequency_domain")
    )
    if step.type not in allowed:
        raise InputError(
            f"the Code_Aster adapter does not run {step.type} {sorted(solved)} steps yet"
        )

    wanted = THERMAL | HEAT_SOURCES if thermal else STRUCTURAL
    features = [
        f for ph in comp.physics if ph.type in solved for f in ph.features if f.type in wanted
    ]
    # Elastic supports are part of the structure, modal steps too (ADR 0037).
    springs = [f for f in features if f.type == "elastic_support"]
    fasteners = list(comp.connectors)
    if fasteners and thermal:
        raise InputError(
            "Code_Aster writes beam fasteners in structural studies only so far",
            code="unsupported",
        )
    lifting = [f for f in features if f.type == "compression_only_support"]
    if lifting and (thermal or step.type != "stationary"):
        raise InputError(
            "Code_Aster writes compression-only supports in static steps only", code="unsupported"
        )
    bolts = [f for f in features if f.type in PRETENSION and step.type != "modal"]
    features = [
        f
        for f in features
        if f.type not in ("elastic_support", "compression_only_support")
        and f.type not in PRETENSION
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in HOLDING]
    features = globalize(project, features)  # Cartesian local axes as global (ADR 0037)
    preload = []
    if step.type == "buckling":
        _check_buckling(features + springs + lifting + bolts, step)
        if preloaded:  # the static step's features, held; the buckling step's, scaled
            preload = [f for f in features if f.active(1)]
            features = [f for f in features if f.active(2)]
    if not comp.materials:
        raise InputError(f"component '{comp.name}' has no material assignments")
    ties = [c for c in comp.contacts if c.capability == "bonded_nonconformal"]
    if ties and thermal:
        raise InputError(
            "Code_Aster ties nonconformal parts in structural studies only so far",
            code="unsupported",
        )
    touching = [c for c in comp.contacts if c.type in ("frictionless", "frictional")]
    if touching and (thermal or step.type != "stationary"):
        raise InputError(
            "Code_Aster contact is written for static structural steps only so far",
            code="unsupported",
        )
    _check_friction(touching, features, mesh)
    _check_curved(touching, mesh)
    nlgeom = bool(step.settings.get("geometric_nonlinearity", False))
    if nlgeom and (thermal or step.type != "stationary"):
        raise InputError(
            "geometric_nonlinearity applies to static structural steps", code="unsupported"
        )
    plastic = (
        [a for a in comp.materials if project.get(a.material, Material).models.plasticity]
        if not thermal and step.type == "stationary"
        else []
    )
    # A tie projects the source (slave) face's nodes into the target part's volume.
    tie_groups = [g for c in ties for g in (c.source, c.target.split("/", 1)[0])]
    tie_groups += [g for c in touching for g in (c.source, c.target)]
    from puzzle.trackers import trackers

    # ADR 0054: probes tracked while solving, as SUIVI_DDL columns in the Newton table.
    tracked = [] if thermal else followed(trackers(project, study, mesh)[0])
    tie_groups += [t.selection for t in tracked if t.selection]
    # ADR 0044 stage 3: an initial stress the static step starts from (STAT_NON_LINE).
    from puzzle.adapters.graded import initial_stresses

    initial = [] if thermal else initial_stresses(comp, study, "Code_Aster")
    groups = list(
        dict.fromkeys(
            [a.selection for a in comp.materials]
            + [f.selection for f in initial]
            + [f.selection for f in features + preload + springs + lifting + bolts]
            + fastener_selections(fasteners)
            + tie_groups
        )
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")
    names = safe_names(groups)
    supports = _spring_nodes(springs, mesh)
    gaps = _spring_nodes(lifting, mesh)
    # ADR 0053, as CalculiX (ADR 0039): a contact-only step starts at the full load and
    # Code_Aster cuts the step down only when an increment fails (DEFI_LIST_INST);
    # plasticity, large deflection and compression-only gaps keep the 10-step ramp,
    # since their answer follows the loading path.
    ramped = nlgeom or bool(plastic) or bool(gaps)
    increments = int(step.settings.get("increments", 10 if ramped else 1))
    top = int(mesh.node_ids.max())
    cuts = list(
        section_cuts(comp, mesh, {id(f): top + 1 + k for k, f in enumerate(bolts)}).values()
    ) if bolts else []  # fmt: skip
    base = _cut(mesh, cuts) if cuts else mesh
    held = {
        int(n)
        for f in features
        if f.type in ("fixed", "displacement") and f.selection in mesh.groups
        for n in mesh.groups[f.selection].node_ids
    }
    base, fastener_ends, tight = _with_bolts(base, fasteners, mesh, names, held)
    if step.type != "modal":  # a tightened fastener: a pretension on its bolt's mid-plane
        bolts += [f for f, _cut in tight]
        cuts += [c for _f, c in tight]
    if fastener_ends:  # the reader sums the bolt end faces' reactions (ADR 0027)
        (workdir / f"{JOB}.fasteners.json").write_text(json.dumps(fastener_ends))
    # Beams (ADR 0027): line bodies as POU_D_T, which takes two-node lines only.
    beam_parts = [
        p.name for p in comp.parts if mesh.groups.get(p.name) and mesh.groups[p.name].dim == 1
    ]
    if any(b.element == "line3" for b in mesh.beam_blocks()) and not thermal:
        raise InputError(
            "Code_Aster's beams (POU_D_T) are two-node lines: set the mesh recipe's element "
            "order to 1 for line bodies",
            code="unsupported",
        )
    beams = []
    for b in comp.beam_sections:
        if b.selection not in mesh.groups:
            raise InputError(f"a beam section names '{b.selection}', which is not meshed")
        names.setdefault(b.selection, f"G{len(names) + 1}")
        beams.append((names[b.selection], b))
    for i, (c, end) in enumerate(zip(fasteners, fastener_ends, strict=True), start=1):
        if c.formulation == "beam":  # a bolt beam's round section, its 1-axis across it
            from puzzle.model.schema import BeamSection

            beams.append((f"F{i}", BeamSection(selection=f"F{i}", shape="circle",
                                               diameter=c.diameter,
                                               orientation=tuple(end["side"]))))  # fmt: skip
    bolt_beams = any(c.formulation == "beam" for c in fasteners)
    for part in beam_parts:
        if not any(b.selection.split("/", 1)[0] == part for b in comp.beam_sections):
            raise InputError(f"the line body '{part}' has no beam section", code="invalid")
    # Shells (ADR 0027): sheet bodies as DKT (Kirchhoff plates and shells), which takes
    # three- and four-node faces.
    shell_parts = [
        p.name
        for p in comp.parts
        if mesh.groups.get(p.name) and mesh.groups[p.name].dim == 2 and not mesh.planar
    ]
    if any(b.element in ("tri6", "quad8", "quad9") for b in mesh.shell_blocks()) and not thermal:
        raise InputError(
            "Code_Aster's shells (DKT) are three- and four-node faces: set the mesh recipe's "
            "element order to 1 for sheet bodies",
            code="unsupported",
        )
    shells = []
    # A plane-stress part's thickness only spreads its loads (C_PLAN is per unit
    # thickness): no shell characteristics for it.
    for s in [] if mesh.planar else comp.shell_sections:
        if s.selection not in mesh.groups:
            raise InputError(f"a shell thickness names '{s.selection}', which is not meshed")
        names.setdefault(s.selection, f"G{len(names) + 1}")
        shells.append((names[s.selection], s))
    for part in shell_parts:
        if not any(s.selection.split("/", 1)[0] == part for s in comp.shell_sections):
            raise InputError(f"the sheet body '{part}' has no thickness", code="invalid")
    points = _Points(base, names)
    # Point selections (a beam's ends) as point elements: the mesh export writes no
    # group of points alone.
    point_groups = [g for g in groups if mesh.groups[g].dim == 0]
    for g in point_groups:
        points.gather(g, mesh.groups[g].node_ids)
    for i, (node, *_rest) in enumerate(supports, start=1):
        points.add(f"S{i}", node, "SPRINGS")
    for i, (node, *_rest) in enumerate(gaps, start=1):
        points.add(f"C{i}", node, "GAPS")
    for i, end in enumerate(fastener_ends, start=1):
        if end.get("cut"):  # a beam bolt's cut: its rotations tied across it
            points.add(f"T{i}A", end["cut"][0])
            points.add(f"T{i}B", end["cut"][1])
        if end.get("beam"):  # the beam's own end nodes: named, no element of their own
            points.add(f"E{i}A", end["masters"][0])  # (an element there would halve the
            points.add(f"E{i}B", end["masters"][1])  # node's averaged beam forces)
        elif end["distributing"]:  # a 6-dof master point per end for LIAISON_RBE3
            points.add(f"M{i}A", end["masters"][0], "MASTERS")
            points.add(f"M{i}B", end["masters"][1], "MASTERS")
    for k, cut in enumerate(cuts, start=1):
        points.add(f"PR{k}", cut.ref, "PRETENSION")
        for i, (node, twin) in enumerate(cut.doubles.items(), start=1):
            points.add(f"P{k}A{i}", node)
            points.add(f"P{k}B{i}", twin)
    # ADR 0044: a pressure from an imported field, as consistent nodal forces at the
    # face's nodes (Code_Aster's PRES_REP takes one value per group).
    given = {i.source: i.field for i in inputs if i.role == "feature"}
    field_forces: list[str] = []
    # A pressure varying with frequency in a harmonic step: the nodal forces at each of
    # the field's frequencies, each its own charge, multiplied in DYNA_VIBRA by a hat
    # function of FREQ (linear between the frequencies, held outside them).
    spectral: list[tuple[tuple[float, ...], list[list[str]]]] = []
    for j, f in enumerate([f for f in features if f.type == "pressure" and "p" in f.fields], 1):
        from puzzle.fields import feature_key
        from puzzle.results.model import Field

        if mesh.planar:  # its normals come from the solids' faces
            raise InputError(
                "Code_Aster takes a pressure from a field in 3D models only so far: in 2D, "
                "a traction normal to the edge does it",
                code="unsupported",
            )

        key = feature_key(f, "p")
        frequencies = next((i.frequencies for i in inputs if i.role == "feature"
                            and i.source == key and i.frequencies), None)  # fmt: skip
        field = given[key]
        columns = range(field.values.shape[1]) if frequencies else [0]
        per = [pressure_forces(mesh.groups[f.selection], mesh,
                               Field(key, "node", [""], field.ids, field.values[:, [c]]))
               for c in columns]  # fmt: skip
        rows: list[list[str]] = [[] for _ in per]
        for i, node in enumerate(sorted(set().union(*per)), start=1):
            points.add(f"FP{j}N{i}", node)
            for forces, out_rows in zip(per, rows, strict=True):
                force = forces.get(node, np.zeros(3))
                parts = ", ".join(f"F{a.upper()}={force[k]:.12g}" for k, a in enumerate("xyz"))
                out_rows.append(f"_F(GROUP_NO='FP{j}N{i}', {parts})")
        if frequencies:
            if step.type != "frequency_domain":
                raise InputError(
                    f"a pressure varying with frequency (on {f.selection}) applies in "
                    f"harmonic steps only, not {step.type}",
                    code="field_axis",
                )
            spectral.append((tuple(frequencies), rows))
        else:
            field_forces += rows[0]
    features = [f for f in features if not (f.type == "pressure" and "p" in f.fields)]
    # ADR 0044 stage 2: a traction or body force from a field as consistent nodal forces
    # (∫N t dA, ∫N f dV), a displacement from a field per node (DDL_IMPO), each node in
    # its own point group. A 2D model's are per metre of depth (C_PLAN, D_PLAN) or per
    # radian (AXIS), as Code_Aster takes FORCE_NODALE there.
    field_imposed: list[str] = []
    vectors = [f for f in features if f.type in VECTOR_LOADS and f.fields]
    for j, f in enumerate(vectors, 1):
        keys = ("tx", "ty", "tz") if f.type == "traction" else ("fx", "fy", "fz")
        nodal = traction_forces if f.type == "traction" else body_forces

        def at(ids, f=f, keys=keys):
            return feature_vector(f, keys, given, ids)[0]

        forces = nodal(mesh.groups[f.selection], mesh, at, comp, per_unit=True)
        axes = "xy" if mesh.planar else "xyz"  # z is out of a 2D model's plane
        for i, (node, force) in enumerate(sorted(forces.items()), start=1):
            if not force[: len(axes)].any():
                continue
            points.add(f"FV{j}N{i}", node)
            parts = ", ".join(f"F{a.upper()}={force[k]:.12g}" for k, a in enumerate(axes))
            field_forces.append(f"_F(GROUP_NO='FV{j}N{i}', {parts})")
    shapes = [f for f in features if f.type == "displacement" and f.fields]
    for j, f in enumerate(shapes, 1):
        ids = mesh.groups[f.selection].node_ids
        u, present = feature_vector(f, "xyz", given, ids)
        present = [d for d in present if not (mesh.planar and d == 2)]
        for i, node in enumerate(ids, start=1):
            points.add(f"FD{j}N{i}", int(node))
            dofs = ", ".join(f"D{'XYZ'[d]}={u[i - 1, d]:.12g}" for d in present)
            field_imposed.append(f"_F(GROUP_NO='FD{j}N{i}', {dofs})")
    features = [f for f in features if not any(f is g for g in vectors + shapes)]
    # ADR 0044 stage 3: graded materials and varying shell thicknesses, binned into
    # element groups of their own; an initial stress on solids only.
    graded, shells = _stage3_bins(project, comp, mesh, points, given, thermal, shells)
    if initial and (beam_parts or shell_parts or bolt_beams):
        raise InputError(
            "Code_Aster takes an initial stress on solid models only so far (no beams, "
            "shells or beam bolts)",
            code="unsupported",
        )
    write_med(points.mesh, workdir / f"{JOB}.med", names)

    model = _modelisation(comp, thermal)
    phenomenon = "THERMIQUE" if thermal else "MECANIQUE"
    faces = {names[g] for c in touching for g in (c.source, c.target)}
    if mesh.planar:  # a 2D model's edges aren't oriented by the mesher: pressure needs it
        faces |= {names[f.selection] for f in features if f.type == "pressure"}
    faces = sorted(faces)
    excluded = _contact_exclusions(touching, fasteners, mesh, names)
    noded = {  # fastener faces made node groups below
        names[sel]
        for c in fasteners
        if c.ends == "distributing" or c.formulation == "beam"
        for sel in (c.source, c.target)
        if sel is not None
    }
    unnoded = sorted(
        (
            {g for gs in excluded.values() for g in gs}
            | {names[t.selection] for t in tracked if t.selection}
        )
        - noded
        - {names[g] for g in point_groups}
    )
    massif = _material_axes(project, comp, names, thermal)
    out = [
        "DEBUT(LANG='EN')",
        "mesh = LIRE_MAILLAGE(FORMAT='MED', UNITE=20)",
        *(
            [
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F(GROUP_MA=("
                + ", ".join(f"'{names[g]}'" for g in point_groups)
                + ",)))"
            ]
            if point_groups
            else []
        ),
        # Contact faces must have outward normals.
        *(
            [
                "mesh = MODI_MAILLAGE(reuse=mesh, MAILLAGE=mesh, ORIE_PEAU=_F(GROUP_MA_PEAU=("
                + ", ".join(f"'{g}'" for g in faces)
                + ",)))"
            ]
            if faces
            else []
        ),
        # Fastener faces left out of contact, and tracked selections (ADR 0054), that
        # aren't node groups yet.
        *(
            [
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F(GROUP_MA=("
                + ", ".join(f"'{g}'" for g in unnoded)
                + ",)))"
            ]
            if unnoded
            else []
        ),
        *_point_groups(tracked, mesh),
        # A field's pressure as nodal forces (ADR 0044): their point groups as node groups.
        *(
            [
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F(GROUP_MA=("
                + ", ".join(f"'{g}'" for g in points.names if g.startswith(("FP", "FV", "FD")))
                + ",)))"
            ]
            if field_forces or field_imposed or spectral
            else []
        ),
        # Pretension relations name single nodes: their point groups as node groups.
        *(
            [
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F(GROUP_MA=("
                + ", ".join(f"'{g}'" for g in points.names if g[0] == "P")
                + ",)))"
            ]
            if cuts
            else []
        ),
        *(
            [
                "mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=_F(GROUP_MA=("
                + ", ".join(
                    f"'{g}'"
                    for g in [n for n in points.names if n.startswith(("M", "T", "E"))]
                    + sorted(
                        {
                            names[sel]
                            for c in fasteners
                            if c.ends == "distributing" or c.formulation == "beam"
                            for sel in (c.source, c.target)
                            if sel is not None
                        }
                    )
                    + [
                        f"F{i}{e}"
                        for i, c in enumerate(fasteners, start=1)
                        if c.ends == "distributing" and c.formulation != "beam"
                        for e in "AB"
                    ]
                )
                + ",)))"
            ]
            if any(c.ends == "distributing" or c.formulation == "beam" for c in fasteners)
            else []
        ),  # fmt: skip
        "model = AFFE_MODELE(MAILLAGE=mesh, "
        f"AFFE=(_F(TOUT='OUI', PHENOMENE='{phenomenon}', MODELISATION='{model}'),"
        + "".join(
            f" _F(GROUP_MA='{names[p]}', PHENOMENE='MECANIQUE', MODELISATION='POU_D_T'),"
            for p in beam_parts
        )
        + "".join(
            f" _F(GROUP_MA='F{i}', PHENOMENE='MECANIQUE', MODELISATION='POU_D_T'),"
            for i, c in enumerate(fasteners, start=1)
            if c.formulation == "beam"
        )
        + "".join(
            f" _F(GROUP_MA='{names[p]}', PHENOMENE='MECANIQUE', MODELISATION='DKT'),"
            for p in shell_parts
        )
        + "".join(
            f" _F(GROUP_MA='{g}', PHENOMENE='MECANIQUE', "
            f"MODELISATION='{'DIS_TR' if g == 'MASTERS' else 'DIS_T'}'),"
            for g in points.sets
        )
        + "))",
        *_discrete_cara(
            supports,
            len(cuts),
            _negligible(project, comp, mesh),
            gaps,
            [n for n in points.names if n.startswith("M")],
            beams,
            shells,
            massif,
        ),
    ]
    cara = "CARA_ELEM=cara, " if points.sets or beams or shells or massif else ""
    assignments = []
    materials = [project.get(a.material, Material) for a in comp.materials]
    tables = [thermal_tables(m, comp) if thermal else {} for m in materials]
    nonlinear = any(tables)  # temperature tables: THER_NL and THER_NON_LINE
    for i, (a, m, t) in enumerate(zip(comp.materials, materials, tables, strict=True), start=1):
        if m.models.orthotropic_elasticity and m.models.plasticity and not thermal:
            raise InputError(f"material '{m.name}': orthotropic plasticity is not written",
                             code="unsupported")  # fmt: skip
        if nonlinear and m.models.anisotropic_conductivity:
            raise InputError(
                f"material '{m.name}': Code_Aster's temperature tables (THER_NL) are isotropic; "
                "set the heat physics' temperature-dependent materials off to use its "
                "anisotropic conductivity", code="unsupported")  # fmt: skip
        if i in graded:  # ADR 0044 stage 3: one material per bin of the graded property
            damping = None if thermal else rayleigh(study)
            for k, (group, mk) in enumerate(graded[i], start=1):
                out.append(
                    f"mat{i}_{k} = DEFI_MATERIAU("
                    f"{_material(mk, thermal, thermal_load is not None, damping)})"
                )
                assignments.append(f"_F(GROUP_MA='{group}', MATER=mat{i}_{k})")
            continue
        if nonlinear:
            out += _ther_nl(i, m, t)
        else:
            expansion = thermal_load is not None
            traction = ""
            if not thermal and step.type == "stationary" and m.models.plasticity:
                out.append(_traction(i, m))
                traction = f", TRACTION=_F(SIGM=trac{i})"
            damping = None if thermal else rayleigh(study)
            order = _axes_order(project, a)
            out.append(
                f"mat{i} = DEFI_MATERIAU({_material(m, thermal, expansion, damping, order)}"
                f"{traction})"
            )
        assignments.append(f"_F(GROUP_MA='{names[a.selection]}', MATER=mat{i})")
    for i, c in enumerate(fasteners, start=1):  # each bolt in its own material
        m = project.get(c.material, Material)
        out.append(f"matf{i} = DEFI_MATERIAU({_material(m, False)})")
        assignments.append(f"_F(GROUP_MA='F{i}', MATER=matf{i})")
    # A compression-only support: DIS_CHOC on each node's discrete, its stiffness kn × area.
    for i, (_node, kn, _kt, _n) in enumerate(gaps, start=1):
        out.append(f"gap{i} = DEFI_MATERIAU(DIS_CONTACT=_F(RIGI_NOR={kn:.10e}))")
        assignments.append(f"_F(GROUP_MA='C{i}', MATER=gap{i})")
    varc = ""
    if thermal_load is not None:
        if thermal or step.type == "modal":
            raise InputError(
                "a temperature load applies to static structural steps", code="unsupported"
            )
        out += _temperature_field(thermal_load, mesh, workdir)
        varc = (
            ", AFFE_VARC=_F(TOUT='OUI', NOM_VARC='TEMP', CHAM_GD=temperature, "
            f"VALE_REF={_reference(comp):.12g})"
        )
    out.append(f"mater = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=({', '.join(assignments)},){varc})")
    charges = ["load"]
    if thermal:  # loads following an amplitude get their own charge (ADR 0037)
        out += _thermal_loads(project, features, names, mesh, comp, given)
    else:
        extra = _pretension_loads(bolts, cuts)
        if field_forces:
            extra["FORCE_NODALE"] = extra.get("FORCE_NODALE", []) + field_forces
        if field_imposed:  # ADR 0044 stage 2: a displacement from a field, per node
            extra["DDL_IMPO"] = extra.get("DDL_IMPO", []) + field_imposed
        for key, items in _fastener_loads(fastener_ends, fasteners, names).items():
            extra[key] = extra.get(key, []) + items  # both write LIAISON_DDL
        # In a transient step, loads following an amplitude get their own charge,
        # multiplied by it (ADR 0041); the rest act from t = 0 at full value.
        timed = [f for f in features if f.amplitude] if step.type == "transient" else []
        if timed:
            features = [f for f in features if not f.amplitude]
            out += _timed_loads(project, timed, names, mesh, comp)
        plain = _loads(features, names, mesh, thermal, ties, extra, comp)
        if plain.endswith("(MODELE=model)"):  # everything is in loadf: no empty charge
            charges = []
        else:
            out.append(plain)
        remote = _remote_loads(features, names, mesh, project)  # ADR 0037: fields of x, y, z
        if remote:
            out += remote
            charges.append("loadf")

    if thermal:
        out += [
            (_ther_non_line(step, comp) if nonlinear else _ther_lineaire(step, comp)).replace(
                "CHAM_MATER=mater, ", f"CHAM_MATER=mater, {cara}", 1
            ),
            _table("res", "TEMP", "temperature"),
        ]
    elif step.type == "buckling":
        out += _buckling(step, cara, charges, preload, names, mesh, ties)
    elif step.type == "frequency_domain":  # harmonic response, direct (ADR 0041)
        spectra = _spectral_loads(spectral)
        out += spectra["charges"]
        if step.settings.get("method", "direct") == "modal":
            raise InputError(
                "Code_Aster solves harmonic response directly here (method direct); "
                "CalculiX superposes modes",
                code="unsupported",
            )
        check_dynamics(step, "Code_Aster", modal=False)
        freqs = ", ".join(f"{f:.12g}" for f in frequency_list(step))
        damped = rayleigh(study) is not None
        out += [
            f"ASSEMBLAGE(MODELE=model, CHAM_MATER=mater, {cara}CHARGE=({', '.join(charges)},), "
            "NUME_DDL=CO('numddl'),",
            "           MATR_ASSE=(_F(MATRICE=CO('stiff'), OPTION='RIGI_MECA'),",
            "                      _F(MATRICE=CO('mass'), OPTION='MASS_MECA'),"
            + (
                "\n                      _F(MATRICE=CO('damp'), OPTION='AMOR_MECA'),"
                if damped
                else ""
            )
            + "),",
            "           VECT_ASSE=(_F(VECTEUR=CO('force'), OPTION='CHAR_MECA', "
            f"CHARGE=({', '.join(charges)},)),{spectra['vectors']}))",
            f"freqs = DEFI_LIST_REEL(VALE=({freqs},))",
            *spectra["functions"],
            "res = DYNA_VIBRA(TYPE_CALCUL='HARM', BASE_CALCUL='PHYS', MATR_MASS=mass, "
            + ("MATR_AMOR=damp, " if damped else "")
            + "MATR_RIGI=stiff, LIST_FREQ=freqs, EXCIT=(_F(VECT_ASSE=force, COEF_MULT=1.0),"
            + spectra["excit"]
            + "))",
            # Tables take no complex values: each frequency's real and imaginary parts
            # as two real results, the frequency in INST; the reader joins them.
            "parts = {'REEL': [], 'IMAG': []}",
            f"for f in ({freqs},):",
            "    c = CREA_CHAMP(TYPE_CHAM='NOEU_DEPL_C', OPERATION='EXTR', RESULTAT=res, "
            "NOM_CHAM='DEPL', FREQ=f)",
            "    for part in parts:",
            "        parts[part].append(_F(NOM_CHAM='DEPL', INST=f, CHAM_GD=CREA_CHAMP("
            "TYPE_CHAM='NOEU_DEPL_R', OPERATION='C2R', CHAM_GD=c, PARTIE=part)))",
            "real = CREA_RESU(OPERATION='AFFE', TYPE_RESU='EVOL_ELAS', AFFE=parts['REEL'])",
            "imag = CREA_RESU(OPERATION='AFFE', TYPE_RESU='EVOL_ELAS', AFFE=parts['IMAG'])",
            _table("real", "DEPL", "displacement"),
            _table("imag", "DEPL", "displacement_imag"),
        ]
    elif step.type == "transient":  # structural dynamics (ADR 0041)
        if step.settings.get("method", "direct") == "modal":
            raise InputError(
                "Code_Aster solves transients directly here (method direct); CalculiX "
                "also superposes modes",
                code="unsupported",
            )
        check_dynamics(step, "Code_Aster", modal=False)
        dt, end, _ = time_settings(step)
        scheme = (
            f"SCHEMA='HHT', ALPHA={float(step.settings['hht_alpha']):.6g}"
            if float(step.settings.get("hht_alpha", 0.0))
            else "SCHEMA='NEWMARK'"  # average acceleration: no numerical damping
        )
        excit = [f"_F(CHARGE={c})" for c in charges] + [
            f"_F(CHARGE=tload{k}, FONC_MULT=tamp{k})"
            for k in range(1, 1 + len({f.amplitude for f in timed}))
        ]
        solids = mesh.planar or any(b.dim == 3 for b in mesh.blocks)
        out += [
            f"times = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A={end:.12g}, PAS={dt:.12g}))",
            f"res = DYNA_NON_LINE(MODELE=model, CHAM_MATER=mater, {cara}"
            f"EXCIT=({', '.join(excit)},), COMPORTEMENT=_F(RELATION='ELAS'), "
            "INCREMENT=_F(LIST_INST=times), "
            f"SCHEMA_TEMPS=_F({scheme}, FORMULATION='DEPLACEMENT'))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, "
            + ("CONTRAINTE=('SIGM_NOEU',), " if solids else "")
            + "FORCE=('REAC_NODA',))",
            _table("res", "DEPL", "displacement"),
            *([_table("res", "SIGM_NOEU", "stress")] if solids else []),
            _table("res", "REAC_NODA", "reaction_force"),
        ]
    elif step.type == "modal":
        n = int(step.settings.get("modes", 10))
        out += [
            f"ASSEMBLAGE(MODELE=model, CHAM_MATER=mater, {cara}CHARGE=({', '.join(charges)},), "
            "NUME_DDL=CO('numddl'),",
            "           MATR_ASSE=(_F(MATRICE=CO('stiff'), OPTION='RIGI_MECA'),",
            "                      _F(MATRICE=CO('mass'), OPTION='MASS_MECA')))",
            "res = CALC_MODES(MATR_RIGI=stiff, MATR_MASS=mass, OPTION='PLUS_PETITE', "
            f"CALC_FREQ=_F(NMAX_FREQ={n}))",
            _table("res", "DEPL", "displacement"),
            f"IMPR_TABLE(TABLE=RECU_TABLE(CO=res, NOM_PARA='FREQ'), UNITE={TABLES['frequency']}, "
            f"FORMAT='TABLEAU', SEPARATEUR=',', FORMAT_R='{FORMAT_R}')",
        ]
    elif touching or nlgeom or plastic or gaps or initial:
        # Green-Lagrange strain: large rotations, as CalculiX's NLGEOM (GROT_GDEP refuses tet10);
        # with plasticity, logarithmic strains (true stress against true strain).
        strains = ", DEFORMATION='GREEN_LAGRANGE'" if nlgeom else ""
        # Every increment is kept when the deflection is large or the material yields
        # (load-deflection and stress-strain histories).
        which = "" if nlgeom or plastic else "INST=1.0"
        parts = [f"_F(TOUT='OUI', RELATION='ELAS'{strains})"]
        if plastic:
            yielding = ", ".join(f"'{names[a.selection]}'" for a in plastic)
            large = ", DEFORMATION='GDEF_LOG'" if nlgeom else ""
            rest = [a for a in comp.materials if a not in plastic]
            parts = [f"_F(GROUP_MA=({yielding},), RELATION='VMIS_ISOT_TRAC'{large})"]
            if rest:
                elastic = ", ".join(f"'{names[a.selection]}'" for a in rest)
                parts.append(f"_F(GROUP_MA=({elastic},), RELATION='ELAS'{strains})")
        if gaps:  # the compression-only discretes
            parts.append("_F(GROUP_MA='GAPS', RELATION='DIS_CHOC')")
        behaviour = f"COMPORTEMENT=({', '.join(parts)},), "
        out += [
            *([_defi_contact(touching, names, excluded)] if touching else []),
            *_initial_stress_field(initial, mesh, given, names, workdir),  # ADR 0044 stage 3
            # Ramp the loads: friction and large deflection depend on the loading path.
            "ramp = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 1.0))",
            # A failed increment is cut in four, up to five times (1/1024 of a step),
            # rather than ending the run.
            "times = DEFI_LIST_INST(DEFI_LIST=_F(LIST_INST=DEFI_LIST_REEL(DEBUT=0.0, "
            f"INTERVALLE=_F(JUSQU_A=1.0, NOMBRE={increments}))), "
            "ECHEC=_F(EVENEMENT='ERREUR', ACTION='DECOUPE', SUBD_METHODE='MANUEL', "
            "SUBD_PAS=4, SUBD_NIVEAU=5))",
            f"res = STAT_NON_LINE(MODELE=model, CHAM_MATER=mater, {cara}"
            "EXCIT=("
            + ", ".join(f"_F(CHARGE={c}, FONC_MULT=ramp)" for c in charges)
            + ",), "
            + ("CONTACT=contact, " if touching else "")
            + ("ETAT_INIT=_F(SIGM=sig0), " if initial else "")
            + behaviour
            # As Code_Aster's 3D contact validation (ssnv128): tangent every iteration.
            + "NEWTON=_F(REAC_ITER=1), CONVERGENCE=_F(ITER_GLOB_MAXI=200, RESI_GLOB_RELA=1e-6), "
            + _suivi(tracked, names)
            + "INCREMENT=_F(LIST_INST=times))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, CONTRAINTE=('SIGM_NOEU',"
            + (" 'EFGE_NOEU'," if bolt_beams else "")
            + "), FORCE=('REAC_NODA',)"
            # V1 of VMIS_ISOT_TRAC is the cumulated (equivalent) plastic strain p.
            + (", VARI_INTERNE=('VARI_NOEU',), DEFORMATION=('EPSP_NOEU',)" if plastic else "")
            + ")",
            _table("res", "DEPL", "displacement", which),
            _table("res", "SIGM_NOEU", "stress", which),
            _table("res", "REAC_NODA", "reaction_force", which),
            *([_table("res", "CONT_NOEU", "contact", which)] if touching else []),
            *([_table("res", "EFGE_NOEU", "beam_forces", which)] if bolt_beams else []),
            *(
                [
                    _table("res", "EPSP_NOEU", "plastic_strain", which),
                    _table("res", "VARI_NOEU", "plastic_strain_equivalent", which),
                ]
                if plastic
                else []
            ),
        ]
    else:
        solids = mesh.planar or any(b.dim == 3 for b in mesh.blocks)
        options = (["'SIGM_NOEU'"] if solids else []) + (
            ["'EFGE_NOEU'"] if beam_parts or bolt_beams else []
        )
        out += [
            f"res = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, {cara}EXCIT=("
            + ", ".join(f"_F(CHARGE={c})" for c in charges)
            + ",))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, "
            + (f"CONTRAINTE=({', '.join(options)},), " if options else "")  # shells alone: none
            + "FORCE=('REAC_NODA',))",
            _table("res", "DEPL", "displacement"),
            *([_table("res", "SIGM_NOEU", "stress")] if solids else []),
            *([_table("res", "EFGE_NOEU", "beam_forces")] if beam_parts or bolt_beams else []),
            _table("res", "REAC_NODA", "reaction_force"),
        ]
    out.append("FIN()")
    comm = workdir / f"{JOB}.comm"
    comm.write_text(with_settings("\n".join(out) + "\n", study, step))

    used = (
        {"temperature"}
        if thermal
        else (
            {"displacement", "frequency"}
            if step.type == "modal"
            else {"displacement", "buckling"}
            if step.type == "buckling"
            else {"displacement", "displacement_imag"}
            if step.type == "frequency_domain"
            else {"displacement", "stress", "reaction_force", "beam_forces"}
            | ({"plastic_strain", "plastic_strain_equivalent"} if plastic else set())
            | ({"contact"} if touching else set())
        )
    )
    from puzzle.adapters.code_aster import CodeAsterAdapter

    processes = CodeAsterAdapter().processes()  # MPI on a parallel build (ADR 0053)
    export = workdir / f"{JOB}.export"
    export.write_text(
        "\n".join(
            [
                *resources(processes, low_rank=_low_rank(comm.read_text())),
                f"F comm {JOB}.comm D 1",
                f"F mmed {JOB}.med D 20",
                f"F mess {JOB}.mess R 6",
                *[f"F libr {name}.csv R {unit}" for name, unit in TABLES.items() if name in used],
                *(
                    [f"F libr transferred-temperature.txt D {TEMPERATURE_UNIT}"]
                    if thermal_load is not None
                    else []
                ),
                *(  # ADR 0044 stage 3
                    [f"F libr {INITIAL_STRESS_FILE} D {INITIAL_STRESS_UNIT}"] if initial else []
                ),
            ]
        )
        + "\n"
    )
    return export


def _spring_nodes(springs, mesh: Mesh) -> list[tuple[int, float, float, np.ndarray]]:
    """(node, kn × area, kt × area, outward normal) for each elastic support's face
    nodes, lumped by ∫N dA as CalculiX's (ADR 0037)."""
    out = []
    for f in springs:
        group = mesh.groups.get(f.selection)
        if group is None:
            raise InputError(f"elastic support on '{f.selection}': no such selection")
        kn, kt = support_stiffness(f)
        ids, areas, normals = support_nodes(group, mesh)
        out += [
            (int(n), kn * a, kt * a, normal)
            for n, a, normal in zip(ids, areas, normals, strict=True)
        ]
    return out


POINT_TAG = 900_000  # Gmsh point entities for discrete elements, clear of the mesh's own


class _Points:
    """The mesh to write plus point elements on single nodes, each in its own group
    (springs to ground, pretension reference nodes, nodes a relation names), optionally
    also in a set (SPRINGS, PRETENSION) that gets the DIS_T model."""

    def __init__(self, mesh: Mesh, names: dict) -> None:
        self.mesh = Mesh(mesh.node_ids, mesh.coords, list(mesh.blocks), dict(mesh.groups))
        self.out = names  # Puzzle group -> written name; points are added to it
        self.names: list[str] = []
        self.sets: dict[str, list[tuple[int, int]]] = {}
        self.next = int(max(b.ids.max() for b in mesh.blocks)) + 1

    def add(self, name: str, node: int, into: str | None = None) -> None:
        entity = (0, POINT_TAG + len(self.names) + 1)
        self.mesh.blocks.append(
            CellBlock("vertex", 0, entity, np.array([self.next]), np.array([[node]]))
        )
        self.next += 1
        self.mesh.groups[f"__{name}"] = Group(f"__{name}", 0, [entity], np.array([node]))
        self.out[f"__{name}"] = name
        self.names.append(name)
        if into:
            self.sets.setdefault(into, []).append(entity)
            held = self.mesh.groups.get(f"__{into}")
            nodes = np.unique([*(held.node_ids if held else []), node]).astype(np.int64)
            self.mesh.groups[f"__{into}"] = Group(f"__{into}", 0, self.sets[into], nodes)
            self.out[f"__{into}"] = into

    def gather(self, group: str, nodes) -> None:
        """A point selection as point elements, one per node, under its own written
        name (the mesh export writes no group of points alone)."""
        entities = []
        for node in nodes:
            entity = (0, POINT_TAG + 50_000 + self.next)
            self.mesh.blocks.append(
                CellBlock("vertex", 0, entity, np.array([self.next]), np.array([[int(node)]]))
            )
            self.next += 1
            entities.append(entity)
        old = self.mesh.groups[group]
        self.mesh.groups[group] = Group(group, 0, entities, old.node_ids)


def _cut(mesh: Mesh, cuts) -> Mesh:
    """The mesh cut at each pretension section: reference nodes and twins added, and the
    elements on the normal's side (faces too) moved onto the twins (ADR 0037)."""
    ids, xyz = [mesh.node_ids], [mesh.coords]
    blocks = list(mesh.blocks)
    for cut in cuts:
        twins = np.array(list(cut.doubles.values()), dtype=np.int64)
        ids += [np.array([cut.ref], dtype=np.int64), twins]
        xyz += [cut.centre[None], mesh.node_coords(np.array(list(cut.doubles)))]
        moved = []
        for b in blocks:
            touching = np.isin(b.connectivity, list(cut.doubles)).any(axis=1)
            if b.dim == 3:
                side = np.isin(b.ids, list(cut.moved)) & touching
            else:  # a face or edge of a moved element: on the normal's side
                centres = (
                    mesh.node_coords(b.connectivity.ravel())
                    .reshape(*b.connectivity.shape, 3)
                    .mean(axis=1)
                )
                side = touching & ((centres - cut.centre) @ cut.normal > 0)
            if side.any():
                conn = b.connectivity.copy()
                conn[side] = np.vectorize(lambda n, c=cut: c.doubles.get(int(n), n))(conn[side])
                b = CellBlock(b.element, b.dim, b.entity, b.ids, conn)
            moved.append(b)
        blocks = moved
    return _sorted_mesh(np.concatenate(ids), np.vstack(xyz), blocks, dict(mesh.groups))


def _sorted_mesh(ids: np.ndarray, xyz: np.ndarray, blocks, groups) -> Mesh:
    """A mesh with its nodes in id order: Code_Aster numbers nodes in the order they are
    written, so the result tables' numbers are the ids only when they increase."""
    order = np.argsort(ids, kind="stable")
    return Mesh(ids[order], xyz[order], blocks, groups)


BOLT_TAG = 800_000  # Gmsh entities for fastener bolts, clear of the mesh's own


def _with_bolts(mesh: Mesh, fasteners, original: Mesh, names: dict, held=None):
    """The mesh plus each fastener as a small solid bolt (ADR 0027, as on CalculiX): its
    tets in group F<i>, its end faces in F<i>A and F<i>B, and for distributing ends a
    master node at each end. -> (mesh, ends info for the reader)."""
    from puzzle.adapters.common import (
        SectionCut,
        bolt_mesh,
        check_rigid_ends,
        fastener_ends,
        fastener_frame,
        split_bolt,
    )

    if not fasteners:
        return mesh, [], []
    check_rigid_ends(fasteners, original, held)
    node = int(mesh.node_ids.max()) + 1
    elem = int(max(b.ids.max() for b in mesh.blocks)) + 1
    ids, xyz, blocks, groups = [mesh.node_ids], [mesh.coords], list(mesh.blocks), dict(mesh.groups)
    edges = {(0, 1): 4, (1, 2): 5, (0, 2): 6, (0, 3): 7, (2, 3): 8, (1, 3): 9}  # Gmsh tet10
    ends, pretensioned = [], []
    for i, f in enumerate(fasteners, start=1):
        what = f"fastener '{f.name}'"
        a, b = fastener_ends(f, original, what)
        axis, side = fastener_frame(a.centre, b.centre)
        tight = f.pretension is not None
        if f.formulation == "beam":  # a POU_D_T line between the ends' centres, no mesh
            mid = (a.centre + b.centre) / 2
            nodes, points = [node, node + 1], [a.centre, b.centre]
            lines = [[node, node + 1]]
            cut_nodes = None
            node += 2
            if tight:  # split at the middle: the halves' end nodes cut by the opening
                m1, m2, ref = node, node + 1, node + 2
                node += 3
                nodes += [m1, m2, ref]
                points += [mid, mid, mid]
                lines = [[nodes[0], m1], [m2, nodes[1]]]
                cut_nodes = [m1, m2]
                pretensioned.append((
                    Feature(type="bolt_pretension", selection=f.source,
                            values={"F": f.pretension}),
                    SectionCut(ref, axis, mid, {m1: m2}, set()),
                ))  # fmt: skip
            ids.append(np.array(nodes))
            xyz.append(np.array(points))
            entity = (1, BOLT_TAG + i)
            conn = np.array(lines, dtype=np.int64)
            blocks.append(CellBlock("line2", 1, entity, np.arange(elem, elem + len(conn)), conn))
            elem += len(conn)
            beam_nodes = [n for n in nodes if n != (nodes[-1] if tight else None)]
            groups[f"__bolt{i}"] = Group(f"__bolt{i}", 1, [entity], np.unique(beam_nodes))
            names[f"__bolt{i}"] = f"F{i}"
            ends.append({
                "name": f.name, "axis": [float(c) for c in axis],
                "side": [float(c) for c in side], "beam": True,
                "a": {"centre": [float(c) for c in a.centre], "nodes": [nodes[0]],
                      "xyz": [a.centre.tolist()]},
                "distributing": f.ends == "distributing", "masters": nodes[:2],
                "cut": cut_nodes,
            })  # fmt: skip
            continue
        coords, tets, at_a, at_b, middle = bolt_mesh(
            a.centre, b.centre, f.diameter.to_si(), f.elements, tight
        )
        twins = {}
        if tight:  # cut at its mid-plane (ADR 0037's bolt section)
            tets, twins, twin_xyz = split_bolt(coords, tets, middle, a.centre, b.centre, 0)
            coords = np.vstack([coords, twin_xyz])
        new = np.arange(node, node + len(coords))
        node += len(coords)
        if tight:
            ref = node + 2  # after the bolt's nodes and its two masters: ids stay in order
            cut = SectionCut(
                ref,
                axis,
                (a.centre + b.centre) / 2,
                {int(new[k]): int(new[t]) for k, t in twins.items()},
                set(),
            )
            section = Feature(
                type="bolt_pretension", selection=f.source, values={"F": f.pretension}
            )
            pretensioned.append((section, cut))
        masters = [node, node + 1]
        node += 2
        # Code_Aster numbers nodes in the order they are written: keep ids increasing.
        ids += [new, np.array(masters)]
        xyz += [coords, np.array([a.centre, b.centre])]
        if tight:
            ids.append(np.array([node]))
            xyz.append(((a.centre + b.centre) / 2)[None])
            node += 1
        body = (3, BOLT_TAG + i)
        blocks.append(CellBlock("tet10", 3, body, np.arange(elem, elem + len(tets)), new[tets]))
        elem += len(tets)
        groups[f"__bolt{i}"] = Group(f"__bolt{i}", 3, [body], np.unique(new))
        names[f"__bolt{i}"] = f"F{i}"
        for tag, face, label in ((1, at_a, "A"), (2, at_b, "B")):
            on = set(face.tolist())
            tris = []
            for t in tets:
                for p, q, r in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
                    if {t[p], t[q], t[r]} <= on:
                        mids = [t[edges[tuple(sorted(e))]] for e in ((p, q), (q, r), (p, r))]
                        tris.append([t[p], t[q], t[r], *mids])
            entity = (2, BOLT_TAG + 10 * i + tag)
            tri = new[np.array(tris)]
            blocks.append(CellBlock("tri6", 2, entity, np.arange(elem, elem + len(tri)), tri))
            elem += len(tri)
            key = f"__bolt{i}{label.lower()}"
            groups[key] = Group(key, 2, [entity], np.unique(new[face]))
            names[key] = f"F{i}{label}"
        ends.append(
            {
                "name": f.name,
                "axis": [float(c) for c in axis],
                "a": {"centre": [float(c) for c in a.centre],
                      "nodes": [int(n) for n in new[at_a]], "xyz": coords[at_a].tolist()},
                "distributing": f.ends == "distributing",
                "masters": masters,
            }
        )  # fmt: skip
    return _sorted_mesh(np.concatenate(ids), np.vstack(xyz), blocks, groups), ends, pretensioned


def _fastener_loads(ends: list[dict], fasteners, names: dict) -> dict[str, list[str]]:
    """Rigid ends: the hole's face and the bolt end move as one (LIAISON_SOLIDE).
    Distributing ends: the bolt end is rigid with its master point, and the master
    follows the hole on average (LIAISON_RBE3)."""
    out: dict[str, list[str]] = {"LIAISON_SOLIDE": [], "LIAISON_RBE3": [], "LIAISON_DDL": []}
    for i, (end, f) in enumerate(zip(ends, fasteners, strict=True), start=1):
        if end.get("beam"):  # the beam's end node is the spider's master
            for label, sel in (("A", f.source), ("B", f.target)):
                master = f"E{i}{label}"
                if sel is None:  # a bolt to ground (ADR 0050): its far end held
                    out.setdefault("DDL_IMPO", []).append(
                        f"_F(GROUP_NO='{master}', DX=0.0, DY=0.0, DZ=0.0, DRX=0.0, DRY=0.0, "
                        "DRZ=0.0)"
                    )
                    continue
                if f.ends == "rigid":
                    out["LIAISON_SOLIDE"].append(f"_F(GROUP_NO=('{names[sel]}', '{master}'))")
                else:
                    out["LIAISON_RBE3"].append(
                        f"_F(GROUP_NO_MAIT='{master}', DDL_MAIT=('DX', 'DY', 'DZ', 'DRX', "
                        f"'DRY', 'DRZ'), GROUP_NO_ESCL=('{names[sel]}',), "
                        "DDL_ESCL=('DX-DY-DZ',), COEF_ESCL=(1.0,))"
                    )
            if end.get("cut"):  # one beam through the cut: its rotations continue
                for dof in ("DRX", "DRY", "DRZ"):
                    out["LIAISON_DDL"].append(
                        f"_F(GROUP_NO=('T{i}B', 'T{i}A'), DDL=('{dof}', '{dof}'), "
                        "COEF_MULT=(1.0, -1.0), COEF_IMPO=0.0)"
                    )
            continue
        for label, sel in (("A", f.source), ("B", f.target)):
            if not end["distributing"]:
                out["LIAISON_SOLIDE"].append(f"_F(GROUP_MA=('{names[sel]}', 'F{i}{label}'))")
                continue
            master = f"M{i}{label}"
            out["LIAISON_SOLIDE"].append(f"_F(GROUP_NO=('F{i}{label}', '{master}'))")
            out["LIAISON_RBE3"].append(
                f"_F(GROUP_NO_MAIT='{master}', DDL_MAIT=('DX', 'DY', 'DZ', 'DRX', 'DRY', 'DRZ'), "
                f"GROUP_NO_ESCL=('{names[sel]}',), DDL_ESCL=('DX-DY-DZ',), COEF_ESCL=(1.0,))"
            )
    return out


def _pretension_loads(bolts, cuts) -> dict[str, list[str]]:
    """The reference node held but along its first dof, which takes the pretension
    (a force, or the shortening imposed), and each twin tied to its node by
    u_twin - u = n u_ref (ADR 0037, as the CalculiX deck)."""
    out: dict[str, list[str]] = {"DDL_IMPO": [], "FORCE_NODALE": [], "LIAISON_DDL": []}
    for k, (f, cut) in enumerate(zip(bolts, cuts, strict=True), start=1):
        ref = f"PR{k}"
        if f.type == "bolt_pretension":  # tension pulls the sides together
            out["DDL_IMPO"].append(f"_F(GROUP_NO='{ref}', DY=0.0, DZ=0.0)")
            out["FORCE_NODALE"].append(f"_F(GROUP_NO='{ref}', FX={-f.values['F'].to_si():.12g})")
        else:  # an adjustment shortens the bolt
            d = -f.values["d"].to_si()
            out["DDL_IMPO"].append(f"_F(GROUP_NO='{ref}', DX={d:.12g}, DY=0.0, DZ=0.0)")
        for i in range(1, len(cut.doubles) + 1):
            for c, dof in enumerate(("DX", "DY", "DZ")):
                n = float(cut.normal[c])
                nodes, dofs, coefs = [f"P{k}B{i}", f"P{k}A{i}"], [dof, dof], [1.0, -1.0]
                if abs(n) > 1e-12:  # the reference node's DX is the opening
                    nodes.append(ref)
                    dofs.append("DX")
                    coefs.append(-n)
                out["LIAISON_DDL"].append(
                    f"_F(GROUP_NO=({', '.join(repr(g) for g in nodes)},), "
                    f"DDL=({', '.join(repr(d) for d in dofs)},), "
                    f"COEF_MULT=({', '.join(f'{x:.12g}' for x in coefs)},), COEF_IMPO=0.0)"
                )
    return out


def _negligible(project, comp, mesh: Mesh) -> float:
    """A stiffness 1e-9 of the part's (E × size), for dofs held only by relations: a
    zero pivot stops Code_Aster's solver."""
    moduli = [
        project.get(a.material, Material).properties.get("youngs_modulus") for a in comp.materials
    ]
    E = max((m.to_si() for m in moduli if m is not None), default=1.0)
    return 1e-9 * E * float(np.linalg.norm(np.ptp(mesh.coords, axis=0)))


def _discrete_cara(
    supports,
    references: int,
    tiny: float = 0.0,
    gaps=(),
    masters=(),
    beams=(),
    shells=(),
    massif=(),
) -> list[str]:
    """Each spring's stiffness in its own axes (REPERE='LOCAL'; global is the default):
    x along the face normal (kn), y and z along the face (kt). A pretension reference
    node carries a negligible stiffness (`tiny`) along its first dof, only to have
    dofs; its other two are held."""
    if not (supports or references or gaps or masters or beams or shells or massif):
        return []
    stiff, axes = [], []
    nodes = [("S", i, s) for i, s in enumerate(supports, start=1)]
    nodes += [("C", i, g) for i, g in enumerate(gaps, start=1)]
    for prefix, i, (_node, kn, kt, n) in nodes:
        if prefix == "C":  # DIS_CHOC closes moving against its local x: x into the face
            n = -np.asarray(n)
        t = np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0])
        t /= np.linalg.norm(t)
        stiff.append(
            f"_F(GROUP_MA='{prefix}{i}', CARA='K_T_D_N', REPERE='LOCAL', "
            f"VALE=({kn:.10e}, {kt:.10e}, {kt:.10e}))"
        )
        axes.append(
            f"_F(GROUP_MA='{prefix}{i}', CARA='VECT_X_Y', VALE=("
            + ", ".join(f"{c:.12g}" for c in (*n, *t))
            + "))"
        )
    stiff += [
        f"_F(GROUP_MA='PR{k}', CARA='K_T_D_N', VALE=({tiny:.6e}, 0.0, 0.0))"
        for k in range(1, references + 1)
    ]
    stiff += [  # a fastener's master point: dofs only, its stiffness negligible
        f"_F(GROUP_MA='{m}', CARA='K_TR_D_N', VALE=({', '.join([f'{tiny:.6e}'] * 6)}))"
        for m in masters
    ]
    sections = []
    for group, b in beams:  # ADR 0027 beams: POU_D_T sections; local y is the first axis
        if b.shape == "library":  # ADR 0055: the table's values; IY about local y, strong
            from puzzle import sections as tables

            sec = tables.get(b.standard, b.designation)
            what = (
                "SECTION='GENERALE', CARA=('A', 'IY', 'IZ', 'JX', 'AY', 'AZ'), VALE=("
                + ", ".join(
                    f"{v:.10g}"
                    for v in (sec.A, sec.I1, sec.I2, sec.J, sec.A / sec.A1, sec.A / sec.A2)
                )  # fmt: skip
                + ")"
            )
        elif b.shape == "rectangle":
            what = (
                f"SECTION='RECTANGLE', CARA=('HY', 'HZ'), VALE=({b.width.to_si():.10g}, "
                f"{b.height.to_si():.10g})"
            )
        elif b.shape == "box":
            what = (
                f"SECTION='RECTANGLE', CARA=('HY', 'HZ', 'EP'), VALE=({b.width.to_si():.10g}, "
                f"{b.height.to_si():.10g}, {b.thickness.to_si():.10g})"
            )
        elif b.shape == "circle":
            what = f"SECTION='CERCLE', CARA=('R',), VALE=({b.diameter.to_si() / 2:.10g},)"
        else:
            what = (
                f"SECTION='CERCLE', CARA=('R', 'EP'), VALE=({b.diameter.to_si() / 2:.10g}, "
                f"{b.thickness.to_si():.10g})"
            )
        sections.append(f"_F(GROUP_MA='{group}', {what})")
        axes.append(
            f"_F(GROUP_MA='{group}', CARA='VECT_Y', VALE=("
            + ", ".join(f"{c:.12g}" for c in b.orientation)
            + "))"
        )
    plates = []
    for group, s in shells:  # ADR 0027 shells: thickness, and the offset in metres
        t = s.thickness.to_si()
        offset = f", EXCENTREMENT={s.offset * t:.10g}, INER_ROTA='OUI'" if s.offset else ""
        plates.append(f"_F(GROUP_MA='{group}', EPAIS={t:.10g}{offset})")
    blocks = [
        ("DISCRET", stiff),
        ("POUTRE", sections),
        ("COQUE", plates),
        ("ORIENTATION", axes),
        ("MASSIF", list(massif)),
    ]
    out = ["cara = AFFE_CARA_ELEM(MODELE=model"]
    for key, items in blocks:
        if items:
            out[-1] += f", {key}=("
            out += [f"    {item}," for item in items]
            out.append(")")
    out[-1] += ")"
    return out


# ADR 0023: the commands that take each setting, and how the shared ones translate.
SOLVING = ("MECA_STATIQUE", "STAT_NON_LINE", "DYNA_NON_LINE", "THER_LINEAIRE", "THER_NON_LINE")
NONLINEAR = ("STAT_NON_LINE", "DYNA_NON_LINE", "THER_NON_LINE")
STEPPING = ("STAT_NON_LINE", "DYNA_NON_LINE", "THER_LINEAIRE", "THER_NON_LINE")
METHODS = (
    ("MUMPS (direct, the default)", "MUMPS"),
    ("Multifrontal (direct, Code_Aster's own)", "MULT_FRONT"),
    ("Preconditioned conjugate gradient (iterative)", "GCPC"),
    ("PETSc (iterative; parallel Code_Aster builds only)", "PETSC"),
)
# MUMPS's own options (ADR 0053). Block low rank (LR) compresses the factors to a
# tolerance: faster and smaller on large 3D solids, approximate to that tolerance.
ACCELERATIONS = (
    ("Code_Aster's choice", "AUTO"),
    ("Full rank (exact)", "FR"),
    ("Full rank, faster (exact)", "FR+"),
    ("Low rank (to a tolerance)", "LR"),
    ("Low rank, faster (to a tolerance)", "LR+"),
)
ORDERINGS = (
    ("Code_Aster's choice", "AUTO"),
    ("METIS", "METIS"),
    ("SCOTCH", "SCOTCH"),
    ("PORD", "PORD"),
    ("Approximate minimum degree (AMD)", "AMD"),
    ("Approximate minimum fill (AMF)", "AMF"),
    ("Quasi-dense AMD (QAMD)", "QAMD"),
)
MEMORY = (
    ("Code_Aster's choice", "AUTO"),
    ("In memory (fastest)", "IN_CORE"),
    ("On disk (least memory)", "OUT_OF_CORE"),
)
MUMPS_ONLY = {"acceleration": "ACCELERATION", "low_rank_threshold": "LOW_RANK_SEUIL",
              "ordering": "RENUM", "factor_memory": "GESTION_MEMOIRE"}  # fmt: skip


def with_settings(comm: str, study, step) -> str:
    """The command file with the study's settings for Code_Aster placed (ADR 0023):
    its own, then the shared ones, then the expert pass-through over them all. A
    setting goes in every command of the file that takes it; only what was set is
    written, so the defaults stay Code_Aster's own."""
    from puzzle.adapters.code_aster import CodeAsterAdapter
    from puzzle.adapters.code_aster.expert import apply

    own = study.solver_settings.get("code_aster", {})
    CodeAsterAdapter().solver_settings(study)  # unknown keys and bad values refused
    given = step.settings
    used = set(re.findall(r"\b([A-Z][A-Z_]{3,})\(", comm))
    overrides: dict[str, str] = {}

    def place(commands, path: str, value: str) -> None:
        for command in commands:
            if command in used:
                overrides[f"{command}.{path}"] = value

    # Iterative: Code_Aster's own conjugate gradient, in every build (PETSc is not).
    method = own.get("method") or ("GCPC" if given.get("linear_solver") == "iterative" else None)
    if method:
        place(SOLVING, "SOLVEUR.METHODE", f"'{method}'")
    mumps = [k for k in MUMPS_ONLY if k in own]
    if mumps and method not in (None, "MUMPS"):
        raise InputError(f"Code_Aster: {', '.join(mumps)} are MUMPS options; the linear "
                         f"solver is {method}", code="invalid")  # fmt: skip
    if "low_rank_threshold" in own and not str(own.get("acceleration", "")).startswith("LR"):
        raise InputError("Code_Aster: a low-rank tolerance needs a low-rank acceleration "
                         "(LR or LR+)", code="invalid")  # fmt: skip
    for key in mumps:
        value = own[key]
        text = f"{float(value):.6g}" if key == "low_rank_threshold" else f"'{value}'"
        place(SOLVING, f"SOLVEUR.{MUMPS_ONLY[key]}", text)
    if "tolerance" in given:
        place(NONLINEAR, "CONVERGENCE.RESI_GLOB_RELA", f"{float(given['tolerance']):.6g}")
    if "max_iterations" in given:
        place(NONLINEAR, "CONVERGENCE.ITER_GLOB_MAXI", str(int(given["max_iterations"])))
    if "output_every" in given:
        place(STEPPING, "ARCHIVAGE.PAS_ARCH", str(int(given["output_every"])))
    if "reac_iter" in own:
        place(NONLINEAR[:2], "NEWTON.REAC_ITER", str(int(own["reac_iter"])))
    overrides.update(study.expert_settings.get("code_aster", {}))
    return apply(comm, overrides) if overrides else comm


def _on_shells(mesh: Mesh, group) -> bool:
    shells = {id(b) for b in mesh.shell_blocks()}
    return group.dim == 2 and any(id(b) in shells for b in group.blocks(mesh))


def _check_buckling(features, step) -> None:
    """What a buckling step here takes: plain supports and loads (no remote loads,
    local axes, pretension, springs or gaps yet)."""
    plain = {"fixed", "displacement", "force", "pressure", "gravity", "acceleration", "rotation"}
    odd = sorted({f.type for f in features if f.type not in plain or f.axes is not None})
    if odd:
        raise InputError(
            f"Code_Aster buckling steps take plain supports and loads so far, not {odd}",
            code="unsupported",
        )


def _spectral_loads(spectral) -> dict:
    """A harmonic step's loads varying with frequency (ADR 0044): per field and given
    frequency f_s a charge of its nodal forces, its assembled vector, and a hat function
    of FREQ (1 at f_s, 0 at the frequencies beside it, the first and last held outside)
    that multiplies it in DYNA_VIBRA; their sum interpolates the field linearly."""
    out = {"charges": [], "vectors": "", "functions": [], "excit": ""}
    for j, (frequencies, rows) in enumerate(spectral, start=1):
        n = len(frequencies)
        for s, (f, items) in enumerate(zip(frequencies, rows, strict=True), start=1):
            name = f"{j}_{s}"
            out["charges"].append(
                f"sp{name} = AFFE_CHAR_MECA(MODELE=model, FORCE_NODALE=({', '.join(items)},))"
            )
            out["vectors"] += (
                f"\n                      _F(VECTEUR=CO('fq{name}'), OPTION='CHAR_MECA', "
                f"CHARGE=(sp{name},)),"
            )
            if n == 1:
                out["excit"] += f" _F(VECT_ASSE=fq{name}, COEF_MULT=1.0),"
                continue
            points = []
            if s > 1:
                points += [(frequencies[s - 2], 0.0)]
            points += [(f, 1.0)]
            if s < n:
                points += [(frequencies[s], 0.0)]
            vale = ", ".join(f"{x:.12g}, {y:g}" for x, y in points)
            out["functions"].append(
                f"ht{name} = DEFI_FONCTION(NOM_PARA='FREQ', VALE=({vale}), INTERPOL='LIN', "
                "PROL_GAUCHE='CONSTANT', PROL_DROITE='CONSTANT')"
            )
            out["excit"] += f" _F(VECT_ASSE=fq{name}, FONC_MULT=ht{name}),"
    return out


def _buckling(step, cara: str, charges: list[str], preload, names, mesh, ties) -> list[str]:
    """Linear buckling (ADR 0028): K + K_G(preload) against K_G of this step's loads,
    from the stresses of a static solve under each. Code_Aster's CHAR_CRIT is the load
    factor (positive for a load that buckles as applied)."""
    n = int(step.settings.get("modes", 4))
    excit = ", ".join(f"_F(CHARGE={c})" for c in charges)
    load_list = ", ".join(charges)
    out = [
        f"buck = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, {cara}EXCIT=({excit},))",
        "sig = CREA_CHAMP(TYPE_CHAM='ELGA_SIEF_R', OPERATION='EXTR', RESULTAT=buck, "
        "NOM_CHAM='SIEF_ELGA', NUME_ORDRE=1)",
        f"kge = CALC_MATR_ELEM(OPTION='RIGI_GEOM', MODELE=model, {cara}SIEF_ELGA=sig)",
        f"ke = CALC_MATR_ELEM(OPTION='RIGI_MECA', MODELE=model, CHAM_MATER=mater, {cara}"
        f"CHARGE=({load_list},))",
        "nu = NUME_DDL(MATR_RIGI=ke)",
        "kk = ASSE_MATRICE(MATR_ELEM=ke, NUME_DDL=nu)",
        "kg = ASSE_MATRICE(MATR_ELEM=kge, NUME_DDL=nu)",
    ]
    if preload:
        out += [
            _loads(preload, names, mesh, False, ties).replace("load = ", "load0 = ", 1),
            f"pre = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, {cara}EXCIT=(_F(CHARGE=load0),))",
            "sig0 = CREA_CHAMP(TYPE_CHAM='ELGA_SIEF_R', OPERATION='EXTR', RESULTAT=pre, "
            "NOM_CHAM='SIEF_ELGA', NUME_ORDRE=1)",
            f"kge0 = CALC_MATR_ELEM(OPTION='RIGI_GEOM', MODELE=model, {cara}SIEF_ELGA=sig0)",
            "kg0 = ASSE_MATRICE(MATR_ELEM=kge0, NUME_DDL=nu)",
            "kk = COMB_MATR_ASSE(COMB_R=(_F(MATR_ASSE=kk, COEF_R=1.0), "
            "_F(MATR_ASSE=kg0, COEF_R=1.0)))",
        ]
    shift = step.settings.get("shift")
    if shift is not None:
        search = (
            f"OPTION='CENTRE', CALC_CHAR_CRIT=_F(CHAR_CRIT={float(shift):.12g}, NMAX_CHAR_CRIT={n})"
        )
    else:
        search = f"OPTION='PLUS_PETITE', CALC_CHAR_CRIT=_F(NMAX_CHAR_CRIT={n})"
    out += [
        f"res = CALC_MODES(MATR_RIGI=kk, MATR_RIGI_GEOM=kg, TYPE_RESU='MODE_FLAMB', {search})",
        "res = NORM_MODE(reuse=res, MODE=res, NORME='TRAN')",
        _table("res", "DEPL", "displacement"),
        f"IMPR_TABLE(TABLE=RECU_TABLE(CO=res, NOM_PARA='CHAR_CRIT'), UNITE={TABLES['buckling']}, "
        f"FORMAT='TABLEAU', SEPARATEUR=',', FORMAT_R='{FORMAT_R}')",
    ]
    return out


def _table(result: str, field: str, name: str, which: str = "") -> str:
    which = f"{which}, " if which else ""
    return (
        f"IMPR_TABLE(TABLE=CREA_TABLE(RESU=_F(RESULTAT={result}, NOM_CHAM='{field}', {which}"
        "TOUT_CMP='OUI', TOUT='OUI')), "
        f"UNITE={TABLES[name]}, FORMAT='TABLEAU', SEPARATEUR=',', FORMAT_R='{FORMAT_R}')"
    )


def _traction(index: int, m: Material) -> str:
    """The tensile curve VMIS_ISOT_TRAC reads: stress against TOTAL strain (eps_p + s/E),
    from the elastic limit on; constant beyond the last point (perfectly plastic)."""
    curves = m.models.plasticity.curves
    if len(curves) > 1:
        raise InputError(
            f"material '{m.name}': the Code_Aster adapter takes one hardening curve so far, "
            "not one per temperature",
            code="unsupported",
        )
    e = m.properties["youngs_modulus"].to_si()
    pairs = ", ".join(f"{ep + s / e:.12g}, {s:.12g}" for ep, s in curves[0].points)
    return (
        f"trac{index} = DEFI_FONCTION(NOM_PARA='EPSI', VALE=({pairs}), "
        "PROL_DROITE='CONSTANT', PROL_GAUCHE='EXCLU')"
    )


def _low_rank(text: str) -> bool:
    """Whether the .comm asks MUMPS for low-rank factors (LR or LR+), from a solver
    setting or an expert one."""
    return re.search(r"ACCELERATION\s*=\s*['\"]LR", text) is not None


def resources(processes: int = 1, low_rank: bool = False) -> list[str]:
    """The export's resource lines (ADR 0053): threads and memory per process from
    Settings > Solvers and the runtime's memory, and no time limit worth meeting.
    A `low_rank` MUMPS run gets one thread: with more, this build's MUMPS hangs (the
    bonded bracket ran 11 hours at 400 % CPU; 35 s on one thread).

    run_aster sets OpenMP's (and so MUMPS's and BLAS's) threads from `ncpus`, ignoring
    OMP_NUM_THREADS, and `memory_limit` is per process in MB: too small makes MUMPS
    factor out of core. `time_limit` stops the run when reached, so it is a week; Puzzle
    stops runs itself."""
    from puzzle.jobs.runtime import runtime_for, threads_per_process

    try:
        memory = runtime_for("code_aster").memory()
    except Exception:  # noqa: BLE001 - no runtime to ask: Code_Aster's usual default
        memory = None
    mb = int(0.75 * memory / 2**20 / processes) if memory else 4096
    return [
        "P time_limit 604800",
        f"P memory_limit {max(mb, 1024)}",
        f"P ncpus {1 if low_rank else threads_per_process(processes)}",
        f"P mpi_nbcpu {processes}",
    ]


def _axes_order(project: Project, assignment) -> tuple[int, int, int]:
    """Which of Puzzle's material axes Code_Aster's L T N are for this assignment."""
    from puzzle.model.schema import CoordinateSystem

    if assignment.axes is None:
        return CARTESIAN
    cs = project.get(assignment.axes, CoordinateSystem)
    return CYLINDRICAL if cs.type == "cylindrical" else CARTESIAN


def _nautical(r: np.ndarray) -> tuple[float, float, float]:
    """Code_Aster's nautical angles (degrees) of a frame whose columns are its axes:
    alpha about Z, then beta about the new Y, then gamma about the new X."""
    x = r[:, 0]
    alpha = np.degrees(np.arctan2(x[1], x[0]))
    beta = np.degrees(np.arctan2(-x[2], np.hypot(x[0], x[1])))
    gamma = np.degrees(np.arctan2(r[2, 1], r[2, 2]))
    return float(alpha), float(beta), float(gamma)


def _material_axes(project: Project, comp: Component, names: dict, thermal: bool) -> list[str]:
    """MASSIF orientations for materials with directions (ADR 0049): the assignment's
    system (Cartesian by ANGL_REP; cylindrical by its axis, ANGL_AXE and ORIG_AXE, whose
    L is axial, T tangential and N radial: `CYLINDRICAL`), else the material's own axis1
    and axis2."""
    from puzzle.model.schema import CoordinateSystem

    out = []
    for a in comp.materials:
        m = project.get(a.material, Material)
        model = m.models.anisotropic_conductivity if thermal else m.models.orthotropic_elasticity
        if model is None:
            continue
        if comp.dimension != "3d":
            raise InputError(
                f"material '{m.name}': Code_Aster writes material directions in 3D models only "
                "so far", code="unsupported")  # fmt: skip
        group = names[a.selection]
        if a.axes is not None:
            cs = project.get(a.axes, CoordinateSystem)
            if cs.type == "spherical":
                raise InputError(f"Code_Aster takes no spherical material axes ('{cs.name}')",
                                 code="unsupported")  # fmt: skip
            r = cs.orientation()
            if cs.type == "cylindrical":
                z = r[:, 2]
                alpha = np.degrees(np.arctan2(z[1], z[0]))
                beta = np.degrees(np.arctan2(-z[2], np.hypot(z[0], z[1])))
                o = cs.centre()
                out.append(f"_F(GROUP_MA='{group}', ANGL_AXE=({alpha:.12g}, {beta:.12g}), "
                           f"ORIG_AXE=({o[0]:.12g}, {o[1]:.12g}, {o[2]:.12g}))")  # fmt: skip
                continue
        else:
            x = np.asarray(model.axis1, float)
            x /= np.linalg.norm(x)
            y = np.asarray(model.axis2, float)
            y = y - (y @ x) * x
            y /= np.linalg.norm(y)
            r = np.column_stack([x, y, np.cross(x, y)])
        angles = ", ".join(f"{v:.12g}" for v in _nautical(r))
        out.append(f"_F(GROUP_MA='{group}', ANGL_REP=({angles}))")
    return out


# Code_Aster's L T N against Puzzle's material axes 1 2 3: the same in Cartesian axes; a
# cylindrical frame (ANGL_AXE) has L axial, T tangential and N radial, where Puzzle's
# (and CalculiX's) are 1 radial, 2 tangential, 3 axial (test_orthotropic.py's tube).
CARTESIAN = (0, 1, 2)
CYLINDRICAL = (2, 1, 0)


def _ortho_constants(o, order=CARTESIAN) -> dict[str, float]:
    """ELAS_ORTH's nine constants with L T N = Puzzle's axes `order`: nu_ji = nu_ij E_j / E_i."""
    e = (o.e1, o.e2, o.e3)
    nu = {(0, 1): o.nu12, (0, 2): o.nu13, (1, 2): o.nu23}
    g = {frozenset((0, 1)): o.g12, frozenset((0, 2)): o.g13, frozenset((1, 2)): o.g23}

    def ratio(i, j):
        return nu[(i, j)] if (i, j) in nu else nu[(j, i)] * e[i] / e[j]

    L, T, N = order
    return {"E_L": e[L], "E_T": e[T], "E_N": e[N],
            "NU_LT": ratio(L, T), "NU_LN": ratio(L, N), "NU_TN": ratio(T, N),
            "G_LT": g[frozenset((L, T))], "G_LN": g[frozenset((L, N))],
            "G_TN": g[frozenset((T, N))]}  # fmt: skip


def _material(
    m: Material, thermal: bool, expansion: bool = False, damping=None, order=CARTESIAN
) -> str:
    p = {k: q.to_si() for k, q in m.properties.items()}

    def need(*keys: str) -> list[float]:
        missing = [k for k in keys if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        return [p[k] for k in keys]

    if thermal:
        rho_cp = (
            p["density"] * p["specific_heat"] if {"density", "specific_heat"} <= p.keys() else 1.0
        )
        aniso = m.models.anisotropic_conductivity
        if aniso is not None:  # along the material axes (ADR 0049), L T N as `order`
            k = (aniso.k1, aniso.k2, aniso.k3)
            lt, tt, nt = (k[i] for i in order)
            return (f"THER_ORTH=_F(LAMBDA_L={lt:.12g}, LAMBDA_T={tt:.12g}, "
                    f"LAMBDA_N={nt:.12g}, RHO_CP={rho_cp:.12g})")  # fmt: skip
        (k,) = need("thermal_conductivity")
        return f"THER=_F(LAMBDA={k:.12g}, RHO_CP={rho_cp:.12g})"
    rho = f", RHO={p['density']:.12g}" if "density" in p else ""
    # Rayleigh damping C = a M + b K (ADR 0041): Code_Aster's AMOR_ALPHA multiplies the
    # stiffness and AMOR_BETA the mass, the other way round from the usual names.
    amor = f", AMOR_ALPHA={damping[1]:.12g}, AMOR_BETA={damping[0]:.12g}" if damping else ""
    o = m.models.orthotropic_elasticity
    if o is not None:  # in the material axes (ADR 0049), L T N taken as `order` of 1 2 3
        a = need("thermal_expansion")[0] if expansion else None
        alpha = f", ALPHA_L={a:.12g}, ALPHA_T={a:.12g}, ALPHA_N={a:.12g}" if expansion else ""
        c = _ortho_constants(o, order)
        return "ELAS_ORTH=_F(" + ", ".join(f"{k}={v:.12g}" for k, v in c.items()) + (
            f"{rho}{alpha}{amor})")  # fmt: skip
    e, nu = need("youngs_modulus", "poissons_ratio")
    alpha = f", ALPHA={need('thermal_expansion')[0]:.12g}" if expansion else ""
    return f"ELAS=_F(E={e:.12g}, NU={nu:.12g}{rho}{alpha}{amor})"


def _check_friction(contacts, features, mesh: Mesh) -> None:
    """Code_Aster's continuous Coulomb friction, where it works (see CLAUDE.md):

    - first-order elements: on 10-node tetrahedra (6-node triangle faces) its friction and
      contact multipliers become singular; Code_Aster validates quadratic 3D friction on
      27-node hexahedra only;
    - no displacement condition on slave (source) nodes: it duplicates a multiplier.
    """
    frictional = [c for c in contacts if c.type == "frictional"]
    if not frictional:
        return
    if any(b.element in ("tet10", "hex20", "wedge15", "pyramid13") for b in mesh.blocks):
        raise InputError(
            "Code_Aster frictional contact needs a first-order mesh (element_order 1); "
            "CalculiX runs frictional contact on quadratic meshes",
            code="unsupported",
        )
    held = [f for f in features if f.type in HOLDING]
    for c in frictional:
        slave = mesh.groups[c.source].node_ids
        clash = [
            f.selection
            for f in held
            if np.intersect1d(slave, mesh.groups[f.selection].node_ids).size
        ]
        if clash:
            raise InputError(
                f"contact '{c.name}': slave face {c.source} shares nodes with displacement "
                f"conditions on {', '.join(clash)}; with friction Code_Aster cannot hold a "
                "node by both. Constrain those parts away from the interface, or swap "
                "source and target",
                details={"conflicts": clash},
            )


def _check_curved(contacts, mesh: Mesh) -> None:
    """A curved face on a face of another shape (Hertz's line or point contact): its
    continuous contact fails to pair the faces before the first increment here, with
    the whole curve or only the part that touches as the source, on linear or quadratic
    tets (tests/integration/test_hertz.py). Refused rather than left to fail."""
    from puzzle.contact_check import nonconforming

    for c in contacts:
        if (line := nonconforming(c, mesh)) is not None:
            raise InputError(
                f"contact '{c.name}': its source face ({c.source}) is curved and meets its "
                "target along a line or at a point (as a cylinder or ball on a plate); "
                "Code_Aster's contact fails to pair such faces. Run the study on CalculiX",
                code="unsupported",
                details=line,
            )


def _contact_exclusions(contacts, fasteners, mesh: Mesh, names: dict) -> dict[str, list[str]]:
    """Per contact, the fastener faces (MED names) that share nodes with its slave face.

    A bolt hole meets the seat face along its edge, and those nodes are tied to the
    bolt (LIAISON_RBE3 or LIAISON_SOLIDE): a contact multiplier on them too makes the
    matrix singular (LAGS_F1 on the bolted bracket), so they are left out of the
    contact (SANS_GROUP_NO), as Code_Aster advises. CalculiX needs no such exclusion.
    """
    tied = sorted(set(fastener_selections(fasteners)))
    out = {}
    for c in contacts:
        slave = mesh.groups[c.source].node_ids
        shared = [names[s] for s in tied if np.intersect1d(slave, mesh.groups[s].node_ids).size]
        if shared:
            out[c.name] = shared
    return out


def contact_summary(project, comp, mesh: Mesh | None = None) -> dict[str, str]:
    """What the .comm gives each contact, in words (ADR 0056), from the functions that
    write it: LIAISON_MAIL ties, DEFI_CONTACT zones and their SANS_GROUP_NO exclusions."""
    from puzzle.bolts import resolved

    out = {}
    touching = [c for c in comp.contacts if c.type in ("frictionless", "frictional")]
    friction = any(c.type == "frictional" for c in touching)
    quadratic = mesh is not None and any(
        b.element in ("tet10", "hex20", "wedge15", "pyramid13") for b in mesh.blocks
    )
    excluded = {}
    if mesh is not None and touching:
        try:
            fasteners = resolved(project).objects.get(comp.id, comp).connectors
        except Exception:  # an unresolvable bolt: shown as stored
            fasteners = comp.connectors
        present = [c for c in touching if c.source in mesh.groups]
        fasteners = [f for f in fasteners if all(
            s is None or s in mesh.groups for s in (f.source, f.target))]  # fmt: skip
        named = {g: g for g in mesh.groups}
        for name, groups in _contact_exclusions(present, fasteners, mesh, named).items():
            slave = mesh.groups[next(c.source for c in present if c.name == name)].node_ids
            nodes = np.unique(np.concatenate([mesh.groups[g].node_ids for g in groups]))
            excluded[name] = (groups, int(np.intersect1d(slave, nodes).size))
    for c in comp.contacts:
        if c.capability == "bonded_conformal":
            out[c.name] = "Shared nodes (meshed conformally): nothing to write"
        elif c.capability not in CONTACTS:
            out[c.name] = f"Not written: Code_Aster refuses {c.capability.replace('_', ' ')}"
        elif c.capability == "bonded_nonconformal":
            out[c.name] = (f"LIAISON_MAIL: the nodes of {c.source} tied into the volume of "
                           f"{c.target.split('/', 1)[0]} (structural studies only)")  # fmt: skip
        else:
            law = (f"Coulomb μ = {c.friction:g}" if c.type == "frictional"
                   else "frictionless" + (" (COULOMB = 0 beside a frictional zone)"
                                          if friction else ""))  # fmt: skip
            text = (
                f"DEFI_CONTACT continuous (augmented Lagrangian), slave {c.source}, {law}; "
                "faces turned outward (ORIE_PEAU); STAT_NON_LINE over 10 increments"
            )
            init = c.extensions.get("code_aster", {}).get("contact_init")
            if init:
                text += f"; CONTACT_INIT='{init}'"
            from puzzle.contact_check import moved_words

            text += moved_words(c)
            if c.name in excluded:
                groups, count = excluded[c.name]
                text += f"; SANS_GROUP_NO: {count} nodes on bolt ends ({', '.join(groups)})"
            if c.type == "frictional" and quadratic:
                text += "; refused on this quadratic mesh (friction needs element_order 1)"
            out[c.name] = text
    return out


def _defi_contact(contacts, names: dict[str, str], excluded=None) -> str:
    """Continuous (augmented Lagrangian) contact, with Coulomb friction when any zone has
    it; source faces are the slave side.

    `extensions["code_aster"]["contact_init"] = "OUI"` starts every slave point closed:
    needed when a body is held only by contacts that touch along an edge, where no
    point starts interpenetrated and the first matrix is singular.
    """
    friction = any(c.type == "frictional" for c in contacts)
    zones = []
    for c in contacts:
        zone = f"_F(GROUP_MA_MAIT='{names[c.target]}', GROUP_MA_ESCL='{names[c.source]}'"
        if (excluded or {}).get(c.name):
            zone += ", SANS_GROUP_NO=(" + ", ".join(f"'{g}'" for g in excluded[c.name]) + ",)"
        init = c.extensions.get("code_aster", {}).get("contact_init")
        if init:
            zone += f", CONTACT_INIT='{init}'"
        zones.append(zone + (f", COULOMB={c.friction or 0.0:.10g})" if friction else ")"))
    return (
        "contact = DEFI_CONTACT(MODELE=model, FORMULATION='CONTINUE', "
        f"FROTTEMENT='{'COULOMB' if friction else 'SANS'}', ZONE=({', '.join(zones)},))"
    )


def _temperature_field(field, mesh: Mesh, workdir: Path) -> list[str]:
    """A nodal temperature field built in the .comm from a table of x, y, z, T.

    Code_Aster numbers nodes its own way, so values are matched to its mesh by
    coordinates (rounded to a nanometre).
    """
    workdir.mkdir(parents=True, exist_ok=True)
    table = np.column_stack([mesh.node_coords(field.ids), field.values[:, 0]])
    np.savetxt(workdir / "transferred-temperature.txt", table, fmt="%.15g")
    return [
        "import numpy as np",
        "temperature = CREA_CHAMP(TYPE_CHAM='NOEU_TEMP_R', OPERATION='AFFE', MAILLAGE=mesh, "
        "AFFE=_F(TOUT='OUI', NOM_CMP='TEMP', VALE=0.0))",
        f"_rows = np.loadtxt('fort.{TEMPERATURE_UNIT}', ndmin=2)",
        "_key = lambda xyz: tuple(np.round(np.asarray(xyz) * 1e9).astype(np.int64))",
        "_given = {_key(r[:3]): r[3] for r in _rows}",
        "_xyz = mesh.getCoordinates().toNumpy().reshape(-1, 3)",
        "temperature.setValues([float(_given[_key(c)]) for c in _xyz])",
    ]


def _reference(comp) -> float:
    from puzzle.adapters.calculix.inp import reference_temperature

    return reference_temperature(comp)


def _ther_nl(index: int, m: Material, tables: dict) -> list[str]:
    """THER_NL: conductivity and volumetric enthalpy (integral of rho cp dT) of temperature."""
    p = {k: q.to_si() for k, q in m.properties.items()}

    def curve(prop: str) -> list[tuple[float, float]]:
        if prop in tables:
            return tables[prop]
        if prop not in p:
            raise InputError(f"material '{m.name}' is missing {prop}")
        return [(ROOM_K, p[prop])]

    def at(points: list[tuple[float, float]], t: float) -> float:
        temps, values = zip(*points, strict=True)
        return float(np.interp(t, temps, values))

    k = curve("thermal_conductivity")
    has_cp = "specific_heat" in p or "specific_heat" in tables
    rho = curve("density") if "density" in p or "density" in tables else [(ROOM_K, 1.0)]
    cp = curve("specific_heat") if has_cp else [(ROOM_K, 1.0)]
    # Enthalpy on every table temperature, by the trapezoid rule on rho cp.
    temps = sorted({t for c in (k, rho, cp) for t, _ in c} | {0.0, 5000.0})
    rho_cp = [at(rho, t) * at(cp, t) for t in temps]
    beta = [0.0]
    for j in range(1, len(temps)):
        beta.append(beta[-1] + 0.5 * (rho_cp[j] + rho_cp[j - 1]) * (temps[j] - temps[j - 1]))

    def function(name: str, points: list[tuple[float, float]], extend: str) -> str:
        vale = ", ".join(f"{t:.12g}, {v:.12g}" for t, v in points)
        return (
            f"{name} = DEFI_FONCTION(NOM_PARA='TEMP', VALE=({vale},), "
            f"PROL_GAUCHE='{extend}', PROL_DROITE='{extend}')"
        )

    lam = k if len(k) > 1 else [(0.0, k[0][1]), (5000.0, k[0][1])]
    return [
        function(f"lam{index}", lam, "CONSTANT"),
        function(f"beta{index}", list(zip(temps, beta, strict=True)), "LINEAIRE"),
        f"mat{index} = DEFI_MATERIAU(THER_NL=_F(LAMBDA=lam{index}, BETA=beta{index}))",
    ]


def _cross_formulas(name: str, base, axis, origin) -> tuple[list[str], list[str]]:
    """FORMULEs for the field base + axis x (x - origin), one per component, and their
    names; Code_Aster evaluates them at each node (X, Y, Z)."""
    a, b, c = (np.asarray(v, float) for v in (base, axis, origin))
    d = [f"({w}-({c[i]:.12g}))" for i, w in enumerate("XYZ")]
    exprs = [
        f"{a[0]:.12g}+({b[1]:.12g})*{d[2]}-({b[2]:.12g})*{d[1]}",
        f"{a[1]:.12g}+({b[2]:.12g})*{d[0]}-({b[0]:.12g})*{d[2]}",
        f"{a[2]:.12g}+({b[0]:.12g})*{d[1]}-({b[1]:.12g})*{d[0]}",
    ]
    lines = [
        f"{name}{i} = FORMULE(VALE='{e}', NOM_PARA=('X', 'Y', 'Z'))" for i, e in enumerate(exprs)
    ]
    return lines, [f"{name}{i}" for i in range(3)]


def _remote_loads(features, names, mesh, project=None) -> list[str]:
    """`loadf`: remote forces as their RBE3-equivalent traction (FORCE_FACE of x, y, z)
    and remote displacements as a rigid motion (DDL_IMPO of x, y, z), ADR 0037."""
    lines, faces, imposed = [], [], []
    for k, f in enumerate(features):
        g = names.get(f.selection)
        if f.type in ("remote_force", "remote_force_rigid"):
            load = remote_load(mesh.groups[f.selection], mesh, *remote_values(f))
            more, (fx, fy, fz) = _cross_formulas(f"rt{k}_", load.uniform, load.twist, load.centroid)
            lines += more
            faces.append(f"_F(GROUP_MA='{g}', FX={fx}, FY={fy}, FZ={fz})")
        elif f.type == "displacement" and f.axes is not None:  # cylindrical, all three
            axes = local_axes(project, f)
            local = [f.values[a].to_si() for a in "xyz"]
            exprs = cylindrical_expressions(axes, local, ("X", "Y", "Z"))
            lines += [
                f"cy{k}_{i} = FORMULE(VALE='{e}', NOM_PARA=('X', 'Y', 'Z'))"
                for i, e in enumerate(exprs)
            ]
            imposed.append(f"_F(GROUP_MA='{g}', DX=cy{k}_0, DY=cy{k}_1, DZ=cy{k}_2)")
        elif f.type == "remote_displacement":
            motion = rigid_motion(f, mesh.groups[f.selection], mesh)
            more, (ux, uy, uz) = _cross_formulas(f"ru{k}_", motion.u0, motion.theta, motion.point)
            lines += more
            imposed.append(f"_F(GROUP_MA='{g}', DX={ux}, DY={uy}, DZ={uz})")
    if not faces and not imposed:
        return []
    call = "loadf = AFFE_CHAR_MECA_F(MODELE=model"
    if faces:
        call += f", FORCE_FACE=({', '.join(faces)},)"
    if imposed:
        call += f", DDL_IMPO=({', '.join(imposed)},)"
    return [*lines, call + ")"]


def _timed_loads(project, features, names, mesh, comp=None) -> list[str]:
    """Structural loads following an amplitude (ADR 0041): one charge per amplitude,
    `tload<k>` with its function `tamp<k>` (held constant outside its points)."""
    from puzzle.model.schema import Amplitude

    out, grouped = [], {}
    for f in features:
        grouped.setdefault(f.amplitude, []).append(f)
    for k, (amplitude_id, members) in enumerate(grouped.items(), start=1):
        points = project.get(amplitude_id, Amplitude).points
        values = ", ".join(f"{t:.12g}, {v:.12g}" for t, v in points)
        out.append(
            f"tamp{k} = DEFI_FONCTION(NOM_PARA='INST', VALE=({values}), "
            "PROL_GAUCHE='CONSTANT', PROL_DROITE='CONSTANT')"
        )
        out.append(
            _loads(members, names, mesh, False, comp=comp).replace("load = ", f"tload{k} = ", 1)
        )
    return out


def _thermal_loads(project, features, names, mesh, comp=None, given=None) -> list[str]:
    """`excit`: the thermal charges, those following an amplitude multiplied by it
    (DEFI_FONCTION of time, held constant outside its points); values from imported
    fields (`given`, feature key -> nodal Field) as functions of x, y, z (ADR 0044)."""
    from puzzle.model.schema import Amplitude

    out, excit = [], []
    fielded = [
        f for f in features if f.type in FIELD_THERMAL and set(f.fields) & FIELD_THERMAL[f.type]
    ]
    features = [f for f in features if f not in fielded]
    if fielded:
        lines, items = _field_thermal_loads(project, fielded, names, mesh, given or {})
        out += lines
        excit += items
    plain = [f for f in features if not f.amplitude]
    base = _loads(plain, names, mesh, True, comp=comp)
    if base != "load = AFFE_CHAR_THER(MODELE=model)":  # Code_Aster refuses an empty charge
        out.append(base)
        excit.append("_F(CHARGE=load)")
    grouped: dict = {}
    for f in features:
        if f.amplitude:
            grouped.setdefault(f.amplitude, []).append(f)
    for k, (amplitude_id, members) in enumerate(grouped.items(), start=1):
        points = project.get(amplitude_id, Amplitude).points
        values = ", ".join(f"{t:.12g}, {v:.12g}" for t, v in points)
        out.append(
            f"amp{k} = DEFI_FONCTION(NOM_PARA='INST', VALE=({values}), "
            "PROL_GAUCHE='CONSTANT', PROL_DROITE='CONSTANT')"
        )
        out.append(
            _loads(members, names, mesh, True, comp=comp).replace("load = ", f"load{k} = ", 1)
        )
        excit.append(f"_F(CHARGE=load{k}, FONC_MULT=amp{k})")
    if not excit:  # nothing imposed at all: the charge as it always was
        out.append(base)
        excit.append("_F(CHARGE=load)")
    out.append(f"excit = ({', '.join(excit)},)")
    return out


# ADR 0044: thermal values Code_Aster takes from imported fields.
FIELD_THERMAL = {"heat_flux": {"q"}, "heat_generation": {"q"}, "convection": {"h", "T_inf"}}

# Written once into a .comm that has any: a field's nodal values on a face selection,
# evaluated where Code_Aster asks (its Gauss points) with the faces' own shape functions,
# so the load is the field's finite-element interpolant, as a consistent nodal load of
# it would be. A point is placed on the nearest faces' corner planes, then (six-node
# faces) on the curved face by Gauss-Newton; values are cached by point.
ON_FACES = """
import numpy as _np


def _shape(six, xi, eta):
    z = 1.0 - xi - eta
    if not six:
        return (_np.array([z, xi, eta]), _np.array([-1.0, 1.0, 0.0]),
                _np.array([-1.0, 0.0, 1.0]))
    n = _np.array([z * (2 * z - 1), xi * (2 * xi - 1), eta * (2 * eta - 1), 4 * z * xi,
                   4 * xi * eta, 4 * eta * z])
    dxi = _np.array([1 - 4 * z, 4 * xi - 1, 0.0, 4 * (z - xi), 4 * eta, -4 * eta])
    deta = _np.array([1 - 4 * z, 0.0, 4 * eta - 1, -4 * xi, 4 * xi, 4 * (z - eta)])
    return n, dxi, deta


class OnFaces:
    def __init__(self, xyz, faces, values):
        self.xyz, self.faces = _np.asarray(xyz, float), _np.asarray(faces, int)
        self.values, self.six = _np.asarray(values, float), self.faces.shape[1] == 6
        self.centre = self.xyz[self.faces[:, :3]].mean(axis=1)
        self.cache = {}

    def _local(self, e, p):
        nodes = self.xyz[self.faces[e]]
        a, b, c = nodes[:3]
        xi, eta = _np.linalg.lstsq(_np.column_stack([b - a, c - a]), p - a, rcond=None)[0]
        for _ in range(4 if self.six else 0):
            n, dxi, deta = _shape(True, xi, eta)
            jac = _np.column_stack([dxi @ nodes, deta @ nodes])
            step = _np.linalg.lstsq(jac, p - n @ nodes, rcond=None)[0]
            xi, eta = xi + step[0], eta + step[1]
        return xi, eta

    def __call__(self, x, y, z):
        key = (x, y, z)
        if key not in self.cache:
            p = _np.array([x, y, z])
            d = ((self.centre - p) ** 2).sum(axis=1)
            near = _np.argsort(d)[:8] if len(d) > 8 else _np.argsort(d)
            best = None
            for e in near:
                xi, eta = self._local(e, p)
                outside = -min(xi, eta, 1.0 - xi - eta)
                if best is None or outside < best[0]:
                    best = (outside, e, xi, eta)
            _, e, xi, eta = best
            n = _shape(self.six, xi, eta)[0]
            self.cache[key] = float(n @ self.values[self.faces[e]])
        return self.cache[key]


class OnCells:
    # A body's tetrahedra (corner nodes): the point's barycentric weights in the nearest
    # cell holding it (or the least outside), its nodes' values weighted.

    def __init__(self, xyz, cells, values):
        self.xyz, self.cells = _np.asarray(xyz, float), _np.asarray(cells, int)
        self.values = _np.asarray(values, float)
        self.centre = self.xyz[self.cells].mean(axis=1)
        self.cache = {}

    def __call__(self, x, y, z):
        key = (x, y, z)
        if key not in self.cache:
            p = _np.array([x, y, z])
            d = ((self.centre - p) ** 2).sum(axis=1)
            near = _np.argsort(d)[:12] if len(d) > 12 else _np.argsort(d)
            best = None
            for e in near:
                a, b, c, q = self.xyz[self.cells[e]]
                w = _np.linalg.solve(_np.column_stack([b - a, c - a, q - a]), p - a)
                weights = _np.array([1.0 - w.sum(), *w])
                outside = -weights.min()
                if best is None or outside < best[0]:
                    best = (outside, e, weights)
            _, e, weights = best
            self.cache[key] = float(weights @ self.values[self.cells[e]])
        return self.cache[key]


class OnEdges(OnFaces):
    # The same on a 2D model's edges (line2, line3: ends, then the middle): a point is
    # placed on the nearest edges' chords, then (three-node edges) on the curve.

    def __init__(self, xyz, faces, values):
        super().__init__(xyz, faces, values)
        self.centre = self.xyz[self.faces[:, :2]].mean(axis=1)

    def _line(self, s):
        if self.faces.shape[1] == 3:
            return (_np.array([s * (s - 1) / 2, s * (s + 1) / 2, 1 - s * s]),
                    _np.array([s - 0.5, s + 0.5, -2 * s]))
        return _np.array([(1 - s) / 2, (1 + s) / 2]), _np.array([-0.5, 0.5])

    def __call__(self, x, y, z=0.0):
        key = (x, y, z)
        if key not in self.cache:
            p = _np.array([x, y, z])
            d = ((self.centre - p) ** 2).sum(axis=1)
            near = _np.argsort(d)[:8] if len(d) > 8 else _np.argsort(d)
            best = None
            for e in near:
                nodes = self.xyz[self.faces[e]]
                a, b = nodes[:2]
                s = 2 * float((p - a) @ (b - a) / max((b - a) @ (b - a), 1e-300)) - 1
                for _ in range(4 if self.faces.shape[1] == 3 else 0):
                    n, dn = self._line(s)
                    tangent = dn @ nodes
                    s += float(tangent @ (p - n @ nodes) / max(tangent @ tangent, 1e-300))
                if best is None or abs(s) - 1 < best[0]:
                    best = (abs(s) - 1, e, min(max(s, -1.0), 1.0))
            _, e, s = best
            self.cache[key] = float(self._line(s)[0] @ self.values[self.faces[e]])
        return self.cache[key]
"""


def _on_cells(name: str, group, mesh: Mesh, field) -> list[str]:
    """`name` = OnCells(...): a field's nodal values on a body's tetrahedra (their
    corners), interpolated linearly in the one holding the point (ADR 0047)."""
    from puzzle.adapters.graded import solid_blocks

    corners = np.vstack([b.connectivity[:, :4] for b in solid_blocks(group, mesh)])
    ids, local = np.unique(corners, return_inverse=True)
    xyz, values = mesh.node_coords(ids), field.at(ids)[:, 0]
    rows = ", ".join("[" + ", ".join(f"{v:.17g}" for v in r) + "]" for r in xyz)
    return [
        f"{name} = OnCells([{rows}],",
        f"    {[list(map(int, r)) for r in local.reshape(corners.shape)]},",
        f"    [{', '.join(f'{v:.17g}' for v in values)}])",
    ]


def _on_faces(name: str, group, mesh: Mesh, field) -> list[str]:
    """`name` = OnFaces(...): a field's nodal values on a face selection's elements (a
    2D model's edges: OnEdges, ADR 0048)."""
    blocks = surface_blocks(group, mesh)
    kinds = ("line2", "line3") if mesh.planar else ("tri3", "tri6")
    if any(b.element not in kinds for b in blocks):
        raise InputError(
            f"Code_Aster takes values from imported fields on triangle faces (3D) or line "
            f"edges (2D) only so far ('{group.name}')",
            code="unsupported",
        )
    if len({b.element for b in blocks}) > 1:
        raise InputError(f"'{group.name}' mixes face element types", code="unsupported")
    connectivity = np.vstack([b.connectivity for b in blocks])
    ids, local = np.unique(connectivity, return_inverse=True)
    xyz = mesh.node_coords(ids)
    values = field.at(ids)[:, 0]

    def rows(a) -> str:
        return "[" + ", ".join("[" + ", ".join(f"{v:.17g}" for v in r) + "]" for r in a) + "]"

    return [
        f"{name} = {'OnEdges' if mesh.planar else 'OnFaces'}({rows(xyz)},",
        f"    {[list(map(int, r)) for r in local.reshape(connectivity.shape)]},",
        f"    [{', '.join(f'{v:.17g}' for v in values)}])",
    ]


def _field_thermal_loads(project, features, names, mesh: Mesh, given: dict):
    """Heat fluxes and films with values from imported fields (ADR 0044): one
    AFFE_CHAR_THER_F charge each, its field values FORMULEs of x, y, z over `OnFaces`
    (a uniform value beside one a DEFI_CONSTANTE), a flux following an amplitude
    multiplied by it. Returns (lines, excit items)."""
    from puzzle.fields import feature_key
    from puzzle.model.schema import Amplitude

    lines, excit = [ON_FACES], []
    for k, f in enumerate(features, start=1):
        g, group = names[f.selection], mesh.groups[f.selection]
        functions = {}
        for j, name in enumerate(("h", "T_inf") if f.type == "convection" else ("q",)):
            fn = f"ff{k}_{j}"
            if name in f.fields:
                field = given.get(feature_key(f, name))
                if field is None:
                    raise InputError(
                        f"{f.type} on '{f.selection}': its field for {name} was not mapped"
                    )
                if f.type == "heat_generation":  # over the body's cells (ADR 0047)
                    lines += _on_cells(f"on{k}_{j}", group, mesh, field)
                else:
                    lines += _on_faces(f"on{k}_{j}", group, mesh, field)
                xyz = "'X', 'Y'" if mesh.planar else "'X', 'Y', 'Z'"  # a 2D model has no Z
                lines.append(
                    f"{fn} = FORMULE(VALE='on({xyz.replace(chr(39), '')})', NOM_PARA=({xyz}), "
                    f"on=on{k}_{j})"
                )
            else:
                lines.append(f"{fn} = DEFI_CONSTANTE(VALE={f.values[name].to_si():.12g})")
            functions[name] = fn
        if f.type == "heat_flux":
            lines.append(
                f"fload{k} = AFFE_CHAR_THER_F(MODELE=model, "
                f"FLUX_REP=_F(GROUP_MA='{g}', FLUN={functions['q']}))"
            )
        elif f.type == "heat_generation":
            lines.append(
                f"fload{k} = AFFE_CHAR_THER_F(MODELE=model, "
                f"SOURCE=_F(GROUP_MA='{g}', SOUR={functions['q']}))"
            )
        else:
            lines.append(
                f"fload{k} = AFFE_CHAR_THER_F(MODELE=model, ECHANGE=_F(GROUP_MA='{g}', "
                f"COEF_H={functions['h']}, TEMP_EXT={functions['T_inf']}))"
            )
        if f.amplitude:
            points = project.get(f.amplitude, Amplitude).points
            values = ", ".join(f"{t:.12g}, {v:.12g}" for t, v in points)
            lines.append(
                f"famp{k} = DEFI_FONCTION(NOM_PARA='INST', VALE=({values}), "
                "PROL_GAUCHE='CONSTANT', PROL_DROITE='CONSTANT')"
            )
            excit.append(f"_F(CHARGE=fload{k}, FONC_MULT=famp{k})")
        else:
            excit.append(f"_F(CHARGE=fload{k})")
    return lines, excit


def _ther_non_line(step, comp: Component) -> str:
    call = (
        "res = THER_NON_LINE(MODELE=model, CHAM_MATER=mater, EXCIT=excit, "
        "CONVERGENCE=_F(RESI_GLOB_RELA=1e-10, ITER_GLOB_MAXI=50), "
    )
    if step.type != "transient":
        return (
            call + "ETAT_INIT=_F(STAT='OUI'), INCREMENT=_F(LIST_INST=DEFI_LIST_REEL(VALE=(0.0,))))"
        )
    dt, end, _ = time_settings(step)
    heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
    times = f"DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A={end:.12g}, PAS={dt:.12g}))"
    return (
        call
        + f"ETAT_INIT=_F(VALE={initial_temperature(heat):.12g}), PARM_THETA={_theta(step)}, "
        + f"INCREMENT=_F(LIST_INST={times}))"
    )


def _loads(
    features: list[Feature],
    names: dict[str, str],
    mesh: Mesh,
    thermal: bool,
    ties=(),
    extra=None,
    comp=None,
) -> str:
    """The plain load charge; `extra` adds items by keyword (pretension's); `comp` gives
    a 2D model's thicknesses (ADR 0048)."""
    extra = extra or {}
    imposed, face_force, pressure, temperature, flux, exchange = [], [], [], [], [], []
    source: list[str] = []  # heat per volume (ADR 0047)
    contour: list[str] = []  # forces on a 2D model's edges
    nodal: list[str] = []
    gravity, rotation, rigid, shell_pressure = [], [], [], []
    internal: list[str] = []  # forces per volume (ADR 0044 stage 2)
    tied = [
        f"_F(GROUP_MA_MAIT='{names[c.target.split('/', 1)[0]]}', "
        f"GROUP_MA_ESCL='{names[c.source]}', TYPE_RACCORD='MASSIF')"
        for c in ties
    ]
    for f in features:
        g = names[f.selection]
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "fixed" and mesh.planar:  # a 2D model's nodes move in its plane
            imposed.append(f"_F(GROUP_MA='{g}', DX=0.0, DY=0.0)")
        elif f.type == "displacement" and mesh.planar:
            dofs = ", ".join(f"D{a.upper()}={v[a]:.12g}" for a in "xy" if a in v)
            if dofs:
                imposed.append(f"_F(GROUP_MA='{g}', {dofs})")
        elif f.type == "force" and mesh.planar:  # on 2D edges: per unit length (ADR 0048)
            area = load_area(mesh.groups[f.selection], mesh, comp)
            comps = ", ".join(f"F{a.upper()}={v[a] / area:.12g}" for a in "xy" if v.get(a))
            contour.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "fixed":
            beam_nodes = mesh.beam_nodes() | mesh.shell_nodes()  # nodes with rotations
            on_beams = beam_nodes & {int(n) for n in mesh.groups[f.selection].node_ids}
            rotations = ", DRX=0.0, DRY=0.0, DRZ=0.0" if on_beams else ""  # a clamp
            imposed.append(f"_F(GROUP_MA='{g}', DX=0.0, DY=0.0, DZ=0.0{rotations})")
        elif f.type == "displacement" and f.axes is None:  # in local axes: loadf
            dofs = ", ".join(f"D{a.upper()}={v[a]:.12g}" for a in "xyz" if a in v)
            imposed.append(f"_F(GROUP_MA='{g}', {dofs})")
        elif f.type == "force" and mesh.groups[f.selection].dim == 0:  # on points (beams)
            share = len(mesh.groups[f.selection].node_ids)
            comps = ", ".join(f"F{a.upper()}={v[a] / share:.12g}" for a in "xyz" if v.get(a))
            nodal.append(f"_F(GROUP_NO='{g}', {comps})")
        elif f.type == "force":
            area = surface_area(mesh.groups[f.selection], mesh)
            comps = ", ".join(f"F{a.upper()}={v[a] / area:.12g}" for a in "xyz" if v.get(a))
            face_force.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "pressure" and _on_shells(mesh, mesh.groups[f.selection]):
            shell_pressure.append(f"_F(GROUP_MA='{g}', PRES={v['p']:.12g})")
        elif f.type == "pressure":
            pressure.append(f"_F(GROUP_MA='{g}', PRES={v['p']:.12g})")
        elif f.type == "remote_force_rigid":  # the face moves as one (its load: loadf)
            rigid.append(f"_F(GROUP_MA='{g}')")
        elif f.type in BODY_LOADS:  # ADR 0037: per volume, through the density
            load = body_load(f)
            if load.uniform is not None:
                b = np.asarray(load.uniform)
                size = float(np.linalg.norm(b))
                if size:
                    d = ", ".join(f"{c:.12g}" for c in b / size)
                    gravity.append(f"_F(GROUP_MA='{g}', GRAVITE={size:.12g}, DIRECTION=({d}))")
            elif load.omega:
                axis = ", ".join(f"{c:.12g}" for c in load.axis)
                centre = ", ".join(f"{c:.12g}" for c in load.centre)
                rotation.append(
                    f"_F(GROUP_MA='{g}', VITESSE={load.omega:.12g}, AXE=({axis}), "
                    f"CENTRE=({centre}))"
                )
        elif f.type == "body_force" and mesh.planar:  # per volume on the region (ADR 0048)
            comps = ", ".join(f"F{a.upper()}={v.get(f'f{a}', 0.0):.12g}" for a in "xy")
            internal.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "traction" and mesh.planar:  # per area on 2D edges: FORCE_CONTOUR
            comps = ", ".join(f"F{a.upper()}={v.get(f't{a}', 0.0):.12g}" for a in "xy")
            contour.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "body_force":  # uniform, per volume, no density (ADR 0044)
            comps = ", ".join(f"F{a.upper()}={v.get(f'f{a}', 0.0):.12g}" for a in "xyz")
            internal.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "traction":  # uniform, per area (ADR 0044)
            comps = ", ".join(f"F{a.upper()}={v.get(f't{a}', 0.0):.12g}" for a in "xyz")
            face_force.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "temperature":
            temperature.append(f"_F(GROUP_MA='{g}', TEMP={v['T']:.12g})")
        elif f.type == "heat_flux":
            flux.append(f"_F(GROUP_MA='{g}', FLUN={v['q']:.12g})")
        elif f.type == "heat_generation":
            source.append(f"_F(GROUP_MA='{g}', SOUR={v['q']:.12g})")
        elif f.type == "convection":
            exchange.append(f"_F(GROUP_MA='{g}', COEF_H={v['h']:.12g}, TEMP_EXT={v['T_inf']:.12g})")

    def kw(name: str, items: list[str]) -> str:
        return f", {name}=({', '.join(items)},)" if items else ""

    if thermal:
        return (
            "load = AFFE_CHAR_THER(MODELE=model"
            + kw("TEMP_IMPO", temperature)
            + kw("FLUX_REP", flux)
            + kw("SOURCE", source)
            + kw("ECHANGE", exchange)
            + ")"
        )
    return (
        "load = AFFE_CHAR_MECA(MODELE=model"
        + kw("DDL_IMPO", imposed + extra.get("DDL_IMPO", []))
        + kw("FORCE_NODALE", nodal + extra.get("FORCE_NODALE", []))
        + kw("LIAISON_DDL", extra.get("LIAISON_DDL", []))
        + kw("FORCE_FACE", face_force)
        + kw("FORCE_CONTOUR", contour)
        + kw("PRES_REP", pressure)
        + kw("FORCE_COQUE", shell_pressure)
        + kw("PESANTEUR", gravity)
        + kw("FORCE_INTERNE", internal)
        + kw("LIAISON_SOLIDE", rigid + extra.get("LIAISON_SOLIDE", []))
        + kw("LIAISON_RBE3", extra.get("LIAISON_RBE3", []))
        + kw("ROTATION", rotation)
        + kw("LIAISON_MAIL", tied)
        + ")"
    )


def _modelisation(comp: Component, thermal: bool) -> str:
    """Code_Aster's modelisation for the component's dimension (ADR 0048): Y is the
    axisymmetric axis, as Code_Aster's AXIS takes it."""
    if comp.dimension == "2d_axisymmetric":
        return "AXIS"
    if comp.dimension == "2d_planar":
        if thermal:
            return "PLAN"
        return "C_PLAN" if comp.planar_model == "plane_stress" else "D_PLAN"
    return "3D"


def _ther_lineaire(step, comp: Component) -> str:
    call = "res = THER_LINEAIRE(MODELE=model, CHAM_MATER=mater, EXCIT=excit, "
    if step.type != "transient":
        # Code_Aster 18 wants an INCREMENT even for a steady solve.
        return call + "TYPE_CALCUL='STAT', INCREMENT=_F(LIST_INST=DEFI_LIST_REEL(VALE=(0.0,))))"
    dt, end, _ = time_settings(step)
    heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
    times = f"DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A={end:.12g}, PAS={dt:.12g}))"
    return (
        call
        + f"TYPE_CALCUL='TRAN', ETAT_INIT=_F(VALE={initial_temperature(heat):.12g}), "
        + f"PARM_THETA={_theta(step)}, INCREMENT=_F(LIST_INST={times}))"
    )


def _theta(step) -> float:
    """Code_Aster's theta method; Puzzle defaults to backward Euler (theta = 1), not 0.57."""
    scheme, theta = time_scheme(step)
    if theta is None:
        raise InputError(f"Code_Aster does not offer the {scheme} time scheme", code="unsupported")
    return theta


# --- ADR 0044 stage 3: initial stress, binned shell thicknesses and graded materials -------

INITIAL_STRESS_UNIT = 97  # the initial stress table
INITIAL_STRESS_FILE = "initial-stress.txt"
SIEF = "('SIXX', 'SIYY', 'SIZZ', 'SIXY', 'SIXZ', 'SIYZ')"
BIN_TAG = 700_000  # Gmsh entities for binned element groups, clear of the mesh's own


def _initial_stress_field(initial, mesh: Mesh, given: dict, names: dict, workdir: Path):
    """`sig0`, the initial stress at the Gauss points for STAT_NON_LINE's ETAT_INIT: per
    feature a nodal SIEF field (matched to Code_Aster's nodes by coordinates, as the
    temperature load), turned into Gauss-point values by the elements' shape functions
    (CREA_CHAMP DISC), then summed over each feature's elements (ASSE)."""
    from puzzle.adapters.graded import nodal, solid_blocks, stress_field, uniform_stress

    if not initial:
        return []
    rows = []
    for j, f in enumerate(initial, start=1):
        group = mesh.groups[f.selection]
        solid_blocks(group, mesh)
        field = stress_field(f, given)
        ids = group.node_ids
        values = (np.broadcast_to(uniform_stress(f), (len(ids), 6)) if field is None
                  else nodal(field, ids))  # fmt: skip
        values = values[:, [0, 1, 2, 3, 5, 4]]  # ours xx yy zz xy yz zx; SIXZ before SIYZ
        rows.append(np.column_stack([mesh.node_coords(ids), np.full(len(ids), j), values]))
    workdir.mkdir(parents=True, exist_ok=True)
    np.savetxt(workdir / INITIAL_STRESS_FILE, np.vstack(rows), fmt="%.15g")
    zero = (
        "CREA_CHAMP(TYPE_CHAM='NOEU_SIEF_R', OPERATION='AFFE', MAILLAGE=mesh, "
        f"AFFE=_F(TOUT='OUI', NOM_CMP={SIEF}, VALE=(0.0,) * 6))"
    )
    out = [
        "import numpy as np",
        f"_srows = np.loadtxt('fort.{INITIAL_STRESS_UNIT}', ndmin=2)",
        "_skey = lambda xyz: tuple(np.round(np.asarray(xyz) * 1e9).astype(np.int64))",
        "_sxyz = mesh.getCoordinates().toNumpy().reshape(-1, 3)",
        f"isn0 = {zero}",
        "isg0 = CREA_CHAMP(TYPE_CHAM='ELGA_SIEF_R', OPERATION='DISC', MODELE=model, CHAM_GD=isn0)",
    ]
    for j in range(1, len(initial) + 1):
        out += [
            f"isn{j} = {zero}",
            f"_sgiven = {{_skey(r[:3]): r[4:] for r in _srows if int(r[3]) == {j}}}",
            f"isn{j}.setValues([float(v) for c in _sxyz for v in _sgiven.get(_skey(c), "
            "(0.0,) * 6)])",
            f"isg{j} = CREA_CHAMP(TYPE_CHAM='ELGA_SIEF_R', OPERATION='DISC', MODELE=model, "
            f"CHAM_GD=isn{j})",
        ]
    parts = ["_F(TOUT='OUI', CHAM_GD=isg0)"] + [
        f"_F(GROUP_MA='{names[f.selection]}', CHAM_GD=isg{j}, CUMUL='OUI')"
        for j, f in enumerate(initial, start=1)
    ]
    out.append("sig0 = CREA_CHAMP(TYPE_CHAM='ELGA_SIEF_R', OPERATION='ASSE', MODELE=model, "
               f"ASSE=({', '.join(parts)},))")  # fmt: skip
    return out


def _stage3_bins(project, comp, mesh: Mesh, points, given: dict, thermal: bool, shells):
    """Graded materials and varying shell thicknesses as element groups (ADR 0044 stage 3).

    Each element's value is the mean of its nodes' mapped values; elements are binned
    so none is more than `graded.TOLERANCE` (0.5 %) from its bin's value (DKT's
    thickness is one value per element anyway). Each bin is an element group of the mesh
    written: -> ({material index: [(group, material with the bin's value)]}, shells with
    each varying one replaced by its bins)."""
    from puzzle.adapters.graded import TOLERANCE, binned, element_values, solid_blocks
    from puzzle.fields import material_key, shell_key
    from puzzle.model.schema import Quantity

    sets: dict[str, np.ndarray] = {}
    graded: dict[int, list] = {}
    units = {"youngs_modulus": "Pa", "thermal_conductivity": "W/(m*K)"}
    for i, a in enumerate(comp.materials, start=1):
        fields = {p: given[material_key(a.selection, p)] for p in sorted(a.fields)
                  if material_key(a.selection, p) in given}  # fmt: skip
        if not fields:
            continue
        m = project.get(a.material, Material)
        if m.models.used():
            raise InputError(
                f"material '{m.name}': a property from a field is written for isotropic "
                "materials only (no plasticity, no material axes)",
                code="unsupported",
            )
        if "thermal_conductivity" in fields and "thermal_conductivity" in thermal_tables(m, comp):
            raise InputError(
                f"material '{m.name}': its conductivity comes from a field, so its "
                "temperature table can't apply; set the heat physics' temperature-dependent "
                "materials off",
                code="unsupported",
            )
        blocks = solid_blocks(mesh.groups[a.selection], mesh)
        ids = np.concatenate([b.ids for b in blocks])
        per, levels = {}, {}
        for prop, field in fields.items():
            values = np.concatenate([element_values(field, b)[:, 0] for b in blocks])
            per[prop], levels[prop], _ = binned(values, TOLERANCE)
        props = sorted(fields)
        combos, labels = np.unique(np.column_stack([per[p] for p in props]), axis=0,
                                   return_inverse=True)  # fmt: skip
        graded[i] = []
        for k, combo in enumerate(combos, start=1):
            update = {p: Quantity(value=float(levels[p][c]), unit=units[p])
                      for p, c in zip(props, combo, strict=True)}  # fmt: skip
            group = f"GM{i}_{k}"
            sets[group] = ids[labels.ravel() == k - 1]
            graded[i].append(
                (group, m.model_copy(update={"properties": {**m.properties, **update}}))
            )
    out_shells = []
    for j, (name, s) in enumerate(shells, start=1):
        if not s.fields:
            out_shells.append((name, s))
            continue
        field = given.get(shell_key(s.selection))
        if field is None:
            raise InputError(f"the shell thickness on '{s.selection}': its field was not mapped")
        own = {id(b) for b in mesh.shell_blocks()}
        blocks = [b for b in mesh.groups[s.selection].blocks(mesh) if id(b) in own]
        ids = np.concatenate([b.ids for b in blocks])
        values = np.concatenate([element_values(field, b)[:, 0] for b in blocks])
        labels, levels, _ = binned(values, TOLERANCE)
        for k, level in enumerate(levels, start=1):
            group = f"SH{j}_{k}"
            sets[group] = ids[labels == k - 1]
            thick = Quantity(value=float(level), unit="m")
            out_shells.append((group, s.model_copy(update={"thickness": thick, "fields": {}})))
    if sets:
        _split_groups(points.mesh, sets, names=points.out)
    return graded, out_shells


def _split_groups(mesh: Mesh, sets: dict[str, np.ndarray], names: dict) -> None:
    """Element groups for bins, in place: each set's elements moved onto entities of
    their own (MED groups are made of whole entities), which every group that held the
    old entity also gets; the set's group named in `names` to be written."""
    tag = BIN_TAG
    for set_name, ids in sets.items():
        entities, nodes = [], []
        for index, b in enumerate(list(mesh.blocks)):
            hit = np.isin(b.ids, ids)
            if not hit.any():
                continue
            tag += 1
            new = (b.dim, tag)
            mesh.blocks[index] = CellBlock(b.element, b.dim, b.entity, b.ids[~hit],
                                           b.connectivity[~hit])  # fmt: skip
            mesh.blocks.append(CellBlock(b.element, b.dim, new, b.ids[hit], b.connectivity[hit]))
            for key, g in list(mesh.groups.items()):
                if b.entity in g.entities:
                    mesh.groups[key] = Group(g.name, g.dim, [*g.entities, new], g.node_ids)
            entities.append(new)
            nodes.append(b.connectivity[hit].ravel())
        dim = entities[0][0]
        mesh.groups[f"__{set_name}"] = Group(f"__{set_name}", dim, entities,
                                             np.unique(np.concatenate(nodes)))  # fmt: skip
        names[f"__{set_name}"] = set_name
    mesh.blocks[:] = [b for b in mesh.blocks if len(b.ids)]
    present = {b.entity for b in mesh.blocks}
    for key, g in list(mesh.groups.items()):
        if any(e not in present for e in g.entities):
            mesh.groups[key] = Group(g.name, g.dim, [e for e in g.entities if e in present],
                                     g.node_ids)  # fmt: skip
