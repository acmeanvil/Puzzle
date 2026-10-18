"""The contract every solver adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Project, Study
from puzzle.results.model import Results

# Feature types, grouped by physics, for adapters to combine.
STRUCTURAL_FEATURES = frozenset({"fixed", "displacement", "force", "pressure"})
THERMAL_FEATURES = frozenset({"temperature", "heat_flux", "convection"})
ELECTROMAGNETIC_FEATURES = frozenset(
    {
        "electric_potential",
        "ground",
        "surface_charge",
        "current_density",
        "coil_current",
        "coil_start",
        "coil_end",
        "magnetic_insulation",
    }
)
FLUID_FEATURES = frozenset(
    {"velocity_inlet", "mass_flow_inlet", "pressure_outlet", "wall", "symmetry"}
)
PDE_FEATURES = frozenset({"dirichlet", "neumann"})


@dataclass(frozen=True)
class Capabilities:
    """What a solver supports. The GUI greys out everything else.

    `physics` maps each physics to the solve step types it supports, because
    support differs by pair (e.g. frequency-domain magnetics but not heat).
    Contact keys come from `Contact.capability`, e.g. "bonded_nonconformal".
    """

    physics: dict[str, frozenset[str]]
    features: frozenset[str]
    contacts: frozenset[str] = frozenset()
    couplings: frozenset[str] = frozenset()
    dimensions: frozenset[str] = field(default_factory=lambda: frozenset({"3d"}))
    time_schemes: frozenset[str] = frozenset({"backward_euler"})
    """Time schemes for transient heat transfer (see adapters.common.time_scheme)."""
    experimental: frozenset[str] = frozenset()
    """Contact or coupling keys that exist in the solver but aren't verified for production."""


class SolverAdapter(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def check(
        self, project: Project, study: Study, *, allow_experimental: bool = False
    ) -> list[str]:
        """Return everything in `study` this solver can't handle; empty means runnable.

        Sweep steps never reach the adapter: Puzzle expands them into one run per point.
        Experimental capabilities count as unsupported unless `allow_experimental`.
        """
        caps = self.capabilities
        allowed = caps.experimental if allow_experimental else frozenset()
        component = project.get(study.component, Component)
        problems: list[str] = []
        if component.dimension not in caps.dimensions:
            problems.append(f"{self.name} does not support {component.dimension} models")
        for physics in component.physics:
            steps = caps.physics.get(physics.type)
            if steps is None:
                problems.append(f"{self.name} does not support {physics.type} physics")
                continue
            problems += [
                f"{self.name} does not support {s.type} {physics.type}"
                for s in study.solve_steps()
                if s.solves(physics.type) and s.type not in steps
            ]
            problems += [
                f"{self.name} does not support {f.type} in {physics.type}"
                for f in physics.features
                if f.type not in caps.features
            ]
        for s in study.solve_steps():
            scheme = s.settings.get("time_scheme", "backward_euler")
            if s.type == "transient" and scheme not in caps.time_schemes:
                problems.append(f"{self.name} does not support the {scheme} time scheme")
        problems += [
            f"{self.name} does not support {c.capability} contact ('{c.name}')"
            for c in component.contacts
            if c.capability not in caps.contacts | allowed
        ]
        problems += [
            f"{self.name} does not support {c} coupling"
            for c in component.couplings
            if c not in caps.couplings | allowed
        ]
        return problems

    @abstractmethod
    def write_input(self, project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
        """Translate the study and its mesh into native input in `workdir`; return the main file."""

    @abstractmethod
    def command(self, input_file: Path) -> list[str]:
        """The command line that runs the solver on `input_file` (inside its container)."""

    @abstractmethod
    def read_results(self, input_file: Path) -> Results:
        """Read the solver's output next to `input_file` into solver-neutral results."""
