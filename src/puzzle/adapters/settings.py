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
    maximum: float | None = None  # the largest value allowed, where there is one

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
        if self.maximum is not None and number > self.maximum:
            raise InputError(f"{self.label} is at most {self.maximum:g}", code="invalid")
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
    # Load stepping of nonlinear static steps (contact, plasticity, large deflection).
    # None: the adapter's own choice (CalculiX and Code_Aster: the full load at once for
    # contact alone, ten increments where the loading path matters).
    SolverSetting("increments", "Load increments", "", "integer", None,
                  steps=frozenset({"stationary"}),
                  description="The load goes in over this many increments at most (the "
                  "largest increment is 1/n of the load). More help a model that will not "
                  "converge; each increment's results are kept."),
    SolverSetting("initial_increment", "First increment", "", "number", None, maximum=1.0,
                  steps=frozenset({"stationary"}),
                  description="The first increment as a fraction of the load (0.1 starts "
                  "with a tenth); smaller helps contact settle."),
    SolverSetting("min_increment", "Smallest increment", "", "number", None, maximum=1.0,
                  steps=frozenset({"stationary"}),
                  description="How far an increment may be cut back (a fraction of the "
                  "load) before the solve stops."),
    SolverSetting("fixed_increments", "Fixed increments", "", "bool", False,
                  steps=frozenset({"stationary"}),
                  description="Every increment the same size, never cut back or grown: a "
                  "failed increment ends the solve."),
]  # fmt: skip
STEPPING = ("increments", "initial_increment", "min_increment", "fixed_increments")
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


def edit_ops(
    study, shared_values: dict | None = None, own: dict | None = None, expert: dict | None = None
) -> list:
    """The edits that set a study's settings (ADR 0023) as one Edit: `shared_values` on
    every solve step it applies to, `own` = {solver: {key: value}} merged into that
    solver's block, `expert` = {solver: {path: value}} replacing its pass-through. A value
    of None removes a setting (back to the solver's default). Values are checked here,
    so a bad one is refused before anything changes."""
    from puzzle.adapters import available_adapters
    from puzzle.commands.model import EditOp

    adapters = {name: cls() for name, cls in available_adapters().items()}
    ops = []
    for key, value in (shared_values or {}).items():
        if key not in SHARED_KEYS:
            raise InputError(f"unknown shared setting '{key}'", code="invalid",
                             details={"known": sorted(SHARED_KEYS)})  # fmt: skip
        setting = SHARED_KEYS[key]
        chosen = None if value is None else setting.check(value)
        for i, step in enumerate(study.steps):
            if step not in study.solve_steps():
                continue
            if setting.steps and step.type not in setting.steps:
                continue
            if chosen is None:
                if key in step.settings:
                    ops.append(EditOp(op="remove", path=f"steps.{i}.settings.{key}"))
            else:
                ops.append(EditOp(op="set", path=f"steps.{i}.settings.{key}", value=chosen))
    blocks = {s: dict(v) for s, v in study.solver_settings.items()}
    for solver, values in (own or {}).items():
        if solver not in adapters:
            raise InputError(f"no solver '{solver}'", code="invalid",
                             details={"known": sorted(adapters)})  # fmt: skip
        schema = {s.key: s for s in adapters[solver].settings_schema()}
        block = blocks.setdefault(solver, {})
        for key, value in values.items():
            if key not in schema:
                raise InputError(f"{solver} has no setting '{key}'", code="invalid",
                                 details={"known": sorted(schema)})  # fmt: skip
            if value is None:
                block.pop(key, None)
            else:
                block[key] = schema[key].check(value)
    if own:
        ops.append(EditOp(op="set", path="solver_settings",
                          value={k: v for k, v in blocks.items() if v}))  # fmt: skip
    if expert:
        passes = {s: dict(v) for s, v in study.expert_settings.items()}
        for solver, entries in expert.items():
            if solver not in adapters:
                raise InputError(f"no solver '{solver}'", code="invalid")
            problems = adapters[solver].check_expert(entries or {})
            if problems:
                raise InputError(problems[0], code="invalid", details={"problems": problems})
            passes[solver] = dict(entries or {})
        ops.append(EditOp(op="set", path="expert_settings",
                          value={k: v for k, v in passes.items() if v}))  # fmt: skip
    return ops
