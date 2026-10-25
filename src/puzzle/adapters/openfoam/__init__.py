"""OpenFOAM adapter (ESI-OpenCFD, openfoam.com): finite-volume CFD with simpleFoam.

Puzzle's second CFD solver (ADR 0016), so CFD studies get a shoot-out partner for
Code_Saturne (ADR 0011). The same features and settings run on both: steady
incompressible flow, laminar or RANS (k-epsilon, k-omega SST), with temperature as a
passive scalar. Meshes go in as Gmsh 2.2 through gmshToFoam; results are per cell.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    FLUID_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.openfoam.case import command as case_command
from puzzle.adapters.openfoam.case import read_results as read_case_results
from puzzle.adapters.openfoam.case import write_case
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class OpenFoamAdapter(SolverAdapter):
    name = "openfoam"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "fluid_flow": frozenset({"stationary"}),
            "heat_transfer": frozenset({"stationary"}),
        },
        features=(FLUID_FEATURES - {"mass_flow_inlet"}) | {"temperature", "heat_flux"},
        couplings=frozenset({"non_isothermal_flow"}),
        # ADR 0023: iterative solvers only; the tolerance is when SIMPLE has converged.
        shared_settings=frozenset({"linear_solver:iterative", "tolerance"}),
    )

    def settings_schema(self) -> list:
        """OpenFOAM's own settings (ADR 0023)."""
        from puzzle.adapters.openfoam.case import P_SOLVERS, U_SOLVERS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting("p_solver", "Pressure solver", "fvSolution solvers/p", "choice",
                          "GAMG", P_SOLVERS),
            SolverSetting("U_solver", "Velocity solver", "fvSolution solvers/U", "choice",
                          "smoothSolver", U_SOLVERS),
            SolverSetting("relaxation_U", "Velocity relaxation", "relaxationFactors U",
                          "number", 0.9, power=True,
                          description="SIMPLE's under-relaxation of the velocity: lower is "
                          "steadier, slower."),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "Dictionary entries, one per line, set with foamDictionary before simpleFoam: "
            "system/fvSolution/solvers/p/tolerance = 1e-8, "
            "system/controlDict/writePrecision = 10"
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.openfoam.case import ENTRY

        return [
            f"{self.name}: '{k}' is not a dictionary entry (system/<dict>/<entry path>)"
            for k, v in entries.items()
            if not ENTRY.match(k) or "\n" in str(v) or not str(v).strip()
        ]

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "OpenFOAM")
        return write_case(project, study, mesh, workdir)

    def command(self, input_file: Path) -> list[str]:
        return case_command()

    def read_results(self, input_file: Path) -> Results:
        return read_case_results(input_file.parent)
