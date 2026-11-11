"""Curves (ADR 0071 decision 3): x and y with their quantities, as an engineer plots them.

A `Series` holds one curve or several sharing their units: a path at every step of a
transient run is one curve per step, a history is one curve against the steps. Result
tools give them (a path's values along it, any tool's value over the steps), Over the
steps turns any number into one, Curve from table takes a table's columns, and a table
wired where a curve is wanted is read the same way. Nodes show a series as a small plot
and agents get a summary (range, extremes, a few points), never rows.

The operators here work on curves point by point with units followed through (stress ÷
stress is a pure number; a pressure plus a length is refused, with the reason): the
arithmetic (Divide, Subtract, Multiply, Add) and the intent-level Utilisation, Safety
factor and Margin of safety; Normalise; Curve formula; At step and Compare with step;
Envelope, Match x values, Area under curve and Slope; and for plots Limit line and
Annotate. Two numbers into the arithmetic give a number, so the same nodes serve a
single check.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from puzzle.errors import PuzzleError

# --- the value -------------------------------------------------------------------------


@dataclass
class Curve:
    x: np.ndarray
    y: np.ndarray
    label: str  # "Von Mises at 0.5 s"
    at: float | None = None  # the step value it belongs to (time, frequency, increment)
    style: str = "line"  # "line", or "limit": a value drawn across (x empty, y one value)
    note: str = ""  # Annotate's label, drawn at `mark`
    mark: tuple[float, float] | None = None


@dataclass
class Series:
    curves: list[Curve]
    x_kind: str | None = None  # model.units.DIMENSIONS keys; None: a pure number
    y_kind: str | None = None
    x_label: str = "x"  # "Distance along the path", "Time", "Temperature"
    label: str = "y"  # what y is: "Von Mises", "Utilisation"
    origin: str = ""  # where it came from: "Stress path on Static"
    along: str = ""  # "steps" when x is the steps (a history), "sweep", "" otherwise
    x_step: str = ""  # the step kind's word when along == "steps" ("Time")

    type = "series"

    @property
    def lines(self) -> list[Curve]:
        return [c for c in self.curves if c.style != "limit"]

    def extremes(self) -> tuple[float, float]:
        ys = [c.y[np.isfinite(c.y)] for c in self.curves]
        ys = [y for y in ys if y.size]
        if not ys:
            return float("nan"), float("nan")
        every = np.concatenate(ys)
        return float(every.min()), float(every.max())

    def text(self) -> str:
        from puzzle.operators.evaluate import quantity_text

        low, high = self.extremes()
        lines = self.lines
        if not lines and self.curves:  # a limit line alone
            return f"{self.label}: {quantity_text(high, self.y_kind)}"
        span = ""
        xs = [c.x[np.isfinite(c.x)] for c in lines if c.x.size]
        xs = [x for x in xs if x.size]
        if xs:
            x0 = min(float(x.min()) for x in xs)
            x1 = max(float(x.max()) for x in xs)
            span = (f" over {quantity_text(x0, self.x_kind)} to "
                    f"{quantity_text(x1, self.x_kind)}")  # fmt: skip
        values = f"{quantity_text(low, self.y_kind)} to {quantity_text(high, self.y_kind)}"
        many = f"{len(lines)} curves, " if len(lines) > 1 else ""
        return f"{self.label}: {many}{values}{span}"

    def table(self):
        """As a table: x and y, plus the step for several curves (exports, Panel)."""
        from puzzle.operators.evaluate import Table

        lines = self.lines
        if len(lines) == 1:
            c = lines[0]
            rows = [[float(a), float(b)] for a, b in zip(c.x, c.y, strict=True)]
            return Table([self.x_label, self.label], rows, [self.x_kind, self.y_kind])
        rows = [[c.label, float(a), float(b)] for c in lines
                for a, b in zip(c.x, c.y, strict=True)]  # fmt: skip
        return Table(["curve", self.x_label, self.label], rows, [None, self.x_kind, self.y_kind])

    def sketch(self, width: int = 40) -> list[list[tuple[float, float]]]:
        """Each curve thinned to about `width` points scaled into 0..1 on both axes: the
        schematic node's small plot."""
        lines = [c for c in self.lines if c.x.size]
        if not lines:
            return []
        xs = np.concatenate([c.x for c in lines])
        ys = np.concatenate([c.y for c in lines])
        ok = np.isfinite(xs) & np.isfinite(ys)
        if not ok.any():
            return []
        x0, x1, y0, y1 = xs[ok].min(), xs[ok].max(), ys[ok].min(), ys[ok].max()
        sx, sy = (x1 - x0) or 1.0, (y1 - y0) or 1.0
        out = []
        for c in lines[:6]:
            keep = np.isfinite(c.x) & np.isfinite(c.y)
            x, y = c.x[keep], c.y[keep]
            if x.size > width:
                pick = np.linspace(0, x.size - 1, width).round().astype(int)
                x, y = x[pick], y[pick]
            out.append(
                [(float((a - x0) / sx), float((b - y0) / sy)) for a, b in zip(x, y, strict=True)]
            )
        return out


