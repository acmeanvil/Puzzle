"""Design optimisation and design of experiments (ADR 0063).

An `Optimisation` runs its study once per point it chooses. A point sets the design
variables (parameters by name: a parameter set's value, or a Number or Slider operator)
on a copy of the project (`sweep.apply`, as a sweep's point does), and the study's run
is stored with the point's labels ("t": "4 mm") and cached by its run key. So a point
already run is never solved again, a stopped optimisation resumes, and every point's
run opens like any other. The objective and constraints are post-solve outputs (ADR
0062), read at each point's run.

Phase 1 is design of experiments: the points are chosen up front (`plan`: full
factorial, Latin hypercube or Sobol, seeded), so `points` can rebuild the table from
the stored runs at any time, without solving.
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.model.schema import DesignOutput, Optimisation, Project, Quantity, Study

METHODS = {
    "full_factorial": "Full factorial",
    "latin_hypercube": "Latin hypercube",
    "sobol": "Sobol sequence",
}


@dataclass
class Point:
    """One point of an optimisation: its variables (SI), its runs, and its outputs."""

    index: int
    values: dict[str, float]
    labels: dict[str, str]
    runs: dict[str, str] = field(default_factory=dict)  # solver -> run id
    objective: float | None = None
    constraints: list[float | None] = field(default_factory=list)
    feasible: bool = False
    reason: str = "not run yet"
    seconds: float = 0.0
    by_solver: dict[str, dict] = field(default_factory=dict)  # a shoot-out's outputs
    kinds: dict[str, str | None] = field(default_factory=dict)  # output -> unit kind

    @property
    def title(self) -> str:
        from puzzle.sweep import title

        return title(self.labels)

    @property
    def run(self) -> bool:
        return bool(self.runs)

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "values": self.values, "labels": self.labels,
                "runs": self.runs, "objective": self.objective,
                "constraints": self.constraints, "feasible": self.feasible,
                "reason": self.reason, "seconds": self.seconds, "kinds": self.kinds,
                **({"solvers": self.by_solver} if self.by_solver else {})}  # fmt: skip


# --- the variables --------------------------------------------------------------------


def _unit(var) -> str:
    """The unit a variable's values are labelled in: its bounds' (or choices')."""
    q = var.low if var.kind != "choice" else var.choices[0]
    return q.unit if q is not None else "dimensionless"


def quantity(var, si: float) -> Quantity:
    """A variable's SI value in its own unit (12 mm, not 0.012 m)."""
    from puzzle.model.units import DIMENSIONS, convert, kind_of

    unit = _unit(var)
    kind = kind_of(unit) if unit != "dimensionless" else None
    value = convert(si, DIMENSIONS[kind][1], unit) if kind else si
    return Quantity(value=float(f"{value:.12g}"), unit=unit)


def check(project: Project, opt: Optimisation) -> None:
    """Refuse what can't run: an unknown variable, a missing study or output."""
    study = project.objects.get(opt.study)
    if not isinstance(study, Study):
        raise PuzzleError(f"optimisation '{opt.name}': its study is gone", code="not_found")
    known = project.parameter_names()
    for var in opt.variables:
        if var.name not in known:
            raise PuzzleError(
                f"optimisation '{opt.name}': no parameter '{var.name}' (a parameter set's "
                "value, a Number or a Slider)", code="not_found",
                details={"parameters": sorted(known)})  # fmt: skip
    for out in ([opt.objective] if opt.objective else []) + list(opt.constraints):
        if out.source not in project.objects:
            raise PuzzleError(f"optimisation '{opt.name}': an output's source is gone",
                              code="not_found")  # fmt: skip
    if opt.objective is None and not opt.constraints:
        raise PuzzleError(f"optimisation '{opt.name}' has no objective or constraint to read",
                          code="inconsistent")  # fmt: skip


def plan(opt: Optimisation) -> list[dict[str, float]]:
    """The design of experiments' points (SI), in order; the same every time."""
    from scipy.stats import qmc

    names = [v.name for v in opt.variables]
    if opt.method == "full_factorial":
        levels = []
        for v in opt.variables:
            if v.kind == "choice":
                levels.append(v.values())
            else:
                row = np.linspace(v.low.to_si(), v.high.to_si(), opt.levels)
                if v.kind == "integer":
                    row = np.unique(np.round(row))
                levels.append([float(x) for x in row])
        rows = [dict(zip(names, combo, strict=True)) for combo in itertools.product(*levels)]
    else:
        d = len(opt.variables)
        if opt.method == "sobol":
            sampler = qmc.Sobol(d, scramble=True, seed=opt.seed)
            m = max(0, math.ceil(math.log2(max(opt.samples, 1))))
            unit = sampler.random_base2(m)[: opt.samples]  # a power of two, cut to size
        else:
            unit = qmc.LatinHypercube(d, seed=opt.seed).random(opt.samples)
        rows = []
        for u in unit:
            row = {}
            for v, x in zip(opt.variables, u, strict=True):
                if v.kind == "choice":
                    vals = v.values()
                    row[v.name] = vals[min(int(x * len(vals)), len(vals) - 1)]
                else:
                    lo, hi = v.low.to_si(), v.high.to_si()
                    value = lo + x * (hi - lo)
                    row[v.name] = float(np.clip(np.round(value), lo, hi)) \
                        if v.kind == "integer" else float(value)  # fmt: skip
            rows.append(row)
    if opt.max_solves is not None:
        rows = rows[: opt.max_solves]
    return rows


def _point(project: Project, opt: Optimisation, index: int, values: dict) -> Point:
    by = {v.name: v for v in opt.variables}
    quantities = {k: quantity(by[k], x) for k, x in values.items()}
    from puzzle.sweep import labels

    return Point(index, dict(values), labels(project, quantities))


def applied(project: Project, opt: Optimisation, values: dict) -> Project:
    """A copy of the project at a point: each variable set, as a sweep point sets its
    parameters (`sweep.apply`)."""
    from puzzle import sweep

    by = {v.name: v for v in opt.variables}
    study = project.get(opt.study, Study)
    return sweep.apply(project, study, {k: quantity(by[k], x) for k, x in values.items()})


# --- reading a point's outputs --------------------------------------------------------


def _read(project: Project, store, out: DesignOutput, point: str, solver: str | None):
    from puzzle.operators import evaluate as ev

    ctx = ev.Context(project, store, point=point, solver=solver)
    got = ev.source(ctx, out.source, out.port)
    if isinstance(got, ev.Verdict):  # a check: its margin (positive passes)
        return got.margin, None
    if not isinstance(got, ev.Number):
        raise PuzzleError("an objective or constraint must be a number", code="invalid")
    return got.value, got.kind


def _violation(out: DesignOutput, value: float | None) -> str:
    """Why a constraint fails at a value, "" when it holds."""
    if value is None or not math.isfinite(value):
        return "no value"
    if out.upper is not None and value > out.upper.to_si():
        return f"above {out.upper.value:g} {out.upper.unit}".removesuffix(" dimensionless")
    if out.lower is not None and value < out.lower.to_si():
        return f"below {out.lower.value:g} {out.lower.unit}".removesuffix(" dimensionless")
    return ""


def evaluate(project: Project, store, opt: Optimisation, point: Point) -> Point:
    """A point's objective and constraints from its stored runs; feasible when every
    constraint holds. A shoot-out point keeps every solver's outputs (`by_solver`, where
    the solvers part company); the objective and constraints are the primary solver's,
    or the worst of the solvers with `reads` worst (a constraint held on every one)."""
    if not point.runs:
        return point
    study = project.get(opt.study, Study)
    names = _names(project)
    per: dict[str, dict] = {}
    for solver in point.runs:
        mine: dict[str, Any] = {"constraints": []}
        try:
            if opt.objective is not None:
                mine["objective"], point.kinds["objective"] = _read(
                    project, store, opt.objective, point.title, solver)  # fmt: skip
            for i, c in enumerate(opt.constraints):
                v, point.kinds[f"constraint {i + 1}"] = _read(project, store, c, point.title,
                                                              solver)  # fmt: skip
                mine["constraints"].append(v)
        except PuzzleError as exc:
            if solver == study.solver:
                point.reason, point.feasible = exc.message, False
                return point
            mine = {"failed": exc.message}
        per[solver] = mine
    if len(per) > 1:
        point.by_solver.update(per)
    read = [study.solver] if opt.reads == "primary" else [s for s in per if "failed" not in per[s]]
    read = [s for s in read if s in per and "failed" not in per[s]]
    sign = 1 if (opt.objective is None or opt.objective.sense == "minimise") else -1
    if opt.objective is not None and read:  # the worst of those read
        point.objective = max((per[s]["objective"] for s in read), key=lambda v: sign * v)
    point.constraints, reason = [], ""
    for i, c in enumerate(opt.constraints):
        vals = [per[s]["constraints"][i] for s in read]
        if not vals:
            point.constraints.append(None)
            continue
        worst = max(vals) if c.upper is not None else min(vals)  # nearest its limit
        point.constraints.append(worst)
        why = _violation(c, worst)
        if why and not reason:
            reason = f"{names.get(c.source, 'constraint')} {why}"
    point.feasible, point.reason = not reason, reason
    return point


def _names(project: Project) -> dict:
    return {o.id: o.name for o in project.objects.values()}


# --- running --------------------------------------------------------------------------


def _solvers(study: Study) -> list[str]:
    return study.all_solvers()


def _stored(store, study: Study, title: str) -> dict[str, Any]:
    """The newest completed run of each solver at a point, by its title."""
    from puzzle.sweep import title as title_of

    found = {}
    for r in store.runs():  # oldest first: the newest wins
        mine = r.study_id == str(study.id) and r.status == "completed" and r.point_labels()
        if mine and title_of(r.point_labels()) == title:
            found[r.solver] = r
    return found


def points(project: Project, store, opt: Optimisation) -> list[Point]:
    """Every planned point with what its stored runs say, without solving (the table)."""
    study = project.get(opt.study, Study)
    out = []
    for i, values in enumerate(plan(opt), start=1):
        p = _point(project, opt, i, values)
        stored = _stored(store, study, p.title) if store is not None else {}
        if stored:
            p.runs = {s: r.run_id for s, r in stored.items()}
            p.seconds = sum(r.duration_s for r in stored.values())
            evaluate(applied(project, opt, values), store, opt, p)
        out.append(p)
    return out


def run(
    project: Project,
    opt: Optimisation,
    root: Path,
    workdir: Path,
    *,
    store,
    solve: Callable | None = None,
    on_output: Callable[[str], None] | None = None,
    on_point: Callable[[Point], None] | None = None,
    cancel=None,
    reuse: bool = True,
) -> list[Point]:
    """Run each planned point not run yet (a stored point is reused: resume), then read
    its outputs. A point that fails (mesh refused, solver diverged) is infeasible with
    its reason; the optimisation goes on. `cancel` stops after the running point."""
    from puzzle.project.graph import run_hashes

    if solve is None:
        from puzzle.solve import run_study as solve
    check(project, opt)
    study = project.get(opt.study, Study)
    started = time.monotonic()
    out: list[Point] = []
    planned = plan(opt)
    for i, values in enumerate(planned, start=1):
        p = _point(project, opt, i, values)
        if cancel is not None and cancel.is_set():
            p.reason = "stopped before it ran"
            out.append(p)
            continue
        if opt.max_minutes is not None and time.monotonic() - started > 60 * opt.max_minutes:
            p.reason = "past the time budget"
            out.append(p)
            continue
        at = applied(project, opt, values)
        keys = run_hashes(at)[study.id]
        if on_output:
            on_output(f"--- {opt.name}: point {i} of {len(planned)}: {p.title}")
        meshed = None
        for solver in _solvers(study):
            try:
                run_ = solve(at, study.id, root, workdir / f"point-{i}" / solver, store=store,
                             reuse=reuse, on_output=on_output, solver=solver, meshed=meshed,
                             _point=(keys[solver][0], p.labels), cancel=cancel)  # fmt: skip
            except PuzzleError as exc:
                if solver == study.solver:
                    p.reason = f"{exc.code}: {exc.message}"
                    break
                p.by_solver[solver] = {"failed": exc.message}  # feasible on the others
                continue
            meshed = meshed or getattr(run_, "mesh_result", None)
            p.runs[solver] = run_.run_id
        if p.runs:
            stored = {r.run_id: r for r in store.runs()}
            p.seconds = sum(stored[r].duration_s for r in p.runs.values() if r in stored)
            evaluate(at, store, opt, p)
        out.append(p)
        if on_point:
            on_point(p)
    return out


# --- the best point and applying it ---------------------------------------------------


def best(opt: Optimisation, pts: list[Point]) -> Point | None:
    """The best feasible point: the objective's least (or greatest); with constraints
    only, the first feasible."""
    feasible = [p for p in pts if p.feasible]
    if not feasible:
        return None
    if opt.objective is None:
        return feasible[0]
    scored = [p for p in feasible if p.objective is not None]
    if not scored:
        return None
    sign = 1 if opt.objective.sense == "minimise" else -1
    return min(scored, key=lambda p: sign * p.objective)


def apply_commands(project: Project, opt: Optimisation, point: Point) -> list[dict]:
    """The edits that put a point's values into the model's parameters (one batch, so
    one undo): a parameter set's value, or a Number's or Slider's."""
    from puzzle.model.schema import Operator, ParameterSet

    by = {v.name: v for v in opt.variables}
    commands = []
    for name, si in point.values.items():
        q = quantity(by[name], si).model_dump(mode="json")
        sets = [ps for ps in project.of_type(ParameterSet) if name in ps.values]
        if sets:
            commands.append(
                {
                    "command": "edit",
                    "object": str(sets[0].id),
                    "ops": [{"op": "set", "path": f"values.{name}", "value": q}],
                }
            )
            continue
        op = next((o for o in project.of_type(Operator) if o.name == name), None)
        if op is not None:
            commands.append({"command": "edit", "object": str(op.id), "ops": [
                {"op": "set", "path": "inputs.value", "value": {"value": q}}]})  # fmt: skip
    return commands


def suggestion(project: Project, opt: Optimisation, pts: list[Point]) -> str:
    """What the Advisor and the finished notice say: the best point against the
    model's current values ("t = 4 mm: mass −12 %, every constraint holds")."""
    top = best(opt, pts)
    if top is None:
        return ""
    from puzzle.operators import parameter_values

    now = parameter_values(project)
    if all(abs(now.get(k, math.nan) - v) <= 1e-12 * max(abs(v), 1) for k, v in
           top.values.items()):  # fmt: skip
        return ""  # the model is at the best point already
    what = ", ".join(f"{k} = {v}" for k, v in top.labels.items())
    if opt.objective is not None and top.objective is not None:
        from puzzle.operators import evaluate as ev

        name = _names(project).get(opt.objective.source, "objective")
        kind = top.kinds.get("objective")
        return f"{what}: {name} {ev.quantity_text(top.objective, kind)}" + (
            ", every constraint holds" if opt.constraints else "")  # fmt: skip
    return f"{what}: every constraint holds"


def sensitivities(opt: Optimisation, pts: list[Point]) -> dict[str, dict[str, float]]:
    """Each variable's correlation with the objective and each constraint over the run
    points (Pearson's r: −1 to 1), the DOE's first look at what matters."""
    done = [p for p in pts if p.objective is not None or any(
        c is not None for c in p.constraints)]  # fmt: skip
    out: dict[str, dict[str, float]] = {}
    if len(done) < 3:
        return out
    outputs = (["objective"] if opt.objective else []) + [
        f"constraint {i + 1}" for i in range(len(opt.constraints))]  # fmt: skip
    for v in opt.variables:
        xs = np.array([p.values[v.name] for p in done], float)
        row = {}
        for k, name in enumerate(outputs):
            if name == "objective":
                ys = [p.objective for p in done]
            else:
                ys = [p.constraints[k - (1 if opt.objective else 0)] for p in done]
            ok = [i for i, y in enumerate(ys) if y is not None]
            if len(ok) < 3 or np.ptp(xs[ok]) == 0 or np.ptp(np.array(ys, float)[ok]) == 0:
                continue
            row[name] = float(np.corrcoef(xs[ok], np.array(ys, float)[ok])[0, 1])
        out[v.name] = row
    return out
