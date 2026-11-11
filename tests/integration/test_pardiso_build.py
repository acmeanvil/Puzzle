"""CalculiX with PARDISO built by `puzzle.solver_builds` (ADR 0068 step 4, ADR 0051).

The whole build: the official source fetched and checked, every file compiled with the
environment's compilers, linked against MKL, and the new ccx solving a one-element deck
with PARDISO before it is put in place. It takes minutes and replaces the environment's
ccx, so it runs only when asked (`PUZZLE_BUILD_TESTS=1`) and where the calculix-pardiso
environment is installed. The benchmarks then run on it as on any CalculiX
(`test_benchmarks.py` finds it first).
"""

import os

import pytest

from puzzle import solver_builds

pytestmark = pytest.mark.skipif(
    os.environ.get("PUZZLE_BUILD_TESTS") != "1" or solver_builds.prefix() is None,
    reason="builds CalculiX (minutes): set PUZZLE_BUILD_TESTS=1, with calculix-pardiso installed",
)


def test_calculix_builds_with_pardiso_and_solves():
    lines = []
    done = solver_builds.build(say=lines.append)
    assert done.state == "built", (done.detail, lines[-5:])
    assert any(line.startswith("compiled") for line in lines)
    log = solver_builds.check(solver_builds.program(solver_builds.prefix()))
    assert "pardiso" in log.lower()
