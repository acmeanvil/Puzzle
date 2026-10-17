"""FEniCSx adapter: generates a dolfinx Python script per study.

FEniCSx has two roles in Puzzle:

- Custom PDEs: `custom_pde` physics carry a user-written UFL weak form, which the
  generated script solves directly. This is Puzzle's equation-based modelling.
- A second electromagnetics solver beside Elmer, so EM studies can have a solver
  shoot-out (ADR 0011). Magnetic fields use Nedelec (N1curl) edge elements;
  frequency-domain and wave problems need a complex-valued PETSc build.

Built-in physics come from templates the adapter ships; each one claimed here
needs a passing benchmark before release. Nonconformal (mortar) interfaces are not
part of dolfinx, so only conformally meshed bonded interfaces are supported.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    ELECTROMAGNETIC_FEATURES,
    PDE_FEATURES,
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.model.schema import Project, Study


class FenicsxAdapter(SolverAdapter):
    name = "fenicsx"
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
        features=ELECTROMAGNETIC_FEATURES | THERMAL_FEATURES | STRUCTURAL_FEATURES | PDE_FEATURES,
        contacts=frozenset({"bonded_conformal"}),
        couplings=frozenset({"joule_heating", "thermal_expansion"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
    )

    def write_input(self, project: Project, study: Study, mesh: Path, workdir: Path) -> Path:
        raise NotImplementedError("Phase 3: render study.py from the physics templates or UFL form")

    def command(self, input_file: Path) -> list[str]:
        return ["python3", input_file.name]

    def import_results(self, workdir: Path, store: Path) -> None:
        raise NotImplementedError(
            "Phase 3: read the script's VTX/XDMF output into the results store"
        )
