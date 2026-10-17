"""CalculiX adapter: Abaqus-style .inp decks, .frd results. MVP solver (ADR 0010)."""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.model.schema import Project, Study


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

    def write_input(self, project: Project, study: Study, mesh: Path, workdir: Path) -> Path:
        raise NotImplementedError("Phase 1: write *NODE, *ELEMENT, *MATERIAL and *STEP blocks")

    def command(self, input_file: Path) -> list[str]:
        return ["ccx", "-i", input_file.with_suffix("").name]

    def import_results(self, workdir: Path, store: Path) -> None:
        raise NotImplementedError("Phase 1: parse .frd into the results store")
