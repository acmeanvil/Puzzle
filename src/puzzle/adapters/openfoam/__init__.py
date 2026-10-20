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
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "OpenFOAM")
        return write_case(project, study, mesh, workdir)

    def command(self, input_file: Path) -> list[str]:
        return case_command()

    def read_results(self, input_file: Path) -> Results:
        return read_case_results(input_file.parent)