def summary(s: Series) -> dict[str, Any]:
    """A series for an agent (ADR 0071 decision 6): its axes, range and extremes and a
    few points per curve, in SI with units named, never every row."""
    from puzzle.model.units import DIMENSIONS

    def unit(kind):
        return DIMENSIONS[kind][1] if kind in DIMENSIONS else ""

    curves = []
    for c in s.curves[:12]:
        d: dict[str, Any] = {"label": c.label, "points": int(c.y.size)}
        if c.style == "limit":
            d |= {"limit": float(c.y[0])}
        else:
            finite = np.isfinite(c.y)
            if finite.any():
                i, j = int(np.nanargmax(c.y)), int(np.nanargmin(c.y))
                d |= {"max": float(c.y[i]), "x_at_max": float(c.x[i]),
                      "min": float(c.y[j]), "x_at_min": float(c.x[j])}  # fmt: skip
            pick = np.linspace(0, c.y.size - 1, min(5, c.y.size)).round().astype(int)
            d["sample"] = [[float(c.x[k]), float(c.y[k])] for k in pick]
        if c.at is not None:
            d["step"] = c.at
        if c.note:
            d["note"] = c.note
        curves.append(d)
    return {"x": {"label": s.x_label, "unit": unit(s.x_kind)},
            "y": {"label": s.label, "unit": unit(s.y_kind)},
            "curve_count": len(s.curves), "curves": curves, "from": s.origin,
            "text": s.text()}  # fmt: skip


# --- making series ---------------------------------------------------------------------


STEP_AXES = {"modal": ("Frequency", "frequency"), "frequency_domain": ("Frequency", "frequency"),
             "transient": ("Time", "time"), "buckling": ("Load factor", None)}  # fmt: skip


def step_axis(results) -> tuple[str, str | None]:
    """What a run's steps are along: ("Time", "time"), ("Frequency", ...), ("Step", None)."""
    from puzzle.model.units import DIMENSIONS

    kind = results.steps[0].kind if results.steps else ""
    label, k = STEP_AXES.get(kind, ("Step", None))
    return label, k if k in DIMENSIONS else None


def from_table(table, x: str = "", y: str = "", label: str = "", along: str = "") -> Series:
    """A table's column `x` against `y` (the first two number columns if not named)."""
    numeric = [c for c, d in zip(table.columns, table.frame.dtypes, strict=True)
               if d.is_numeric()]  # fmt: skip
    if not x or not y:
        if len(numeric) < 2 and not (x or y):
            raise PuzzleError(f"{label or 'the table'} has no two number columns to plot",
                              code="invalid")  # fmt: skip
        x = x or next(c for c in numeric if c != y)
        y = y or next(c for c in numeric if c != x)
    for name in (x, y):
        if name not in table.columns:
            raise PuzzleError(f"the table has no column '{name}' (its columns: "
                              f"{', '.join(table.columns)})", code="not_found")  # fmt: skip
    kinds = dict(zip(table.columns, table.kinds, strict=False))
    xs = table.frame[x].cast(float, strict=False).to_numpy().astype(float)
    ys = table.frame[y].cast(float, strict=False).to_numpy().astype(float)
    return Series([Curve(xs, ys, y)], kinds.get(x), kinds.get(y), x, y, along=along)


