"""Prepare a Code_Saturne run (mesh.msh, case.json, cs_setup.py) and read its results.

Feature values: velocity_inlet U (inflow speed normal to the face, m/s), and for
turbulent flow intensity (fraction, default 0.05) and hydraulic_diameter (default 4A/P
of the inlet face); pressure_outlet p (reference pressure, Pa, default 0); wall (no
slip); symmetry. Heat transfer (with fluid_flow): temperature T on an inlet or wall,
heat_flux q (W/m^2, positive into the fluid) or convection h, T_inf on a wall; other
walls are adiabatic. Fluid properties come from the material: density,
dynamic_viscosity, and thermal_conductivity and specific_heat for heat transfer.
fluid_flow settings: turbulence_model = laminar (default), k-epsilon or k-omega-SST.
Step settings: iterations (steady SIMPLE iterations, default 500) and pseudo_time_step
(s; Code_Saturne's default 0.1 s is slow for conduction-dominated scalars).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.code_saturne.ensight import read_case, read_variable
from puzzle.adapters.common import (
    FLUID,
    THERMAL,
    InputError,
    check_contacts,
    hydraulic_diameter,
    match_points,
)
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
FIELDS = {
    "Velocity": ("velocity", ["x", "y", "z"]),
    "Pressure": ("pressure", [""]),
    "TempK": ("temperature", [""]),
    "temperature": ("temperature", [""]),
    "k": ("turbulent_kinetic_energy", [""]),
    "epsilon": ("turbulent_dissipation", [""]),
    "omega": ("specific_dissipation", [""]),
    "turbulent_viscosity": ("turbulent_viscosity", [""]),
}
TURBULENCE = {"laminar": "off", "k-epsilon": "k-epsilon-PL", "k-omega-SST": "k-omega-SST"}


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
    check_contacts(comp, set(), "Code_Saturne")
    steps = study.solve_steps()
    if len(steps) != 1 or steps[0].type != "stationary":
        raise InputError("the Code_Saturne adapter runs one stationary solve step for now")
    solved = set(steps[0].physics) or {p.type for p in comp.physics}
    if solved not in ({"fluid_flow"}, {"fluid_flow", "heat_transfer"}):
        raise InputError(
            "the Code_Saturne adapter solves fluid_flow, with or without heat_transfer, "
            f"not {sorted(solved)}"
        )
    heat = "heat_transfer" in solved
    if len(comp.materials) != 1:
        raise InputError("the Code_Saturne adapter needs exactly one fluid material for now")
    props = {
        k: q.to_si()
        for k, q in project.get(comp.materials[0].material, Material).properties.items()
    }
    needed = ["density", "dynamic_viscosity"] + (
        ["thermal_conductivity", "specific_heat"] if heat else []
    )
    missing = [k for k in needed if k not in props]
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
    flow = next(p for p in comp.physics if p.type == "fluid_flow")
    model = str(flow.settings.get("turbulence_model", "laminar"))
    if model not in TURBULENCE:
        raise InputError(
            f"unknown turbulence_model '{model}'",
            code="invalid",
            details={"models": sorted(TURBULENCE)},
        )
    thermal = [
        f
        for p in comp.physics
        if p.type == "heat_transfer" and heat
        for f in p.features
        if f.type in THERMAL
    ]
    body = comp.materials[0].selection
    groups = list(dict.fromkeys([body] + [f.selection for f in features]))
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")

    workdir.mkdir(parents=True, exist_ok=True)
    tags = write_med(mesh, workdir / "mesh.msh", {g: g for g in groups}, msh_version=2.2)
    zones, by_selection = [], {}
    for f in features:
        v = {k: q.to_si() for k, q in f.values.items()}
        zone = {
            "label": _label(f.selection),
            "selection": str(tags[f.selection]),
            "nature": NATURES[f.type],
        }
        if f.type == "velocity_inlet":
            zone["velocity"] = v["U"]
            if model != "laminar":
                group = mesh.groups[f.selection]
                zone["intensity"] = 100.0 * v.get("intensity", 0.05)  # Code_Saturne: percent
                zone["hydraulic_diameter"] = v.get("hydraulic_diameter") or hydraulic_diameter(
                    group, mesh
                )
        elif f.type == "pressure_outlet":
            zone["pressure"] = v.get("p", 0.0)
        zones.append(zone)
        by_selection[f.selection] = zone
    for f in thermal:
        zone = by_selection.get(f.selection)
        if zone is None or zone["nature"] not in ("inlet", "wall"):
            raise InputError(
                f"{f.type} on '{f.selection}' needs an inlet or wall there in fluid_flow"
            )
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "temperature":
            zone["temperature"] = v["T"]
        elif zone["nature"] != "wall":
            raise InputError(f"{f.type} applies to walls, not the inlet '{f.selection}'")
        elif f.type == "heat_flux":
            zone["heat_flux"] = v["q"]
        else:
            zone["exchange"] = [v["h"], v["T_inf"]]
    spec = {
        "mesh": "mesh.msh",
        "density": props["density"],
        "viscosity": props["dynamic_viscosity"],
        "iterations": int(steps[0].settings.get("iterations", 500)),
        "pseudo_time_step": _seconds(steps[0].settings.get("pseudo_time_step")),
        "turbulence": TURBULENCE[model],
        "thermal": (
            {
                "conductivity": props["thermal_conductivity"],
                "specific_heat": props["specific_heat"],
            }
            if heat
            else None
        ),
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


def _seconds(value) -> float | None:
    if value is None:
        return None
    return value.to_si() if hasattr(value, "to_si") else float(value)


def _label(selection: str) -> str:
    return selection.replace("/", "_").replace(" ", "_")
