"""Evaluating operators (ADR 0062): each output's value from what is wired in.

Values are plain dataclasses, one per port type: `Number` (SI, with its unit's kind),
`Table`, `FieldValue` (a listed result per node or element), `ResultValue` (a run or a
derived result), `Verdict`, and `Text` for a typed text. Nothing is stored: a value is
computed from the stored runs each time it is asked for, as derived results are.

A `Context` carries the store and, for a Plot, the step or sweep point being read: the
same chain of operators is evaluated once per step or point, with every study it reads
taking that point's run.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.operators import TYPES

REDUCTIONS = {"max": "Maximum", "min": "Minimum", "mean": "Mean", "integral": "Integral"}


# --- values ---------------------------------------------------------------------------


@dataclass
class Number:
    value: float
    kind: str | None = None  # model.units.DIMENSIONS key, None if dimensionless or unknown
    label: str = ""
    point: list[float] | None = None  # where a maximum or minimum is
    percent: bool = False  # a fraction shown as a percentage (a check's margin)

    type = "number"

    def text(self) -> str:
        if self.percent:
            return f"{self.value:+.0%}" if np.isfinite(self.value) else str(self.value)
        return quantity_text(self.value, self.kind)


@dataclass
class Text:
    text_: str
    type = "text"

    def text(self) -> str:
        return self.text_


@dataclass
class Table:
    columns: list[str]
    rows: list[list[Any]]
    kinds: list[str | None] = field(default_factory=list)

    type = "table"

    def text(self) -> str:
        return f"{len(self.rows)} row{'s' if len(self.rows) != 1 else ''}: " + ", ".join(
            self.columns
        )

    def csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        kinds = self.kinds or [None] * len(self.columns)
        w.writerow([_heading(c, k) for c, k in zip(self.columns, kinds, strict=False)])
        for r in self.rows:
            w.writerow([f"{v:.9g}" if isinstance(v, float) else v for v in r])
        return buf.getvalue()


@dataclass
class FieldValue:
    mesh: Any
    step: Any  # the StepResult read
    item: Any  # the listed result (post.quantities.Item)
    ids: np.ndarray
    values: np.ndarray  # NaN where left out
    location: str  # "node" or "element"
    selection: str | None = None  # kept on this selection only
    label: str = ""

    type = "field"

    def text(self) -> str:
        kept = int(np.isfinite(self.values).sum())
        what = "nodes" if self.location == "node" else "elements"
        return f"{self.label}: {kept} of {len(self.values)} {what}"


@dataclass
class ResultValue:
    mesh: Any
    results: Any
    items: list
    run_ids: list[str]
    label: str
    field: str | None = None  # a study's field port: that field's results only

    type = "result"

    def text(self) -> str:
        return self.label


@dataclass
class Verdict:
    passed: bool
    value: float
    limit: float
    compare: str  # "at_most" or "at_least"
    kind: str | None = None

    type = "verdict"

    @property
    def margin(self) -> float:
        """How far inside the limit, as a fraction: limit / value − 1 for at most (0.25:
        25 % to spare), value / limit − 1 for at least; negative when it fails."""
        a, b = (self.limit, self.value) if self.compare == "at_most" else (self.value, self.limit)
        if b == 0:
            return float("inf") if a > 0 else (0.0 if a == 0 else float("-inf"))
        return a / b - 1

    def short(self) -> str:
        """What the node shows: "Pass, margin +24 %"."""
        margin = f"{self.margin:+.0%}" if np.isfinite(self.margin) else str(self.margin)
        return f"{'Pass' if self.passed else 'Fail'}, margin {margin}"

    def comparison(self) -> str:
        sign = "≤" if self.compare == "at_most" else "≥"
        value, limit = quantity_text(self.value, self.kind), quantity_text(self.limit, self.kind)
        return f"{value} {sign} {limit}"

    def text(self) -> str:
        return f"{self.short()}: {self.comparison()}"


def quantity_text(value: float, kind: str | None) -> str:
    """A value in the display units (ADR 0024): "212 MPa" with the engineering system."""
    from puzzle.model.units import DIMENSIONS, pretty, shown
    from puzzle.settings import current

    if not np.isfinite(value):
        return str(value)
    if kind is None or kind not in DIMENSIONS:
        return f"{value:.4g}"
    v, unit = shown(value, DIMENSIONS[kind][1], current().display_units())
    return f"{v:.4g} {pretty(unit)}"


def _heading(column: str, kind: str | None) -> str:
    from puzzle.model.units import DIMENSIONS

    return f"{column} [{DIMENSIONS[kind][1]}]" if kind in DIMENSIONS else column


# --- context --------------------------------------------------------------------------


@dataclass
class Context:
    project: Any
    store: Any
    point: str | None = None  # a sweep point's title: every study read takes that run
    step: int | None = None  # every result read takes this step index
    memo: dict = field(default_factory=dict)

    def at(self, *, point: str | None = None, step: int | None = None) -> Context:
        return replace(self, point=point if point is not None else self.point,
                       step=step if step is not None else self.step, memo={})  # fmt: skip


_READ: dict[tuple[str, str], tuple] = {}


def _read(store, record):
    """A run's mesh and results; a run never changes, so the last few are kept."""
    key = (str(getattr(store, "root", "")), record.run_id)
    if key not in _READ:
        if len(_READ) >= 8:
            _READ.pop(next(iter(_READ)))
        _READ[key] = (store.read_mesh(record.mesh_id), store.read_results(record.run_id))
    return _READ[key]


