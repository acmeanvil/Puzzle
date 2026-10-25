"""FEniCSx adapter: a fixed dolfinx script (study.py) driven by a JSON case description.

FEniCSx has two roles in Puzzle:

- Custom PDEs: `custom_pde` physics carry a user-written UFL weak form, which the
  generated script solves directly. This is Puzzle's equation-based modelling.
- A second electromagnetics solver beside Elmer, so EM studies can have a solver
  shoot-out (ADR 0011). Magnetic fields use Nedelec (N1curl) edge elements;
  frequency-domain and wave problems need a complex-valued PETSc build.

Built-in physics live in study.py, which runs inside the FEniCSx environment;
the adapter writes mesh.msh, case.json and a copy of the script, and reads back
results.npz. Implemented so far: linear elasticity (static, modal) and steady heat.
Nonconformal (mortar) interfaces are not part of dolfinx, so only conformally meshed
bonded interfaces are supported.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    ELECTROMAGNETIC_FEATURES,
    PDE_FEATURES,
    REMOTE_FEATURES,
    STRUCTURAL_FEATURES,
    SUPPORT_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.adapters.common import InputError
from puzzle.adapters.fenicsx.case import fenicsx_python, read_results, write_case
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class FenicsxAdapter(SolverAdapter):
    name = "fenicsx"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "custom_pde": frozenset({"stationary", "transient", "frequency_domain", "modal"}),
            "electrostatics": frozenset({"stationary"}),
            "electric_currents": frozenset({"stationary"}),
            "magnetic_fields": frozenset({"stationary", "transient", "frequency_domain"}),
            "electromagnetic_waves": frozenset({"frequency_domain"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
            "solid_mechanics": frozenset({"stationary", "modal"}),
        },
        features=ELECTROMAGNETIC_FEATURES
        | THERMAL_FEATURES
        | STRUCTURAL_FEATURES
        | PDE_FEATURES
        | REMOTE_FEATURES
        | SUPPORT_FEATURES,
        shared_settings=frozenset(  # ADR 0023
            {"linear_solver", "tolerance", "max_iterations", "output_every"}
        ),
        amplitude_features=frozenset({"heat_flux"}),  # ADR 0037, benchmarked
        axes_features=frozenset(
            {"displacement:cartesian", "displacement:cylindrical", "force:cartesian"}
        ),  # ADR 0037: local axes
        contacts=frozenset({"bonded_conformal"}),
        couplings=frozenset({"joule_heating", "thermal_expansion"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
        time_schemes=frozenset({"backward_euler", "crank_nicolson", "bdf2", "theta"}),
    )

    def settings_schema(self) -> list:
        """FEniCSx's own settings (ADR 0023); the shared ones are on each step."""
        from puzzle.adapters.fenicsx.case import METHODS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting(
                "linear_method", "Linear solver", "PETSc ksp_type/pc_type", "choice", "lu",
                METHODS, description="Each field's own solve; iterative ones need PETSc (not "
                "in the Windows build).",
            ),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "PETSc options, one per line: ksp_type = gmres, pc_type = hypre, ksp_rtol = 1e-10; "
            "they apply over the linear solver's to each field's own solve"
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.fenicsx.case import PETSC_KEY

        return [
            f"{self.name}: '{k}' is not a PETSc option name (ksp_type, pc_type, ...)"
            for k, v in entries.items()
            if not PETSC_KEY.match(k) or not str(v).strip() or " " in str(v).strip()
        ]

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        return write_case(project, study, mesh, workdir, inputs)

    def command(self, input_file: Path) -> list[str]:
        python = fenicsx_python()
        if python is None:
            raise InputError(
                "no Python with dolfinx found; run `pixi install -e fenicsx` and put its bin "
                "directory on PUZZLE_SOLVER_PATH, or set PUZZLE_FENICSX_PYTHON"
            )
        return [str(python), input_file.name]

    def read_results(self, input_file: Path) -> Results:
        return read_results(input_file.parent)
