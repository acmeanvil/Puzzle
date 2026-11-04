"""Design optimisation and design of experiments (ADR 0063).

An `Optimisation` runs its study once per point it chooses. A point sets the design
variables (parameters by name: a parameter set's value, or a Number or Slider operator)
on a copy of the project (`sweep.apply`, as a sweep's point does), and the study's run
is stored with the point's labels ("t": "4 mm") and cached by its run key. So a point
already run is never solved again, a stopped optimisation resumes, and every point's
run opens like any other. The objective and constraints are post-solve outputs (ADR
0062), read at each point's run.

A design of experiments chooses its points up front (`plan`: full factorial, Latin
hypercube or Sobol, seeded). An optimiser (COBYLA, Nelder–Mead, differential evolution,
from scipy; Bayesian optimisation and response surfaces, on `puzzle.surrogates`)
chooses each point from the outputs of the last. Both are deterministic:
the same project, settings and seed ask for the same points in the same order. So
`points` rebuilds the table from the stored runs at any time without solving (an
optimiser is replayed, each point it asks for read from the store, until one isn't
there), and `run` resumes the same way: the replay reaches the first point not run,
and solving goes on from there.
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
    "cobyla": "COBYLA",
    "nelder_mead": "Nelder–Mead",
    "differential_evolution": "Differential evolution",
    "bayesian": "Bayesian (Gaussian process)",
    "response_surface": "Response surface",
}
MODELLED = ("bayesian", "response_surface")  # a model of the outputs picks each point
CANDIDATES = 11  # 2^11 Sobol candidates a step, where a model is searched
DOE = ("full_factorial", "latin_hypercube", "sobol")
LOCAL_POINTS = 50  # a local optimiser's most points when the optimisation sets none
PENALTY = 100.0  # Nelder–Mead: the objective plus this times each constraint's breach


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
    origin: str = ""  # what chose it: "initial plan", "expected improvement"...

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
                **({"origin": self.origin} if self.origin else {}),
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
    if opt.method == "response_surface" and opt.surface == "quadratic":
        from puzzle.surrogates import quadratic_terms

        need = quadratic_terms(len(opt.variables))
        if opt.samples < need:
            raise PuzzleError(
                f"optimisation '{opt.name}': a quadratic surface over {len(opt.variables)} "
                f"variables has {need} coefficients, so it starts from {need} points at "
                f"least (samples is {opt.samples})", code="invalid")  # fmt: skip
    if opt.method in ("cobyla", "nelder_mead"):
        listed = [v.name for v in opt.variables if v.kind == "choice"]
        if listed:
            raise PuzzleError(
                f"optimisation '{opt.name}': {METHODS[opt.method]} moves continuous and "
                f"integer variables; {', '.join(listed)} is a list of choices (use "
                "differential evolution or a design of experiments)", code="invalid")  # fmt: skip


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


def _stored(store, study: Study, keys: dict[str, str]) -> dict[str, Any]:
    """The newest completed run of each solver at a point, by the point's run keys (so a
    point of a changed model is not read from an old run)."""
    found = {}
    for r in store.runs():  # oldest first: the newest wins
        mine = r.study_id == str(study.id) and r.status == "completed"
        if mine and keys.get(r.solver) == r.run_key:
            found[r.solver] = r
    return found


class Points(list):
    """An optimisation's points in the order they were asked for, and how it ended
    (`ended`: converged, the budget, stopped, or a point not run yet)."""

    ended: str = ""


class _Stop(Exception):
    """No more points: the budget, Stop, the time, or (reading only) a point not run."""


class _Runner:
    """The points an optimisation asks for, each once: read from the store when its run
    is there, solved otherwise (or, reading only, `_Stop` at the first not run)."""

    def __init__(self, project, opt, root, workdir, store, solve, on_output=None,
                 on_point=None, cancel=None, reuse=True, budget=None) -> None:  # fmt: skip
        self.project, self.opt, self.root, self.workdir = project, opt, root, workdir
        self.store, self.solve, self.on_output, self.on_point = store, solve, on_output, on_point
        self.cancel, self.reuse, self.budget = cancel, reuse, budget
        self.study = project.get(opt.study, Study)
        self.started, self.solved = time.monotonic(), 0
        self.points: Points = Points()
        self.seen: dict[str, Point] = {}
        self.limit = limit(opt)

    def at(self, values: dict[str, float], origin: str = "") -> Point:
        from puzzle.project.graph import run_hashes

        p = _point(self.project, self.opt, len(self.points) + 1, values)
        p.origin = origin
        if p.title in self.seen:
            return self.seen[p.title]
        if len(self.points) >= self.limit:
            raise _Stop("reached its most points")
        try:
            at = applied(self.project, self.opt, values)
            keys = {s: k[0] for s, k in run_hashes(at)[self.study.id].items()}
        except PuzzleError as exc:  # the point can't be set up: infeasible, go on
            p.reason = f"{exc.code}: {exc.message}"
            return self._keep(p)
        stored = {}
        if self.store is not None and (self.reuse or self.solve is None):
            stored = _stored(self.store, self.study, keys)
        if stored:
            p.runs = {s: r.run_id for s, r in stored.items()}
            p.seconds = sum(r.duration_s for r in stored.values())
        elif self.solve is None:
            raise _Stop("not run yet")
        else:
            if self.cancel is not None and self.cancel.is_set():
                raise _Stop("stopped before it ran")
            minutes = self.opt.max_minutes
            if minutes is not None and time.monotonic() - self.started > 60 * minutes:
                raise _Stop("past the time budget")
            if self.budget is not None and self.solved >= self.budget:
                raise _Stop("past this run's budget")
            self._solve(at, p, keys)
            self.solved += 1
        if p.runs:
            evaluate(at, self.store, self.opt, p)
        return self._keep(p)

    def _keep(self, p: Point) -> Point:
        self.points.append(p)
        self.seen[p.title] = p
        if self.on_point is not None and self.solve is not None:
            self.on_point(p)
        return p

    def _solve(self, at: Project, p: Point, keys: dict[str, str]) -> None:
        if self.on_output:
            self.on_output(f"--- {self.opt.name}: point {p.index}: {p.title}")
        meshed = None
        for solver in _solvers(self.study):
            try:
                run_ = self.solve(at, self.study.id, self.root,
                                  self.workdir / f"point-{p.index}" / solver, store=self.store,
                                  reuse=self.reuse, on_output=self.on_output, solver=solver,
                                  meshed=meshed, _point=(keys[solver], p.labels),
                                  cancel=self.cancel)  # fmt: skip
            except PuzzleError as exc:
                if solver == self.study.solver:
                    p.reason = f"{exc.code}: {exc.message}"
                    break
                p.by_solver[solver] = {"failed": exc.message}  # feasible on the others
                continue
            meshed = meshed or getattr(run_, "mesh_result", None)
            p.runs[solver] = run_.run_id
        if p.runs:
            stored = {r.run_id: r for r in self.store.runs()}
            p.seconds = sum(stored[r].duration_s for r in p.runs.values() if r in stored)


def adaptive(opt: Optimisation) -> bool:
    """An optimiser, choosing each point from the last (not a design of experiments)."""
    return opt.method not in DOE


def _members(opt: Optimisation) -> int:
    """Differential evolution's points per generation, as scipy makes them."""
    d = len(opt.variables)
    return max(5, math.ceil(opt.population / d) * d)


