"""Write a Code_Aster command file (.comm), run description (.export) and MED mesh.

Results come back as comma-separated tables (IMPR_TABLE), which need no MED or HDF5
reader: nodal displacement, stress (SIGM_NOEU) or temperature, plus frequencies for
modal steps. Feature values follow the other adapters: displacement x/y/z, force
x/y/z (total, applied as a uniform traction), pressure p (positive pushes in),
temperature T, heat_flux q (positive into the body), convection h and T_inf. SI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    STRUCTURAL,
    THERMAL,
    InputError,
    body_load,
    check_contacts,
    initial_temperature,
    surface_area,
    thermal_tables,
    time_scheme,
    time_settings,
)
from puzzle.mesh.med import safe_names, write_med
from puzzle.mesh.mesh import Mesh
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
}
FORMAT_R = "1PE18.10"


CONTACTS = {"bonded_nonconformal", "frictionless", "frictional"}


TEMPERATURE_UNIT = 90  # the transferred temperature table


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    comp = project.get(study.component, Component)
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    check_contacts(comp, CONTACTS, "Code_Aster")
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the Code_Aster adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    if solved not in ({"solid_mechanics"}, {"heat_transfer"}):
        raise InputError(
            "the Code_Aster adapter solves solid_mechanics or heat_transfer alone for now, "
            f"not {sorted(solved)}: set the step's physics to one of them (study > steps > "
            "physics). For thermal stress, solve heat in its own study and pass its "
            "temperature to a structural study as a transfer (role load)."
        )
    thermal = solved == {"heat_transfer"}
    allowed = ("stationary", "transient") if thermal else ("stationary", "modal")
    if step.type not in allowed:
        raise InputError(
            f"the Code_Aster adapter does not run {step.type} {sorted(solved)} steps yet"
        )

    wanted = THERMAL if thermal else STRUCTURAL
    features = [
        f for ph in comp.physics if ph.type in solved for f in ph.features if f.type in wanted
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in ("fixed", "displacement")]
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
            [a.selection for a in comp.materials] + [f.selection for f in features] + tie_groups
        )
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")
    names = safe_names(groups)
    write_med(mesh, workdir / f"{JOB}.med", names)

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
        "model = AFFE_MODELE(MAILLAGE=mesh, "
        f"AFFE=_F(TOUT='OUI', PHENOMENE='{phenomenon}', MODELISATION='{model}'))",
    ]
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
    if thermal:  # loads following an amplitude get their own charge (ADR 0037)
        out += _thermal_loads(project, features, names, mesh)
    else:
        out.append(_loads(features, names, mesh, thermal, ties))

    if thermal:
        out += [
            _ther_non_line(step, comp) if nonlinear else _ther_lineaire(step, comp),
            _table("res", "TEMP", "temperature"),
        ]
    elif step.type == "modal":
        n = int(step.settings.get("modes", 10))
        out += [
            "ASSEMBLAGE(MODELE=model, CHAM_MATER=mater, CHARGE=load, NUME_DDL=CO('numddl'),",
            "           MATR_ASSE=(_F(MATRICE=CO('stiff'), OPTION='RIGI_MECA'),",
            "                      _F(MATRICE=CO('mass'), OPTION='MASS_MECA')))",
            "res = CALC_MODES(MATR_RIGI=stiff, MATR_MASS=mass, OPTION='PLUS_PETITE', "
            f"CALC_FREQ=_F(NMAX_FREQ={n}))",
            _table("res", "DEPL", "displacement"),
            f"IMPR_TABLE(TABLE=RECU_TABLE(CO=res, NOM_PARA='FREQ'), UNITE={TABLES['frequency']}, "
            f"FORMAT='TABLEAU', SEPARATEUR=',', FORMAT_R='{FORMAT_R}')",
        ]
    elif touching or nlgeom or plastic:
        # Green-Lagrange strain: large rotations, as CalculiX's NLGEOM (GROT_GDEP refuses tet10);
        # with plasticity, logarithmic strains (true stress against true strain).
        strains = ", DEFORMATION='GREEN_LAGRANGE'" if nlgeom else ""
        # Every increment is kept when the deflection is large or the material yields
        # (load-deflection and stress-strain histories).
        which = "" if nlgeom or plastic else "INST=1.0"
        behaviour = f"COMPORTEMENT=_F(RELATION='ELAS'{strains}), "
        if plastic:
            yielding = ", ".join(f"'{names[a.selection]}'" for a in plastic)
            large = ", DEFORMATION='GDEF_LOG'" if nlgeom else ""
            rest = [a for a in comp.materials if a not in plastic]
            parts = [f"_F(GROUP_MA=({yielding},), RELATION='VMIS_ISOT_TRAC'{large})"]
            if rest:
                elastic = ", ".join(f"'{names[a.selection]}'" for a in rest)
                parts.append(f"_F(GROUP_MA=({elastic},), RELATION='ELAS'{strains})")
            behaviour = f"COMPORTEMENT=({', '.join(parts)},), "
        out += [
            *([_defi_contact(touching, names)] if touching else []),
            # Ramp the loads: friction and large deflection depend on the loading path.
            "ramp = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 1.0))",
            "res = STAT_NON_LINE(MODELE=model, CHAM_MATER=mater, "
            "EXCIT=_F(CHARGE=load, FONC_MULT=ramp), "
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
            "res = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, EXCIT=_F(CHARGE=load))",
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
    held = [f for f in features if f.type in ("fixed", "displacement")]
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
    features: list[Feature], names: dict[str, str], mesh: Mesh, thermal: bool, ties=()
) -> str:
    imposed, face_force, pressure, temperature, flux, exchange = [], [], [], [], [], []
    gravity, rotation = [], []
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
        elif f.type == "displacement":
            dofs = ", ".join(f"D{a.upper()}={v[a]:.12g}" for a in "xyz" if a in v)
            imposed.append(f"_F(GROUP_MA='{g}', {dofs})")
        elif f.type == "force":
            area = surface_area(mesh.groups[f.selection], mesh)
            comps = ", ".join(f"F{a.upper()}={v[a] / area:.12g}" for a in "xyz" if v.get(a))
            face_force.append(f"_F(GROUP_MA='{g}', {comps})")
        elif f.type == "pressure":
            pressure.append(f"_F(GROUP_MA='{g}', PRES={v['p']:.12g})")
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
        + kw("DDL_IMPO", imposed)
        + kw("FORCE_FACE", face_force)
        + kw("PRES_REP", pressure)
        + kw("PESANTEUR", gravity)
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
