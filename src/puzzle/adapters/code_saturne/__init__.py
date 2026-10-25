"""Code_Saturne adapter: finite-volume CFD, case directory with setup.xml, MED meshes.

Code_Saturne is Puzzle's first CFD solver: incompressible and weakly compressible
flow, turbulence (settings["turbulence_model"], e.g. k-epsilon, k-omega SST, LES),
and heat transfer as a thermal scalar in the fluid. Results are stored per cell
(ADR 0008). Fluid-structure coupling with Code_Aster is a later addition.

CFD meshes come from the meshing layer's CFD path (ADR 0005): Gmsh with boundary
layers, exported as MED.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    FLUID_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.code_saturne.case import command as case_command
from puzzle.adapters.code_saturne.case import read_results as read_case_results
from puzzle.adapters.code_saturne.case import write_case
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class CodeSaturneAdapter(SolverAdapter):
    name = "code_saturne"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "fluid_flow": frozenset({"stationary", "transient"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=FLUID_FEATURES | THERMAL_FEATURES,
        couplings=frozenset({"non_isothermal_flow"}),
        # ADR 0023: its linear solvers are iterative; the steady algorithm runs its own
        # iteration count (the step's iterations), keeping only the converged state.
        shared_settings=frozenset({"linear_solver:iterative"}),
    )

    def settings_schema(self) -> list:
        """Code_Saturne's own settings (ADR 0023)."""
        from puzzle.adapters.code_saturne.case import SCHEMES, SOLVERS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting("pressure_solver", "Pressure solver", "solver_choice (pressure)",
                          "choice", "automatic", SOLVERS),
            SolverSetting("velocity_solver", "Velocity solver", "solver_choice (velocity)",
                          "choice", "automatic", SOLVERS),
            SolverSetting("velocity_scheme", "Velocity convection scheme",
                          "order_scheme (velocity)", "choice", "automatic", SCHEMES, power=True,
                          description="How momentum is carried: centred is accurate but can "
                          "oscillate at high Péclet numbers; SOLU is the robust second order."),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "setup.xml paths, one per line: path = value sets an element's text, "
            "path@attribute = value an attribute; [@name='...'] predicates pick a variable, "
            "e.g. numerical_parameters/gradient_reconstruction@choice = green_iter"
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.code_saturne.case import XML_PATH

        return [
            f"{self.name}: '{k}' is not a setup.xml path (element/element@attribute)"
            for k, v in entries.items()
            if not XML_PATH.match(k) or k.startswith("/") or "\n" in str(v)
        ]

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "Code_Saturne")
        return write_case(project, study, mesh, workdir)

    def command(self, input_file: Path) -> list[str]:
        return case_command()

    def read_results(self, input_file: Path) -> Results:
        return read_case_results(input_file.parent)