def limit(opt: Optimisation) -> int:
    """The most points an optimisation runs: its plan's, its `max_solves`, or else an
    optimiser's own (50 for a local one; differential evolution's generations)."""
    if not adaptive(opt):
        return len(plan(opt))
    if opt.method == "differential_evolution":
        n = _members(opt) * (opt.generations + 1)
    elif opt.method == "bayesian":
        n = opt.samples + 20
    elif opt.method == "response_surface":
        n = opt.samples + 10
    else:
        n = LOCAL_POINTS
    return opt.max_solves if opt.max_solves is not None else n


def resolution(var) -> float:
    """What an optimiser rounds a continuous variable to (SI): its `resolution`, else a
    round thousandth of its range (0.001 mm for 4 mm .. 10 mm)."""
    if var.resolution is not None:
        return var.resolution.to_si()
    span = var.high.to_si() - var.low.to_si()
    return 10.0 ** math.floor(math.log10(span / 1000))


def snap(var, x: float) -> float:
    """A variable's value as an optimiser's point holds it: an integer, or a multiple of
    its resolution, inside its bounds. The same value always gives the same point."""
    lo, hi = var.low.to_si(), var.high.to_si()
    return _snapped(var.kind, lo, hi, None if var.kind == "integer" else resolution(var), x)


