"""Mesh convergence (ADR 0066 phase 1): a study run on a sequence of meshes, each finer
than the last by a ratio, with its quantities of interest read at each, and Roache's
grid convergence index from the last three: the observed order, the extrapolated
value, the GCI and a verdict ("converged within 0.3 %", "not converged: refine",
"oscillating: not in the asymptotic range").

Each level is the study's own run, stored with its labels ("mesh": "level 2: 4 mm") and
cached by its run key, so a convergence check that stopped resumes, and its runs open
as any run does. A level's mesh is the recipe with every size divided by ratio^k (its
global size, each control's size; transfinite counts multiplied; Gmsh's size factor
when the recipe has no global size).

Quantities are operators or result tools (as optimisation outputs are, ADR 0063), or,
when none are given, the maximum of the study's first result (its total deformation,
its temperature).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puzzle.errors import PuzzleError

SAFETY = 1.25  # Roache's factor of safety for three or more meshes
ORDER_RANGE = (0.5, 6.0)  # an observed order outside it: not in the asymptotic range


@dataclass
class Level:
    """One mesh of a convergence study: its scale, nodes, elements, run and values."""

    index: int
    factor: float  # sizes divided by this
    label: str
    run: str = ""
    nodes: int = 0
    elements: int = 0
    values: dict[str, float | None] = field(default_factory=dict)
    reason: str = ""
    kinds: dict[str, str | None] = field(default_factory=dict)  # each value's unit kind

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "factor": self.factor, "label": self.label,
                "run": self.run, "nodes": self.nodes, "elements": self.elements,
                "values": self.values, "kinds": self.kinds, "reason": self.reason}  # fmt: skip


@dataclass
class Verdict:
    """A quantity's convergence from the last three levels (Roache, GCI)."""

    quantity: str
    order: float | None = None
    extrapolated: float | None = None
    gci: float | None = None  # a share of the finest value (0.003 = 0.3 %)
    state: str = "too few levels"
    words: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"quantity": self.quantity, "order": self.order,
                "extrapolated": self.extrapolated, "gci": self.gci, "state": self.state,
                "words": self.words}  # fmt: skip


