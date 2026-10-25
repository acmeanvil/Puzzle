"""Solver-neutral results, as adapters return them (before storage, ADR 0008)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Field:
    """One result quantity at one step: values per node or per element, SI units."""

    name: str  # "displacement", "temperature", "stress", "heat_flux", ...
    location: str  # "node" or "element"
    components: list[str]  # ["x", "y", "z"], [""], ["xx", "yy", "zz", "xy", "yz", "zx"]
    ids: np.ndarray  # (n,) node or element ids
    values: np.ndarray  # (n, len(components))

    def at(self, ids: np.ndarray) -> np.ndarray:
        """Values for the given ids, in their order."""
        order = np.argsort(self.ids)
        return self.values[order[np.searchsorted(self.ids, ids, sorter=order)]]


@dataclass
class StepResult:
    index: int  # 1-based solve step
    kind: str  # "stationary", "transient", "modal", "frequency_domain"
    value: float  # time (s), or frequency (Hz) for a mode
    mode: int | None = None
    fields: dict[str, Field] = field(default_factory=dict)

    def label(self) -> str:
        """What the step is, for titles and lists: a buckling mode by its load factor
        (ADR 0028), a vibration mode by its frequency, else the time."""
        if self.kind == "buckling" and self.mode is not None:
            reversed_ = " (load reversed)" if self.value < 0 else ""
            return f"Buckling mode {self.mode}: load factor {self.value:.4g}{reversed_}"
        if self.mode is not None:
            return f"mode {self.mode}: {self.value:.4g} Hz"
        return f"t = {self.value:g}"


@dataclass
class Results:
    solver: str
    steps: list[StepResult] = field(default_factory=list)

    def last(self, name: str) -> Field:
        """The named field at the last step that has it."""
        for step in reversed(self.steps):
            if name in step.fields:
                return step.fields[name]
        raise KeyError(f"no '{name}' field in the results")

    def modes(self) -> list[StepResult]:
        return [s for s in self.steps if s.mode is not None]
