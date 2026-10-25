"""Buckling steps (ADR 0028) in the model: labels, what each solver takes, refusals.

The benchmarks are in tests/integration/test_buckling.py.
"""

import pytest

from puzzle.adapters.calculix.inp import InputError, _preload_steps
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.model.catalog import STEP_DEFAULTS, STEP_TYPES
from puzzle.model.schema import SolveStep
from puzzle.results.model import StepResult


def test_labels_say_what_multiplies_what():
    assert StepResult(1, "buckling", 3.4217, 1).label() == "Buckling mode 1: load factor 3.422"
    assert StepResult(1, "buckling", -2.0, 2).label().endswith("(load reversed)")
    assert StepResult(1, "modal", 12.5, 1).label() == "mode 1: 12.5 Hz"
    assert StepResult(1, "stationary", 1.0).label() == "t = 1"


def test_a_new_buckling_step_shows_its_settings():
    assert "buckling" in STEP_TYPES
    assert STEP_DEFAULTS["buckling"] == {"modes": 4, "preload": False}


def test_a_preload_needs_a_static_step_before():
    steps = [SolveStep(type="stationary"), SolveStep(type="buckling", settings={"preload": True})]
    assert _preload_steps(steps) == {1: 2}
    with pytest.raises(InputError):
        _preload_steps([SolveStep(type="buckling", settings={"preload": True})])
    assert _preload_steps([SolveStep(type="buckling")]) == {}


def test_elmer_and_fenicsx_refuse_buckling():
    for adapter in (ElmerAdapter(), FenicsxAdapter()):
        assert "buckling" not in adapter.capabilities.physics["solid_mechanics"]
