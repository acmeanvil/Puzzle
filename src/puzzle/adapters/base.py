"""The contract every solver adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Project, Study
from puzzle.results.model import Results

# Feature types, grouped by physics, for adapters to combine.
STRUCTURAL_FEATURES = frozenset(
    {"fixed", "displacement", "force", "pressure", "gravity", "acceleration", "rotation"}
)  # body loads (ADR 0037): benchmarked on CalculiX, Code_Aster, Elmer and FEniCSx
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
# Remote loads (ADR 0037): the deformable force and the rigid displacement are tractions
# and prescribed fields every solver takes; a rigid force needs constraint equations.
REMOTE_FEATURES = frozenset({"remote_force", "remote_displacement"})
RIGID_REMOTE_FEATURES = frozenset({"remote_force_rigid"})
SUPPORT_FEATURES = frozenset({"elastic_support"})  # ADR 0037: stiffness per area
LIFTING_FEATURES = frozenset({"compression_only_support"})  # ADR 0037: nonlinear
PRETENSION_FEATURES = frozenset({"bolt_pretension", "bolt_adjustment"})  # ADR 0037


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
    material_models: frozenset[str] = frozenset()
    """Material.models this adapter writes (plasticity, ...); others are refused."""
    material_keywords: bool = False
    """Whether the adapter places a material's own keywords for it (solver_keywords)."""
    amplitude_features: frozenset[str] = frozenset()
    """Feature types whose values may follow an Amplitude in transient steps (ADR 0037)."""
    connectors: frozenset[str] = frozenset()
    """Connector kinds written (ADR 0027: "beam_fastener:rigid", ":distributing"); the
    rest are refused."""
    feature_steps: bool = False
    """Whether a one-deck adapter writes features in some solve steps only (Feature.steps,
    ADR 0037); adapters with max_solve_steps get them per step from run_study."""
    axes_features: frozenset[str] = frozenset()
    """Features given in local axes (ADR 0037), as "type:axes type", with ":partial" when
    only some components are prescribed (e.g. "displacement:cylindrical:partial")."""


def refuse_inputs(inputs: Sequence, solver: str, accepted: set[tuple[str, str]] = frozenset()):
    """Raise for transferred fields this adapter doesn't apply: (role, field) pairs."""
    from puzzle.adapters.common import InputError

    for i in inputs:
        if (i.role, i.field.name) not in accepted:
            raise InputError(
                f"the {solver} adapter does not take a transferred {i.field.name} "
                f"({i.role}, from '{i.source}') yet",
                code="unsupported",
            )


class SolverAdapter(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[Capabilities]
    # Solve steps one input file can hold; run_study splits longer studies (None: any).
    max_solve_steps: ClassVar[int | None] = None

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
            for f in physics.features:  # local axes (ADR 0037): refused, never ignored
                if f.axes is None:
                    continue
                axes = project.objects.get(f.axes)
                if axes is None:
                    continue  # a missing reference: Project.problems says so
                key = f"{f.type}:{axes.type}"
                if f.type == "displacement" and not all(a in f.values for a in "xyz"):
                    key += ":partial"
                if key not in caps.axes_features:
                    words = key.replace(":partial", " (some components)").replace(":", " in ")
                    problems.append(
                        f"{self.name} does not take {words} axes yet (on {f.selection})"
                    )
            for f in physics.features:  # some steps only (ADR 0037): refused, never ignored
                # Split studies (one job per step) see only each step's features.
                if f.steps and not (caps.feature_steps or self.max_solve_steps is not None):
                    problems.append(
                        f"{self.name} applies every feature in every step; it can't take "
                        f"{f.type} on {f.selection} in steps {f.steps} only yet"
                    )
            # A pretension is locked after its step: one job per step can't carry that.
            loaded = [
                s for s in study.solve_steps() if s.solves(physics.type) and s.type != "modal"
            ]
            if self.max_solve_steps is not None and len(loaded) > 1:
                problems += [
                    f"{self.name} runs each step as its own analysis, so the pretension on "
                    f"{f.selection} can't be locked into a later step; use one step, or "
                    "CalculiX"
                    for f in physics.features
                    if f.type in PRETENSION_FEATURES
                ]
                problems += [
                    f"{self.name} runs each step as its own analysis, so fastener "
                    f"{c.name}'s pretension can't be locked into a later step; use one "
                    "step, or CalculiX"
                    for c in component.connectors
                    if c.pretension is not None and physics.type == "solid_mechanics"
                ]
            for f in physics.features:  # amplitudes (ADR 0037): refused, never ignored
                if f.amplitude is None:
                    continue
                if f.type not in caps.amplitude_features:
                    problems.append(
                        f"{self.name} does not vary {f.type} with an amplitude yet "
                        f"(on {f.selection})"
                    )
                steady = [
                    s.type
                    for s in study.solve_steps()
                    if s.solves(physics.type) and s.type != "transient"
                ]
                if steady:
                    problems.append(
                        f"an amplitude on {f.type} ({f.selection}) applies in transient steps; "
                        f"this study also has {', '.join(sorted(set(steady)))} steps"
                    )
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
        problems += self._material_problems(project, component, study)
        for c in component.connectors:  # ADR 0027: refused, never ignored
            if f"{c.kind}:{c.ends}" not in caps.connectors:
                problems.append(
                    f"{self.name} does not write {c.ends} beam fasteners yet ({c.name})"
                )
        return problems

    def _material_problems(
        self, project: Project, component: Component, study: Study | None = None
    ) -> list[str]:
        """Material models and keywords this adapter can't write: refused, not dropped.
        A model counts only when the study solves its physics."""
        from puzzle.materials.registry import MODEL_PHYSICS, MODELS
        from puzzle.model.schema import Material

        solved = {p.type for p in component.physics}
        if study is not None:
            solved = {p for p in solved if any(s.solves(p) for s in study.solve_steps())}
        caps, out = self.capabilities, []
        for assignment in component.materials:
            m = project.objects.get(assignment.material)
            if not isinstance(m, Material):
                continue
            for model in m.models.used():
                if MODEL_PHYSICS.get(model) in solved and model not in caps.material_models:
                    out.append(
                        f"{self.name} does not use {MODELS.get(model, model).lower()} yet "
                        f"(material '{m.name}')"
                    )
            if m.solver_keywords.get(self.name) and not caps.material_keywords:
                out.append(f"{self.name} cannot place material keywords yet (material '{m.name}')")
        return out

    @abstractmethod
    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs: Sequence = ()
    ) -> Path:
        """Translate the study and its mesh into native input in `workdir`; return the main file.

        `inputs` are fields transferred from other studies (puzzle.transfer.Input), already
        on `mesh`'s nodes. An adapter that can't use one must refuse it (`refuse_inputs`).
        """

    @abstractmethod
    def command(self, input_file: Path) -> list[str]:
        """The command line that runs the solver on `input_file` (inside its container)."""

    @abstractmethod
    def read_results(self, input_file: Path) -> Results:
        """Read the solver's output next to `input_file` into solver-neutral results."""
