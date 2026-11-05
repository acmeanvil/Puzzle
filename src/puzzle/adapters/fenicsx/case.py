"""Prepare a FEniCSx run (mesh.msh, case.json, study.py) and read its results.npz."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    ELECTROSTATIC,
    HEAT_SOURCES,
    MAGNETIC,
    STRUCTURAL,
    THERMAL,
    InputError,
    as_planar,
    body_load,
    body_load_on_material,
    check_contacts,
    check_dynamics,
    frequency_list,
    globalize,
    harmonic_fields,
    initial_temperature,
    load_area,
    local_axes,
    match_points,
    plane_thickness,
    rayleigh,
    refuse_large_deflection,
    refuse_line_bodies,
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
    holding a python whose environment contains dolfinx, else the pixi fenicsx env
    beside Puzzle's own.
    """
    explicit = os.environ.get("PUZZLE_FENICSX_PYTHON")
    if explicit:
        return Path(explicit)
    from puzzle.jobs.runtime import pixi_solver_dirs

    dirs = [Path(d) for d in os.environ.get("PUZZLE_SOLVER_PATH", "").split(os.pathsep) if d]
    for d in [*dirs, *pixi_solver_dirs()]:
        for name in ("python", "python3", "python.exe"):
            exe = d / name
            if exe.exists() and _has_dolfinx(exe):
                return exe
    return None


def _has_dolfinx(python: Path) -> bool:
    prefix = python.parent if os.name == "nt" else python.parent.parent
    patterns = (
        ["Lib/site-packages/dolfinx"] if os.name == "nt" else ["lib/python3*/site-packages/dolfinx"]
    )
    return any(any(prefix.glob(p)) for p in patterns)


METHODS = (
    ("LU, direct (MUMPS where PETSc has it)", "lu"),
    ("Conjugate gradient + algebraic multigrid (large symmetric)", "cg_gamg"),
    ("Conjugate gradient + Jacobi", "cg_jacobi"),
    ("GMRES + algebraic multigrid", "gmres_gamg"),
)
PETSC_KEY = re.compile(r"^-?[a-z][a-z0-9_]*$")


