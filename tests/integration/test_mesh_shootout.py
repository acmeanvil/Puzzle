"""A mesh shoot-out (ADR 0019): the cantilever's tip load on Gmsh's and on Netgen's mesh
of the same recipe agree on the peak displacement within 1 %."""

import shutil
from pathlib import Path

import pytest

from puzzle.model.schema import ComparisonMetric, Study
from puzzle.project import load_project
from puzzle.shootout import run_mesh_shootout
from puzzle.solve import StudyError

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"


def test_gmsh_and_netgen_agree(tmp_path):
    from solvers import available

    from puzzle.mesh.netgen_mesher import netgen_python

    if netgen_python() is None or not available("calculix"):
        pytest.skip("needs Netgen and CalculiX")
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    project = load_project(root)
    study = next(s for s in project.of_type(Study) if s.name == "Tip load")
    metric = ComparisonMetric(quantity="displacement", norm="relative_value", tolerance=0.01)
    out = run_mesh_shootout(project, study.id, root, tmp_path / "w", [metric])
    (c,) = out.comparisons
    print(f"mesh shoot-out: peak displacement differs by {100 * c.value:.3f} %")
    assert out.passed
    nodewise = ComparisonMetric(quantity="displacement", norm="relative_l2", tolerance=0.01)
    with pytest.raises(StudyError, match="different nodes"):
        run_mesh_shootout(project, study.id, root, tmp_path / "x", [nodewise])