def as_series(op, got, what: str = "curve") -> Series:
    """What is wired into a curve input, as a series: a table read as columns; a
    number is refused (it has no x)."""
    from puzzle.operators.evaluate import Table

    if isinstance(got, Series):
        return got
    if isinstance(got, Table):
        return from_table(got, label=what)
    raise PuzzleError(f"'{op.name}': its {what} must be a curve, not "
                      f"{_word(got)}", code="invalid")  # fmt: skip


def _word(got) -> str:
    from puzzle.operators import TYPE_WORDS

    t = getattr(got, "type", "")
    return f"a {TYPE_WORDS.get(t, t or 'value').lower()}"


def from_probe(ctx, probe, port: str) -> Series:
    """A result tool's curves: `along` its path (a curve per step, or the context's
    step), or its value `history` over the steps."""
    from puzzle.model.schema import Study
    from puzzle.operators.evaluate import quantity_text, source
    from puzzle.post import probes

    placed = probes.placed(ctx.project, probe)
    src = ctx.project.objects.get(placed.source)
    r = source(ctx, src.id if isinstance(src, Study) else placed.source, "result")
    item = probes._item(placed, r.items)
    steps = [s for s in r.results.steps if item.field in s.fields]
    if not steps:
        raise PuzzleError(f"result tool '{probe.name}': no step has {item.label}",
                          code="not_found")  # fmt: skip
    x_label, x_kind = step_axis(r.results)
    origin = f"{probe.name} on {r.label}"
    depth = probes.depth_of(probes._component_of(ctx.project, probe))
    if port == "history":
        values = [probes.at_step(r.mesh, s, item, placed, depth)["value"] for s in steps]
        curve = Curve(np.array([s.value for s in steps], float), np.array(values, float),
                      probe.name)  # fmt: skip
        return Series([curve], x_kind, item.unit, x_label, probe.name, origin, "steps", x_label)
    if placed.type not in ("path", "linearized"):
        raise PuzzleError(f"result tool '{probe.name}' is not along a path", code="invalid")
    if ctx.step is not None:
        steps = [steps[min(ctx.step, len(steps) - 1)]]
    elif placed.type == "linearized":
        steps = [steps[min(placed.step, len(steps) - 1)] if placed.step >= 0
                 else steps[placed.step]]  # fmt: skip
    curves = []
    for s in steps:
        found = probes.at_step(r.mesh, s, item, placed, depth)
        d = np.asarray(found["distance"], float)
        when = f" at {quantity_text(s.value, x_kind)}" if len(steps) > 1 else ""
        if placed.type == "linearized":
            from puzzle.post.linearize import LABELS

            for k, v in found["series"].items():
                curves.append(
                    Curve(
                        d, np.asarray(v, float), f"{probe.name}: {LABELS[k].lower()}{when}", s.value
                    )
                )
        else:
            curves.append(Curve(d, np.asarray(found["values"], float),
                                f"{item.label}{when}", s.value))  # fmt: skip
    return Series(curves, "length", item.unit, "Distance along the path", item.label, origin)


# --- combining -------------------------------------------------------------------------


def _kind_name(kind: str | None) -> str:
    from puzzle.model.units import DIMENSIONS

    return DIMENSIONS[kind][0].lower() if kind in DIMENSIONS else "a pure number"


def _same_x(op, a: Series, b: Series, what: str) -> None:
    if a.x_kind != b.x_kind and a.lines and b.lines:
        raise PuzzleError(
            f"'{op.name}': {what}'s x is {_kind_name(b.x_kind)} ({b.x_label}), the "
            f"first's {_kind_name(a.x_kind)} ({a.x_label})", code="invalid")  # fmt: skip


def together(op, parts: list[Series]) -> Series:
    """Several series drawn on one plot: one x and one y quantity, or refused."""
    first = next((p for p in parts if p.lines), parts[0])
    curves = []
    for i, p in enumerate(parts):
        what = f"curve {i + 1}"
        _same_x(op, first, p, what)
        if p.y_kind != first.y_kind:
            raise PuzzleError(
                f"'{op.name}': {what} is {_kind_name(p.y_kind)} ({p.label}), the first "
                f"{_kind_name(first.y_kind)} ({first.label}): plot them apart, or divide "
                "one to make them alike", code="invalid")  # fmt: skip
        curves += p.curves
    return replace(first, curves=curves)