def _snapped(kind: str, lo: float, hi: float, step: float | None, x: float) -> float:
    if kind == "integer":
        return float(np.clip(round(x), math.ceil(lo), math.floor(hi)))
    return float(f"{float(np.clip(round(x / step) * step, lo, hi)):.12g}")


class _Merit:
    """The numbers an optimiser sees for a point: the objective scaled by its first
    value (minimised: a maximised one is negated), and each limit as a margin scaled by
    the limit (positive holds). A point that failed is worse than every point so far
    and breaks every limit, so the optimiser moves away from it."""

    def __init__(self, opt: Optimisation) -> None:
        self.opt = opt
        self.scale: float | None = None
        self.worst = 0.0
        self.limits = [
            (i, q, side)
            for i, c in enumerate(opt.constraints)
            for q, side in ((c.upper, -1.0), (c.lower, 1.0))
            if q is not None
        ]

    def objective(self, p: Point) -> float:
        obj = self.opt.objective
        if obj is None:
            return 0.0
        if p.objective is None or not math.isfinite(p.objective):
            return self.worst + 1.0
        if self.scale is None:
            self.scale = abs(p.objective) or 1.0
        value = (1 if obj.sense == "minimise" else -1) * p.objective / self.scale
        self.worst = max(self.worst, value)
        return value

    def margins(self, p: Point) -> list[float]:
        out = []
        for i, q, side in self.limits:
            v = p.constraints[i] if i < len(p.constraints) else None
            limit_ = q.to_si()
            ok = v is not None and math.isfinite(v)
            out.append(side * (v - limit_) / (abs(limit_) or 1.0) if ok else -1.0)
        return out

    def penalised(self, p: Point) -> float:
        return self.objective(p) + PENALTY * sum(max(0.0, -g) for g in self.margins(p))


def _coded(opt: Optimisation, project: Project) -> tuple[list, list, list[bool]]:
    """The optimiser's view of the variables: bounds, the start (the variables' current
    values), and which are whole numbers. Local methods move every variable between 0
    and 1 (low to high), so steps mean the same for each; differential evolution keeps
    integers and choices (by index) whole."""
    from puzzle.operators import parameter_values

    now = parameter_values(project)
    de = opt.method == "differential_evolution"
    bounds, start, whole = [], [], []
    for v in opt.variables:
        if v.kind == "choice":
            vals = v.values()
            here = now.get(v.name, vals[0])
            bounds.append((0, len(vals) - 1))
            start.append(float(np.argmin([abs(x - here) for x in vals])))
            whole.append(True)
            continue
        lo, hi = v.low.to_si(), v.high.to_si()
        here = float(np.clip(now.get(v.name, (lo + hi) / 2), lo, hi))
        if de and v.kind == "integer":
            bounds.append((math.ceil(lo), math.floor(hi)))
            start.append(float(np.clip(round(here), math.ceil(lo), math.floor(hi))))
            whole.append(True)
        else:
            bounds.append((0.0, 1.0))
            start.append((here - lo) / (hi - lo))
            whole.append(False)
    return bounds, start, whole


