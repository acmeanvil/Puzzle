from uuid import uuid4

import numpy as np
import pytest

from puzzle.model import ComparisonMetric, Project, Study
from puzzle.results.model import Field, Results, StepResult
from puzzle.shootout import compare
from puzzle.solve import StudyError, run_study


def _results(values, ids=(1, 2, 3), location="element", modes=()):
    step = StepResult(1, "stationary", 1.0)
    step.fields["velocity"] = Field(
        "velocity", location, ["x"], np.array(ids), np.asarray(values, float).reshape(-1, 1)
    )
    out = Results("s", [step])
    for n, f in enumerate(modes, start=1):
        out.steps.append(StepResult(1, "modal", f, mode=n))
    return out


def _metric(norm, quantity="velocity"):
    return ComparisonMetric(quantity=quantity, norm=norm, tolerance=0.01)


def test_relative_l2_and_max_difference():
    a = _results([1.0, 2.0, 2.0])
    b = _results([1.0, 2.0, 2.3])
    assert compare(a, b, _metric("relative_l2")) == pytest.approx(0.1)
    assert compare(a, b, _metric("max_abs_difference")) == pytest.approx(0.3)


def test_entities_are_matched_by_id():
    a = _results([1.0, 2.0, 3.0], ids=(1, 2, 3))
    b = _results([3.0, 1.0], ids=(3, 1))  # another order, one entity missing
    assert compare(a, b, _metric("max_abs_difference")) == 0.0
    with pytest.raises(StudyError, match="different entities"):
        compare(a, _results([1.0], ids=(9,)), _metric("relative_l2"))


def test_relative_values():
    assert compare(
        _results([1.0, -4.0]), _results([2.0, 4.2]), _metric("relative_value")
    ) == pytest.approx(0.05)  # peak magnitude
    assert compare(
        _results([1.0], ids=(0,), location="global"),
        _results([1.1], ids=(0,), location="global"),
        _metric("relative_value"),
    ) == pytest.approx(0.1)
    frequencies = _metric("relative_value", "eigenfrequencies")
    a, b = _results([0.0], modes=(10.0, 20.0, 30.0)), _results([0.0], modes=(10.1, 19.0))
    assert compare(a, b, frequencies) == pytest.approx(0.05)  # mode 2; mode 3 unmatched


def test_only_listed_solvers_run(tmp_path):
    p = Project(name="x")
    study = p.add(Study(name="s", component=uuid4(), solver="calculix"))
    with pytest.raises(StudyError, match="neither the primary nor a shoot-out"):
        run_study(p, study.id, tmp_path, tmp_path / "run", solver="openfoam")
