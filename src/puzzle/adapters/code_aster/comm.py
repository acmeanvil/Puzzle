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
    STRUCTURAL,
    THERMAL,
    InputError,
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
}
FORMAT_R = "1PE18.10"


CONTACTS = {"bonded_nonconformal", "frictionless", "frictional"}


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
    check_contacts(comp, CONTACTS, "Code_Aster")
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the Code_Aster adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    if solved not in ({"solid_mechanics"}, {"heat_transfer"}):
        raise InputError(
            "the Code_Aster adapter solves solid_mechanics or heat_transfer alone for now, "
            f"not {sorted(solved)}"
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
            out.append(f"mat{i} = DEFI_MATERIAU({_material(m, thermal)})")
        assignments.append(f"_F(GROUP_MA='{names[a.selection]}', MATER=mat{i})")
    out.append(f"mater = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=({', '.join(assignments)},))")
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
    elif touching:
        out += [
            _defi_contact(touching, names),
            # Ramp the loads over ten increments: friction depends on the loading path.
            "ramp = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, 1.0, 1.0))",
            "res = STAT_NON_LINE(MODELE=model, CHAM_MATER=mater, "
            "EXCIT=_F(CHARGE=load, FONC_MULT=ramp), CONTACT=contact, "
            "COMPORTEMENT=_F(RELATION='ELAS'), "
            # As Code_Aster's 3D contact validation (ssnv128): tangent every iteration.
            "NEWTON=_F(REAC_ITER=1), CONVERGENCE=_F(ITER_GLOB_MAXI=200, RESI_GLOB_RELA=1e-6), "
            "INCREMENT=_F(LIST_INST=DEFI_LIST_REEL(DEBUT=0.0, "
            "INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, CONTRAINTE=('SIGM_NOEU',), "
            "FORCE=('REAC_NODA',))",
            _table("res", "DEPL", "displacement", "INST=1.0"),
            _table("res", "SIGM_NOEU", "stress", "INST=1.0"),
            _table("res", "REAC_NODA", "reaction_force", "INST=1.0"),
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


def _material(m: Material, thermal: bool) -> str:
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
    return f"ELAS=_F(E={e:.12g}, NU={nu:.12g}{rho})"


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
    it; source faces are the slave side."""
    friction = any(c.type == "frictional" for c in contacts)
    zones = []
    for c in contacts:
        zone = f"_F(GROUP_MA_MAIT='{names[c.target]}', GROUP_MA_ESCL='{names[c.source]}'"
        zones.append(zone + (f", COULOMB={c.friction or 0.0:.10g})" if friction else ")"))
    return (
        "contact = DEFI_CONTACT(MODELE=model, FORMULATION='CONTINUE', "
        f"FROTTEMENT='{'COULOMB' if friction else 'SANS'}', ZONE=({', '.join(zones)},))"
    )


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


def _ther_non_line(step, comp: Component) -> str:
    call = (
        "res = THER_NON_LINE(MODELE=model, CHAM_MATER=mater, EXCIT=_F(CHARGE=load), "
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
        + kw("LIAISON_MAIL", tied)
        + ")"
    )


def _ther_lineaire(step, comp: Component) -> str:
    call = "res = THER_LINEAIRE(MODELE=model, CHAM_MATER=mater, EXCIT=_F(CHARGE=load), "
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