def _decoded(opt: Optimisation, c) -> dict[str, float]:
    de = opt.method == "differential_evolution"
    out = {}
    for v, x in zip(opt.variables, c, strict=True):
        if v.kind == "choice":
            vals = v.values()
            out[v.name] = vals[int(np.clip(round(float(x)), 0, len(vals) - 1))]
        elif de and v.kind == "integer":
            out[v.name] = snap(v, float(x))
        else:
            lo, hi = v.low.to_si(), v.high.to_si()
            out[v.name] = snap(v, lo + float(np.clip(x, 0.0, 1.0)) * (hi - lo))
    return out


def _optimise(r: _Runner) -> str:
    """Run the optimiser over the runner's points; how it ended, in words."""
    import warnings

    from scipy.optimize import NonlinearConstraint, differential_evolution, minimize

    opt = r.opt
    if opt.method in MODELLED:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # fits on few points
                return (_bayesian if opt.method == "bayesian" else _surface)(r)
        except _Stop as stop:
            return str(stop)
    merit = _Merit(opt)
    bounds, start, whole = _coded(opt, r.project)

    def at(c) -> Point:
        return r.at(_decoded(opt, c))

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # scipy on penalised points
            if opt.method == "cobyla":
                # a little inside each limit: COBYLA ends on an active limit, and the
                # grid's nearest point to it may be just past
                inside = opt.tolerance / 10
                cons = [{"type": "ineq",
                         "fun": lambda c, j=j: merit.margins(at(c))[j] - inside}
                        for j in range(len(merit.limits))]  # fmt: skip
                res = minimize(lambda c: merit.objective(at(c)), np.array(start),
                               method="COBYLA", bounds=bounds, constraints=cons,
                               options={"rhobeg": 0.25, "tol": opt.tolerance,
                                        "maxiter": 100000})  # fmt: skip
            elif opt.method == "nelder_mead":
                x0 = np.array(start)
                simplex = [x0] + [x0 + np.eye(len(x0))[i] * (0.25 if x0[i] <= 0.75 else -0.25)
                                  for i in range(len(x0))]  # fmt: skip
                res = minimize(lambda c: merit.penalised(at(c)), x0, method="Nelder-Mead",
                               bounds=bounds, options={"initial_simplex": np.array(simplex),
                               "xatol": opt.tolerance, "fatol": opt.tolerance,
                               "maxfev": 100000})  # fmt: skip
            else:
                cons = ()
                if merit.limits:
                    cons = NonlinearConstraint(lambda c: np.array(merit.margins(at(c))), 0,
                                               np.inf)  # fmt: skip
                res = differential_evolution(
                    lambda c: merit.objective(at(c)), bounds, x0=np.array(start),
                    maxiter=opt.generations, popsize=math.ceil(opt.population /
                    len(opt.variables)), tol=opt.tolerance, seed=opt.seed, polish=False,
                    init="latinhypercube", updating="deferred", integrality=whole,
                    constraints=cons)  # fmt: skip
    except _Stop as stop:
        return str(stop)
    return "converged" if res.success else str(res.message)


# --- methods that model the outputs (phase 3) -----------------------------------------


def _spec(opt: Optimisation) -> list[tuple]:
    """Each variable in SI once (name, kind, low, high, resolution, choices): unit
    conversions are slow, and a model's search converts thousands of points."""
    out = []
    for v in opt.variables:
        if v.kind == "choice":
            out.append((v.name, "choice", 0.0, 0.0, None, v.values()))
        else:
            step = None if v.kind == "integer" else resolution(v)
            out.append((v.name, v.kind, v.low.to_si(), v.high.to_si(), step, None))
    return out


def _scaled(opt: Optimisation, values: dict[str, float], spec=None) -> np.ndarray:
    """A point's variables scaled 0..1, low to high (a choice by its index)."""
    out = []
    for name, kind, lo, hi, _step, vals in spec or _spec(opt):
        x = values[name]
        if kind == "choice":
            i = int(np.argmin([abs(c - x) for c in vals]))
            out.append(i / (len(vals) - 1) if len(vals) > 1 else 0.0)
        else:
            out.append((x - lo) / (hi - lo))
    return np.array(out)


