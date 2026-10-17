"""NAFEMS LE1 through the whole pipeline: sigma_yy at point D must match 92.7 MPa."""

import importlib.util
from pathlib import Path

import pytest

from puzzle.jobs import runtime_for
from puzzle.model import Study
from puzzle.post import component, sample
from puzzle.project import load_project
from puzzle.solve import run_study

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "nafems_le1"

pytestmark = pytest.mark.skipif(
    not runtime_for("calculix").available("ccx"),
    reason="CalculiX not available (install ccx, or build its image and set PUZZLE_RUNTIME)",
)


def _build_module():
    spec = importlib.util.spec_from_file_location("le1_build", BENCHMARK / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_project_matches_the_builder(tmp_path):
    le1 = _build_module()
    fresh = le1.build(tmp_path)
    committed = load_project(BENCHMARK)
    assert {type(o).__name__ for o in fresh.objects.values()} == {
        type(o).__name__ for o in committed.objects.values()
    }


def test_le1_sigma_yy_at_d(tmp_path):
    le1 = _build_module()
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "LE1")
    run = run_study(project, study.id, BENCHMARK, tmp_path / "run", timeout_s=900)

    stress = run.results.last("stress")
    at_d = sample(run.mesh, stress, (*le1.POINT_D, None))
    assert at_d.distance == 0.0 and len(at_d.node_ids) >= 2  # real nodes through the thickness
    assert at_d.values[component(stress, "yy")] == pytest.approx(le1.TARGET_SYY, rel=0.01)