def _operand(op, got, key: str):
    """A number (value, kind, label), a series or a field, from what is wired into
    `key`."""
    from puzzle.operators.evaluate import FieldValue, ListValue, Number, Table

    if got is None:
        return None
    if isinstance(got, Number | FieldValue):
        return got
    if isinstance(got, ListValue) and got.numeric and len(got.values) == 1:
        return Number(float(got.values[0]), got.kind, got.label)
    if isinstance(got, Series):
        if got.curves and not got.lines:  # a limit line is a number
            return Number(float(got.curves[0].y[0]), got.y_kind, got.label)
        return got
    if isinstance(got, Table):
        return from_table(got, label=key)
    raise PuzzleError(f"'{op.name}': its {key} input must be a curve, a field or a number, "
                      f"not {_word(got)}", code="invalid")  # fmt: skip


def _formula(op, text: str, values: dict) -> tuple[Any, str | None]:
    from puzzle.operators.expression import evaluate as formula

    try:
        return formula(text, values)
    except PuzzleError as exc:
        raise PuzzleError(f"'{op.name}': {exc.message}", code=exc.code) from None


def _pairs(op, a: Series, b: Series) -> list[tuple[Curve, Curve]]:
    """Curves matched for point-by-point work: one to one, or one against each."""
    la, lb = a.lines, b.lines
    if len(la) == len(lb):
        pairs = list(zip(la, lb, strict=True))
    elif len(lb) == 1:
        pairs = [(c, lb[0]) for c in la]
    elif len(la) == 1:
        pairs = [(la[0], c) for c in lb]
    else:
        raise PuzzleError(f"'{op.name}': A has {len(la)} curves and B {len(lb)}; pick a "
                          "step first (At step)", code="invalid")  # fmt: skip
    for ca, cb in pairs:
        if ca.x.shape != cb.x.shape or not np.allclose(
            ca.x, cb.x, rtol=1e-9, atol=1e-12, equal_nan=True
        ):
            raise PuzzleError(f"'{op.name}': A's and B's x values differ; read B at A's "
                              "with Match x values first", code="invalid")  # fmt: skip
    return pairs


FORMULAS = {"divide_by": ("a / b", "{a} ÷ {b}"), "subtract": ("a - b", "{a} − {b}"),
            "multiply": ("a * b", "{a} × {b}"), "add": ("a + b", "{a} + {b}"),
            "utilisation": ("a / b", "Utilisation"), "safety_factor": ("b / a", "Safety factor"),
            "margin": ("b / a - 1", "Margin of safety")}  # fmt: skip


def arithmetic(ctx, op) -> dict[str, Any]:
    """A and B point by point (or two numbers): units followed through."""
    from puzzle.operators.evaluate import _input

    a = _operand(op, _input(ctx, op, "a"), "a")
    b = _operand(op, _input(ctx, op, "b"), "b")
    return {"out": combine(op, op.type, a, b)}


def combine(op, how: str, a, b):
    """A and B (each a Number, a Series or a FieldValue) by `how` (a FORMULAS key)."""
    from puzzle.operators.evaluate import FieldValue, Number

    text, title = FORMULAS[how]
    names = {"a": getattr(a, "label", "") or "A", "b": getattr(b, "label", "") or "B"}
    label = title.format(**names)
    if isinstance(a, Number) and isinstance(b, Number):
        value, kind = _formula(op, text, {"a": (a.value, a.kind), "b": (b.value, b.kind)})
        return Number(float(value), kind, label)
    if isinstance(a, FieldValue) or isinstance(b, FieldValue):
        return field_formula(op, text, {"a": a, "b": b}, label)
    base = a if isinstance(a, Series) else b
    if isinstance(a, Series) and isinstance(b, Series):
        _same_x(op, a, b, "B")
        pairs = _pairs(op, a, b)
        curves = []
        for ca, cb in pairs:
            y, kind = _formula(op, text, {"a": (ca.y, a.y_kind), "b": (cb.y, b.y_kind)})
            curves.append(
                replace(
                    ca,
                    y=np.broadcast_to(y, ca.y.shape).astype(float),
                    label=_name(op, ca.label, label),
                    note="",
                    mark=None,
                )
            )
    else:
        curves, kind = [], None
        number = b if base is a else a
        for c in base.lines:
            ya, yb = (c.y, number.value) if base is a else (number.value, c.y)
            ka, kb = (base.y_kind, number.kind) if base is a else (number.kind, base.y_kind)
            y, kind = _formula(op, text, {"a": (ya, ka), "b": (yb, kb)})
            curves.append(
                replace(
                    c,
                    y=np.broadcast_to(y, c.y.shape).astype(float),
                    label=_name(op, c.label, label),
                    note="",
                    mark=None,
                )
            )
    return replace(base, curves=curves, y_kind=kind, label=label)