# --- evaluation -----------------------------------------------------------------------


def value(ctx: Context, op, port: str = "out"):
    """The value of `op`'s output `port`."""
    got = outputs(ctx, op)
    if port not in got:
        raise PuzzleError(f"'{op.name}' has no output '{port}'", code="not_found")
    return got[port]


def outputs(ctx: Context, op) -> dict[str, Any]:
    """Every output of `op`, by port."""
    key = ("op", op.id)
    if key in ctx.memo:
        return ctx.memo[key]
    out = _EVALUATE[op.type](ctx, op)
    ctx.memo[key] = out
    return out


def source(ctx: Context, oid: UUID, port: str):
    """The value an object offers on its output `port`."""
    from puzzle.model.schema import (
        Operator,
        ParameterSet,
        ResultDefinition,
        ResultProbe,
        Study,
    )
    from puzzle.post import derived

    obj = ctx.project.objects.get(oid)
    if obj is None:
        raise PuzzleError(f"object {oid} is gone", code="not_found")
    if isinstance(obj, Operator):
        return value(ctx, obj, port)
    if isinstance(obj, ParameterSet):
        from puzzle.model.units import kind_of

        q = obj.values.get(port)
        if q is None:
            raise PuzzleError(f"parameter set '{obj.name}' has no '{port}'", code="not_found")
        unit = q.unit if q.unit != "dimensionless" else ""
        return Number(q.to_si(), kind_of(unit) if unit else None, port)
    if isinstance(obj, Study):
        key = ("run", oid, ctx.point)
        if key not in ctx.memo:
            record = derived.run_for(ctx.project, ctx.store, oid, ctx.point)
            mesh, results = _read(ctx.store, record)
            ctx.memo[key] = ResultValue(mesh, results, derived._items_of(results),
                                        [record.run_id], obj.name)  # fmt: skip
        found = ctx.memo[key]
        return replace(found, field=None if port in ("result", "out") else port)
    if isinstance(obj, ResultDefinition):
        d = derived.evaluate(ctx.project, ctx.store, obj)
        return ResultValue(d.mesh, d.results, d.items, d.run_ids, obj.name)
    if isinstance(obj, ResultProbe):
        return _probe(ctx, obj)
    raise PuzzleError(f"'{obj.name}' has no outputs for an operator", code="invalid")


def _input(ctx: Context, op, key: str):
    spec = TYPES[op.type].input(key)
    inp = op.inputs.get(key)
    if inp is None or (inp.source is None and inp.value is None):
        if spec is not None and spec.required:
            raise PuzzleError(
                f"{TYPES[op.type].label.lower()} '{op.name}': its {spec.label.lower()} input "
                "is not wired", code="inconsistent", path=op.name)  # fmt: skip
        return None
    if inp.source is not None:
        return source(ctx, inp.source, inp.port)
    return typed(inp.value)


def typed(v) -> Number | Text:
    """A value typed into an input: a quantity (SI, with its kind), a number, a text."""
    from puzzle.model.schema import Quantity
    from puzzle.model.units import kind_of

    if isinstance(v, Quantity):
        unit = v.unit if v.unit != "dimensionless" else ""
        return Number(v.to_si(), kind_of(unit) if unit else None)
    if isinstance(v, (int, float)):
        return Number(float(v))
    return Text(str(v))


