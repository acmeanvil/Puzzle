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
import math
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


class Table:
    """Named columns, values SI and a quantity kind per column, held in a polars
    DataFrame (ADR 0064). Built from columns and rows (a typed table, a plot's points)
    or from a frame (`frame=`). `units` are columns' shown units set by Convert units;
    the others show in the display units."""

    type = "table"

    def __init__(self, columns=(), rows=(), kinds=(), *, frame=None, units=None) -> None:
        import polars as pl

        if frame is None:
            names, data = list(columns), {}
            for i, c in enumerate(names):
                cells = [r[i] if i < len(r) else None for r in rows]
                data[c] = _cells(pl, c, cells)
            frame = pl.DataFrame(data) if names else pl.DataFrame()
        self.frame = frame
        given = list(kinds)
        self.kinds = [given[i] if i < len(given) else None for i in range(frame.width)]
        self.units: dict[str, str] = dict(units or {})

    @property
    def columns(self) -> list[str]:
        return list(self.frame.columns)

    @property
    def rows(self) -> list[list[Any]]:
        return [list(r) for r in self.frame.rows()]

    def text(self) -> str:
        h, w = self.frame.height, self.frame.width
        names = ", ".join(self.columns[:6]) + (", …" if w > 6 else "")
        return f"{h} row{'s' if h != 1 else ''} × {w}: {names}"

    def csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow([_heading(c, k) for c, k in zip(self.columns, self.kinds, strict=False)])
        for r in self.frame.rows():
            w.writerow(["" if v is None else f"{v:.9g}" if isinstance(v, float) else v
                        for v in r])  # fmt: skip
        return buf.getvalue()


def _cells(pl, name: str, cells: list):
    """A column from Python cells: integers, numbers, true/false or text."""
    present = [v for v in cells if v is not None]
    if present and all(isinstance(v, bool) for v in present):
        return pl.Series(name, cells, dtype=pl.Boolean)
    if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return pl.Series(name, cells, dtype=pl.Int64)
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return pl.Series(name, [None if v is None else float(v) for v in cells],
                         dtype=pl.Float64)  # fmt: skip
    return pl.Series(name, [None if v is None else str(v) for v in cells], dtype=pl.String)


PREVIEW = 20  # a table's rows an agent or the CLI's JSON is given (ADR 0064)


def display_unit(kind: str | None) -> str | None:
    """The unit a kind shows in (Settings > Units), else its SI unit."""
    from puzzle.model.units import DIMENSIONS
    from puzzle.settings import current

    if kind is None or kind not in DIMENSIONS:
        return None
    return current().display_units().get(kind) or DIMENSIONS[kind][1]


@dataclass
class ListValue:
    values: Any  # an array of SI values, or a list of texts
    kind: str | None = None
    label: str = ""

    type = "list"

    @property
    def numeric(self) -> bool:
        return isinstance(self.values, np.ndarray)

    def text(self) -> str:
        n = len(self.values)
        shown = [quantity_text(float(v), self.kind) if self.numeric else str(v)
                 for v in list(self.values)[:4]]  # fmt: skip
        more = ", …" if n > 4 else ""
        return f"[{', '.join(shown)}{more}] ({n} item{'s' if n != 1 else ''})"


def as_list(v) -> ListValue:
    """A value as a list: a number is a one-item list (ADR 0062 item 2)."""
    if isinstance(v, ListValue):
        return v
    if isinstance(v, Number):
        return ListValue(np.array([v.value]), v.kind, v.label)
    raise PuzzleError("a list is needed here", code="invalid")


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
    overrides: dict = field(default_factory=dict)  # a slider being dragged: id -> SI value
    solver: str | None = None  # a shoot-out's solver whose runs are read (ADR 0063)
    root: Any = None  # the project folder, where a file operator reads (ADR 0064)

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
    if port == "points":  # a sweep's or optimisation's points as a table (ADR 0064)
        from puzzle.model.schema import Optimisation
        from puzzle.operators import frames

        if isinstance(obj, (Study, Optimisation)):
            return frames.points_table(ctx, obj)
    if isinstance(obj, Study):
        key = ("run", oid, ctx.point, ctx.solver)
        if key not in ctx.memo:
            record = derived.run_for(ctx.project, ctx.store, oid, ctx.point, ctx.solver)
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
    if isinstance(got, ListValue) and len(got.values) == 1 and got.numeric:
        return Number(float(got.values[0]), got.kind, got.label)
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
    got = _input(ctx, op, "value")
    if isinstance(got, Table):  # a column against another (ADR 0064 phase 2)
        from puzzle.operators import frames

        x = op.by[0] if op.by else got.columns[0]
        y = op.column or (got.columns[1] if len(got.columns) > 1 else got.columns[0])
        frames._names(op, [x, y], got)
        kinds = dict(zip(got.columns, got.kinds, strict=False))
        rows = [list(r) for r in got.frame.select([x, y]).rows()]
        return {"out": Table([x, y], rows, [kinds.get(x), kinds.get(y)])}
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