def _as_field(base, values, kind: str | None, label: str):
    """`base` (a FieldValue) with new values, of quantity kind `kind`, named `label`."""
    from dataclasses import replace as copy

    item = copy(base.item, key=f"operator:{label}", label=label, unit=kind)
    return copy(base, values=np.asarray(values, float), item=item, label=label)


def _mapped(op, f, base):
    """Field `f` on `base`'s mesh (another study's, another mesh): interpolated in `f`'s
    elements at `base`'s nodes (`transfer.map_field`, nearest outside them). Per-element
    fields don't map: refused."""
    from dataclasses import replace as copy

    from puzzle.results.model import Field
    from puzzle.transfer import map_field

    if f.location != "node" or base.location != "node":
        raise PuzzleError(f"'{op.name}': fields on different meshes are combined per node; "
                          "these are per element", code="unsupported")  # fmt: skip
    every = np.full(len(f.mesh.node_ids), np.nan)  # nodes it leaves out stay out
    order = np.argsort(f.mesh.node_ids)
    pos = np.searchsorted(f.mesh.node_ids, f.ids, sorter=order)
    every[order[pos]] = np.asarray(f.values, float)
    whole = Field("value", "node", [""], f.mesh.node_ids.copy(), every[:, None])
    got = map_field(whole, f.mesh, base.mesh)
    return copy(f, mesh=base.mesh, ids=got.ids, values=got.values[:, 0])


def field_formula(op, text: str, values: dict, label: str):
    """A formula over fields and numbers at every node (or element), units checked:
    fields of the same kind of location; one on another mesh (another study's) mapped
    onto the first's nodes; their common nodes are kept, a node one of them leaves out
    (NaN) stays out."""
    from puzzle.operators.evaluate import FieldValue, Number

    fields = {k: v for k, v in values.items() if isinstance(v, FieldValue)}
    base = next(iter(fields.values()))
    ids = base.ids
    for k, f in fields.items():
        if f.location != base.location:
            raise PuzzleError(f"'{op.name}': {k.upper()} is per {f.location} and the other "
                              f"per {base.location}", code="invalid")  # fmt: skip
        if f.mesh is not base.mesh and not (
            len(f.mesh.node_ids) == len(base.mesh.node_ids)
            and np.array_equal(f.mesh.node_ids, base.mesh.node_ids)
            and np.allclose(f.mesh.coords, base.mesh.coords)
        ):
            fields[k] = f = _mapped(op, f, base)  # another mesh: onto the first's nodes
            values[k] = f
        ids = np.intersect1d(ids, f.ids)
    args = {}
    for k, v in values.items():
        if v is None:
            continue
        if isinstance(v, FieldValue):
            order = np.argsort(v.ids)
            where = order[np.searchsorted(v.ids[order], ids)]
            args[k] = (np.asarray(v.values, float)[where], v.item.unit)
        elif isinstance(v, Number):
            args[k] = (v.value, v.kind)
        else:
            raise PuzzleError(f"'{op.name}': a field can be combined with a field or a "
                              f"number, not {_word(v)}", code="invalid")  # fmt: skip
    with np.errstate(invalid="ignore", divide="ignore"):
        out, kind = _formula(op, text, args)
    out = np.broadcast_to(np.asarray(out, float), ids.shape).astype(float)
    out[~np.isfinite(out)] = np.nan
    from dataclasses import replace as copy

    base = copy(base, ids=ids)
    return _as_field(base, out, kind, label)


