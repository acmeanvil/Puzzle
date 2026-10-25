"""Solver shoot-outs (ADR 0011): one study, one mesh, several solvers, compared.

The primary solver runs first; each shoot-out solver then runs on the same mesh, and
every metric compares its last-step result with the primary's:

- relative_l2: |b - a| / |a| over the entities both report (all components);
- max_abs_difference: the largest |b - a| there, in SI units;
- relative_value: one number per solver: a global field's value, the eigenfrequencies
  (the largest relative difference over the modes both found) for "eigenfrequencies",
  otherwise the field's peak magnitude.

The primary's results stay the study's results; shoot-out runs only feed this report.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.model.schema import ComparisonMetric, Project, Study
from puzzle.results.model import Results
from puzzle.results.store import ResultStore
from puzzle.solve import StudyError, StudyRun, run_study


@dataclass
class Comparison:
    solver: str  # the shoot-out solver compared with the primary
    metric: ComparisonMetric
    value: float

    @property
    def passed(self) -> bool:
        return self.value <= self.metric.tolerance


@dataclass
class ShootoutRun:
    primary: str
    runs: dict[str, StudyRun]  # by solver, the primary first
    comparisons: list[Comparison] = field(default_factory=list)
    # Each solver's settings that differ from its defaults (ADR 0023): its own, the
    # shared ones set on the steps, and any pass-through.
    settings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.comparisons)

    def table(self) -> str:
        """A Markdown report: one row per solver and metric."""
        rows = [
            "| Solver | Quantity | Metric | Value | Tolerance | Result |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        rows += [
            f"| {c.solver} vs {self.primary} | {c.metric.quantity} | {c.metric.norm} | "
            f"{c.value:.4g} | {c.metric.tolerance:g} | {'pass' if c.passed else 'FAIL'} |"
            for c in self.comparisons
        ]
        if any(self.settings.values()):
            rows += ["", "| Solver | Settings (not the defaults) |", "| --- | --- |"]
            rows += [
                f"| {solver} | {'; '.join(items) or 'defaults'} |"
                for solver, items in self.settings.items()
            ]
        return "\n".join(rows)


def run_shootout(
    project: Project,
    study_id,
    root: Path,
    workdir: Path,
    *,
    store: ResultStore | None = None,
    reuse: bool = True,
    on_output: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
) -> ShootoutRun:
    study = project.get(study_id, Study)
    if study.shootout is None:
        raise StudyError("this study has no shoot-out", code="invalid", path=study.name)
    kwargs = {"store": store, "reuse": reuse, "on_output": on_output, "timeout_s": timeout_s}
    first = run_study(project, study.id, root, workdir / study.solver, **kwargs)
    meshed = first.mesh_result
    out = ShootoutRun(primary=study.solver, runs={study.solver: first})
    for solver in study.shootout.solvers:
        out.runs[solver] = run_study(
            project, study.id, root, workdir / solver, solver=solver, meshed=meshed, **kwargs
        )
        if meshed is None:  # the primary came from the store: mesh once, here
            meshed = out.runs[solver].mesh_result
    for solver in study.shootout.solvers:
        for metric in study.shootout.metrics:
            value = compare(first.results, out.runs[solver].results, metric)
            out.comparisons.append(Comparison(solver, metric, value))
    out.settings = {solver: settings_in_use(study, solver) for solver in out.runs}
    return out


def settings_in_use(study: Study, solver: str) -> list[str]:
    """What a solver was told beyond its defaults (ADR 0023), as "key = value"."""
    from puzzle.adapters.settings import SHARED_KEYS

    shared = sorted({f"{k} = {v}" for s in study.solve_steps() for k, v in s.settings.items()
                     if k in SHARED_KEYS})  # fmt: skip
    own = [f"{k} = {v}" for k, v in sorted(study.solver_settings.get(solver, {}).items())]
    expert = [f"expert {k}" for k in sorted(study.expert_settings.get(solver, {}))]
    return shared + own + expert


def compare(a: Results, b: Results, metric: ComparisonMetric) -> float:
    """The metric's value for result `b` against reference `a` (see the module doc)."""
    if metric.quantity == "eigenfrequencies":
        fa = np.array([s.value for s in a.modes()])
        fb = np.array([s.value for s in b.modes()])
        n = min(len(fa), len(fb))
        if n == 0:
            raise StudyError("a shoot-out solver found no modes", code="invalid")
        return float(np.max(np.abs(fb[:n] - fa[:n]) / np.abs(fa[:n])))
    fa, fb = a.last(metric.quantity), b.last(metric.quantity)
    if metric.norm == "relative_value":
        if fa.location == "global":
            va, vb = float(fa.values.ravel()[0]), float(fb.values.ravel()[0])
        else:
            va = float(np.linalg.norm(fa.values, axis=1).max())
            vb = float(np.linalg.norm(fb.values, axis=1).max())
        return abs(vb - va) / abs(va)
    common = np.intersect1d(fa.ids, fb.ids)
    if len(common) == 0:
        raise StudyError(
            f"the solvers report '{metric.quantity}' on different entities", code="invalid"
        )
    da, db = fa.at(common), fb.at(common)
    if metric.norm == "max_abs_difference":
        return float(np.abs(db - da).max())
    return float(np.linalg.norm(db - da) / np.linalg.norm(da))


def run_mesh_shootout(
    project: Project,
    study_id,
    root: Path,
    workdir: Path,
    metrics: list[ComparisonMetric],
    *,
    on_output: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
) -> ShootoutRun:
    """A mesh shoot-out (ADR 0019): the study run on its recipe meshed by Gmsh and by
    Netgen (the recipe otherwise unchanged), compared by `metrics`. The meshes differ,
    so only whole-result measures compare: relative_value (a global value or the peak)
    and eigenfrequencies; node-by-node norms are refused."""
    import copy

    from puzzle.model.schema import Component, MeshRecipe

    odd = [m for m in metrics if m.norm != "relative_value" and m.quantity != "eigenfrequencies"]
    if odd:
        raise StudyError(
            "two meshes have different nodes: a mesh shoot-out compares relative_value "
            "metrics or eigenfrequencies only",
            code="invalid",
        )
    study = project.get(study_id, Study)
    runs = {}
    for mesher in ("gmsh", "netgen"):
        copy_ = copy.deepcopy(project)
        comp = copy_.get(study.component, Component)
        copy_.get(comp.mesh, MeshRecipe).mesher = mesher
        runs[mesher] = run_study(
            copy_, study.id, root, workdir / mesher, on_output=on_output, timeout_s=timeout_s
        )
    out = ShootoutRun(primary="gmsh", runs=runs)
    for metric in metrics:
        value = compare(runs["gmsh"].results, runs["netgen"].results, metric)
        out.comparisons.append(Comparison("netgen", metric, value))
    return out