# --- data operators (phase 2) ----------------------------------------------------------


def _typed_number(ctx: Context, op, key: str, default: Number | None = None) -> Number | None:
    got = _input(ctx, op, key)
    return default if got is None else _number(op, got, key)


def _number_op(ctx: Context, op) -> dict[str, Any]:
    got = _typed_number(ctx, op, "value")
    if op.id in ctx.overrides:  # dragged: the preview's value
        got = Number(float(ctx.overrides[op.id]), got.kind if got else None)
    return {"out": Number(got.value, got.kind, op.name)}


def _slider(ctx: Context, op) -> dict[str, Any]:
    low = typed(op.low) if op.low is not None else None
    high = typed(op.high) if op.high is not None else None
    got = _typed_number(ctx, op, "value", low or Number(0.0, high.kind if high else None))
    if op.id in ctx.overrides:
        got = Number(float(ctx.overrides[op.id]), got.kind)
    for bound in (low, high):
        if bound is not None:
            _same_kind(op, got.kind, bound.kind)
    v = got.value
    if op.increment is not None and op.increment.value:
        step, base = typed(op.increment).value, low.value if low else 0.0
        v = base + round((v - base) / step) * step
    if low is not None:
        v = max(v, low.value)
    if high is not None:
        v = min(v, high.value)
    kind = got.kind or (low.kind if low else None) or (high.kind if high else None)
    return {"out": Number(float(v), kind, op.name)}


def _count(op, got: Number) -> int:
    n = int(round(got.value))
    if not 0 <= n <= 100_000:
        raise PuzzleError(f"'{op.name}': a count from 0 to 100000, not {n}", code="invalid")
    return n


def _series(ctx: Context, op) -> dict[str, Any]:
    start, step = _typed_number(ctx, op, "start"), _typed_number(ctx, op, "step")
    n = _count(op, _typed_number(ctx, op, "count"))
    _same_kind(op, start.kind, step.kind)
    values = start.value + step.value * np.arange(n)
    return {"out": ListValue(values, start.kind or step.kind, op.name)}


def _range(ctx: Context, op) -> dict[str, Any]:
    start, end = _typed_number(ctx, op, "start"), _typed_number(ctx, op, "end")
    n = _count(op, _typed_number(ctx, op, "count"))
    _same_kind(op, start.kind, end.kind)
    return {"out": ListValue(np.linspace(start.value, end.value, n), start.kind or end.kind,
                             op.name)}  # fmt: skip


def _tables() -> dict:
    """The table operators (ADR 0064), in `operators.frames`."""
    from puzzle.operators import frames, reshape
    from puzzle.operators import table_sources as sources

    def out(fn):
        return lambda ctx, op: {"out": fn(ctx, op)}

    def points(ctx, op):
        inp = op.inputs.get("source")
        if inp is None or inp.source is None:
            raise PuzzleError(f"'{op.name}': wire a swept study's or an optimisation's "
                              "points into it", code="inconsistent")  # fmt: skip
        return {"out": source(ctx, inp.source, "points")}

    return {"result_table": out(frames.result_table), "points_table": points,
            "file": out(frames.file_table), "columns": frames.columns,
            "filter_rows": frames.filter_rows, "sort": frames.sort, "top_n": frames.top_n,
            "new_column": frames.new_column, "convert_units": frames.convert_units,
            "round": frames.round_values, "group": frames.group,
            "to_number": frames.to_number, "lookup": _lookup, "measure": _measure,
            "join": reshape.join, "stack": reshape.stack, "pivot": reshape.pivot,
            "unpivot": reshape.unpivot, "distinct": reshape.distinct,
            "fill_missing": reshape.fill_missing, "rolling": reshape.rolling,
            "mesh_table": out(sources.mesh_table), "project_table": out(sources.project_table),
            "run_history": out(sources.run_history),
            "history_table": out(sources.history_table), "mode_table": out(sources.mode_table),
            "contact_table": out(sources.contact_table), "bolt_table": out(sources.bolt_table),
            "nearest": out(sources.nearest), "section": sources.section,
            "divide": out(sources.divide), "clearance": out(sources.clearance),
            "text": _text, "preview": lambda ctx, op: {}}  # fmt: skip