def _settings(study: Study, step) -> dict:
    """What study.py reads (ADR 0023): only what the study set, so the defaults stay."""
    from puzzle.adapters.fenicsx import FenicsxAdapter

    own = study.solver_settings.get("fenicsx", {})
    FenicsxAdapter().solver_settings(study)  # unknown keys and bad values refused
    out: dict = {}
    method = own.get("linear_method")
    if method is None and "linear_solver" in step.settings:
        method = "cg_gamg" if step.settings["linear_solver"] == "iterative" else "lu"
    if method:
        out["linear_method"] = method
    for key, kind in (("tolerance", float), ("max_iterations", int), ("output_every", int)):
        if key in step.settings:
            out[key] = kind(step.settings[key])
    expert = study.expert_settings.get("fenicsx", {})
    if expert:
        out["petsc_options"] = {k.lstrip("-"): v for k, v in expert.items()}
    return out


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    from puzzle.adapters.base import refuse_inputs

    comp = project.get(study.component, Component)
    refuse_inputs(inputs, "FEniCSx", {("load", "temperature"), ("feature", "*")})
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    check_contacts(comp, set(), "FEniCSx")
    refuse_line_bodies(mesh, "FEniCSx")
    refuse_large_deflection(study.solve_steps(), "FEniCSx")
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the FEniCSx adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    allowed = {
        "solid_mechanics": ("stationary", "modal", "transient", "frequency_domain"),
        "heat_transfer": ("stationary", "transient"),
        "electrostatics": ("stationary",),
        "magnetic_fields": ("stationary", "frequency_domain", "transient"),  # ADR 0047
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
        "heat_transfer": THERMAL | HEAT_SOURCES,
        "electrostatics": ELECTROSTATIC,
        "magnetic_fields": MAGNETIC,
        "custom_pde": {"dirichlet", "neumann"},
    }[physics]
    features = globalize(
        project,
        [f for ph in comp.physics if ph.type == physics for f in ph.features if f.type in wanted],
    )
    for f in features:
        if f.type in BODY_LOADS | {"body_force"} | HEAT_SOURCES:  # cells: one tag each
            body_load_on_material(comp, f, "FEniCSx")
    magnetics = physics == "magnetic_fields" and (
        step.type in ("frequency_domain", "transient")
        or any(f.type == "stranded_coil" for f in features)
    )  # ADR 0047: the A–V solve; else the static one-coil path
    coils = [f for f in features if f.type == "coil_current"]
    if physics == "magnetic_fields" and not magnetics and len(coils) != 1:
        raise InputError("the FEniCSx adapter solves magnetic fields driven by one coil for now")
    if not comp.materials and physics != "custom_pde":  # a PDE may need no material
        raise InputError(f"component '{comp.name}' has no material assignments")
    groups = list(
        dict.fromkeys([a.selection for a in comp.materials] + [f.selection for f in features])
    )
    for g in groups:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")
    as_planar(mesh, comp)
    if mesh.planar:
        _check_2d(comp, physics, step, features, thermal_load)

    case = {
        "physics": physics,
        "dimension": comp.dimension,  # ADR 0048
        "planar_model": comp.planar_model,
        "step": {
            "type": step.type,
            "modes": int(step.settings.get("modes", 10)),
            **_time(step),
            **(_dynamics(study, step) if physics == "solid_mechanics" else {}),
        },
        "initial_temperature": initial_temperature(
            next((p for p in comp.physics if p.type == "heat_transfer"), None)
        ),
        "materials": [
            {
                "group": a.selection,
                "properties": {
                    k: q.to_si() for k, q in project.get(a.material, Material).properties.items()
                }
                | _thickness(comp, a.selection, physics),
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
                    {"area": load_area(mesh.groups[f.selection], mesh, comp)}
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
        if physics != "solid_mechanics" or step.type != "stationary":
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
    _field_files(features, case["features"], inputs, mesh, workdir)
    if magnetics:
        case["magnetics"] = _magnetics(project, comp, step, features, mesh, workdir)
    write_med(mesh, workdir / "mesh.msh", {g: g for g in groups})
    case["settings"] = _settings(study, step)  # ADR 0023
    (workdir / "case.json").write_text(json.dumps(case, indent=2))
    target = workdir / SCRIPT.name
    shutil.copyfile(SCRIPT, target)
    np.savetxt(workdir / "node-ids.txt", mesh.node_ids, fmt="%d")
    np.save(workdir / "node-coords.npy", mesh.coords)
    return target


def _magnetics(project, comp, step, features, mesh, workdir: Path) -> dict:
    """The A–V solve's coils and bodies (ADR 0047), and the reader's metadata."""
    from puzzle.adapters import eddy

    coils = eddy.coils(project, comp, features, mesh, solver="FEniCSx")
    if step.type == "stationary" and any(c.kind == "massive" for c in coils):
        raise InputError(
            "a static magnetic study takes one massive conductor or windings, not both",
            code="unsupported",
        )
    if step.type != "frequency_domain" and any(c.current[1] for c in coils):
        raise InputError("a coil's phase applies in frequency-domain steps only")
    winding = {c.body for c in coils if c.kind == "stranded"}
    bodies = []
    for a in comp.materials:
        props = project.get(a.material, Material).properties
        needs = ("relative_permeability", "electrical_conductivity")
        missing = [k for k in needs if k not in props]
        if missing:
            raise InputError(f"material for '{a.selection}' is missing {', '.join(missing)}")
        bodies.append({"name": a.selection, "mu_r": props["relative_permeability"].to_si(),
                       "sigma": 0.0 if a.selection in winding
                       else props["electrical_conductivity"].to_si()})  # fmt: skip
    frequencies = frequency_list(step) if step.type == "frequency_domain" else [0.0]
    times = None
    if step.type == "transient":
        from puzzle.adapters.elmer.magnetics import transient_times

        times = transient_times(step)
    axisymmetric = comp.dimension == "2d_axisymmetric"
    eddy.write_meta(workdir / eddy.META, bodies, coils, frequencies, times, axisymmetric)
    table = eddy.axisymmetric_table if axisymmetric else eddy.element_table
    table(mesh, [a.selection for a in comp.materials], workdir / eddy.ELEMENTS)
    return {
        "bodies": bodies,
        "frequencies": frequencies,
        "coils": [
            {"body": c.body, "kind": c.kind, "current": list(c.current), "turns": c.turns,
             "start": c.start, "end": c.end,
             "area": surface_area(mesh.groups[c.start], mesh) if c.start else None,
             "amplitude": c.amplitude, "centre": c.centre, "axis": c.axis}
            for c in coils
        ],
    }  # fmt: skip


def _read_magnetics(workdir: Path, data, node_ids, ids) -> Results:
    """One step per frequency from the A–V solve: element values matched to Puzzle's
    elements by their centres, then the shared results (`adapters.eddy`)."""
    from puzzle.adapters import eddy

    meta = eddy.read_meta(workdir / eddy.META)
    table = dict(np.load(workdir / eddy.ELEMENTS))
    if meta.get("axisymmetric"):  # A_φ at the nodes; Puzzle derives B, J, losses
        coords = np.load(workdir / "node-coords.npy")
        xy = {int(n): c[:2] for n, c in zip(node_ids, coords, strict=True)}
        results = Results(solver="fenicsx")
        per = 1 + sum(c.kind == "massive" for c in meta["coils"])  # basis per frequency
        for k, hz in enumerate(data["magnetic_frequencies"]):
            basis = [dict(zip(ids.tolist(), data["axi_re"][i] + 1j * data["axi_im"][i],
                              strict=True)) for i in range(k * per, (k + 1) * per)]  # fmt: skip
            omega = 2 * np.pi * hz
            potential, volts = eddy.combine_axisymmetric(meta, table, basis, omega)
            B, J, A = eddy.axisymmetric_fields(meta, table, xy, potential, omega, volts)
            b_nodal = eddy.nodal_average(table, B, node_ids)
            results.steps.append(eddy.step_result(meta, float(hz), table, B, J, A, volts,
                                                  node_ids, b_nodal))  # fmt: skip
        return results
    rows = match_points(data["cell_midpoints"], table["centres"], np.arange(len(table["ids"])))
    where = np.empty(len(rows), np.int64)
    where[rows] = np.arange(len(rows))  # element k of the table is cell where[k]
    results = Results(solver="fenicsx")
    massive = [c for c in meta["coils"] if c.kind == "massive"]
    transient = "magnetic_times" in data
    axis = data["magnetic_times"] if transient else data["magnetic_frequencies"]
    for k, at in enumerate(axis):
        hz, time = (0.0, float(at)) if transient else (float(at), None)

        def cplx(name, k=k):
            im = data.get(f"mag_{name}_im")
            return data[f"mag_{name}_re"][k] + (1j * im[k] if im is not None else 0j)

        volts = cplx("voltage") if massive and not transient else [0.0] * len(massive)
        start = {c.body: complex(v) for c, v in zip(massive, volts, strict=True)}
        b_nodal = np.empty((len(ids), 3), complex)
        b_nodal[:] = cplx("b")
        results.steps.append(eddy.step_result(
            meta, float(hz), table, cplx("B")[where], cplx("J")[where], cplx("A")[where],
            start, ids, b_nodal, time,
        ))  # fmt: skip
    return results


def read_results(workdir: Path) -> Results:
    path = workdir / RESULTS
    if not path.exists():
        raise FileNotFoundError(f"FEniCSx wrote no {RESULTS}; see the solver log")
    data = np.load(path)
    node_ids = np.loadtxt(workdir / "node-ids.txt", dtype=np.int64, ndmin=1)
    ids = match_points(data["coords"], np.load(workdir / "node-coords.npy"), node_ids)
    if "magnetic_frequencies" in data or "magnetic_times" in data:  # ADR 0047
        return _read_magnetics(workdir, data, node_ids, ids)
    order = np.argsort(ids)

    def field(name: str, comps: list[str], values: np.ndarray) -> Field:
        return Field(name, "node", comps, ids[order], values[order])

    results = Results(solver="fenicsx")
    if "dynamic_times" in data:  # a structural transient (ADR 0041)
        for t, u, s in zip(
            data["dynamic_times"], data["displacement_series"], data["stress_series"], strict=True
        ):
            fields = {
                "displacement": field("displacement", ["x", "y", "z"], u),
                "stress": field("stress", ["xx", "yy", "zz", "xy", "yz", "zx"], s),
            }
            results.steps.append(StepResult(1, "transient", float(t), None, fields))
        return results
    if "harmonic_frequencies" in data:  # one step per frequency (ADR 0041)
        for hz, re_, im in zip(
            data["harmonic_frequencies"],
            data["displacement_real"],
            data["displacement_imag"],
            strict=True,
        ):
            parts = harmonic_fields("displacement", ids[order], re_[order], im[order], "xyz")
            results.steps.append(
                StepResult(1, "frequency_domain", float(hz), None, {f.name: f for f in parts})
            )
        return results
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


def _field_files(features, written: list[dict], inputs, mesh: Mesh, workdir: Path) -> None:
    """Values from imported fields (ADR 0044): each one's nodal values on its selection
    in an .npz by coordinates, named in the feature's "fields", for study.py to build a
    Lagrange function from. A field varying with frequency in a harmonic step: a file
    per frequency, listed in "spectra" with the frequencies (Hz)."""
    from puzzle.fields import feature_key

    given = {i.source: i.field for i in inputs if i.role == "feature"}
    spectra = {i.source: i.frequencies for i in inputs if i.role == "feature" and i.frequencies}
    for k, (f, entry) in enumerate(zip(features, written, strict=True)):
        for name in sorted(f.fields):
            field = given.get(feature_key(f, name))
            if field is None:
                raise InputError(
                    f"{f.type} on '{f.selection}': its field for {name} was not mapped"
                )
            frequencies = spectra.get(feature_key(f, name))
            if frequencies:
                files = []
                for s in range(len(frequencies)):
                    files.append(f"field-{k}-{name}-{s}.npz")
                    np.savez(workdir / files[-1], coords=mesh.node_coords(field.ids),
                             values=field.values[:, s])  # fmt: skip
                entry.setdefault("spectra", {})[name] = {
                    "frequencies": [float(x) for x in frequencies],
                    "files": files,
                }
                continue
            file = f"field-{k}-{name}.npz"
            np.savez(workdir / file, coords=mesh.node_coords(field.ids), values=field.values[:, 0])
            entry.setdefault("fields", {})[name] = file


def _reference_temperature(comp) -> float:
    from puzzle.adapters.calculix.inp import reference_temperature

    return reference_temperature(comp)


def _time(step) -> dict:
    if step.type != "transient":
        return {}
    dt, _, n = time_settings(step)
    scheme, theta = time_scheme(step)
    return {"time_step": dt, "steps": n, "scheme": scheme, "theta": theta}


def _check_2d(comp, physics, step, features, thermal_load) -> None:
    """What FEniCSx's 2D models (ADR 0048) don't take yet, refused by name."""
    if physics == "magnetic_fields" and comp.dimension == "2d_axisymmetric":
        if step.type not in ("stationary", "frequency_domain"):
            raise InputError("axisymmetric magnetic fields run static or frequency-domain "
                             "steps", code="unsupported")  # fmt: skip
        return  # ADR 0047 item 7: windings only, checked with the coils
    if physics not in ("solid_mechanics", "heat_transfer"):
        raise InputError(
            f"2D models on FEniCSx solve structures or heat, not {physics}", code="unsupported"
        )
    if physics == "solid_mechanics" and step.type != "stationary":
        raise InputError(
            f"2D structures on FEniCSx are static so far, not {step.type}", code="unsupported"
        )
    if thermal_load is not None:
        raise InputError("a temperature load is not written in 2D yet", code="unsupported")
    allowed = {"fixed", "displacement", "force", "pressure", "elastic_support"} | THERMAL
    for f in features:
        fielded = f.type == "displacement" and f.fields  # ADR 0044: 3D only so far
        if f.type not in allowed or f.axes is not None or f.amplitude is not None or fielded:
            raise InputError(
                f"{f.type} on '{f.selection}' is not written in 2D models on FEniCSx yet",
                code="unsupported",
            )


def _thickness(comp, selection: str, physics: str) -> dict:
    """A planar part's thickness for study.py's weights (plane stress; 1 m in plane
    strain); none needed for heat, whose loads are per area."""
    if comp.dimension != "2d_planar" or physics != "solid_mechanics":
        return {}
    return {"thickness": plane_thickness(comp, selection.split("/", 1)[0])}


def _dynamics(study, step) -> dict:
    """A structural transient's or harmonic step's settings for study.py (ADR 0041):
    solved directly (FEniCSx has no modal superposition here), Rayleigh damping."""
    if step.type not in ("transient", "frequency_domain"):
        return {}
    check_dynamics(step, "FEniCSx", modal=False)
    if step.settings.get("method", "direct") == "modal":
        raise InputError(
            "FEniCSx solves structural dynamics directly: set method to direct",
            code="unsupported",
        )
    out: dict = {"rayleigh": list(rayleigh(study) or (0.0, 0.0))}
    if step.type == "transient":
        alpha = float(step.settings.get("hht_alpha", 0.0))
        if not -1 / 3 <= alpha <= 0:
            raise InputError("hht_alpha is between -1/3 and 0", code="invalid")
        out["hht_alpha"] = alpha
    else:
        out["frequencies"] = frequency_list(step)
    return out


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
            "origin": axes.centre().tolist(),
            "axis": axes.orientation()[:, 2].tolist(),
            "local": [f.values[a].to_si() for a in "xyz"],
        }
    }
