"""Solver settings (ADR 0023): typed, named, with the solver's own word beside them.

Two kinds, both on the study:

- **Shared settings** (`SHARED`), which every solver translates into its own terms, as
  `time_scheme` already is: linear solver family, preconditioner, relative tolerance,
  nonlinear iteration limit, output frequency. They sit on each solve step's settings,
  so a shoot-out compares like with like.
- **Each solver's own** (`SolverAdapter.settings_schema()`), in `Study.solver_settings
  [solver]`; a run's key covers only its own solver's block, so changing CalculiX's
  settings leaves a shoot-out's Code_Aster runs current. Settings marked `power` show
  only at the power-user level (Settings > Solvers).

What no setting covers goes through each solver's **expert pass-through**
(`Study.expert_settings[solver]`: a path to a value), which the adapter places or
refuses (`InputError`), never drops. Every default is what the adapter wrote before
there were settings, so no result moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from puzzle.adapters.common import InputError


@dataclass(frozen=True)
class SolverSetting:
    key: str
    label: str  # English, as the GUI shows it
    native: str  # the solver's own name for it (SOLVER=, RESI_GLOB_RELA)
    kind: str  # "choice", "number", "integer" or "bool"
    default: Any
    choices: tuple = ()  # (label, value) pairs for a choice
    unit: str = ""
    description: str = ""
    steps: frozenset[str] = field(default_factory=frozenset)  # step types; empty: all
    power: bool = False  # shown at the power-user level only

    def check(self, value: Any) -> Any:
        """The value as this setting takes it, or InputError saying why not."""
        if self.kind == "choice":
            allowed = [v for _, v in self.choices]
            if value not in allowed:
                raise InputError(
                    f"{self.label} is one of {', '.join(map(str, allowed))}, not {value!r}",
                    code="invalid",
                )
            return value
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise InputError(f"{self.label} is yes or no, not {value!r}", code="invalid")
            return value
        try:
            number = int(value) if self.kind == "integer" else float(value)
        except (TypeError, ValueError):
            raise InputError(f"{self.label} is a number, not {value!r}", code="invalid") from None
        if number <= 0:
            raise InputError(f"{self.label} must be above zero", code="invalid")
        return number


SHARED = [
    SolverSetting("linear_solver", "Linear solver", "", "choice", "direct",
                  (("Direct (robust; memory grows fast with size)", "direct"),
                   ("Iterative (large models)", "iterative")),
                  description="How each linear system is solved; each solver uses its own "
                  "direct or iterative method."),
    SolverSetting("tolerance", "Relative tolerance", "", "number", 1e-6,
                  description="When a nonlinear solve has converged: the residual relative "
                  "to the loads."),
    SolverSetting("max_iterations", "Iteration limit", "", "integer", 50,
                  description="Nonlinear iterations allowed per increment."),
    SolverSetting("output_every", "Output every", "", "integer", 1, unit="increments",
                  description="Keep results every n-th increment or time step."),
]  # fmt: skip
SHARED_KEYS = {s.key: s for s in SHARED}


def resolved(schema: list[SolverSetting], given: dict) -> dict[str, Any]:
    """Every setting's value: the study's where given, else the default. Unknown keys
    are refused (a typo would otherwise be silently ignored)."""
    known = {s.key: s for s in schema}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise InputError(
            f"unknown solver settings: {', '.join(unknown)}",
            code="invalid",
            details={"known": sorted(known)},
        )
    return {k: s.check(given[k]) if k in given else s.default for k, s in known.items()}


def shared(step) -> dict[str, Any]:
    """A step's shared settings, defaults filled in (ADR 0023)."""
    return {k: s.check(step.settings[k]) if k in step.settings else s.default
            for k, s in SHARED_KEYS.items()}  # fmt: skip