def _text(ctx: Context, op) -> dict[str, Any]:
    """A line from its inputs (ADR 0064A rows C12, J7): {x}, {y}, {z} each as its value
    reads in the display units ("212 MPa"); {x:.1f} gives places of the shown number."""
    import re

    from puzzle.model.units import DIMENSIONS, pretty, shown
    from puzzle.settings import current

    got = {k: _input(ctx, op, k) for k in ("x", "y", "z")}

    def fill(m) -> str:
        key, spec = m.group(1), m.group(2)
        v = got.get(key)
        if v is None:
            raise PuzzleError(f"'{op.name}': {{{key}}} has nothing wired", code="inconsistent")
        if isinstance(v, Number) and spec:
            if v.kind in DIMENSIONS:
                n, unit = shown(v.value, DIMENSIONS[v.kind][1], current().display_units())
                return f"{n:{spec}} {pretty(unit)}"
            return f"{v.value:{spec}}"
        return v.text()

    text = re.sub(r"\{([xyz])(?::([^}]*))?\}", fill, op.template or "{x}")
    return {"out": Text(text)}


def _random(ctx: Context, op) -> dict[str, Any]:
    """Seeded samples (the same each time): normal or log-normal with the spread as the
    standard deviation (log-normal's mean is the mean given), or uniform within plus or
    minus the spread."""
    mean, spread = _typed_number(ctx, op, "mean"), _typed_number(ctx, op, "spread")
    n = _count(op, _typed_number(ctx, op, "count"))
    _same_kind(op, mean.kind, spread.kind)
    values = draw(op.distribution, mean.value, spread.value, n, op.seed)
    return {"out": ListValue(values, mean.kind or spread.kind, op.name)}


def draw(distribution: str, mean: float, spread: float, n: int, seed: int) -> np.ndarray:
    """n seeded samples about `mean`; shared with robust design's Monte Carlo."""
    rng = np.random.default_rng(seed)
    if distribution == "uniform":
        return rng.uniform(mean - spread, mean + spread, n)
    if distribution == "lognormal":
        if mean <= 0:
            raise PuzzleError("a log-normal scatter needs a positive mean", code="invalid")
        sigma = math.sqrt(math.log(1 + (spread / mean) ** 2))
        return mean * np.exp(rng.normal(-(sigma**2) / 2, sigma, n))  # its mean is `mean`
    return rng.normal(mean, spread, n)


def _expression(ctx: Context, op) -> dict[str, Any]:
    from puzzle.model.schema import ParameterSet
    from puzzle.model.units import kind_of
    from puzzle.operators.expression import evaluate as formula

    values: dict[str, tuple] = {}
    for ps in ctx.project.of_type(ParameterSet):
        for k, q in ps.values.items():
            unit = q.unit if q.unit != "dimensionless" else ""
            values.setdefault(k, (q.to_si(), kind_of(unit) if unit else None))
    listed = False
    for key in ("x", "y", "z"):
        got = _input(ctx, op, key)
        if got is None:
            continue
        if isinstance(got, ListValue):
            if not got.numeric:
                raise PuzzleError(f"'{op.name}': {key} is a list of texts", code="invalid")
            values[key], listed = (got.values, got.kind), True
        else:
            got = _number(op, got, key)
            values[key] = (got.value, got.kind)
    out, kind = formula(op.expression, values)
    if listed or np.ndim(out):
        return {"out": ListValue(np.atleast_1d(np.asarray(out, float)), kind, op.name)}
    result = {"out": Number(float(out), kind, op.name)}
    verdict = _comparison(op, values, formula)
    if verdict is not None:
        result["passes"] = verdict
    return result


