import json
import os
from pathlib import Path

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.common import InputError
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.adapters.fenicsx.case import fenicsx_python, read_results
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study


def _case(tmp_path, features, step, physics="solid_mechanics"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile, [Physics(type=physics, features=features)], mesh_size_mm=5
    )
    study = project.add(Study(name="s", component=comp.id, solver="fenicsx", steps=[step]))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    return FenicsxAdapter().write_input(project, study, mesh, tmp_path / "case"), mesh


def test_case_files(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    script, _ = _case(tmp_path, [fixed, force], SolveStep(type="stationary"))
    work = script.parent
    assert script.name == "study.py" and (work / "mesh.msh").exists()
    case = json.loads((work / "case.json").read_text())
    assert case["physics"] == "solid_mechanics" and case["step"]["type"] == "stationary"
    assert case["materials"][0]["group"] == "Bar/Body"
    assert case["materials"][0]["properties"]["poissons_ratio"] == pytest.approx(0.3)
    tip = next(f for f in case["features"] if f["type"] == "force")
    assert tip["values"]["y"] == -100 and tip["area"] == pytest.approx(1e-4, rel=1e-9)


def test_heat_case(tmp_path):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    script, _ = _case(tmp_path, [hot], SolveStep(type="stationary"), physics="heat_transfer")
    case = json.loads((script.parent / "case.json").read_text())
    assert case["physics"] == "heat_transfer"
    assert case["features"][0]["values"]["T"] == pytest.approx(373.15)
    tables = case["materials"][0]["tables"]
    assert [573.15, 44.01] in tables["thermal_conductivity"] and "specific_heat" in tables


def test_unsupported_steps_are_refused(tmp_path):
    with pytest.raises(InputError, match="transient"):
        _case(tmp_path, [Feature(type="fixed", selection="Bar/Root")], SolveStep(type="transient"))


def test_results_map_back_to_mesh_nodes(tmp_path):
    script, mesh = _case(
        tmp_path, [Feature(type="fixed", selection="Bar/Root")], SolveStep(type="stationary")
    )
    work = script.parent
    shuffle = np.random.default_rng(0).permutation(len(mesh.node_ids))
    coords = mesh.coords[shuffle]
    np.savez(
        work / "results.npz",
        coords=coords,
        displacement=coords * 2.0,
        stress=np.ones((len(coords), 6)),
    )
    (step,) = read_results(work).steps
    disp = step.fields["displacement"]
    assert np.array_equal(disp.ids, np.sort(mesh.node_ids))
    assert np.allclose(disp.values, 2.0 * mesh.node_coords(disp.ids))


def test_modes(tmp_path):
    script, mesh = _case(
        tmp_path, [Feature(type="fixed", selection="Bar/Root")], SolveStep(type="modal")
    )
    work = script.parent
    n = len(mesh.node_ids)
    np.savez(
        work / "results.npz",
        coords=mesh.coords,
        frequencies=np.array([10.0, 20.0]),
        modes=np.ones((2, n, 3)),
    )
    assert [(m.mode, m.value) for m in read_results(work).modes()] == [(1, 10.0), (2, 20.0)]


def test_python_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("PUZZLE_FENICSX_PYTHON", raising=False)
    env = tmp_path / "env"
    bindir = env / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    python = bindir / "python"
    python.write_text("")
    monkeypatch.setenv("PUZZLE_SOLVER_PATH", str(bindir))
    from puzzle.jobs import runtime

    monkeypatch.setattr(runtime, "pixi_solver_dirs", lambda: [])  # not this checkout's
    assert fenicsx_python() is None  # no dolfinx in that environment
    (env / "lib" / "python3.12" / "site-packages" / "dolfinx").mkdir(parents=True)
    if os.name != "nt":
        assert fenicsx_python() == python
    monkeypatch.setenv("PUZZLE_FENICSX_PYTHON", "/opt/x/python")
    assert fenicsx_python() == Path("/opt/x/python")


def test_electromagnetic_cases(tmp_path):
    live = Feature(
        type="electric_potential", selection="Bar/Tip", values={"V": Quantity(value=5, unit="kV")}
    )
    ground = Feature(type="ground", selection="Bar/Root")
    script, _ = _case(
        tmp_path / "e", [live, ground], SolveStep(type="stationary"), physics="electrostatics"
    )
    case = json.loads((script.parent / "case.json").read_text())
    assert case["physics"] == "electrostatics"
    assert {f["type"] for f in case["features"]} == {"electric_potential", "ground"}

    coil = [
        Feature(
            type="coil_current", selection="Bar/Body", values={"I": Quantity(value=1, unit="kA")}
        ),
        Feature(type="coil_start", selection="Bar/Root"),
        Feature(type="coil_end", selection="Bar/Tip"),
    ]
    script, _ = _case(tmp_path / "m", coil, SolveStep(type="stationary"), physics="magnetic_fields")
    case = json.loads((script.parent / "case.json").read_text())
    current = next(f for f in case["features"] if f["type"] == "coil_current")
    assert current["group"] == "Bar/Body" and current["values"]["I"] == pytest.approx(1000)

    with pytest.raises(InputError, match="one coil"):
        _case(tmp_path / "n", coil[1:], SolveStep(type="stationary"), physics="magnetic_fields")


def test_electromagnetic_results(tmp_path):
    script, mesh = _case(
        tmp_path,
        [Feature(type="ground", selection="Bar/Root")],
        SolveStep(type="stationary"),
        physics="electrostatics",
    )
    work = script.parent
    n = len(mesh.node_ids)
    np.savez(
        work / "results.npz",
        coords=mesh.coords,
        potential=np.zeros((n, 1)),
        magnetic_flux_density=np.ones((n, 3)),
        magnetic_energy=np.array(2.5e-5),
    )
    (step,) = read_results(work).steps
    assert step.fields["magnetic_flux_density"].components == ["x", "y", "z"]
    assert step.fields["electric_potential"].values.shape == (n, 1)
    energy = step.fields["magnetic_energy"]
    assert energy.location == "global" and energy.values[0, 0] == pytest.approx(2.5e-5)


def test_custom_pde_cases(tmp_path):
    from puzzle.model import CustomEquation, Unknown

    def case(equation, tmp):
        tmp.mkdir(parents=True, exist_ok=True)
        stepfile = write_bar_step(tmp / "bar.step")
        physics = Physics(
            type="custom_pde",
            equation=equation,
            features=[
                Feature(type="dirichlet", selection="Bar/Root", values={"u": Quantity(value=1.0)})
            ],
        )
        project, comp = bar_project(stepfile, [physics], mesh_size_mm=5)
        study = project.add(
            Study(
                name="s", component=comp.id, solver="fenicsx", steps=[SolveStep(type="stationary")]
            )
        )
        mesh = generate_mesh(project, comp.id, tmp, tmp / "m").mesh
        return FenicsxAdapter().write_input(project, study, mesh, tmp / "case")

    script = case(
        CustomEquation(
            unknowns=[Unknown(name="u")],
            weak_form="u * v_u * dx",
            coefficients={"k": Quantity(value=2, unit="kW/(m*K)")},
        ),
        tmp_path / "a",
    )
    written = json.loads((script.parent / "case.json").read_text())
    assert written["equation"]["weak_form"] == "u * v_u * dx"
    assert written["equation"]["coefficients"] == {"k": 2000.0}
    assert written["features"][0]["type"] == "dirichlet"

    two = CustomEquation(unknowns=[Unknown(name="u"), Unknown(name="p")], weak_form="0")
    with pytest.raises(InputError, match="one unknown"):
        case(two, tmp_path / "b")
    edge = CustomEquation(unknowns=[Unknown(name="E", family="N1curl")], weak_form="0")
    with pytest.raises(InputError, match="Lagrange"):
        case(edge, tmp_path / "c")
