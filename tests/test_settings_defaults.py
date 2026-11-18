"""ADR 0023's consequence: "the schema's defaults reproduce today's input files byte for
byte" (ADR 0090 package 16, ADR-63). Per adapter, a study with an empty settings block
of its own (and an empty pass-through block) writes exactly the input of a study with
none, so an untouched Solver settings… form never moves a result. Written through
`run_study`, stopped before the solver (`written_or_refused`)."""

from __future__ import annotations

import pytest
import written_or_refused as wor

CASES = [
    wor.Case("calculix", "solid_mechanics", "stationary"),
    wor.Case("code_aster", "solid_mechanics", "stationary"),
    wor.Case("elmer", "solid_mechanics", "stationary"),
    wor.Case("fenicsx", "solid_mechanics", "stationary"),
    wor.Case("code_saturne", "fluid_flow", "stationary"),
    wor.Case("openfoam", "fluid_flow", "stationary"),
    wor.Case("opencourant", "solid_mechanics", "explicit"),
    wor.Case("pynite", "solid_mechanics", "stationary"),
]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return wor.World(tmp_path_factory.mktemp("settings-defaults"))


def _empty_blocks(project, comp, study):
    study.solver_settings = {study.solver: {}}
    study.expert_settings = {study.solver: {}}


@pytest.mark.parametrize("case", CASES, ids=[c.solver for c in CASES])
def test_an_empty_settings_block_writes_the_default_input(world, case):
    if case.where == "frame":
        pytest.importorskip("OCP")
        from puzzle.adapters.pynite import pynite_python

        if pynite_python() is None:
            pytest.skip("no Python with PyNite here")
    base = wor.base(world, case)
    assert base.kind == "written", f"{case.ident()}: {base}"
    got = wor.cell(world, case, _empty_blocks)
    assert got.kind == "silent", f"{case.ident()}: an empty settings block changed {got}"