def _comparison(op, values, formula) -> Verdict | None:
    """An Expression that compares (ADR 0064A row B2) as a verdict: one comparison
    (σ <= 250 MPa) keeps its two sides, so the margin means something; and/or of
    several is just pass or fail."""
    import ast

    from puzzle.operators.expression import _prepared

    try:
        body = ast.parse(_prepared(op.expression), mode="eval").body
    except SyntaxError:
        return None
    if isinstance(body, ast.Compare) and len(body.ops) == 1 and isinstance(
            body.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):  # fmt: skip
        left, lk = formula(ast.unparse(body.left), values)
        right, _ = formula(ast.unparse(body.comparators[0]), values)
        at_most = isinstance(body.ops[0], (ast.Lt, ast.LtE))
        passed = left <= right if at_most else left >= right
        if isinstance(body.ops[0], (ast.Lt, ast.Gt)):
            passed = passed and left != right
        return Verdict(bool(passed), float(left), float(right),
                       "at_most" if at_most else "at_least", lk)  # fmt: skip
    if isinstance(body, (ast.Compare, ast.BoolOp)) or (
            isinstance(body, ast.UnaryOp) and isinstance(body.op, ast.Not)):  # fmt: skip
        passed = bool(formula(op.expression, values)[0])
        return Verdict(passed, 1.0 if passed else 0.0, 1.0, "at_least", None)
    return None


def _lookup(ctx: Context, op) -> dict[str, Any]:
    """y read off a table at x (ADR 0064A row A5): linear between rows (ends held),
    stepped (the row at or before x) or the nearest row."""
    from puzzle.operators import frames

    t = frames._table(op, _input(ctx, op, "table"))
    xcol = op.by[0] if op.by else t.columns[0]
    ycol = op.column or (t.columns[1] if len(t.columns) > 1 else "")
    frames._names(op, [xcol, ycol], t)
    kinds = dict(zip(t.columns, t.kinds, strict=False))
    data = t.frame.select([xcol, ycol]).drop_nulls().sort(xcol)
    xs, ys = data[xcol].cast(float).to_numpy(), data[ycol].cast(float).to_numpy()
    if not len(xs):
        raise PuzzleError(f"'{op.name}': the table has no rows to read", code="not_found")
    at = _input(ctx, op, "at")
    lst = as_list(at) if at is not None else ListValue(np.array([xs[0]]), kinds.get(xcol))
    _same_kind(op, lst.kind, kinds.get(xcol))
    x = np.asarray(lst.values, float)
    if op.interpolation == "linear":
        y = np.interp(x, xs, ys)
    elif op.interpolation == "step":
        y = ys[np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 1)]
    else:
        y = ys[np.abs(xs[None, :] - x[:, None]).argmin(axis=1)]
    kind = kinds.get(ycol)
    if isinstance(at, ListValue):
        return {"out": ListValue(y, kind, op.name)}
    return {"out": Number(float(y[0]), kind, op.name)}


MEASURED = {"length": ("length", "length"), "area": ("area", "area"),
            "volume": ("volume", "volume"), "radius": ("radius", "length")}  # fmt: skip


