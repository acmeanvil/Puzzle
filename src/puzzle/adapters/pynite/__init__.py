"""PyNite (MIT, a 3D frame solver in Python) for frames of line members (ADR 0079).

Each line element is a PyNite member (12 dofs, Euler-Bernoulli), with its section's
A, Iy (about the first axis), Iz and J, oriented so its local y is the section's first
axis (as Code_Aster's POU_D_T), end releases and rigid offsets from the component's
member ends. It runs natively in its own environment (the pixi `pynite` env, or
PUZZLE_PYNITE_PYTHON); `frame.py` there reads case.json and writes results.json.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import Capabilities, SolverAdapter
from puzzle.adapters.common import InputError
from puzzle.adapters.pynite.case import FEATURES, pynite_python, read_results, write_case
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class PyNiteAdapter(SolverAdapter):
    name = "pynite"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={"solid_mechanics": frozenset({"stationary"})},
        features=FEATURES,
        beams=True,
        member_ends=True,
        frame_analysis=True,  # ADR 0079 step 2: combinations, P-Delta, Direct Analysis
        axes_features=frozenset(
            {"force:cartesian", "displacement:cartesian", "gravity:cartesian"}
        ),  # fmt: skip
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        return write_case(project, study, mesh, workdir, inputs)

    def command(self, input_file: Path) -> list[str]:
        python = pynite_python()
        if python is None:
            raise InputError(
                "no Python with PyNite found: run `pixi install -e pynite`, or set "
                "PUZZLE_PYNITE_PYTHON to one (`pip install PyNiteFEA`)"
            )
        return [str(python), input_file.name]

    def read_results(self, input_file: Path) -> Results:
        return read_results(input_file.parent)


def available() -> bool:
    """Whether a PyNite environment is here."""
    python = pynite_python()
    return python is not None and python.exists()
