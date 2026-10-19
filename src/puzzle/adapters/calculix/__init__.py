"""CalculiX adapter: Abaqus-style .inp decks, .frd results. MVP solver (ADR 0010)."""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import InputError, prestress_step_count, write_inp
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results

JOB = "job"


class CalculixAdapter(SolverAdapter):
    name = "calculix"
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES | THERMAL_FEATURES,
        # Nonconformal bonded maps to *TIE; the others to *CONTACT PAIR (+ *FRICTION).
        contacts=frozenset(
            {"bonded_conformal", "bonded_nonconformal", "frictionless", "frictional"}
        ),
        couplings=frozenset({"thermal_expansion"}),
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        path = workdir / f"{JOB}.inp"
        path.write_text(write_inp(project, study, mesh, inputs))
        # Replayed prestress steps come first in the results; the reader skips them.
        path.with_suffix(".prestress").write_text(str(prestress_step_count(project, study, inputs)))
        return path

    def command(self, input_file: Path) -> list[str]:
        return ["ccx", "-i", input_file.with_suffix("").name]

    def read_results(self, input_file: Path) -> Results:
        frd = input_file.with_suffix(".frd")
        if not frd.exists():
            raise FileNotFoundError(f"CalculiX wrote no results ({frd.name}); see the solver log")
        results = read_frd(frd)
        marker = input_file.with_suffix(".prestress")
        skip = int(marker.read_text()) if marker.exists() else 0
        if skip:
            results.steps = [s for s in results.steps if s.index > skip]
            for s in results.steps:
                s.index -= skip
        return results


__all__ = ["CalculixAdapter", "InputError"]
