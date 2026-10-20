"""Parametrize integration tests over the solvers that are available here."""

import pytest

from puzzle.jobs import runtime_for

EXECUTABLES = {
    "calculix": "ccx",
    "elmer": "ElmerSolver",
    "code_aster": "run_aster",
    "code_saturne": "code_saturne",
    "openfoam": "simpleFoam",
    "fenicsx": None,
}


def available(solver: str) -> bool:
    if solver == "fenicsx":
        from puzzle.adapters.fenicsx.case import fenicsx_python

        return fenicsx_python() is not None
    return runtime_for(solver).available(EXECUTABLES[solver])


def over(*solvers: str):
    """`@over("elmer", "fenicsx")`: run the test once per solver, skipping missing ones."""
    return pytest.mark.parametrize(
        "solver",
        [
            pytest.param(s, marks=pytest.mark.skipif(not available(s), reason=f"{s} not available"))
            for s in solvers
        ],
    )
