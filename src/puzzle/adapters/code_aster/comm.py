"""Write a Code_Aster command file (.comm), run description (.export) and MED mesh.

Results come back as comma-separated tables (IMPR_TABLE), which need no MED or HDF5
reader: nodal displacement, stress (SIGM_NOEU) or temperature, plus frequencies for
modal steps. Feature values follow the other adapters: displacement x/y/z, force
x/y/z (total, applied as a uniform traction), pressure p (positive pushes in),
temperature T, heat_flux q (positive into the body), convection h and T_inf. SI.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.common import (
    STRUCTURAL,
    THERMAL,
    InputError,
    initial_temperature,
    surface_area,
    time_settings,
)
from puzzle.mesh.med import safe_names, write_med
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Feature, Material, Project, Study

JOB = "job"
ROOM_K = 293.15
TABLES = {"displacement": 81, "stress": 82, "temperature": 83, "frequency": 84}
FORMAT_R = "1PE18.10"


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
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
    groups = list(
        dict.fromkeys([a.selection for a in comp.materials] + [f.selection for f in features])
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")
    names = safe_names(groups)
    write_med(mesh, workdir / f"{JOB}.med", names)

    phenomenon, model = ("THERMIQUE", "3D") if thermal else ("MECANIQUE", "3D")
    out = [
        "DEBUT(LANG='EN')",
        "mesh = LIRE_MAILLAGE(FORMAT='MED', UNITE=20)",
        "model = AFFE_MODELE(MAILLAGE=mesh, "
        f"AFFE=_F(TOUT='OUI', PHENOMENE='{phenomenon}', MODELISATION='{model}'))",
    ]
    assignments = []
    for i, a in enumerate(comp.materials, start=1):
        out.append(
            f"mat{i} = DEFI_MATERIAU({_material(project.get(a.material, Material), thermal)})"
        )
        assignments.append(f"_F(GROUP_MA='{names[a.selection]}', MATER=mat{i})")
    out.append(f"mater = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=({', '.join(assignments)},))")
    out.append(_loads(features, names, mesh, thermal))

    if thermal:
        out += [
            _ther_lineaire(step, comp),
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
    else:
        out += [
            "res = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, EXCIT=_F(CHARGE=load))",
            "res = CALC_CHAMP(reuse=res, RESULTAT=res, CONTRAINTE=('SIGM_NOEU',))",
            _table("res", "DEPL", "displacement"),
            _table("res", "SIGM_NOEU", "stress"),
        ]
    out.append("FIN()")
    comm = workdir / f"{JOB}.comm"
    comm.write_text("\n".join(out) + "\n")

    used = (
        {"temperature"}
        if thermal
        else ({"displacement", "frequency"} if step.type == "modal" else {"displacement", "stress"})
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


def _table(result: str, field: str, name: str) -> str:
    return (
        f"IMPR_TABLE(TABLE=CREA_TABLE(RESU=_F(RESULTAT={result}, NOM_CHAM='{field}', "
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


def _loads(features: list[Feature], names: dict[str, str], mesh: Mesh, thermal: bool) -> str:
    imposed, face_force, pressure, temperature, flux, exchange = [], [], [], [], [], []
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
        + f"INCREMENT=_F(LIST_INST={times}))"
    )
