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

from puzzle.adapters.base import FLUID_FEATURES, THERMAL_FEATURES, Capabilities, SolverAdapter
from puzzle.model.schema import Project, Study


class CodeSaturneAdapter(SolverAdapter):
    name = "code_saturne"
    capabilities = Capabilities(
        physics={
            "fluid_flow": frozenset({"stationary", "transient"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=FLUID_FEATURES | THERMAL_FEATURES,
        couplings=frozenset({"non_isothermal_flow"}),
    )

    def write_input(self, project: Project, study: Study, mesh: Path, workdir: Path) -> Path:
        raise NotImplementedError("Phase 4: create the case directory and DATA/setup.xml")

    def command(self, input_file: Path) -> list[str]:
        return ["code_saturne", "run", "--param", input_file.name]

    def import_results(self, workdir: Path, store: Path) -> None:
        raise NotImplementedError("Phase 4: read the postprocessing MED or EnSight output")