def _from_scaled(opt: Optimisation, u, spec=None) -> dict[str, float]:
    out = {}
    for (name, kind, lo, hi, step, vals), x in zip(spec or _spec(opt), u, strict=True):
        x = float(np.clip(x, 0.0, 1.0))
        if kind == "choice":
            out[name] = vals[int(round(x * (len(vals) - 1)))]
        else:
            out[name] = _snapped(kind, lo, hi, step, lo + x * (hi - lo))
    return out


def _initial(r: _Runner) -> None:
    """The Latin hypercube a modelled method starts from: the same points as a Latin
    hypercube design with this seed and size, so its runs are shared."""
    first = r.opt.model_copy(update={"method": "latin_hypercube", "max_solves": None})
    for values in plan(first):
        r.at(values, "initial plan")


def _data(opt: Optimisation, pts: list[Point]):
    """What the models are fitted to: the points (0..1), the scaled objective, and each
    limit's scaled margin (a failed point the worst, every limit broken)."""
    merit = _Merit(opt)
    spec = _spec(opt)
    x = np.array([_scaled(opt, p.values, spec) for p in pts])
    f = np.array([merit.objective(p) for p in pts])
    g = np.array([merit.margins(p) for p in pts], float).reshape(len(pts), len(merit.limits))
    return x, f, g


def _candidates(opt: Optimisation, step: int, near) -> np.ndarray:
    """Where a model is searched: a Sobol set over the whole range, and (Bayesian)
    a cloud round the best point so far; seeded by the step, so a replay matches."""
    from scipy.stats import qmc

    d = len(opt.variables)
    u = qmc.Sobol(d, scramble=True, seed=opt.seed + 1000 * step).random_base2(CANDIDATES)
    if near is not None:
        rng = np.random.default_rng(opt.seed + step)
        u = np.vstack([u, np.clip(near + rng.normal(0.0, 0.05, (512, d)), 0.0, 1.0)])
    return u


def _key(opt: Optimisation, values: dict[str, float]) -> tuple:
    return tuple(values[v.name] for v in opt.variables)


