"""Prepare a FEniCSx run (mesh.msh, case.json, study.py) and read its results.npz."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    STRUCTURAL,
    THERMAL,
    InputError,
    initial_temperature,
    match_points,
    surface_area,
    time_settings,
)
from puzzle.mesh.med import write_med
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project, Study
from puzzle.results.model import Field, Results, StepResult

SCRIPT = Path(__file__).with_name("study.py")
RESULTS = "results.npz"


def fenicsx_python() -> Path | None:
    """A Python interpreter whose environment has dolfinx.

    PUZZLE_FENICSX_PYTHON wins; otherwise the first directory on PUZZLE_SOLVER_PATH
    holding a python whose environment contains dolfinx (e.g. the pixi fenicsx env).
    """
    explicit = os.environ.get("PUZZLE_FENICSX_PYTHON")
    if explicit:
        return Path(explicit)
    for d in filter(None, os.environ.get("PUZZLE_SOLVER_PATH", "").split(os.pathsep)):
        for name in ("python", "python3", "python.exe"):
            exe = Path(d) / name
            if exe.exists() and _has_dolfinx(exe):
                return exe
    return None


def _has_dolfinx(python: Path) -> bool:
    prefix = python.parent if os.name == "nt" else python.parent.parent
    patterns = (
        ["Lib/site-packages/dolfinx"] if os.name == "nt" else ["lib/python3*/site-packages/dolfinx"]
    )
    return any(any(prefix.glob(p)) for p in patterns)


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the FEniCSx adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    if solved not in ({"solid_mechanics"}, {"heat_transfer"}):
        raise InputError(
            "the FEniCSx adapter solves solid_mechanics or heat_transfer alone for now, "
            f"not {sorted(solved)}"
        )
    physics = solved.pop()
    allowed = ("stationary", "transient") if physics == "heat_transfer" else ("stationary", "modal")
    if step.type not in allowed:
        raise InputError(f"the FEniCSx adapter does not run {step.type} {physics} steps yet")

    wanted = STRUCTURAL if physics == "solid_mechanics" else THERMAL
    features = [
        f for ph in comp.physics if ph.type == physics for f in ph.features if f.type in wanted
    ]
    if not comp.materials:
        raise InputError(f"component '{comp.name}' has no material assignments")
    groups = list(
        dict.fromkeys([a.selection for a in comp.materials] + [f.selection for f in features])
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")

    case = {
        "physics": physics,
        "step": {"type": step.type, "modes": int(step.settings.get("modes", 10)), **_time(step)},
        "initial_temperature": initial_temperature(
            next((p for p in comp.physics if p.type == "heat_transfer"), None)
        ),
        "materials": [
            {
                "group": a.selection,
                "properties": {
                    k: q.to_si() for k, q in project.get(a.material, Material).properties.items()
                },
            }
            for a in comp.materials
        ],
        "features": [
            {
                "type": f.type,
                "group": f.selection,
                "values": {k: q.to_si() for k, q in f.values.items()},
                **(
                    {"area": surface_area(mesh.groups[f.selection], mesh)}
                    if f.type == "force"
                    else {}
                ),
            }
            for f in features
        ],
    }
    workdir.mkdir(parents=True, exist_ok=True)
    write_med(mesh, workdir / "mesh.msh", {g: g for g in groups})
    (workdir / "case.json").write_text(json.dumps(case, indent=2))
    target = workdir / SCRIPT.name
    shutil.copyfile(SCRIPT, target)
    np.savetxt(workdir / "node-ids.txt", mesh.node_ids, fmt="%d")
    np.save(workdir / "node-coords.npy", mesh.coords)
    return target


def read_results(workdir: Path) -> Results:
    path = workdir / RESULTS
    if not path.exists():
        raise FileNotFoundError(f"FEniCSx wrote no {RESULTS}; see the solver log")
    data = np.load(path)
    node_ids = np.loadtxt(workdir / "node-ids.txt", dtype=np.int64, ndmin=1)
    ids = match_points(data["coords"], np.load(workdir / "node-coords.npy"), node_ids)
    order = np.argsort(ids)

    def field(name: str, comps: list[str], values: np.ndarray) -> Field:
        return Field(name, "node", comps, ids[order], values[order])

    results = Results(solver="fenicsx")
    if "times" in data:
        for t, values in zip(data["times"], data["temperature_series"], strict=True):
            temperature = field("temperature", [""], values.reshape(-1, 1))
            results.steps.append(
                StepResult(1, "transient", float(t), None, {"temperature": temperature})
            )
        return results
    if "frequencies" in data:
        for i, hz in enumerate(data["frequencies"], start=1):
            shape = field("displacement", ["x", "y", "z"], data["modes"][i - 1])
            results.steps.append(StepResult(1, "modal", float(hz), i, {"displacement": shape}))
        return results
    step = StepResult(1, "", 1.0)
    if "displacement" in data:
        step.fields["displacement"] = field("displacement", ["x", "y", "z"], data["displacement"])
    if "stress" in data:
        step.fields["stress"] = field(
            "stress", ["xx", "yy", "zz", "xy", "yz", "zx"], data["stress"]
        )
    if "temperature" in data:
        step.fields["temperature"] = field("temperature", [""], data["temperature"])
    results.steps.append(step)
    return results


def _time(step) -> dict:
    if step.type != "transient":
        return {}
    dt, _, n = time_settings(step)
    return {"time_step": dt, "steps": n}
