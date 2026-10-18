"""Prepare a Code_Saturne run (mesh.msh, case.json, cs_setup.py) and read its results.

Feature values: velocity_inlet U (inflow speed normal to the face, m/s);
pressure_outlet p (reference pressure, Pa, default 0); wall (no slip); symmetry.
Fluid properties come from the material: density and dynamic_viscosity.
Step settings: iterations (steady SIMPLE iterations, default 500).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.code_saturne.ensight import read_case, read_variable
from puzzle.adapters.common import FLUID, InputError, check_contacts, match_points
from puzzle.mesh.med import write_med
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project, Study
from puzzle.results.model import Field, Results, StepResult

DRIVER = Path(__file__).with_name("cs_setup.py")
RUN_ID = "puzzle"
NATURES = {
    "velocity_inlet": "inlet",
    "pressure_outlet": "outlet",
    "wall": "wall",
    "symmetry": "symmetry",
}
FIELDS = {"Velocity": ("velocity", ["x", "y", "z"]), "Pressure": ("pressure", [""])}


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
    check_contacts(comp, set(), "Code_Saturne")
    steps = study.solve_steps()
    if len(steps) != 1 or steps[0].type != "stationary":
        raise InputError("the Code_Saturne adapter runs one stationary solve step for now")
    solved = set(steps[0].physics) or {p.type for p in comp.physics}
    if solved != {"fluid_flow"}:
        raise InputError(
            f"the Code_Saturne adapter solves fluid_flow alone for now, not {sorted(solved)}"
        )
    if len(comp.materials) != 1:
        raise InputError("the Code_Saturne adapter needs exactly one fluid material for now")
    props = {
        k: q.to_si()
        for k, q in project.get(comp.materials[0].material, Material).properties.items()
    }
    missing = [k for k in ("density", "dynamic_viscosity") if k not in props]
    if missing:
        raise InputError(f"the fluid material is missing {', '.join(missing)}")

    features = [
        f for p in comp.physics if p.type == "fluid_flow" for f in p.features if f.type in FLUID
    ]
    unsupported = [f.type for f in features if f.type not in NATURES]
    if unsupported:
        raise InputError(
            f"the Code_Saturne adapter does not support {sorted(set(unsupported))} yet"
        )
    body = comp.materials[0].selection
    groups = list(dict.fromkeys([body] + [f.selection for f in features]))
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")

    workdir.mkdir(parents=True, exist_ok=True)
    tags = write_med(mesh, workdir / "mesh.msh", {g: g for g in groups}, msh_version=2.2)
    zones = []
    for f in features:
        v = {k: q.to_si() for k, q in f.values.items()}
        zone = {
            "label": _label(f.selection),
            "selection": str(tags[f.selection]),
            "nature": NATURES[f.type],
        }
        if f.type == "velocity_inlet":
            zone["velocity"] = v["U"]
        elif f.type == "pressure_outlet":
            zone["pressure"] = v.get("p", 0.0)
        zones.append(zone)
    spec = {
        "mesh": "mesh.msh",
        "density": props["density"],
        "viscosity": props["dynamic_viscosity"],
        "iterations": int(steps[0].settings.get("iterations", 500)),
        "zones": zones,
    }
    (workdir / "case.json").write_text(json.dumps(spec, indent=2))
    shutil.copyfile(DRIVER, workdir / DRIVER.name)

    volume = [b for b in mesh.blocks if b.dim == 3]
    np.savetxt(workdir / "element-ids.txt", np.concatenate([b.ids for b in volume]), fmt="%d")
    np.save(
        workdir / "element-centroids.npy",
        np.vstack(
            [
                mesh.node_coords(b.connectivity.ravel())
                .reshape(*b.connectivity.shape, 3)
                .mean(axis=1)
                for b in volume
            ]
        ),
    )
    return workdir / "case.json"


def command() -> list[str]:
    return [
        "sh",
        "-c",
        f"python3 {DRIVER.name} && code_saturne create --noref -c CASE > /dev/null "
        f"&& cp setup.xml CASE/DATA/ && cd CASE/DATA && code_saturne run --id {RUN_ID}",
    ]


def read_results(workdir: Path) -> Results:
    case_file = workdir / "CASE" / "RESU" / RUN_ID / "postprocessing" / "RESULTS_FLUID_DOMAIN.case"
    if not case_file.exists():
        raise FileNotFoundError(
            "Code_Saturne wrote no results; see CASE/RESU/puzzle/run_solver.log"
        )
    parts, variables = read_case(case_file)
    centroids = np.vstack([b.centroids for p in parts for b in p.blocks])
    element_ids = np.loadtxt(workdir / "element-ids.txt", dtype=np.int64, ndmin=1)
    ids = match_points(centroids, np.load(workdir / "element-centroids.npy"), element_ids)
    order = np.argsort(ids)
    step = StepResult(1, "stationary", 1.0)
    for name, (_, components, path) in variables.items():
        if name in FIELDS:
            field_name, comps = FIELDS[name]
            values = read_variable(path, parts, components)
            step.fields[field_name] = Field(field_name, "element", comps, ids[order], values[order])
    return Results(solver="code_saturne", steps=[step])


def _label(selection: str) -> str:
    return selection.replace("/", "_").replace(" ", "_")
