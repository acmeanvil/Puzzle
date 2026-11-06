"""Adaptive refinement (ADR 0066 item 4, phase 3): solve → estimate → new sizes, pass
after pass, until the error is at the target.

Pass 1 is the study on its own recipe. Each later pass meshes the same recipe plus one
`size_field` control from the pass before's run: each element's new size is its old
one × (target / its error)^(1/p), at most 4× finer and 2× coarser in one pass, within
the recipe's sizes. Each pass is the study's own stored run, labelled `adapt: pass 2`
and cached by its run key (its sizes come from the run before, by id), so an adaptive
run that stopped resumes.

It stops when the criterion holds (`energy`: the energy-norm error at or under the
target; `quantity`: each quantity's change from the pass before at or under it), or at
the budget (passes, elements). The last pass is the result. Earlier passes are kept
for review by default; `keep_passes = False` deletes their runs when it finishes,
keeping their table (`results/adapt/<study id>.json`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puzzle.errors import PuzzleError


@dataclass
class Pass:
    """One pass: its run, mesh size, error and quantities."""

    index: int
    run: str = ""
    nodes: int = 0
    elements: int = 0
    error: float | None = None  # the energy-norm relative error (η)
    values: dict[str, float | None] = field(default_factory=dict)
    kinds: dict[str, str | None] = field(default_factory=dict)
    reason: str = ""

    @property
    def label(self) -> str:
        return f"pass {self.index}"

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "run": self.run, "nodes": self.nodes,
                "elements": self.elements, "error": self.error, "values": self.values,
                "kinds": self.kinds, "reason": self.reason}  # fmt: skip


@dataclass
class Outcome:
    passes: list[Pass]
    ended: str  # why it stopped, in words

    @property
    def converged(self) -> bool:
        return self.ended.startswith("converged")

    def to_dict(self) -> dict[str, Any]:
        return {"passes": [p.to_dict() for p in self.passes], "ended": self.ended,
                "converged": self.converged}  # fmt: skip


def refined(project, study, source_run: str, settings):
    """The project with the study's recipe plus a size field from `source_run`."""
    from puzzle.convergence import _replace
    from puzzle.model.schema import Component, MeshRecipe, SizeFieldControl

    comp = project.get(study.component, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    control = SizeFieldControl(run=source_run, target=settings.target, field=settings.field,
                               finer=settings.finer, coarser=settings.coarser)  # fmt: skip
    new = recipe.model_copy(update={"controls": [*recipe.controls, control]})
    return _replace(project, recipe.id, new)


def _mmg_key(base: str, source_run: str, a) -> str:
    """An MMG pass's run key: the study's own, the run its metric came from, and the
    settings that shape it."""
    import hashlib

    text = json.dumps([base, source_run, "mmg", a.target, a.field, a.finer, a.stretch])
    return hashlib.sha256(text.encode()).hexdigest()


SCALARS = ("temperature", "pressure", "electric_potential")
VECTORS = ("displacement", "velocity")


def adaptation_field(results, mesh, name: str | None):
    """The nodal scalar MMG adapts to: `name`'s values (a vector's magnitude), or the
    run's own displacement, temperature or velocity; cell values averaged to nodes."""
    import numpy as np

    from puzzle.post.estimates import nodal

    step = results.steps[-1]
    name = name or next((n for n in (*VECTORS, *SCALARS) if n in step.fields), None)
    if name is None or name not in step.fields:
        raise PuzzleError("no field to adapt to: name one (adapt.field)", code="invalid")
    f = step.fields[name]
    if f.location == "element":
        f = nodal(mesh, f)
    vals = f.at(mesh.node_ids)
    return np.linalg.norm(vals, axis=1) if vals.shape[1] > 1 else vals[:, 0]


def _mmg_pass(project, study, store, source_run: str, a, workdir: Path):
    """The next pass's mesh from the source run by MMG, as a MeshResult."""
    import numpy as np

    from puzzle.mesh import mmg
    from puzzle.mesh.generate import MeshResult, _model_size  # noqa: F401
    from puzzle.model.schema import Component, MeshRecipe

    record = next(r for r in store.runs() if r.run_id == source_run)
    mesh = store.read_mesh(record.mesh_id)
    values = adaptation_field(store.read_results(source_run), mesh, a.field)
    comp = project.get(study.component, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    span = float(np.linalg.norm(np.ptp(mesh.coords, axis=0)))
    hmax = recipe.global_size.to_si() if recipe.global_size else span / 10
    new = mmg.adapt_mesh(mesh, values, workdir / "mmg", target=a.target,
                         hmin=hmax / (a.finer**2 * 4), hmax=hmax,
                         order=recipe.element_order, stretch=a.stretch)  # fmt: skip
    return MeshResult(mesh=new, path=workdir / "mmg", min_quality=mmg.quality(new))


def _stored(store, study, key: str):
    return next((r for r in reversed(store.runs()) if r.study_id == str(study.id)
                 and r.status == "completed" and r.run_key == key), None)  # fmt: skip


def _read(project, store, study, p: Pass, outputs) -> None:
    from puzzle.operators import evaluate as ev

    ctx = ev.Context(project, store, point=f"adapt = {p.label}")
    for o in outputs:
        names = {x.id: x.name for x in project.objects.values()}
        name = names.get(o.source, str(o.source))
        got = ev.source(ctx, o.source, o.port)
        p.values[name] = got.margin if isinstance(got, ev.Verdict) else got.value
        p.kinds[name] = None if isinstance(got, ev.Verdict) else got.kind


def run(project, study, root: Path, workdir: Path, *, store, settings=None, solve=None,
        on_output=None, cancel=None, read_only: bool = False) -> Outcome:  # fmt: skip
    """Solve the passes not run yet (or, `read_only`, read back those that ran)."""
    from puzzle.model.schema import Adapt
    from puzzle.post import estimates
    from puzzle.project.graph import run_hashes

    a = settings or study.adapt or Adapt()
    if solve is None and not read_only:
        from puzzle.solve import run_study as solve
    passes: list[Pass] = []
    previous: str | None = None
    ended = f"stopped at the budget of {a.passes} passes"
    for k in range(1, a.passes + 1):
        p = Pass(k)
        passes.append(p)
        meshed = None
        if previous is None:
            at = project
            key = run_hashes(at)[study.id][study.solver][0]
        elif a.method == "anisotropic":  # ADR 0066 phase 4: MMG along the Hessian
            at = project
            key = _mmg_key(run_hashes(at)[study.id][study.solver][0], previous, a)
        else:
            at = refined(project, study, previous, a)
            key = run_hashes(at)[study.id][study.solver][0]
        record = _stored(store, study, key)
        if record is None:
            if read_only:
                p.reason = "not run yet"
                ended = f"{k - 1} of up to {a.passes} passes run"
                break
            if cancel is not None and cancel.is_set():
                p.reason = "stopped before it ran"
                ended = "stopped"
                break
            if on_output:
                on_output(f"--- adaptive refinement: {p.label}")
            try:
                if previous is not None and a.method == "anisotropic":
                    meshed = _mmg_pass(at, study, store, previous, a, workdir / f"pass-{k}")
                got = solve(at, study.id, root, workdir / f"pass-{k}", store=store,
                            _point=(key, {"adapt": p.label}), cancel=cancel,
                            **({"meshed": meshed} if meshed is not None else {}))  # fmt: skip
            except PuzzleError as exc:
                p.reason = f"{exc.code}: {exc.message}"
                ended = f"{p.label} failed: {exc.message}"
                break
            record = next((r for r in store.runs() if r.run_id == got.run_id), None)
            if record is None:
                p.reason = "no run was stored"
                ended = f"{p.label} stored nothing"
                break
        p.run = record.run_id
        mesh = store.read_mesh(record.mesh_id)
        p.nodes = int(mesh.node_ids.size)
        p.elements = sum(len(b.ids) for b in mesh.blocks if b.dim == 3)
        est = estimates.of_run(at, store, record.run_id, field_name=a.field)
        p.error = est.relative if est.physics != "field" else None
        try:
            _read(at, store, study, p, a.quantities)
        except PuzzleError as exc:
            p.reason = exc.message
        previous = record.run_id
        done = _done(a, passes)
        if done:
            ended = done
            break
        if a.max_elements and p.elements >= a.max_elements:
            ended = f"stopped at the budget of {a.max_elements} elements"
            break
    out = Outcome(passes, ended)
    if not read_only:
        _keep_table(root, study, out)
        if not a.keep_passes:
            for p in passes[:-1]:
                if p.run:
                    store.delete_run(p.run)
    return out


def _done(a, passes: list[Pass]) -> str:
    """Why to stop now, or ""."""
    last = passes[-1]
    if a.criterion == "energy":
        if last.error is not None and last.error <= a.target:
            return f"converged: error {last.error * 100:.2g} % ≤ {a.target * 100:g} %"
        return ""
    if len(passes) < 2 or not last.values:
        return ""
    before = passes[-2].values
    changes = []
    for name, v in last.values.items():
        b = before.get(name)
        if v is None or b is None:
            return ""
        changes.append(abs(v - b) / abs(v) if v else abs(v - b))
    if max(changes) <= a.target:
        return (f"converged: the quantities changed {max(changes) * 100:.2g} % ≤ "
                f"{a.target * 100:g} %")  # fmt: skip
    return ""


def _keep_table(root: Path, study, out: Outcome) -> None:
    """The passes' table, kept when their runs are deleted (ADR 0066 review answer 5)."""
    from puzzle.project.layout import RESULTS_DIR

    folder = Path(root) / RESULTS_DIR / "adapt"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{study.id}.json").write_text(json.dumps(out.to_dict(), indent=1), encoding="utf-8")


def lines(out: Outcome) -> list[str]:
    """The passes as text for a terminal."""
    rows = []
    for p in out.passes:
        if not p.run:
            rows.append(f"{p.label}: {p.reason}")
            continue
        err = f", error {p.error * 100:.3g} %" if p.error is not None else ""
        vals = "".join(f", {k} = {v:.6g}" for k, v in p.values.items() if v is not None)
        rows.append(f"{p.label}: {p.elements} elements, {p.nodes} nodes{err}{vals}")
    rows.append(out.ended)
    return rows
