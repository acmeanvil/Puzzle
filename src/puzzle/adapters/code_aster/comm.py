"""Write a Code_Aster command file (.comm), run description (.export) and MED mesh.

Results come back as comma-separated tables (IMPR_TABLE), which need no MED or HDF5
reader: nodal displacement, stress (SIGM_NOEU) or temperature, plus frequencies for
modal steps. Feature values follow the other adapters: displacement x/y/z, force
x/y/z (total, applied as a uniform traction), pressure p (positive pushes in),
temperature T, heat_flux q (positive into the body), convection h and T_inf. SI.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    HOLDING,
    PRETENSION,
    STRUCTURAL,
    THERMAL,
    InputError,
    body_load,
    check_contacts,
    cylindrical_expressions,
    globalize,
    initial_temperature,
    local_axes,
    remote_load,
    remote_values,
    rigid_motion,
    section_cuts,
    support_nodes,
    support_stiffness,
    surface_area,
    thermal_tables,
    time_scheme,
    time_settings,
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
}
FORMAT_R = "1PE18.10"


CONTACTS = {"bonded_nonconformal", "frictionless", "frictional"}


TEMPERATURE_UNIT = 90  # the transferred temperature table


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    comp = project.get(study.component, Component)
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
    allowed = ("stationary", "transient") if thermal else ("stationary", "modal", "buckling")
    if step.type not in allowed:
        raise InputError(
            f"the Code_Aster adapter does not run {step.type} {sorted(solved)} steps yet"
        )

    wanted = THERMAL if thermal else STRUCTURAL
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
    nlgeom = bool(step.settings.get("geometric_nonlinearity", False))
    if nlgeom and (thermal or step.type != "stationary"):
        raise InputError(
            "geometric_nonlinearity applies to static structural steps", code="unsupported"
        )
    increments = int(step.settings.get("increments", 10))
    plastic = (
        [a for a in comp.materials if project.get(a.material, Material).models.plasticity]
        if not thermal and step.type == "stationary"
        else []
    )
    # A tie projects the source (slave) face's nodes into the target part's volume.
    tie_groups = [g for c in ties for g in (c.source, c.target.split("/", 1)[0])]
    tie_groups += [g for c in touching for g in (c.source, c.target)]
    groups = list(
        dict.fromkeys(
            [a.selection for a in comp.materials]
            + [f.selection for f in features + preload + springs + lifting + bolts]
            + [sel for c in fasteners for sel in (c.source, c.target)]
            + tie_groups
        )
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")
    names = safe_names(groups)
    supports = _spring_nodes(springs, mesh)
    gaps = _spring_nodes(lifting, mesh)
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
    points = _Points(base, names)
    for i, (node, *_rest) in enumerate(supports, start=1):
        points.add(f"S{i}", node, "SPRINGS")
    for i, (node, *_rest) in enumerate(gaps, start=1):
        points.add(f"C{i}", node, "GAPS")
    for i, end in enumerate(fastener_ends, start=1):
        if end["distributing"]:  # a 6-dof master point per end for LIAISON_RBE3
            points.add(f"M{i}A", end["masters"][0], "MASTERS")
            points.add(f"M{i}B", end["masters"][1], "MASTERS")
    for k, cut in enumerate(cuts, start=1):
        points.add(f"PR{k}", cut.ref, "PRETENSION")
        for i, (node, twin) in enumerate(cut.doubles.items(), start=1):
            points.add(f"P{k}A{i}", node)
            points.add(f"P{k}B{i}", twin)
    write_med(points.mesh, workdir / f"{JOB}.med", names)

    phenomenon, model = ("THERMIQUE", "3D") if thermal else ("MECANIQUE", "3D")
    faces = sorted({names[g] for c in touching for g in (c.source, c.target)})
    out = [
        "DEBUT(LANG='EN')",
        "mesh = LIRE_MAILLAGE(FORMAT='MED', UNITE=20)",
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
                    for g in [n for n in points.names if n.startswith("M")]
                    + sorted(
                        {
                            names[sel]
                            for c in fasteners
                            if c.ends == "distributing"
                            for sel in (c.source, c.target)
                        }
                    )
                    + [
                        f"F{i}{e}"
                        for i, c in enumerate(fasteners, start=1)
                        if c.ends == "distributing"
                        for e in "AB"
                    ]
                )
                + ",)))"
            ]
            if any(c.ends == "distributing" for c in fasteners)
            else []
        ),  # fmt: skip
        "model = AFFE_MODELE(MAILLAGE=mesh, "
        f"AFFE=(_F(TOUT='OUI', PHENOMENE='{phenomenon}', MODELISATION='{model}'),"
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
        ),
    ]
    cara = "CARA_ELEM=cara, " if points.sets else ""
    assignments = []
    materials = [project.get(a.material, Material) for a in comp.materials]
    tables = [thermal_tables(m, comp) if thermal else {} for m in materials]
    nonlinear = any(tables)  # temperature tables: THER_NL and THER_NON_LINE
    for i, (a, m, t) in enumerate(zip(comp.materials, materials, tables, strict=True), start=1):
        if nonlinear:
            out += _ther_nl(i, m, t)
        else:
            expansion = thermal_load is not None
            traction = ""
            if not thermal and step.type == "stationary" and m.models.plasticity:
                out.append(_traction(i, m))
                traction = f", TRACTION=_F(SIGM=trac{i})"
            out.append(f"mat{i} = DEFI_MATERIAU({_material(m, thermal, expansion)}{traction})")
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
        out += _thermal_loads(project, features, names, mesh)
    else:
        extra = _pretension_loads(bolts, cuts)
        extra.update(_fastener_loads(fastener_ends, fasteners, names))
        plain = _loads(features, names, mesh, thermal, ties, extra)
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
            _ther_non_line(step, comp) if nonlinear else _ther_lineaire(step, comp),
            _table("res", "TEMP", "temperature"),
        ]
    elif step.type == "buckling":
        out += _buckling(step, cara, charges, preload, names, mesh, ties)
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
    elif touching or nlgeom or plastic or gaps:
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
            *([_defi_contact(touching, names)] if touching else []),
            # Ramp the loads: friction and large deflection depend on the loading path.
            "ramp = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 1.0))",
            f"res = STAT_NON_LINE(MODELE=model, CHAM_MATER=mater, {cara}"
            "EXCIT=("
            + ", ".join(f"_F(CHARGE={c}, FONC_MULT=ramp)" for c in charges)
            + ",), "
            + ("CONTACT=contact, " if touching else "")
            + behaviour
            # As Code_Aster's 3D contact validation (ssnv128): tangent every iteration.
            + "NEWTON=_F(REAC_ITER=1), CONVERGENCE=_F(ITER_GLOB_MAXI=200, RESI_GLOB_RELA=1e-6), "
            "INCREMENT=_F(LIST_INST=DEFI_LIST_REEL(DEBUT=0.0, "
            f"INTERVALLE=_F(JUSQU_A=1.0, NOMBRE={increments}))))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, CONTRAINTE=('SIGM_NOEU',), "
            "FORCE=('REAC_NODA',)"
            # V1 of VMIS_ISOT_TRAC is the cumulated (equivalent) plastic strain p.
            + (", VARI_INTERNE=('VARI_NOEU',), DEFORMATION=('EPSP_NOEU',)" if plastic else "")
            + ")",
            _table("res", "DEPL", "displacement", which),
            _table("res", "SIGM_NOEU", "stress", which),
            _table("res", "REAC_NODA", "reaction_force", which),
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
        out += [
            f"res = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, {cara}EXCIT=("
            + ", ".join(f"_F(CHARGE={c})" for c in charges)
            + ",))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, CONTRAINTE=('SIGM_NOEU',), "
            "FORCE=('REAC_NODA',))",
            _table("res", "DEPL", "displacement"),
            _table("res", "SIGM_NOEU", "stress"),
            _table("res", "REAC_NODA", "reaction_force"),
        ]
    out.append("FIN()")
    comm = workdir / f"{JOB}.comm"
    comm.write_text("\n".join(out) + "\n")

    used = (
        {"temperature"}
        if thermal
        else (
            {"displacement", "frequency"}
            if step.type == "modal"
            else {"displacement", "buckling"}
            if step.type == "buckling"
            else {"displacement", "stress", "reaction_force"}
            | ({"plastic_strain", "plastic_strain_equivalent"} if plastic else set())
        )
    )
    export = workdir / f"{JOB}.export"
    export.write_text(
        "\n".join(
            [
                "P time_limit 3600",
                "P memory_limit 4096",
                "P ncpus 1",
                "P mpi_nbcpu 1",
                f"F comm {JOB}.comm D 1",
                f"F mmed {JOB}.med D 20",
                f"F mess {JOB}.mess R 6",
                *[f"F libr {name}.csv R {unit}" for name, unit in TABLES.items() if name in used],
                *(
                    [f"F libr transferred-temperature.txt D {TEMPERATURE_UNIT}"]
                    if thermal_load is not None
                    else []
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
        fastener_end,
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
        a, b = (fastener_end(s, original, what) for s in (f.source, f.target))
        axis, _side = fastener_frame(a.centre, b.centre)
        tight = f.pretension is not None
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
    out: dict[str, list[str]] = {"LIAISON_SOLIDE": [], "LIAISON_RBE3": []}
    for i, (end, f) in enumerate(zip(ends, fasteners, strict=True), start=1):
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


def _discrete_cara(supports, references: int, tiny: float = 0.0, gaps=(), masters=()) -> list[str]:
    """Each spring's stiffness in its own axes (REPERE='LOCAL'; global is the default):
    x along the face normal (kn), y and z along the face (kt). A pretension reference
    node carries a negligible stiffness (`tiny`) along its first dof, only to have
    dofs; its other two are held."""
    if not supports and not references and not gaps and not masters:
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
    return [
        "cara = AFFE_CARA_ELEM(MODELE=model, DISCRET=(",
        *(f"    {s}," for s in stiff),
        ")" + (", ORIENTATION=(" if axes else ")"),
        *(f"    {a}," for a in axes),
        *(["))"] if axes else []),
    ]


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


def _material(m: Material, thermal: bool, expansion: bool = False) -> str:
    p = {k: q.to_si() for k, q in m.properties.items()}

    def need(*keys: str) -> list[float]:
        missing = [k for k in keys if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        return [p[k] for k in keys]

    if thermal:
        (k,) = need("thermal_conductivity")
        rho_cp = (
            p["density"] * p["specific_heat"] if {"density", "specific_heat"} <= p.keys() else 1.0
        )
        return f"THER=_F(LAMBDA={k:.12g}, RHO_CP={rho_cp:.12g})"
    e, nu = need("youngs_modulus", "poissons_ratio")
    rho = f", RHO={p['density']:.12g}" if "density" in p else ""
    alpha = f", ALPHA={need('thermal_expansion')[0]:.12g}" if expansion else ""
    return f"ELAS=_F(E={e:.12g}, NU={nu:.12g}{rho}{alpha})"


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


def _defi_contact(contacts, names: dict[str, str]) -> str:
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


def _thermal_loads(project, features, names, mesh) -> list[str]:
    """`excit`: the thermal charges, those following an amplitude multiplied by it
    (DEFI_FONCTION of time, held constant outside its points)."""
    from puzzle.model.schema import Amplitude

    out, excit = [], []
    plain = [f for f in features if not f.amplitude]
    base = _loads(plain, names, mesh, True)
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
        out.append(_loads(members, names, mesh, True).replace("load = ", f"load{k} = ", 1))
        excit.append(f"_F(CHARGE=load{k}, FONC_MULT=amp{k})")
    if not excit:  # nothing imposed at all: the charge as it always was
        out.append(base)
        excit.append("_F(CHARGE=load)")
    out.append(f"excit = ({', '.join(excit)},)")
    return out


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
    features: list[Feature], names: dict[str, str], mesh: Mesh, thermal: bool, ties=(), extra=None
) -> str:
    """The plain load charge; `extra` adds items by keyword (pretension's)."""
    extra = extra or {}
    imposed, face_force, pressure, temperature, flux, exchange = [], [], [], [], [], []
    gravity, rotation, rigid = [], [], []
    tied = [
        f"_F(GROUP_MA_MAIT='{names[c.target.split('/', 1)[0]]}', "
        f"GROUP_MA_ESCL='{names[c.source]}', TYPE_RACCORD='MASSIF')"
        for c in ties
    ]
    for f in features:
        g = names[f.selection]
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "fixed":
            imposed.append(f"_F(GROUP_MA='{g}', DX=0.0, DY=0.0, DZ=0.0)")
        elif f.type == "displacement" and f.axes is None:  # in local axes: loadf
            dofs = ", ".join(f"D{a.upper()}={v[a]:.12g}" for a in "xyz" if a in v)
            imposed.append(f"_F(GROUP_MA='{g}', {dofs})")
        elif f.type == "force":
            area = surface_area(mesh.groups[f.selection], mesh)
            comps = ", ".join(f"F{a.upper()}={v[a] / area:.12g}" for a in "xyz" if v.get(a))
            face_force.append(f"_F(GROUP_MA='{g}', {comps})")
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
        elif f.type == "temperature":
            temperature.append(f"_F(GROUP_MA='{g}', TEMP={v['T']:.12g})")
        elif f.type == "heat_flux":
            flux.append(f"_F(GROUP_MA='{g}', FLUN={v['q']:.12g})")
        elif f.type == "convection":
            exchange.append(f"_F(GROUP_MA='{g}', COEF_H={v['h']:.12g}, TEMP_EXT={v['T_inf']:.12g})")

    def kw(name: str, items: list[str]) -> str:
        return f", {name}=({', '.join(items)},)" if items else ""

    if thermal:
        return (
            "load = AFFE_CHAR_THER(MODELE=model"
            + kw("TEMP_IMPO", temperature)
            + kw("FLUX_REP", flux)
            + kw("ECHANGE", exchange)
            + ")"
        )
    return (
        "load = AFFE_CHAR_MECA(MODELE=model"
        + kw("DDL_IMPO", imposed + extra.get("DDL_IMPO", []))
        + kw("FORCE_NODALE", extra.get("FORCE_NODALE", []))
        + kw("LIAISON_DDL", extra.get("LIAISON_DDL", []))
        + kw("FORCE_FACE", face_force)
        + kw("PRES_REP", pressure)
        + kw("PESANTEUR", gravity)
        + kw("LIAISON_SOLIDE", rigid + extra.get("LIAISON_SOLIDE", []))
        + kw("LIAISON_RBE3", extra.get("LIAISON_RBE3", []))
        + kw("ROTATION", rotation)
        + kw("LIAISON_MAIL", tied)
        + ")"
    )


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