def _probe(ctx: Context, probe) -> Number:
    """A result tool's value (ADR 0032, 0042) at the context's step and point."""
    from puzzle.model.schema import Study
    from puzzle.post import probes

    probe = probes.placed(ctx.project, probe)
    src = ctx.project.objects.get(probe.source)
    if isinstance(src, Study):
        r = source(ctx, src.id, "result")
    else:
        r = source(ctx, probe.source, "result")
    item = probes._item(probe, r.items)
    step = _step(ctx, r.results, item, probe.step)
    found = probes.at_step(r.mesh, step, item, probe)
    return Number(float(found["value"]), item.unit, probe.name, found.get("point"))


def _step(ctx: Context, results, item, index: int):
    """The step a result is read at: the context's (a Plot's), else `index` among the
    steps holding the item's field (-1: the last)."""
    if ctx.step is not None:
        if not 0 <= ctx.step < len(results.steps):
            raise PuzzleError(f"no step {ctx.step + 1}", code="not_found")
        step = results.steps[ctx.step]
        if item.field not in step.fields:
            raise PuzzleError(f"no {item.label} at step {ctx.step + 1}", code="not_found")
        return step
    steps = [s for s in results.steps if item.field in s.fields]
    if not steps:
        raise PuzzleError(f"no step has {item.label}", code="not_found")
    return steps[min(index, len(steps) - 1)] if index >= 0 else steps[max(index, -len(steps))]


def _field(ctx: Context, op, given) -> FieldValue:
    """A result wired in as a field: the operator's quantity (else the field's first
    listed result) at its step."""
    from puzzle.post.quantities import compute, default

    if isinstance(given, FieldValue):
        return given
    if not isinstance(given, ResultValue):
        raise PuzzleError(f"'{op.name}' needs a result wired in", code="invalid")
    items = given.items
    if given.field:
        items = [i for i in items if i.field == given.field] or items
    if op.quantity:
        item = next((i for i in given.items if i.key == op.quantity), None)
        if item is None:
            raise PuzzleError(
                f"'{op.name}': {given.label} has no '{op.quantity}'", code="not_found",
                details={"quantities": [i.key for i in items]})  # fmt: skip
    else:
        item = default(items)
        if item is None:
            raise PuzzleError(f"'{op.name}': {given.label} has no results", code="not_found")
    step = _step(ctx, given.results, item, op.step)
    f = step.fields[item.field]
    values = np.asarray(compute(item, f.values, f.components), float)
    return FieldValue(given.mesh, step, item, f.ids, values, f.location, label=item.label)


def _restrict(fv: FieldValue, selection: str | None) -> FieldValue:
    """The field with NaN outside `selection`."""
    if not selection:
        return fv
    from puzzle.post.probes import _group

    group = _group(fv.mesh, selection)
    if fv.location == "element":
        cells = [b.ids for b in group.blocks(fv.mesh) if b.dim == group.dim]
        keep = np.isin(fv.ids, np.concatenate(cells) if cells else [])
    else:
        keep = np.isin(fv.ids, group.node_ids)
    if not keep.any():
        raise PuzzleError(f"'{selection}' has no {fv.label} values", code="not_found")
    values = np.where(keep, fv.values, np.nan)
    if fv.selection and fv.selection != selection:
        selection = fv.selection  # an earlier filter's region still bounds the integral
    return replace(fv, values=values, selection=selection)


def _blocks(fv: FieldValue):
    """The elements a mean or an integral runs over: the selection's (faces or bodies),
    else every part's elements of the model's own dimension."""
    mesh = fv.mesh
    if fv.selection:
        from puzzle.post.probes import _group

        group = _group(mesh, fv.selection)
        return [b for b in group.blocks(mesh) if b.dim == group.dim and b.dim >= 2]
    dims = [g.dim for g in mesh.groups.values() if "/" not in g.name]
    dim = max(dims) if dims else max((b.dim for b in mesh.blocks), default=3)
    keys = {e for g in mesh.groups.values() if "/" not in g.name and g.dim == dim
            for e in g.entities}  # fmt: skip
    return [b for b in mesh.blocks if b.dim == dim and (not keys or b.entity in keys)]


