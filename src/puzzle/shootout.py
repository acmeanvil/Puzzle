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
    return out


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
