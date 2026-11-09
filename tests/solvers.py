"""Parametrize integration tests over the solvers that are available here."""

import os
import subprocess
from functools import cache

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
        from puzzle.adapters.fenicsx.case import fenicsx_available

        return fenicsx_available()
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


@cache
def fenicsx_has_petsc() -> bool:
    """Whether FEniCSx solves through PETSc here. Windows' dolfinx has none, so it
    solves with SciPy: no PETSc options or linear methods, and a direct solve of a
    large 3D magnetics problem runs out of memory (TEAM 7 in SuperLU)."""
    if os.environ.get("PUZZLE_FENICSX_BACKEND") == "scipy":
        return False
    from puzzle.adapters.fenicsx.case import fenicsx_python

    python = fenicsx_python()
    if python is None:
        return False
    probe = [str(python), "-c", "import petsc4py, dolfinx.fem.petsc"]
    return subprocess.run(probe, capture_output=True, timeout=300).returncode == 0


def needs_petsc(solver: str) -> None:
    """Skip a FEniCSx test that needs PETSc where FEniCSx has none."""
    if solver == "fenicsx" and not fenicsx_has_petsc():
        pytest.skip("this FEniCSx has no PETSc (the SciPy backend)")