def _reduce(ctx: Context, op) -> dict[str, Any]:
    fv = _restrict(_field(ctx, op, _input(ctx, op, "result")), op.selection)
    finite = np.isfinite(fv.values)
    if not finite.any():
        raise PuzzleError(f"'{op.name}': nothing left to reduce", code="not_found")
    label = f"{REDUCTIONS[op.reduction]} {fv.label[:1].lower()}{fv.label[1:]}"
    if op.reduction in ("max", "min"):
        pick = np.where(finite, fv.values, -np.inf if op.reduction == "max" else np.inf)
        i = int(np.argmax(pick) if op.reduction == "max" else np.argmin(pick))
        where = (fv.mesh.node_coords(fv.ids[i : i + 1])[0].tolist()
                 if fv.location == "node" else None)  # fmt: skip
        return {"out": Number(float(fv.values[i]), fv.item.unit, label, where)}
    from puzzle.results.query import _measures, _weights

    if fv.location != "node":
        if op.reduction == "integral":
            raise PuzzleError(f"'{op.name}': an integral needs values per node",
                              code="unsupported")  # fmt: skip
        return {"out": Number(float(np.nanmean(fv.values)), fv.item.unit, label)}
    lookup = np.full(int(fv.mesh.node_ids.max()) + 1, np.nan)
    lookup[fv.ids] = fv.values
    total = measure = 0.0
    for b in _blocks(fv):
        sizes = _measures(b, fv.mesh)
        weights = _weights(b.element, b.connectivity.shape[1])
        at = lookup[b.connectivity]
        whole = np.isfinite(at).all(axis=1)  # elements wholly kept
        total += float((sizes[whole, None] * weights[None, :] * at[whole]).sum())
        measure += float(sizes[whole].sum())
    if measure == 0.0:
        raise PuzzleError(f"'{op.name}': no elements to integrate over", code="invalid")
    if op.reduction == "integral":
        return {"out": Number(total, None, label)}
    return {"out": Number(total / measure, fv.item.unit, label)}


def _filter(ctx: Context, op) -> dict[str, Any]:
    fv = _restrict(_field(ctx, op, _input(ctx, op, "result")), op.selection)
    if op.keep != "all":
        limit = _number(op, _input(ctx, op, "limit"), "limit")
        if limit is None:
            raise PuzzleError(f"'{op.name}': keeping values {op.keep} a limit needs the limit",
                              code="inconsistent")  # fmt: skip
        _same_kind(op, fv.item.unit, limit.kind)
        with np.errstate(invalid="ignore"):
            keep = fv.values >= limit.value if op.keep == "above" else fv.values <= limit.value
        fv = replace(fv, values=np.where(keep, fv.values, np.nan))
    return {"out": fv}


def _number(op, got, what: str) -> Number | None:
    if got is None or isinstance(got, Number):
        return got
    raise PuzzleError(f"'{op.name}': its {what} must be a number", code="invalid")


def _same_kind(op, a: str | None, b: str | None) -> None:
    if a and b and a != b:
        from puzzle.model.units import DIMENSIONS

        raise PuzzleError(
            f"'{op.name}' compares {DIMENSIONS[a][0].lower()} with {DIMENSIONS[b][0].lower()}",
            code="invalid")  # fmt: skip


def _check(ctx: Context, op) -> dict[str, Any]:
    got = _input(ctx, op, "value")
    limit = _number(op, _input(ctx, op, "limit"), "limit")
    if isinstance(got, FieldValue):  # the field's worst value
        finite = got.values[np.isfinite(got.values)]
        if not finite.size:
            raise PuzzleError(f"'{op.name}': the field has no values", code="not_found")
        worst = finite.max() if op.compare == "at_most" else finite.min()
        got = Number(float(worst), got.item.unit, got.label)
    got = _number(op, got, "value")
    _same_kind(op, got.kind, limit.kind)
    passed = got.value <= limit.value if op.compare == "at_most" else got.value >= limit.value
    verdict = Verdict(bool(passed), got.value, limit.value, op.compare, got.kind or limit.kind)
    return {"out": verdict, "margin": Number(verdict.margin, None, "margin", percent=True)}


def _panel(ctx: Context, op) -> dict[str, Any]:
    return {}


def _export(ctx: Context, op) -> dict[str, Any]:
    return {}


