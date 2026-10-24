"""Prepare a FEniCSx run (mesh.msh, case.json, study.py) and read its results.npz."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    ELECTROSTATIC,
    MAGNETIC,
    STRUCTURAL,
    THERMAL,
    InputError,
    body_load,
    body_load_on_material,
    check_contacts,
    globalize,
    initial_temperature,
    local_axes,
    match_points,
    refuse_large_deflection,
    remote_load,
    remote_values,
    rigid_motion,
    surface_area,
    thermal_tables,
    time_scheme,
    time_settings,
)
from puzzle.mesh.med import write_med
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Amplitude, Component, Material, Project, Study
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


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    from puzzle.adapters.base import refuse_inputs

    comp = project.get(study.component, Component)
    refuse_inputs(inputs, "FEniCSx", {("load", "temperature")})
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    check_contacts(comp, set(), "FEniCSx")
    refuse_large_deflection(study.solve_steps(), "FEniCSx")
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the FEniCSx adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    allowed = {
        "solid_mechanics": ("stationary", "modal"),
        "heat_transfer": ("stationary", "transient"),
        "electrostatics": ("stationary",),
        "magnetic_fields": ("stationary",),
        "custom_pde": ("stationary", "transient"),
    }
    if len(solved) != 1 or next(iter(solved)) not in allowed:
        raise InputError(
            f"the FEniCSx adapter solves one of {sorted(allowed)} at a time for now, "
            f"not {sorted(solved)}"
        )
    physics = solved.pop()
    if step.type not in allowed[physics]:
        raise InputError(f"the FEniCSx adapter does not run {step.type} {physics} steps yet")

    wanted = {
        "solid_mechanics": STRUCTURAL,
        "heat_transfer": THERMAL,
        "electrostatics": ELECTROSTATIC,
        "magnetic_fields": MAGNETIC,
        "custom_pde": {"dirichlet", "neumann"},
    }[physics]
    features = globalize(
        project,
        [f for ph in comp.physics if ph.type == physics for f in ph.features if f.type in wanted],
    )
    for f in features:
        if f.type in BODY_LOADS:  # cells carry one tag: the material's body (ADR 0037)
            body_load_on_material(comp, f, "FEniCSx")
    coils = [f for f in features if f.type == "coil_current"]
    if physics == "magnetic_fields" and len(coils) != 1:
        raise InputError("the FEniCSx adapter solves magnetic fields driven by one coil for now")
    if not comp.materials and physics != "custom_pde":  # a PDE may need no material
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
                "tables": (
                    thermal_tables(project.get(a.material, Material), comp)
                    if physics == "heat_transfer"
                    else {}
                ),
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
                **({"load": _body_load(f)} if f.type in BODY_LOADS else {}),
                **(_remote(f, mesh) if f.type in ("remote_force", "remote_displacement") else {}),
                **(_cylindrical(project, f) if f.axes is not None else {}),
                **(
                    {"amplitude": [list(p) for p in project.get(f.amplitude, Amplitude).points]}
                    if f.amplitude
                    else {}
                ),
            }
            for f in features
        ],
    }
    if physics == "custom_pde":
        pde = next(ph for ph in comp.physics if ph.type == "custom_pde")
        eq = pde.equation
        if len(eq.unknowns) != 1:
            raise InputError(
                "the FEniCSx adapter solves custom PDEs with one unknown so far", code="unsupported"
            )
        if eq.unknowns[0].family != "Lagrange":
            raise InputError("custom PDE unknowns are Lagrange fields so far", code="unsupported")
        case["equation"] = {
            "unknowns": [u.model_dump() for u in eq.unknowns],
            "weak_form": eq.weak_form,
            "coefficients": {k: q.to_si() for k, q in eq.coefficients.items()},
            "initial_value": float(pde.settings.get("initial_value", 0.0)),
        }
        # The weak form names its integration regions, so every face selection goes in
        # the mesh. dolfinx needs each cell tagged once: volumes are the material
        # selections, or the parts when the PDE has no materials.
        volumes = [a.selection for a in comp.materials] or [p.name for p in comp.parts]
        faces = sorted(g for g, grp in mesh.groups.items() if grp.dim < 3)
        groups = list(dict.fromkeys(volumes + groups + faces))
    workdir.mkdir(parents=True, exist_ok=True)
    if thermal_load is not None:
        if physics != "solid_mechanics" or step.type == "modal":
            raise InputError(
                "a temperature load applies to static structural steps", code="unsupported"
            )
        # Nodal temperatures by coordinates; study.py interpolates them onto its dofs.
        np.savez(
            workdir / "temperature.npz",
            coords=mesh.node_coords(thermal_load.ids),
            values=thermal_load.values[:, 0],
        )
        case["temperature_load"] = {
            "file": "temperature.npz",
            "reference": _reference_temperature(comp),
        }
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
    if "times" in data and "custom_fields" in data:
        name = str(data["custom_fields"][0])
        for t, values in zip(data["times"], data[f"series_{name}"], strict=True):
            values = values.reshape(len(ids), -1)
            comps = ["x", "y", "z"] if values.shape[1] == 3 else [""]
            results.steps.append(
                StepResult(1, "transient", float(t), None, {name: field(name, comps, values)})
            )
        return results
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
    for name, key in (
        ("electric_potential", "potential"),
        ("electric_field", "electric_field"),
        ("magnetic_flux_density", "magnetic_flux_density"),
        ("current_density", "current_density"),
    ):
        if key in data:
            values = data[key].reshape(len(ids), -1)
            comps = ["x", "y", "z"] if values.shape[1] == 3 else [""]
            step.fields[name] = field(name, comps, values)
    for name in [str(n) for n in data["custom_fields"]] if "custom_fields" in data else []:
        values = data[f"field_{name}"].reshape(len(ids), -1)
        comps = ["x", "y", "z"] if values.shape[1] == 3 else [""]
        step.fields[name] = field(name, comps, values)
    for name in ("electric_energy", "magnetic_energy"):
        if name in data:
            value = np.array([[float(data[name])]])
            step.fields[name] = Field(name, "global", [""], np.array([0]), value)
    results.steps.append(step)
    return results


def _reference_temperature(comp) -> float:
    from puzzle.adapters.calculix.inp import reference_temperature

    return reference_temperature(comp)


def _time(step) -> dict:
    if step.type != "transient":
        return {}
    dt, _, n = time_settings(step)
    scheme, theta = time_scheme(step)
    return {"time_step": dt, "steps": n, "scheme": scheme, "theta": theta}


def _body_load(f) -> dict:
    """A body load for study.py (which can't import Puzzle): b in SI, uniform or about an
    axis."""
    load = body_load(f)
    return {
        "uniform": list(load.uniform) if load.uniform is not None else None,
        "omega": load.omega,
        "centre": list(load.centre),
        "axis": list(load.axis),
    }


def _remote(f, mesh) -> dict:
    """A remote load's field for study.py (ADR 0037): the traction's uniform part, twist
    and centroid, or the rigid motion's translation, rotation and point."""
    group = mesh.groups[f.selection]
    if f.type == "remote_force":
        load = remote_load(group, mesh, *remote_values(f))
        return {"traction": [load.uniform.tolist(), load.twist.tolist(), load.centroid.tolist()]}
    motion = rigid_motion(f, group, mesh)
    return {"motion": [motion.u0.tolist(), motion.theta.tolist(), motion.point.tolist()]}


def _cylindrical(project, f) -> dict:
    """A displacement in cylindrical axes for study.py: origin, axis, (r, θ, z) values."""
    axes = local_axes(project, f)
    return {
        "cylindrical": {
            "origin": list(axes.origin),
            "axis": list(np.asarray(axes.axis, float) / np.linalg.norm(axes.axis)),
            "local": [f.values[a].to_si() for a in "xyz"],
        }
    }
