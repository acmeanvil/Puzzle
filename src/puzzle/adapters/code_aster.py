"""Code_Aster adapter: .comm command files plus an .export run file, MED meshes and results.

Code_Aster is Puzzle's nonlinear structural solver: contact with friction, plasticity
and large deformation (STAT_NON_LINE, DYNA_NON_LINE), alongside linear statics,
dynamics, modal (CALC_MODES) and harmonic response, and linear or nonlinear heat
transfer. Material and geometric nonlinearity settings travel in per-solver
extension blocks until the neutral schema covers them.

Contact maps to its contact definitions (frictionless or Coulomb friction);
nonconformal bonded interfaces map to mesh tying (LIAISON_MAIL).
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class CodeAsterAdapter(SolverAdapter):
    name = "code_aster"
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal", "frequency_domain"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES | THERMAL_FEATURES,
        contacts=frozenset(
            {"bonded_conformal", "bonded_nonconformal", "frictionless", "frictional"}
        ),
        couplings=frozenset({"thermal_expansion"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
    )

    def write_input(self, project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
        raise NotImplementedError("Phase 3: write study.comm and study.export around the MED mesh")

    def command(self, input_file: Path) -> list[str]:
        return ["run_aster", input_file.with_suffix(".export").name]

    def read_results(self, input_file: Path) -> Results:
        raise NotImplementedError("Phase 3: read the .rmed result file into the results store")