def _plot(ctx: Context, op) -> dict[str, Any]:
    if op.against == "sweep":
        return {"out": _over_sweep(ctx, op)}
    base = _upstream_result(ctx, op)
    rows, label, kind = [], "Value", None
    for i, step in enumerate(base.results.steps):
        try:
            got = _number(op, _input(ctx.at(step=i), op, "value"), "value")
        except PuzzleError as exc:
            if exc.code == "not_found":  # a step without that result
                continue
            raise
        label, kind = got.label or label, got.kind
        rows.append([float(step.value), got.value])
    x, x_kind = {"modal": ("Frequency", None), "frequency_domain": ("Frequency", None),
                 "transient": ("Time", "time"), "buckling": ("Load factor", None)}.get(
        base.results.steps[0].kind if base.results.steps else "", ("Step", None))  # fmt: skip
    return {"out": Table([x, label], rows, [x_kind, kind])}


def _upstream_studies(ctx: Context, op, seen=()) -> list:
    """The studies (and derived results) an operator reads, nearest first."""
    from puzzle.model.schema import Operator, ResultDefinition, ResultProbe, Study

    found = []
    for inp in op.inputs.values():
        obj = ctx.project.objects.get(inp.source) if inp.source else None
        if isinstance(obj, (Study, ResultDefinition)):
            found.append(obj)
        elif isinstance(obj, ResultProbe):
            src = ctx.project.objects.get(obj.source)
            if src is not None:
                found.append(src)
        elif isinstance(obj, Operator) and obj.id not in seen:
            found += _upstream_studies(ctx, obj, (*seen, op.id))
    return list({o.id: o for o in found}.values())


def _upstream_result(ctx: Context, op) -> ResultValue:
    studies = _upstream_studies(ctx, op)
    if not studies:
        raise PuzzleError(f"'{op.name}' reads no study's results", code="inconsistent")
    return source(ctx, studies[0].id, "result")


def _over_sweep(ctx: Context, op) -> Table:
    """The value once per sweep point of the swept study it reads (the newest run of
    each point), against the swept parameter when there is one."""
    from puzzle.model.schema import Quantity, Study
    from puzzle.model.units import kind_of
    from puzzle.sweep import title

    studies = [s for s in _upstream_studies(ctx, op) if isinstance(s, Study) and s.sweeps()]
    if not studies:
        raise PuzzleError(f"'{op.name}': nothing it reads is a swept study",
                          code="inconsistent")  # fmt: skip
    study = studies[0]
    points: dict[str, Any] = {}
    for r in ctx.store.runs():  # oldest first: the newest of each point wins
        if r.study_id == str(study.id) and r.status == "completed" and r.point_labels():
            points[title(r.point_labels())] = r
    if not points:
        raise PuzzleError(f"study '{study.name}' has no sweep runs yet", code="not_found")
    params = [p.name for s in study.sweeps() for p in s.parameters if p.location is None]
    x_name = params[0] if len(params) == 1 else "Point"
    rows, label, kind, x_kind = [], "Value", None, None
    for point, record in points.items():
        got = _number(op, _input(ctx.at(point=point), op, "value"), "value")
        label, kind = got.label or label, got.kind
        x: Any = point
        label_ = record.point_labels().get(x_name) if x_name != "Point" else None
        if label_:  # "2 mm", as sweep.labels writes it
            number, _, unit = label_.partition(" ")
            try:
                q = Quantity(value=float(number), unit=unit or "dimensionless")
                x = q.to_si()
                x_kind = kind_of(unit) if unit else None
            except ValueError:
                pass
        rows.append([x, got.value])
    if all(isinstance(r[0], float) for r in rows):
        rows.sort(key=lambda r: r[0])
    return Table([x_name, label], rows, [x_kind, kind])


_EVALUATE = {"reduce": _reduce, "filter": _filter, "check": _check, "panel": _panel,
             "plot": _plot, "export": _export}  # fmt: skip


# --- what a node shows, and exporting -------------------------------------------------


def shown(ctx: Context, op) -> tuple[str, str]:
    """What the node shows on the canvas, and its verdict ("pass", "fail" or ""). Errors
    are shown as their message, never raised: the schematic always draws."""
    try:
        if op.type in ("panel", "export"):
            got = _input(ctx, op, "value")
            if op.type == "export":
                return f"to {export_path(op)}", ""
            return describe(got), _verdict(got)
        got = outputs(ctx, op)
        first = got.get("out")
        if isinstance(first, Verdict):
            return first.short(), _verdict(first)
        return describe(first), _verdict(first)
    except PuzzleError as exc:
        return exc.message, ""