def _name(op, curve: str, label: str) -> str:
    """A curve's name after an operator: the step it is at kept ("Utilisation at 2 s")."""
    _, sep, when = curve.partition(" at ")
    return f"{label} at {when}" if sep else label


def normalise(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import FieldValue, _input

    got = _input(ctx, op, "series")
    by = _operand(op, _input(ctx, op, "by"), "by")
    if isinstance(got, FieldValue):
        label = f"{got.label or got.item.label} (normalised)"
        if by is None:
            finite = got.values[np.isfinite(got.values)]
            size = float(np.abs(finite).max()) if finite.size else 0.0
            if not size:
                raise PuzzleError(f"'{op.name}': the field is zero everywhere", code="invalid")
            return {"out": _as_field(got, got.values / size, None, label)}
        return {"out": field_formula(op, "a / b", {"a": got, "b": by}, label)}
    s = as_series(op, got)
    label = f"{s.label} (normalised)"
    if by is None:
        curves = []
        for c in s.lines:
            size = np.nanmax(np.abs(c.y)) if np.isfinite(c.y).any() else 0.0
            if not size:
                raise PuzzleError(f"'{op.name}': {c.label} is zero everywhere", code="invalid")
            curves.append(replace(c, y=c.y / size, label=_name(op, c.label, label)))
        return {"out": replace(s, curves=curves, y_kind=None, label=label)}
    got = combine(op, "divide_by", s, by)
    return {"out": replace(got, label=label,
                           curves=[replace(c, label=_name(op, c.label, label))
                                   for c in got.curves])}  # fmt: skip


def curve_formula(ctx, op) -> dict[str, Any]:
    from puzzle.model.schema import ParameterSet
    from puzzle.model.units import kind_of
    from puzzle.operators.evaluate import _input, _number

    s = as_series(op, _input(ctx, op, "series"))
    if not op.expression.strip():
        raise PuzzleError(f"'{op.name}': write its formula, over x and y (y / 250 MPa)",
                          code="missing_input")  # fmt: skip
    values: dict[str, tuple] = {}
    for ps in ctx.project.of_type(ParameterSet):
        for k, q in ps.values.items():
            unit = q.unit if q.unit != "dimensionless" else ""
            values.setdefault(k, (q.to_si(), kind_of(unit) if unit else None))
    for key in "abc":
        got = _number(op, _input(ctx, op, key), key)
        if got is not None:
            values[key] = (got.value, got.kind)
    curves, kind = [], None
    for c in s.lines:
        y, kind = _formula(op, op.expression, values | {"x": (c.x, s.x_kind), "y": (c.y, s.y_kind)})
        curves.append(replace(c, y=np.broadcast_to(np.asarray(y, float), c.y.shape).copy(),
                              label=_name(op, c.label, op.name)))  # fmt: skip
    return {"out": replace(s, curves=curves, y_kind=kind, label=op.name)}


def field_formula_op(ctx, op) -> dict[str, Any]:
    """Field formula (ADR 0071 step 5): an expression over fields f and g, numbers a, b,
    c and the project's parameters, at every node or element."""
    from puzzle.model.schema import ParameterSet
    from puzzle.model.units import kind_of
    from puzzle.operators.evaluate import FieldValue, Number, _input, _number

    if not op.expression.strip():
        raise PuzzleError(f"'{op.name}': write its formula, over f (and g): f / 250 MPa",
                          code="missing_input")  # fmt: skip
    values: dict[str, Any] = {}
    for ps in ctx.project.of_type(ParameterSet):
        for k, q in ps.values.items():
            unit = q.unit if q.unit != "dimensionless" else ""
            values.setdefault(k, Number(q.to_si(), kind_of(unit) if unit else None, k))
    for key in "abc":
        got = _number(op, _input(ctx, op, key), key)
        if got is not None:
            values[key] = got
    for key in "fg":
        got = _input(ctx, op, key)
        if got is not None and not isinstance(got, FieldValue):
            raise PuzzleError(f"'{op.name}': {key} must be a field (a Filter's, or another "
                              f"field operator's), not {_word(got)}", code="invalid")  # fmt: skip
        if got is not None:
            values[key] = got
    if "f" not in values:
        raise PuzzleError(f"'{op.name}': wire a field into f", code="missing_input")
    return {"out": field_formula(op, op.expression, values, op.name)}


def curve_from_table(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    table = _input(ctx, op, "table")
    return {"out": from_table(table, op.by[0] if op.by else "", op.column, op.name)}


def over_steps(ctx, op) -> dict[str, Any]:
    """A number at every step (or sweep point) of what it reads, as a curve."""
    from puzzle.operators.evaluate import _over_sweep, _steps_table

    sweep = op.against == "sweep"
    table = _over_sweep(ctx, op) if sweep else _steps_table(ctx, op)
    s = from_table(table, table.columns[0], table.columns[1], op.name,
                   along="sweep" if sweep else "steps")  # fmt: skip
    s.x_step = table.columns[0]
    s.curves[0].label = table.columns[1]
    return {"out": s}


def _index(op, count: int) -> int:
    i = op.step if op.step >= 0 else count + op.step
    if not 0 <= i < count:
        raise PuzzleError(f"'{op.name}': there are {count} steps; step {op.step} is not "
                          "one of them", code="invalid")  # fmt: skip
    return i


def at_step(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    s = as_series(op, _input(ctx, op, "series"))
    if s.along:
        raise PuzzleError(f"'{op.name}': {s.label} is already one value per step; read a "
                          "step's value with Result summary, or compare with Compare with "
                          "step", code="invalid")  # fmt: skip
    lines = s.lines
    if len(lines) == 1:
        return {"out": s}
    c = lines[_index(op, len(lines))]
    return {"out": replace(s, curves=[c])}


def compare_steps(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    s = as_series(op, _input(ctx, op, "series"))
    ratio = op.comparison == "ratio"
    kind = None if ratio else s.y_kind
    word = "÷" if ratio else "−"
    if s.along == "steps" or len(s.lines) == 1:
        c = s.lines[0]
        if not s.along:
            raise PuzzleError(f"'{op.name}': {s.label} has one curve; wire a curve per step "
                              "(a path in a run of several steps) or a history",
                              code="invalid")  # fmt: skip
        i = _index(op, c.y.size)
        ref = c.y[i]
        y = c.y / ref if ratio else c.y - ref
        label = f"{s.label} {word} step {i + 1}"
        return {"out": replace(s, curves=[replace(c, y=y, label=label)], y_kind=kind,
                               label=label)}  # fmt: skip
    lines = s.lines
    i = _index(op, len(lines))
    ref = lines[i]
    curves = []
    for k, c in enumerate(lines):
        if k == i:
            continue
        r = ref.y if c.x.shape == ref.x.shape and np.allclose(c.x, ref.x) \
            else _interp(ref.x, ref.y, c.x)  # fmt: skip
        with np.errstate(divide="ignore", invalid="ignore"):
            y = c.y / r if ratio else c.y - r
        curves.append(replace(c, y=y, label=f"{c.label} {word} step {i + 1}"))
    return {"out": replace(s, curves=curves, y_kind=kind,
                           label=f"{s.label} {word} step {i + 1}")}  # fmt: skip


def _interp(x, y, at) -> np.ndarray:
    """y read at `at` (linear; NaN past the ends)."""
    order = np.argsort(x)
    x, y = np.asarray(x, float)[order], np.asarray(y, float)[order]
    keep = np.isfinite(x) & np.isfinite(y)
    if not keep.any():
        return np.full(np.shape(at), np.nan)
    return np.interp(at, x[keep], y[keep], left=np.nan, right=np.nan)


def envelope(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    parts = [as_series(op, _input(ctx, op, "series"))]
    for key in "bc":
        got = _input(ctx, op, key)
        if got is not None:
            parts.append(as_series(op, got, f"curve {'bc'.index(key) + 2}"))
    every = together(op, parts)
    lines = every.lines
    base = lines[0]
    stack = np.vstack([c.y if c.x.shape == base.x.shape and np.allclose(c.x, base.x)
                       else _interp(c.x, c.y, base.x) for c in lines])  # fmt: skip
    how = {"max": (np.nanmax, "Largest"), "min": (np.nanmin, "Smallest"),
           "mean": (np.nanmean, "Mean")}.get(op.reduction)  # fmt: skip
    if how is None:
        raise PuzzleError(f"'{op.name}': an envelope is the largest, smallest or mean "
                          "value at each x", code="invalid")  # fmt: skip
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns
        y = how[0](stack, axis=0)
    label = f"{how[1]} {every.label.lower()}"
    return {"out": replace(every, curves=[Curve(base.x, y, label)], label=label)}


def match_x(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    s = as_series(op, _input(ctx, op, "series"))
    onto = as_series(op, _input(ctx, op, "onto"), "onto")
    _same_x(op, s, onto, "onto")
    at = onto.lines[0].x
    curves = [replace(c, x=at.copy(), y=_interp(c.x, c.y, at)) for c in s.lines]
    return {"out": replace(s, curves=curves)}


def _product_kind(op, a: str | None, b: str | None, text: str) -> str | None:
    _, kind = _formula(op, text, {"a": (1.0, a), "b": (1.0, b)})
    return kind


def area_under(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import Number, _input

    s = as_series(op, _input(ctx, op, "series"))
    lines = s.lines
    if len(lines) != 1:
        raise PuzzleError(f"'{op.name}': {s.label} has {len(lines)} curves; pick one with "
                          "At step, or take their Envelope", code="invalid")  # fmt: skip
    c = lines[0]
    keep = np.isfinite(c.x) & np.isfinite(c.y)
    order = np.argsort(c.x[keep])
    x, y = c.x[keep][order], c.y[keep][order]
    area = float(np.sum((y[1:] + y[:-1]) * np.diff(x)) / 2) if x.size > 1 else 0.0
    kind = _product_kind(op, s.y_kind, s.x_kind, "a * b")
    return {"out": Number(area, kind, f"Area under {s.label.lower()}")}


def slope(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input

    s = as_series(op, _input(ctx, op, "series"))
    kind = _product_kind(op, s.y_kind, s.x_kind, "a / b")
    label = f"Slope of {s.label.lower()}"
    curves = []
    for c in s.lines:
        if c.x.size < 2:
            raise PuzzleError(f"'{op.name}': {c.label} has fewer than two points",
                              code="invalid")  # fmt: skip
        with np.errstate(all="ignore"):
            y = np.gradient(c.y, c.x)
        curves.append(replace(c, y=y, label=_name(op, c.label, label), note="", mark=None))
    return {"out": replace(s, curves=curves, y_kind=kind, label=label)}


def limit_line(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input, _number

    got = _number(op, _input(ctx, op, "value"), "value")
    label = got.label or op.name
    if op.name and got.label and op.name != got.label:
        label = op.name
    curve = Curve(np.array([], float), np.array([got.value]), label, style="limit")
    return {"out": Series([curve], None, got.kind, "x", label)}


def annotate(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input, quantity_text

    s = as_series(op, _input(ctx, op, "series"))
    if op.reduction not in ("max", "min"):
        raise PuzzleError(f"'{op.name}': it marks the largest or the smallest point",
                          code="invalid")  # fmt: skip
    smallest = op.reduction == "min"
    best, where = None, None
    for k, c in enumerate(s.lines):
        if not np.isfinite(c.y).any():
            continue
        i = int(np.nanargmin(c.y) if smallest else np.nanargmax(c.y))
        if best is None or (c.y[i] < best if smallest else c.y[i] > best):
            best, where = float(c.y[i]), (k, i)
    if where is None:
        raise PuzzleError(f"'{op.name}': {s.label} has no values", code="not_found")
    k, i = where
    lines = list(s.lines)
    c = lines[k]
    note = op.note or f"{'min' if smallest else 'max'} {quantity_text(best, s.y_kind)}"
    lines[k] = replace(c, note=note, mark=(float(c.x[i]), best))
    limits = [c for c in s.curves if c.style == "limit"]
    return {"out": replace(s, curves=lines + limits)}


def evaluators() -> dict:
    ops = {k: arithmetic for k in FORMULAS}
    return ops | {"normalise": normalise, "curve_formula": curve_formula,
                  "field_formula": field_formula_op,
                  "curve_from_table": curve_from_table, "over_steps": over_steps,
                  "at_step": at_step, "compare_steps": compare_steps, "envelope": envelope,
                  "match_x": match_x, "area_under": area_under, "slope": slope,
                  "limit_line": limit_line, "annotate": annotate}  # fmt: skip