def _fresh(r: _Runner, u: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """The candidates as points not run yet, each once, snapped onto the grid."""
    seen = {_key(r.opt, p.values) for p in r.points}
    spec = _spec(r.opt)
    keep, values = [], []
    for row in u:
        vals = _from_scaled(r.opt, row, spec)
        k = _key(r.opt, vals)
        if k not in seen:
            seen.add(k)
            keep.append(_scaled(r.opt, vals, spec))
            values.append(vals)
    return np.array(keep), values


def _bayesian(r: _Runner) -> str:
    """Bayesian optimisation (ADR 0063 item 4): a Gaussian process of the objective and
    of each limit's margin over the points so far; the next point the one with the
    most expected improvement times the probability that every limit holds (before a
    feasible point, the most likely feasible). Stops when the best expected improvement
    is below the tolerance (a share of the objective's first value)."""
    from puzzle.surrogates import expected_improvement, feasibility, fit_gaussian

    opt = r.opt
    _initial(r)
    step = 0
    while True:
        step += 1
        pts = list(r.points)
        x, f, g = _data(opt, pts)
        ok = np.array([p.feasible for p in pts])
        near = x[np.flatnonzero(ok)[np.argmin(f[ok])]] if ok.any() else None
        cand, values = _fresh(r, _candidates(opt, step, near))
        if not values:
            return "every point of its grid is run"
        score = np.ones(len(cand))
        for j in range(g.shape[1]):
            score *= feasibility(*fit_gaussian(x, g[:, j]).predict(cand))
        if opt.objective is not None and ok.any():
            mu, sd = fit_gaussian(x, f).predict(cand)
            score *= expected_improvement(mu, sd, float(f[ok].min()))
            if float(score.max()) < opt.tolerance:
                return "converged"
            why = "expected improvement"
        elif ok.any():
            return "found a feasible point"  # constraints only: that was the question
        else:
            why = "likely feasible"
        r.at(values[int(np.argmax(score))], why)


def _surface(r: _Runner) -> str:
    """A response surface (ADR 0063 item 4): surfaces of the objective and each limit's
    margin through the solved points, optimised (a Sobol search, then COBYLA on the
    surface), and that point solved to check it; refitted with it, until the surface's
    best point is one already solved."""
    from scipy.optimize import minimize

    from puzzle.surrogates import Surface

    opt = r.opt
    d = len(opt.variables)
    _initial(r)
    step = 0
    while True:
        step += 1
        pts = [p for p in r.points if p.run]
        if len(pts) < d + 2:
            return "too few points solved to fit a surface"
        x, f, g = _data(opt, pts)
        sf = Surface(opt.surface, x, f) if opt.objective is not None else None
        sg = [Surface(opt.surface, x, g[:, j]) for j in range(g.shape[1])]
        u = _candidates(opt, step, None)
        pf = sf(u) if sf is not None else np.zeros(len(u))
        pg = np.column_stack([s(u) for s in sg]) if sg else np.zeros((len(u), 0))
        feasible = np.all(pg >= 0, axis=1)
        k = int(np.argmin(np.where(feasible, pf, np.inf)) if feasible.any()
                else np.argmax(pg.min(axis=1)))  # fmt: skip
        best = u[k]
        if sf is not None and feasible.any():
            inside = opt.tolerance / 10
            cons = [{"type": "ineq", "fun": lambda c, s=s, a=inside: float(s(c)[0]) - a}
                    for s in sg]  # fmt: skip
            got = minimize(lambda c, f_=sf: float(f_(c)[0]), best, method="COBYLA",
                           bounds=[(0.0, 1.0)] * d, constraints=cons,
                           options={"rhobeg": 0.1, "tol": opt.tolerance / 10,
                                    "maxiter": 2000})  # fmt: skip
            if all(float(s(got.x)[0]) >= 0 for s in sg) and float(sf(got.x)[0]) <= pf[k]:
                best = got.x
        values = _from_scaled(opt, best)
        if _key(opt, values) in {_key(opt, p.values) for p in r.points}:
            return "converged: the surface's best point is solved"
        r.at(values, "surface optimum")


def fit_quality(opt: Optimisation, pts: list[Point]) -> dict[str, float | None]:
    """How well a response surface (of the optimisation's kind) fits each output: its
    leave-one-out error as a share of the output's range over the solved points (ADR
    0063 item 4: a poor fit is seen). None where too few points to tell."""
    from puzzle.surrogates import leave_one_out

    out = {}
    for key in output_keys(opt):
        done = [p for p in pts if p.run and output_of(p, key) is not None]
        if not done:
            out[key] = None
            continue
        x = np.array([_scaled(opt, p.values) for p in done])
        out[key] = leave_one_out(opt.surface, x, np.array([output_of(p, key) for p in done]))
    return out


_REPLAYS: dict[tuple, Points] = {}  # replayed tables: a Bayesian replay refits its models


def _replay_key(project: Project, store, opt: Optimisation) -> tuple | None:
    """What a replayed table depends on: the optimisation, the model (its study's run
    keys), and the store's runs (their count and the newest)."""
    if store is None:
        return None
    from puzzle.project.graph import run_hashes

    try:
        keys = run_hashes(project).get(opt.study)
    except PuzzleError:
        return None
    runs = store.runs()
    outputs = "".join(
        o.model_dump_json()
        for o in project.objects.values()
        if o.kind in ("operator", "probe", "result")
    )  # what reads the runs
    return (opt.model_dump_json(), repr(keys), outputs, str(getattr(store, "root", id(store))),
            len(runs), runs[-1].run_id if runs else "")  # fmt: skip


def points(project: Project, store, opt: Optimisation) -> Points:
    """Every point with what its stored runs say, without solving (the table). A design
    of experiments lists every planned point; an optimiser is replayed over the stored
    runs, so its points are listed up to the first not run yet."""
    r = _Runner(project, opt, None, None, store, None)
    if adaptive(opt):
        key = _replay_key(project, store, opt)
        if key is not None and key in _REPLAYS:
            return _REPLAYS[key]
        r.points.ended = _optimise(r)
        if key is not None:
            if len(_REPLAYS) >= 8:
                _REPLAYS.pop(next(iter(_REPLAYS)))
            _REPLAYS[key] = r.points
        return r.points
    for values in plan(opt):
        try:
            r.at(values)
        except _Stop:
            r._keep(_point(project, opt, len(r.points) + 1, values))
    return r.points


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
    budget: int | None = None,
) -> Points:
    """Run the points not run yet (a stored point is read back, not solved: resume),
    each read for its outputs. A point that fails (mesh refused, solver diverged) is
    infeasible with its reason; the optimisation goes on. `cancel` stops after the
    running point; `budget` solves at most that many new points this time."""
    if solve is None:
        from puzzle.solve import run_study as solve
    check(project, opt)
    r = _Runner(project, opt, root, workdir, store, solve, on_output, on_point, cancel, reuse,
                budget)  # fmt: skip
    if adaptive(opt):
        r.points.ended = _optimise(r)
        if on_output:
            on_output(f"--- {opt.name}: {METHODS[opt.method]} {r.points.ended} after "
                      f"{len(r.points)} points ({r.solved} solved now)")  # fmt: skip
        return r.points
    for values in plan(opt):
        try:
            r.at(values)
        except _Stop as stop:
            p = _point(project, opt, len(r.points) + 1, values)
            if p.title not in r.seen:
                p.reason = str(stop)
                r._keep(p)
    r.points.ended = "planned points run"
    return r.points


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


