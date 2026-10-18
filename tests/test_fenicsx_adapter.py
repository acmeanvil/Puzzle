import json
import os

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
    assert fenicsx_python() is None  # no dolfinx in that environment
    (env / "lib" / "python3.12" / "site-packages" / "dolfinx").mkdir(parents=True)
    if os.name != "nt":
        assert fenicsx_python() == python
    monkeypatch.setenv("PUZZLE_FENICSX_PYTHON", "/opt/x/python")
    assert str(fenicsx_python()) == "/opt/x/python"
