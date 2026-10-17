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
from puzzle.adapters.calculix.inp import InputError, write_inp
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

    def write_input(self, project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
        path = workdir / f"{JOB}.inp"
        path.write_text(write_inp(project, study, mesh))
        return path

    def command(self, input_file: Path) -> list[str]:
        return ["ccx", "-i", input_file.with_suffix("").name]

    def read_results(self, input_file: Path) -> Results:
        frd = input_file.with_suffix(".frd")
        if not frd.exists():
            raise FileNotFoundError(f"CalculiX wrote no results ({frd.name}); see the solver log")
        return read_frd(frd)


__all__ = ["CalculixAdapter", "InputError"]