def scaled(project, study, factor: float):
    """The project with the study's mesh recipe refined: sizes divided by `factor`."""
    from puzzle.model.schema import Component, MeshRecipe

    comp = project.get(study.component, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    data = recipe.model_dump(mode="json")

    def smaller(q):
        return {"value": q["value"] / factor, "unit": q["unit"]} if q else q

    if data.get("global_size"):
        data["global_size"] = smaller(data["global_size"])
    elif recipe.mesher == "gmsh":
        opts = dict(data.get("gmsh_options") or {})
        opts["Mesh.MeshSizeFactor"] = float(opts.get("Mesh.MeshSizeFactor", 1.0)) / factor
        data["gmsh_options"] = opts
    else:
        raise PuzzleError(f"mesh '{recipe.name}' has no global size to refine: give it one",
                          code="inconsistent")  # fmt: skip
    for c in data.get("controls", []):
        if c.get("size"):
            c["size"] = smaller(c["size"])
        for key in ("count", "counts"):
            if isinstance(c.get(key), int):
                c[key] = max(1, math.ceil(c[key] * factor))
    return _replace(project, recipe.id, MeshRecipe.model_validate(data))


def _replace(project, oid, obj):
    import dataclasses

    return dataclasses.replace(project, objects={**project.objects, oid: obj})


def _label(study_project, study, factor: float) -> str:
    from puzzle.model.schema import Component, MeshRecipe

    comp = study_project.get(study.component, Component)
    recipe = study_project.get(comp.mesh, MeshRecipe)
    if recipe.global_size is not None:
        q = recipe.global_size
        return f"{q.value:.4g} {q.unit}"
    return f"size × {1 / factor:.3g}"


def levels(project, study, count: int, ratio: float) -> list[Level]:
    """The planned levels, coarsest first: factor ratio^k for k = 0 .. count − 1."""
    out = []
    for k in range(count):
        f = ratio**k
        out.append(Level(k + 1, f, f"level {k + 1}: {_label(scaled(project, study, f), study, f)}"))
    return out


def _quantities(project, outputs) -> list[tuple[str, Any]]:
    names = {o.id: o.name for o in project.objects.values()}
    return [(names.get(o.source, str(o.source)), o) for o in outputs]


def _read(project, store, study, level: Level, outputs) -> dict[str, float | None]:
    """A level's quantities: each output read at its run (by its point labels), or the
    maximum of the study's first result. Their unit kinds go in `level.kinds`."""
    from puzzle.operators import evaluate as ev

    title = f"mesh = {level.label}"
    ctx = ev.Context(project, store, point=title)
    if outputs:
        out = {}
        for name, o in _quantities(project, outputs):
            got = ev.source(ctx, o.source, o.port)
            out[name] = got.margin if isinstance(got, ev.Verdict) else got.value
            level.kinds[name] = None if isinstance(got, ev.Verdict) else got.kind
        return out
    from puzzle.post.quantities import compute, default

    res = ev.source(ctx, study.id, "result")
    item = default(res.items)
    step = res.results.steps[-1]
    f = step.fields[item.field]
    vals = compute(item, f.values, f.components)
    name = f"Largest {item.label.lower()}"
    level.kinds[name] = item.unit
    return {name: float(max(vals))}


def _stored(store, study, key: str):
    return next((r for r in reversed(store.runs()) if r.study_id == str(study.id)
                 and r.status == "completed" and r.run_key == key), None)  # fmt: skip


def run(project, study, root: Path, workdir: Path, *, store, count: int = 3,
        ratio: float = 1.5, outputs=(), solve=None, on_output=None, cancel=None,
        read_only: bool = False) -> list[Level]:  # fmt: skip
    """Solve (or, `read_only`, read back) each level not run yet; read its quantities."""
    from puzzle.project.graph import run_hashes

    if solve is None and not read_only:
        from puzzle.solve import run_study as solve
    if count < 2:
        raise PuzzleError("a convergence check needs two meshes or more", code="invalid")
    if ratio <= 1:
        raise PuzzleError("the refinement ratio is above 1 (each mesh finer)", code="invalid")
    planned = levels(project, study, count, ratio)
    for lv in planned:
        at = scaled(project, study, lv.factor)
        key = run_hashes(at)[study.id][study.solver][0]
        record = _stored(store, study, key) if store is not None else None
        if record is None:
            if read_only:
                lv.reason = "not run yet"
                continue
            if cancel is not None and cancel.is_set():
                lv.reason = "stopped before it ran"
                continue
            if on_output:
                on_output(f"--- mesh convergence: {lv.label}")
            try:
                got = solve(at, study.id, root, workdir / f"level-{lv.index}", store=store,
                            _point=(key, {"mesh": lv.label}), cancel=cancel)  # fmt: skip
            except PuzzleError as exc:
                lv.reason = f"{exc.code}: {exc.message}"
                continue
            record = next((r for r in store.runs() if r.run_id == got.run_id), None)
        if record is None:
            continue
        lv.run = record.run_id
        mesh = store.read_mesh(record.mesh_id)
        lv.nodes = int(mesh.node_ids.size)
        lv.elements = sum(len(b.ids) for b in mesh.blocks if b.dim == max(
            (x.dim for x in mesh.blocks), default=3))  # fmt: skip
        try:
            lv.values = _read(at, store, study, lv, outputs)
        except PuzzleError as exc:
            lv.reason = exc.message
    return planned


def verdicts(levels_: list[Level], target: float = 0.02, dim: int = 3) -> list[Verdict]:
    """Each quantity's observed order, extrapolated value and GCI from the three finest
    levels that ran (Roache's method: the refinement ratios from the element counts,
    r = (N_fine / N_coarse)^(1/dim), the order solved by fixed-point iteration)."""
    done = [lv for lv in levels_ if lv.run and lv.values]
    names = list(done[0].values) if done else []
    out = []
    for name in names:
        v = Verdict(name)
        series = [(lv.elements, lv.values.get(name)) for lv in done
                  if lv.values.get(name) is not None]  # fmt: skip
        if len(series) < 3:
            v.words = "run three meshes or more to judge it"
            out.append(v)
            continue
        (n3, f3), (n2, f2), (n1, f1) = series[-3:]  # coarse, medium, fine
        r21 = (n1 / n2) ** (1 / dim)
        r32 = (n2 / n3) ** (1 / dim)
        e21, e32 = f2 - f1, f3 - f2
        if e21 == 0:
            v.order, v.extrapolated, v.gci, v.state = None, f1, 0.0, "converged"
            v.words = "unchanged between the two finest meshes"
            out.append(v)
            continue
        ratio = e32 / e21
        s = 1.0 if ratio > 0 else -1.0
        if s < 0:
            v.state = "oscillating"
            v.words = ("the values go up and down between meshes: not in the asymptotic "
                       "range; refine further")  # fmt: skip
            out.append(v)
            continue
        p = abs(math.log(abs(ratio))) / math.log(r21)
        for _ in range(50):  # p = |ln|e32/e21| + q(p)| / ln r21
            q = math.log((r21**p - s) / (r32**p - s))
            new = abs(math.log(abs(ratio)) + q) / math.log(r21)
            if abs(new - p) < 1e-8:
                break
            p = new
        v.order = p
        v.extrapolated = (r21**p * f1 - f2) / (r21**p - 1)
        rel = abs((f1 - f2) / f1) if f1 else abs(f1 - f2)
        v.gci = SAFETY * rel / (r21**p - 1)
        if not ORDER_RANGE[0] <= p <= ORDER_RANGE[1]:
            v.state = "not in the asymptotic range"
            v.words = (f"observed order {p:.2g}: the meshes are too coarse to judge it; "
                       "refine further")  # fmt: skip
        elif v.gci <= target:
            v.state = "converged"
            v.words = f"converged within {v.gci * 100:.2g} % (GCI), order {p:.2g}"
        else:
            v.state = "not converged"
            v.words = f"GCI {v.gci * 100:.2g} %, above {target * 100:g} %: refine"
        out.append(v)
    return out


def settings(project, name: str):
    """The study and its convergence check from a study's or a mesh study's name: a mesh
    study's own settings, else the study's `convergence`, else the defaults."""
    from puzzle.commands import resolve
    from puzzle.model.schema import Convergence, MeshStudy, Study

    found = next((m for m in project.of_type(MeshStudy) if m.name == name), None)
    if found is not None:
        return project.get(found.study, Study), found.check()
    study = resolve(project, name, Study)
    return study, study.convergence or Convergence()


def report(levels_: list[Level], verdicts_: list[Verdict], target: float) -> dict[str, Any]:
    """Levels and verdicts as plain data (CLI --json, MCP)."""
    return {
        "target": target,
        "levels": [lv.to_dict() for lv in levels_],
        "verdicts": [v.to_dict() for v in verdicts_],
        "converged": bool(verdicts_) and all(v.state == "converged" for v in verdicts_),
    }


def lines(levels_: list[Level], verdicts_: list[Verdict]) -> list[str]:
    """Levels and verdicts as text for a terminal."""
    out = []
    for lv in levels_:
        vals = ", ".join(f"{k} = {v:.6g}" for k, v in lv.values.items() if v is not None)
        state = f"{lv.elements} elements, {lv.nodes} nodes: {vals}" if lv.run else lv.reason
        out.append(f"{lv.label}: {state}")
    for v in verdicts_:
        extra = f" (extrapolated {v.extrapolated:.6g})" if v.extrapolated is not None else ""
        out.append(f"{v.quantity}: {v.words}{extra}")
    return out