def _measure(ctx: Context, op) -> dict[str, Any]:
    """A part's or selection's measure from the exact geometry (ADR 0042's Measure as a
    node, ADR 0064A rows E4 and F3): no mesh, no solve. Mass is the volume times the
    density of the material assigned to the part."""
    from puzzle.geometry.measure import MeasureError, measure
    from puzzle.geometry.naming import by_rule, named_geometry
    from puzzle.model.schema import Component, Geometry, Material

    obj = ctx.project.objects.get(op.target) if op.target else None
    part_name, _, sel = (op.selection or "").partition("/")
    if isinstance(obj, Component):
        part = next((p for p in obj.parts if p.name == part_name), None)
        if part is None:
            raise PuzzleError(f"'{op.name}': {obj.name} has no part '{part_name}'",
                              code="not_found")  # fmt: skip
        geo = ctx.project.get(part.geometry, Geometry)
    elif isinstance(obj, Geometry):
        geo, sel = obj, op.selection or ""
    else:
        raise PuzzleError(f"'{op.name}': measure a component's part or a geometry",
                          code="inconsistent")  # fmt: skip
    key = ("named", geo.id)
    if key not in ctx.memo:
        ctx.memo[key] = named_geometry(ctx.project, geo, ctx.root or Path("."))
    named = ctx.memo[key]
    if sel:
        s = next((x for x in geo.selections if x.name == sel), None)
        if s is None:
            raise PuzzleError(f"'{op.name}': {geo.name} has no selection '{sel}'",
                              code="not_found")  # fmt: skip
        members = by_rule(named, s.entity, s.rule) if s.rule else list(s.members)
        picks = [(s.entity, m) for m in members]
    else:
        picks = [("body", n) for n in named.of.get("body", {})]
    try:
        m = measure(named, picks)
    except MeasureError as exc:
        raise PuzzleError(f"'{op.name}': {exc}", code="invalid") from None
    what = op.measurement
    if what in MEASURED:
        attr, kind = MEASURED[what]
        vals = [getattr(e, attr) for e in m.entities if getattr(e, attr) is not None]
        if not vals:
            raise PuzzleError(f"'{op.name}': {op.selection or geo.name} has no {what}",
                              code="not_found")  # fmt: skip
        value = float(min(vals)) if what == "radius" else float(sum(vals))
        return {"out": Number(value, kind, f"{what.capitalize()} of "
                              f"{op.selection or geo.name}")}  # fmt: skip
    if what == "mass":
        vols = [e.volume for e in m.entities if e.volume is not None]
        density = None
        if isinstance(obj, Component):
            for a in obj.materials:
                if a.selection.split("/")[0] == part_name:
                    mat = ctx.project.objects.get(a.material)
                    if isinstance(mat, Material) and "density" in mat.properties:
                        density = mat.properties["density"].to_si()
                        break
        if density is None or not vols:
            raise PuzzleError(
                f"'{op.name}': a mass needs the part's volume and the density of the "
                "material assigned to it", code="incomplete_material")  # fmt: skip
        return {"out": Number(float(sum(vols)) * density, "mass", f"Mass of {part_name}")}
    axis = "xyz".index(what[-1])
    if what.startswith("centroid"):
        weights = [e.volume or e.area or e.length or 1.0 for e in m.entities]
        c = np.average([e.centre[axis] for e in m.entities], weights=weights)
        return {"out": Number(float(c), "length", f"Centroid {what[-1]}")}
    lo = min(e.bounds[0][axis] for e in m.entities if e.bounds)
    hi = max(e.bounds[1][axis] for e in m.entities if e.bounds)
    return {"out": Number(float(hi - lo), "length", f"Size in {what[-1]}")}


def parse_table(text: str) -> Table:
    """A typed table: a header row of names, each with its unit in brackets (t [mm],
    load [kN]), then rows; numbers are turned into SI, other cells kept as text."""
    from puzzle.model.units import kind_of, known, to_si

    rows = [r for r in csv.reader(io.StringIO(text.strip())) if any(c.strip() for c in r)]
    if not rows:
        return Table([], [], [])
    names, units = [], []
    for cell in rows[0]:
        cell = cell.strip()
        name, _, unit = cell.partition("[")
        unit = unit.rstrip("]").strip()
        if unit and not known(unit):
            raise PuzzleError(f"unknown unit '{unit}' in the column '{name.strip()}'",
                              code="invalid")  # fmt: skip
        names.append(name.strip())
        units.append(unit)
    out = []
    for r in rows[1:]:
        row = []
        for i, cell in enumerate(r[: len(names)]):
            if not cell.strip():  # an empty cell is a missing value, not text
                row.append(None)
                continue
            try:
                v = float(cell)
                row.append(to_si(v, units[i]) if units[i] else v)
            except ValueError:
                row.append(cell.strip())
        out.append(row)
    return Table(names, out, [kind_of(u) if u else None for u in units])


def _table(ctx: Context, op) -> dict[str, Any]:
    return {"out": parse_table(op.text)}


def _column(op, table: Table) -> ListValue:
    name = op.column or (table.columns[0] if table.columns else "")
    if name not in table.columns:
        raise PuzzleError(f"'{op.name}': the table has no column '{name}'", code="not_found",
                          details={"columns": table.columns})  # fmt: skip
    i = table.columns.index(name)
    cells = [r[i] for r in table.rows if i < len(r)]
    kind = table.kinds[i] if i < len(table.kinds) else None
    if all(isinstance(c, (int, float)) for c in cells):
        return ListValue(np.array(cells, float), kind, name)
    return ListValue([str(c) for c in cells], None, name)


