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


# What a shoot-out compares when its study names no metrics: each physics' first output,
# node by node (the solvers share one mesh), within 1 %; modal steps their frequencies.
DEFAULT_TOLERANCE = 0.01
REPORTS = "shootout"  # results/shootout/<study id>.json: the latest report, kept


def default_metrics(project: Project, study: Study) -> list[ComparisonMetric]:
    from puzzle.model.catalog import OUTPUTS
    from puzzle.model.schema import Component

    comp = project.objects.get(study.component)
    out: list[ComparisonMetric] = []
    if any(s.type in ("modal", "buckling") for s in study.solve_steps()):
        out.append(ComparisonMetric(quantity="eigenfrequencies", norm="relative_value",
                                    tolerance=DEFAULT_TOLERANCE))  # fmt: skip
        return out
    physics = [p.type for p in comp.physics if any(s.solves(p.type) for s in
               study.solve_steps())] if isinstance(comp, Component) else []  # fmt: skip
    for p in physics:
        if OUTPUTS.get(p):
            out.append(ComparisonMetric(quantity=OUTPUTS[p][0], norm="relative_l2",
                                        tolerance=DEFAULT_TOLERANCE))  # fmt: skip
    return out


def shootout_report(
    project: Project,
    study_id,
    root: Path,
    workdir: Path,
    solvers: list[str] | None = None,
    *,
    store: ResultStore | None = None,
    on_output: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
) -> dict:
    """The study's solver shoot-out (ADR 0011) as plain data: the GUI's Run shoot-out…,
    MCP `solver_shootout` and `puzzle run --shootout` all call this through the
    session's `run_shootout` command. `solvers` (the others to run beside the study's
    own) replace the study's `shootout.solvers` for this run; with no metrics on the
    study, `default_metrics`. Each run is stored and reused as any run is; the report
    is kept as `results/shootout/<study id>.json`."""
    import copy
    import json
    from datetime import UTC, datetime

    from puzzle.labels import solver_label
    from puzzle.model.schema import Shootout

    study = project.get(study_id, Study)
    chosen = solvers if solvers else (study.shootout.solvers if study.shootout else [])
    chosen = [s for s in dict.fromkeys(chosen) if s != study.solver]
    if not chosen:
        raise StudyError("a shoot-out needs a solver beside the study's own: name one",
                         code="invalid", path=study.name)  # fmt: skip
    metrics = (study.shootout.metrics if study.shootout else []) or default_metrics(project, study)
    if not metrics:
        raise StudyError("nothing to compare: give the shoot-out a metric", code="invalid",
                         path=study.name)  # fmt: skip
    work = copy.deepcopy(project)
    work.get(study.id, Study).shootout = Shootout(solvers=chosen, metrics=metrics)
    out = run_shootout(work, study.id, root, workdir, store=store, on_output=on_output,
                       timeout_s=timeout_s)  # fmt: skip
    report = {
        "study": study.name,
        "primary": study.solver,
        "passed": out.passed,
        "runs": {
            s: {"solver": solver_label(s), "run_id": r.run_id, "cached": r.cached}
            for s, r in out.runs.items()
        },  # fmt: skip
        "comparisons": [
            {
                "solver": c.solver,
                "quantity": c.metric.quantity,
                "norm": c.metric.norm,
                "value": c.value,
                "tolerance": c.metric.tolerance,
                "passed": c.passed,
            }
            for c in out.comparisons
        ],  # fmt: skip
        "settings": out.settings,
        "table": out.table(),
        "made": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if store is not None:
        from puzzle.project import atomic

        path = Path(store.root) / REPORTS / f"{study.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(report, indent=2), encoding="utf-8")
    return report


def kept_report(root: Path, study_id) -> dict | None:
    """The latest shoot-out report kept for a study, or None."""
    import json

    from puzzle.project.layout import RESULTS_DIR

    path = Path(root) / RESULTS_DIR / REPORTS / f"{study_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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


def mesh_shootout_report(project: Project, study_id, root: Path, workdir: Path,
                         quantities: list[str] | None = None, tolerance: float = 0.02, *,
                         on_output: Callable[[str], None] | None = None) -> dict:  # fmt: skip
    """A mesh shoot-out as plain data: MCP `mesh_shootout` and the GUI's Mesh
    shoot-out… both call this, so what they ask and show can't drift. `quantities`
    are result fields compared by their peak (or a global value), or
    "eigenfrequencies"; displacement by default."""
    metrics = [ComparisonMetric(quantity=q, norm="relative_value", tolerance=tolerance)
               for q in (quantities or ["displacement"])]  # fmt: skip
    out = run_mesh_shootout(project, study_id, root, workdir, metrics, on_output=on_output)
    return {
        "passed": out.passed,
        "comparisons": [
            {"quantity": c.metric.quantity, "relative_difference": c.value, "passed": c.passed}
            for c in out.comparisons
        ],
    }