# --- the Pareto front -----------------------------------------------------------------


def output_keys(opt: Optimisation) -> list[str]:
    """The outputs a point has: "objective", then "constraint 1", "constraint 2"..."""
    return (["objective"] if opt.objective else []) + [
        f"constraint {i + 1}" for i in range(len(opt.constraints))]  # fmt: skip


def output_of(p: Point, key: str) -> float | None:
    if key == "objective":
        return p.objective
    i = int(key.removeprefix("constraint ")) - 1
    return p.constraints[i] if i < len(p.constraints) else None


def better(opt: Optimisation, key: str) -> int:
    """Which way an output is better, 1 smaller or −1 larger: the objective by its
    sense; a constraint away from its limit (an upper one smaller, a lower one larger)."""
    if key == "objective":
        return 1 if opt.objective.sense == "minimise" else -1
    c = opt.constraints[int(key.removeprefix("constraint ")) - 1]
    return -1 if c.upper is None and c.lower is not None else 1


def pareto(opt: Optimisation, pts: list[Point], keys: list[str] | None = None) -> list[Point]:
    """The feasible points no other feasible point beats on every output of `keys`
    (default all: the objective and each constraint), in order of the first: the
    trade-off to pick from when one objective was made constraints (ADR 0063 item 5)."""
    keys = keys or output_keys(opt)
    signs = [better(opt, k) for k in keys]
    rows = []
    for p in pts:
        vals = [output_of(p, k) for k in keys]
        if p.feasible and all(v is not None and math.isfinite(v) for v in vals):
            rows.append((p, [s * v for s, v in zip(signs, vals, strict=True)]))
    front = []
    for p, a in rows:
        beaten = any(all(x <= y for x, y in zip(b, a, strict=True)) and b != a
                     for _, b in rows)  # fmt: skip
        if not beaten:
            front.append((p, a))
    return [p for p, _ in sorted(front, key=lambda pa: pa[1])]