MODES = ("index_of", "shift", "chunk", "insert", "replace", "repeat", "every_nth",
         "at_indexes", "unique", "union", "intersection", "difference", "remap", "bin",
         "differences", "cumulative_sum", "moving_average")  # fmt: skip


def _mode(ctx: Context, op, lst: ListValue, index, end):
    """The List operator's other modes (ADR 0064A rows B6, B8, C4, C5, C11), on a list
    and the `index`, `end` and `other` inputs."""
    values, kind, numeric = lst.values, lst.kind, lst.numeric
    other = _input(ctx, op, "other")
    other = as_list(other) if other is not None else None
    n = int(index.value) if index is not None else 1
    pick = np.array if numeric else list
    seq = list(values)
    how = op.operation
    if how == "index_of":
        if other is None:
            raise PuzzleError(f"'{op.name}': index of what? wire the other list",
                              code="inconsistent")  # fmt: skip
        out = [float(seq.index(v)) if v in seq else -1.0 for v in list(other.values)]
        return np.array(out), None
    if how == "shift":
        return pick(seq[n:] + seq[:n]) if seq else pick([]), kind
    if how == "chunk":  # the n-th chunk of `end` items (from 0)
        size = int(end.value) if end is not None else 1
        return pick(seq[n * size : (n + 1) * size]), kind
    if how == "insert":
        if other is None:
            raise PuzzleError(f"'{op.name}': insert what? wire the other list",
                              code="inconsistent")  # fmt: skip
        return pick(seq[:n] + list(other.values) + seq[n:]), kind
    if how == "replace":
        if other is None or not len(other.values):
            raise PuzzleError(f"'{op.name}': replace with what? wire the other list",
                              code="inconsistent")  # fmt: skip
        seq[n] = list(other.values)[0]
        return pick(seq), kind
    if how == "repeat":
        return pick(seq * max(n, 0)), kind
    if how == "every_nth":
        start = int(end.value) if end is not None else 0
        return pick(seq[start :: max(n, 1)]), kind
    if how == "at_indexes":
        if other is None:
            raise PuzzleError(f"'{op.name}': which indexes? wire them as the other list",
                              code="inconsistent")  # fmt: skip
        return pick([seq[int(i)] for i in other.values if -len(seq) <= int(i) < len(seq)]), kind
    if how in ("unique", "union", "intersection", "difference"):
        b = list(other.values) if other is not None else []
        if how == "unique":
            got = list(dict.fromkeys(seq))
        elif how == "union":
            got = list(dict.fromkeys(seq + b))
        elif how == "intersection":
            got = [v for v in dict.fromkeys(seq) if v in b]
        else:
            got = [v for v in dict.fromkeys(seq) if v not in b]
        return pick(got), kind
    if not numeric:
        raise PuzzleError(f"'{op.name}': {how.replace('_', ' ')} needs numbers", code="invalid")
    x = np.asarray(values, float)
    if how == "remap":  # to 0..1, or onto index..end
        lo, hi = float(x.min()), float(x.max())
        u = (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)
        if index is not None and end is not None:
            _same_kind(op, index.kind, end.kind)
            return index.value + u * (end.value - index.value), index.kind
        return u, None
    if how == "bin":  # which of the other list's edges each value falls between
        if other is None:
            raise PuzzleError(f"'{op.name}': bin by what? wire the edges as the other list",
                              code="inconsistent")  # fmt: skip
        _same_kind(op, kind, other.kind)
        return np.digitize(x, np.asarray(other.values, float)).astype(float), None
    if how == "differences":
        return np.diff(x), kind
    if how == "cumulative_sum":
        return np.cumsum(x), kind
    w = max(n, 1)  # moving average over `index` items, the ends over what there is
    c = np.convolve(x, np.ones(w), "same") / np.convolve(np.ones_like(x), np.ones(w), "same")
    return c, kind