def detail(ctx: Context, op) -> str:
    """The longer text (Properties, the CLI): a check's comparison in full."""
    text, verdict = shown(ctx, op)
    if verdict:
        return outputs(ctx, op)["out"].text()
    return text


def _verdict(v) -> str:
    return ("pass" if v.passed else "fail") if isinstance(v, Verdict) else ""


def describe(v) -> str:
    if v is None:
        return "nothing wired"
    return v.text()


def export_path(op) -> str:
    """Where an export writes, inside the project: its `file`, else exports/<name>."""
    if op.file:
        return op.file
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in op.name).strip("_")
    return f"exports/{stem or 'export'}.csv"


def export(ctx: Context, op, root: Path) -> list[Path]:
    """Write an export operator's input to its file in the project folder: CSV for a
    number, verdict, table or field; a result in its `format` (ADR 0043)."""
    rel = Path(export_path(op))
    if rel.is_absolute() or ".." in rel.parts:
        raise PuzzleError(f"'{op.name}': the file must be inside the project folder",
                          code="invalid")  # fmt: skip
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    got = _input(ctx, op, "value")
    if isinstance(got, ResultValue):
        from puzzle.post.exports import export as write

        if op.format == "csv":
            raise PuzzleError(f"'{op.name}': a result exports in a result format (VTU, "
                              "Exodus, ...): set its format", code="invalid")  # fmt: skip
        if len(got.run_ids) != 1:
            raise PuzzleError(f"'{op.name}': a derived result can't be exported as a run",
                              code="unsupported")  # fmt: skip
        return write(ctx.project, ctx.store, got.run_ids[0], op.format, path, root=Path(root))
    path.write_text(as_csv(got))
    return [path]


def as_csv(v) -> str:
    if isinstance(v, Table):
        return v.csv()
    if isinstance(v, Number):
        return Table(["name", "value"], [[v.label or "value", v.value]], [None, v.kind]).csv()
    if isinstance(v, Verdict):
        return Table(["passed", "value", "limit", "margin"],
                     [[v.passed, v.value, v.limit, v.margin]],
                     [None, v.kind, v.kind, None]).csv()  # fmt: skip
    if isinstance(v, FieldValue):
        keep = np.isfinite(v.values)
        ids, vals = v.ids[keep], v.values[keep]
        if v.location == "node":
            xyz = v.mesh.node_coords(ids)
            rows = [[int(i), *map(float, p), float(x)] for i, p, x in zip(ids, xyz, vals,
                                                                         strict=True)]  # fmt: skip
            return Table(["node", "x", "y", "z", v.label], rows,
                         [None, "length", "length", "length", v.item.unit]).csv()  # fmt: skip
        rows = [[int(i), float(x)] for i, x in zip(ids, vals, strict=True)]
        return Table(["element", v.label], rows, [None, v.item.unit]).csv()
    if v is None:
        raise PuzzleError("nothing is wired into it", code="inconsistent")
    return Table(["value"], [[v.text()]]).csv()


def summary(ctx: Context, op) -> dict[str, Any]:
    """An operator's value as data (the CLI, MCP and the outline): its outputs in SI."""
    out: dict[str, Any] = {"name": op.name, "type": op.type}
    try:
        got = outputs(ctx, op)
        if op.type in ("panel", "export"):
            got = {"value": _input(ctx, op, "value")}
        out["outputs"] = {k: _data(v) for k, v in got.items()}
        out["shown"] = shown(ctx, op)[0]
    except PuzzleError as exc:
        out["error"] = exc.to_dict()
    return out


def _data(v) -> Any:
    from puzzle.model.units import DIMENSIONS

    if isinstance(v, Number):
        d = {"value": v.value, "text": v.text()}
        if v.kind:
            d["unit"] = DIMENSIONS[v.kind][1]
        if v.point is not None:
            d["at"] = v.point
        return d
    if isinstance(v, Verdict):
        return {"passed": v.passed, "value": v.value, "limit": v.limit, "margin": v.margin,
                "text": v.text()}  # fmt: skip
    if isinstance(v, Table):
        return {"columns": [_heading(c, k) for c, k in zip(v.columns, v.kinds, strict=False)],
                "rows": v.rows}  # fmt: skip
    if v is None:
        return None
    return {"text": v.text()}
