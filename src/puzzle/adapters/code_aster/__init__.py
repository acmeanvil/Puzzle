"""Code_Aster adapter: .comm command file, .export run file and a MED mesh; results as tables.

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
    REMOTE_FEATURES,
    RIGID_REMOTE_FEATURES,
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.code_aster.comm import JOB, write_case
from puzzle.adapters.code_aster.tables import read_results as read_tables
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class CodeAsterAdapter(SolverAdapter):
    name = "code_aster"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal", "frequency_domain"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES | THERMAL_FEATURES | REMOTE_FEATURES | RIGID_REMOTE_FEATURES,
        amplitude_features=frozenset({"heat_flux"}),  # ADR 0037, benchmarked
        axes_features=frozenset(
            {"displacement:cartesian", "displacement:cylindrical", "force:cartesian"}
        ),  # ADR 0037: local axes
        contacts=frozenset(
            {"bonded_conformal", "bonded_nonconformal", "frictionless", "frictional"}
        ),
        couplings=frozenset({"thermal_expansion"}),
        # VMIS_ISOT_TRAC in STAT_NON_LINE, p and EPSP out (tests/integration/test_plasticity.py).
        material_models=frozenset({"plasticity"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
        time_schemes=frozenset({"backward_euler", "crank_nicolson", "theta"}),
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "Code_Aster", {("load", "temperature")})
        return write_case(project, study, mesh, workdir, inputs)

    def command(self, input_file: Path) -> list[str]:
        return ["run_aster", input_file.name]

    def read_results(self, input_file: Path) -> Results:
        workdir = input_file.parent
        if not list(workdir.glob("*.csv")):
            raise FileNotFoundError(
                f"Code_Aster wrote no result tables; see {JOB}.mess and the solver log"
            )
        return read_tables(workdir)