def _list(ctx: Context, op) -> dict[str, Any]:
    got = _input(ctx, op, "list")
    lst = _column(op, got) if isinstance(got, Table) else as_list(got)
    values = lst.values
    index = _typed_number(ctx, op, "index")
    end = _typed_number(ctx, op, "end")
    numeric = lst.numeric
    kind = lst.kind
    if op.operation in ("item", "slice"):
        n = len(values)
        if op.operation == "item":
            i = int(index.value) if index else 0
            if not -n <= i < n:
                raise PuzzleError(f"'{op.name}': no item {i} in {n}", code="not_found")
            picked = [values[i]]
        else:
            a = int(index.value) if index else 0
            b = int(end.value) if end else n
            picked = list(values[a:b])
        values = np.array(picked, float) if numeric else picked
    elif op.operation in MODES:
        values, kind = _mode(ctx, op, lst, index, end)
    elif op.operation == "sort":
        values = np.sort(values) if numeric else sorted(values)
    elif op.operation == "reverse":
        values = values[::-1] if numeric else list(reversed(values))
    elif op.operation == "length":
        values, kind = np.array([float(len(values))]), None
    elif op.operation in ("sum", "min", "max", "mean", "median", "std", "percentile",
                          "product", "weighted_mean"):  # fmt: skip
        if not numeric:
            raise PuzzleError(f"'{op.name}': a {op.operation} needs numbers", code="invalid")
        if op.operation == "weighted_mean":
            w = _input(ctx, op, "weights")
            if not isinstance(w, ListValue) or len(w.values) != len(values):
                raise PuzzleError(f"'{op.name}': a weighted mean needs weights, one a value",
                                  code="inconsistent")  # fmt: skip
            got = float(np.average(values, weights=w.values))
        elif op.operation == "percentile":
            got = float(np.percentile(values, index.value if index else 50.0))
        elif op.operation == "product":
            got, kind = float(np.prod(values)), (kind if len(values) == 1 else None)
        else:
            fn = {"sum": np.sum, "min": np.min, "max": np.max, "mean": np.mean,
                  "median": np.median, "std": np.std}[op.operation]  # fmt: skip
            got = float(fn(values))
        values = np.array([got]) if len(values) else np.array([])
    out = ListValue(values, kind, op.name)
    first = None
    if len(values) and isinstance(values, np.ndarray):
        first = Number(float(values[0]), kind, op.name)
    result = {"out": out}
    if first is not None:
        result["first"] = first
    return result


def _pairs(ctx: Context, op) -> dict[str, Any]:
    a, b = as_list(_input(ctx, op, "a")), as_list(_input(ctx, op, "b"))
    av, bv = list(a.values), list(b.values)
    if op.combination == "cross":
        rows = [[x, y] for x in av for y in bv]
    else:  # item by item; the shorter list repeats its last item (longest list)
        n = max(len(av), len(bv))
        rows = [[av[min(i, len(av) - 1)], bv[min(i, len(bv) - 1)]] for i in range(n)] \
            if av and bv else []  # fmt: skip
    rows = [[float(x) if not isinstance(x, str) else x for x in r] for r in rows]
    return {"out": Table([a.label or "a", b.label or "b"], rows, [a.kind, b.kind])}


_EVALUATE = {"reduce": _reduce, "filter": _filter, "check": _check, "panel": _panel,
             "plot": _plot, "export": _export, "number": _number_op, "slider": _slider,
             "series": _series, "range": _range, "expression": _expression,
             "random": _random, **_tables(),
             "table": _table, "list": _list, "pairs": _pairs}  # fmt: skip


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
    if isinstance(got, Table):  # in its shown units, by the file's suffix (ADR 0064)
        from puzzle.operators import frames

        fmt = {".parquet": "parquet", ".xlsx": "xlsx"}.get(path.suffix.lower(), op.format)
        frames.write(got, path, fmt if fmt in ("parquet", "xlsx") else "csv")
        return [path]
    path.write_text(as_csv(got))
    return [path]


def as_csv(v) -> str:
    if isinstance(v, ListValue):
        rows = [[float(x)] if v.numeric else [x] for x in v.values]
        return Table([v.label or "value"], rows, [v.kind]).csv()
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
    if isinstance(v, ListValue):
        d = {"values": [float(x) for x in v.values] if v.numeric else list(v.values),
             "text": v.text()}  # fmt: skip
        if v.kind:
            d["unit"] = DIMENSIONS[v.kind][1]
        return d
    if isinstance(v, Table):  # its schema and first rows, never a whole result table
        return {"columns": [_heading(c, k) for c, k in zip(v.columns, v.kinds, strict=False)],
                "row_count": v.frame.height, "rows": [list(r) for r in
                                                       v.frame.head(PREVIEW).rows()]}  # fmt: skip
    if v is None:
        return None
    return {"text": v.text()}
