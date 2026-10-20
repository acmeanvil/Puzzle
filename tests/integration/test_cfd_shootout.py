"""Code_Saturne and OpenFOAM as a shoot-out pair (ADR 0011) on the laminar channel.

The benchmark study (benchmarks/channel_flow) lists OpenFOAM as its shoot-out solver:
both run on one hexahedral mesh and their cell values are compared directly, with the
tolerances the study carries. Each solver's own accuracy against plane Poiseuille flow
is tested in test_channel_flow.py.
"""

from pathlib import Path

import pytest
from solvers import available

from puzzle.model import Study
from puzzle.project import load_project
from puzzle.shootout import run_shootout

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "channel_flow"

pytestmark = pytest.mark.skipif(
    not (available("code_saturne") and available("openfoam")),
    reason="needs code_saturne and openfoam",
)


def test_channel_flow_shootout(tmp_path):
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    out = run_shootout(project, study.id, BENCHMARK, tmp_path, timeout_s=3600)
    print("\n" + out.table())
    assert set(out.runs) == {"code_saturne", "openfoam"}
    assert out.runs["openfoam"].mesh is out.runs["code_saturne"].mesh  # one mesh
    assert out.passed
