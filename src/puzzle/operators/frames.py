"""Tables as polars frames (ADR 0064): the sources that make them (a run's results, a
sweep's or optimisation's points, files) and the operators that shape them (columns,
filter rows, sort, top N, new column, convert units, round, group and aggregate) and
take a number out of them.

A table's values are SI and each column has a quantity kind (`Table.kinds`), so units
travel with the data: a filter keeps them, a new column's comes from its expression,
an aggregate keeps its column's (a count has none). The views and exports show display
units.

Expressions come in two syntaxes (review answer 2):

- **Puzzle's** (`syntax = "puzzle"`): the Expression operator's language over the
  columns by name, units checked (`von_mises > 200 MPa and part == "Bracket"`).
- **polars'** (`syntax = "polars"`): `pl.col("von_mises") > 2e8`. It is parsed with
  `ast` and built from an allow-list of polars' expression API, never `eval`'d, so
  it needs no trust gate. Its columns are SI and no unit is checked.
"""

from __future__ import annotations

import ast
import glob
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from puzzle.errors import PuzzleError

AGGREGATIONS = ("max", "min", "mean", "median", "sum", "std", "count", "first", "last",
                "quantile")  # fmt: skip
KINDLESS = ("count",)  # aggregates that have no unit
# "max(von_mises)", "quantile(von_mises, 0.95)", "mean(stress_xx) as avg"
AGGREGATION = re.compile(
    r"\s*(\w+)\(\s*([^,)]*?)\s*(?:,\s*([\d.]+)\s*)?\)\s*(?:as\s+(\w[\w ]*))?\s*"
)


def _pl():
    import polars as pl

    return pl


def _ev():
    from puzzle.operators import evaluate as ev

    return ev


def _table(op, got):
    ev = _ev()
    if isinstance(got, ev.Table):
        return got
    if got is None:
        raise PuzzleError(f"'{op.name}': a table is wired into it", code="inconsistent")
    raise PuzzleError(f"'{op.name}': needs a table, not a {got.type}", code="invalid")


def _kinds(table) -> dict[str, str | None]:
    return dict(zip(table.columns, table.kinds, strict=False))


def _made(frame, kinds: dict[str, str | None]):
    return _ev().Table(frame=frame, kinds=[kinds.get(c) for c in frame.columns])


def _names(op, spec: list[str], table) -> list[str]:
    missing = [c for c in spec if c not in table.columns]
    if missing:
        raise PuzzleError(
            f"'{op.name}': the table has no column {', '.join(repr(c) for c in missing)}",
            code="not_found", details={"columns": table.columns})  # fmt: skip
    return spec


# --- sources ---------------------------------------------------------------------------


def _parts(mesh) -> dict[str, Any]:
    return {name: g for name, g in mesh.groups.items() if "/" not in name}


def _node_parts(mesh, ids: np.ndarray) -> list[str | None]:
    out: list[str | None] = [None] * len(ids)
    for name, g in _parts(mesh).items():
        inside = np.isin(ids, g.node_ids)
        for i in np.flatnonzero(inside):
            if out[i] is None:
                out[i] = name
    return out


def _element_places(mesh, ids: np.ndarray) -> tuple[np.ndarray, list[str | None]]:
    """Each element's centre (the mean of its nodes) and part."""
    owner = {e: name for name, g in _parts(mesh).items() for e in g.entities}
    centre, part = {}, {}
    for b in mesh.blocks:
        xyz = mesh.node_coords(b.connectivity.ravel()).reshape(*b.connectivity.shape, 3)
        for eid, c in zip(b.ids, xyz.mean(axis=1), strict=True):
            centre[int(eid)] = c
            part[int(eid)] = owner.get(b.entity)
    pts = np.array([centre.get(int(i), (np.nan,) * 3) for i in ids], float).reshape(-1, 3)
    return pts, [part.get(int(i)) for i in ids]


STEP_COLUMNS = {"transient": ("time", "time"), "frequency_domain": ("frequency", "frequency"),
                "modal": ("frequency", "frequency"), "buckling": ("load factor", None)}  # fmt: skip


def result_table(ctx, op):
    """A run's (or derived result's) results as a table: an id, x, y, z, the part, a
    step column, and each quantity, one row per node or element per step (long form;
    `layout` wide puts a column per step)."""
    from puzzle.post.quantities import compute, default

    ev = _ev()
    pl = _pl()
    given = ev._input(ctx, op, "result")
    if isinstance(given, ev.FieldValue):  # a filter's field: those values, at its step
        fv = given
        keep = np.isfinite(fv.values)
        steps = [(1, fv.step, {fv.item.key: (fv.ids[keep], fv.values[keep], fv.item)})]
        location, mesh = fv.location, fv.mesh
    elif isinstance(given, ev.ResultValue):
        items = given.items
        if given.field:
            items = [i for i in items if i.field == given.field] or items
        wanted = [k for k in op.quantities if k != "*"]
        if "*" in op.quantities:
            chosen = items
        elif wanted:
            chosen = []
            for k in wanted:
                item = next((i for i in given.items if i.key == k), None)
                if item is None:
                    raise PuzzleError(f"'{op.name}': {given.label} has no '{k}'",
                                      code="not_found",
                                      details={"quantities": [i.key for i in items]})  # fmt: skip
                chosen.append(item)
        else:
            first = default(items)
            if first is None:
                raise PuzzleError(f"'{op.name}': {given.label} has no results", code="not_found")
            chosen = [first]
        mesh = given.mesh
        every = list(given.results.steps)
        picked = (every if op.steps == "all"
                  else [ev._step(ctx, given.results, chosen[0], op.step)])  # fmt: skip
        steps, location = [], None
        for st in picked:
            number = next(n for n, s in enumerate(every, 1) if s is st)  # by position
            row = {}
            for item in chosen:
                f = st.fields.get(item.field)
                if f is None:
                    continue
                if location is None:
                    location = f.location
                elif f.location != location:
                    raise PuzzleError(
                        f"'{op.name}': {item.label} is per {f.location} and the others per "
                        f"{location}: make a table of each", code="invalid")  # fmt: skip
                row[item.key] = (f.ids, np.asarray(compute(item, f.values, f.components),
                                                   float), item)  # fmt: skip
            steps.append((number, st, row))
        location = location or "node"
    else:
        raise PuzzleError(f"'{op.name}' needs a result wired in", code="invalid")
    frames, kinds = [], {}
    idcol = "node" if location == "node" else "element"
    for number, st, row in steps:
        if not row:
            continue
        ids = next(iter(row.values()))[0]
        if op.selection:
            from puzzle.post.probes import _group

            group = _group(mesh, op.selection)
            if location == "element":
                cells = [b.ids for b in group.blocks(mesh) if b.dim == group.dim]
                keep = np.isin(ids, np.concatenate(cells) if cells else [])
            else:
                keep = np.isin(ids, group.node_ids)
        else:
            keep = np.ones(len(ids), bool)
        ids = ids[keep]
        if location == "node":
            xyz, part = mesh.node_coords(ids), _node_parts(mesh, ids)
        else:
            xyz, part = _element_places(mesh, ids)
        cols: dict[str, Any] = {
            idcol: ids.astype(np.int64),
            "x": xyz[:, 0],
            "y": xyz[:, 1],
            "z": xyz[:, 2],
            "part": part,
            "step": [number] * len(ids),
        }
        kinds.update({"x": "length", "y": "length", "z": "length"})
        name, kind = STEP_COLUMNS.get(st.kind, (None, None))
        if name is not None and st.value is not None:
            cols[name] = [float(st.value)] * len(ids)
            kinds[name] = kind
        for fids, vals, item in row.values():
            lookup = dict(zip(fids.tolist(), vals.tolist(), strict=True))
            label = _column_name(item)
            cols[label] = [lookup.get(int(i), math.nan) for i in ids]
            kinds[label] = item.unit
        frames.append(pl.DataFrame(cols))
    if not frames:
        raise PuzzleError(f"'{op.name}': nothing to put in the table", code="not_found")
    frame = pl.concat(frames, how="diagonal_relaxed")
    layout = op.layout
    if layout == "setting":
        from puzzle.settings import current

        layout = current().get("results.table_layout")
    if layout == "wide" and len(frames) > 1:
        frame, kinds = _wide(frame, kinds, idcol)
    return _made(frame, kinds)


def _column_name(item) -> str:
    """A quantity as a column name: its key's words, in snake case (von_mises)."""
    from puzzle.post.derived import name_of

    try:
        return name_of(item)
    except Exception:  # noqa: BLE001 - any listed result still gets a column
        return item.key.replace(":", "_")


def _wide(frame, kinds, idcol):
    """A column per step for each result ("von_mises · step 2"), a row per item."""
    keep = [c for c in (idcol, "x", "y", "z", "part") if c in frame.columns]
    values = [c for c in frame.columns if c not in keep
              and c not in ("step", "time", "frequency", "load factor")]  # fmt: skip
    out, new = None, {c: kinds.get(c) for c in keep}
    for v in values:
        piv = frame.pivot(on="step", index=keep, values=v)
        names = {c: f"{v} · step {c}" for c in piv.columns if c not in keep}
        piv = piv.rename(names)
        new.update({n: kinds.get(v) for n in names.values()})
        out = piv if out is None else out.join(piv, on=keep, how="full", coalesce=True)
    return out, new


def points_table(ctx, obj):
    """A swept study's or an optimisation's points (ADR 0018, 0063): one row per point,
    its variables, outputs, feasible and the run."""
    from puzzle.model.schema import Optimisation, Study

    pl = _pl()
    if isinstance(obj, Optimisation):
        from puzzle import optimise

        pts = optimise.points(ctx.project, ctx.store, obj)
        names = {o.id: o.name for o in ctx.project.objects.values()}
        heads = [names.get(o.source, k) for k, o in zip(
            optimise.output_keys(obj), ([obj.objective] if obj.objective else [])
            + list(obj.constraints), strict=True)]  # fmt: skip
        cols: dict[str, list] = {"point": [p.index for p in pts]}
        kinds: dict[str, str | None] = {}
        for v in obj.variables:
            cols[v.name] = [p.values.get(v.name) for p in pts]
            q = optimise.quantity(v, 0.0, ctx.project)
            kinds[v.name] = _kind_of_unit(q.unit)
        for key, head in zip(optimise.output_keys(obj), heads, strict=True):
            cols[head] = [optimise.output_of(p, key) for p in pts]
            kinds[head] = next((p.kinds.get(key) for p in pts if p.kinds.get(key)), None)
        cols["feasible"] = [p.feasible for p in pts]
        cols["reason"] = [p.reason for p in pts]
        cols["run"] = [next(iter(p.runs.values()), None) for p in pts]
        return _made(pl.DataFrame(cols, strict=False), kinds)
    if isinstance(obj, Study):
        runs = [r for r in ctx.store.runs() if r.study_id == str(obj.id)
                and r.status == "completed" and r.point_labels()]  # fmt: skip
        labels = sorted({k for r in runs for k in r.point_labels()})
        cols = {"point": list(range(1, len(runs) + 1))}
        for k in labels:
            cols[k] = [r.point_labels().get(k) for r in runs]
        cols["solver"] = [r.solver for r in runs]
        cols["run"] = [r.run_id for r in runs]
        return _made(pl.DataFrame(cols, strict=False), {})
    raise PuzzleError("points come from a swept study or an optimisation", code="invalid")


def _kind_of_unit(unit: str) -> str | None:
    from puzzle.model.units import kind_of

    return kind_of(unit) if unit and unit != "dimensionless" else None


def file_table(ctx, op):
    """A file (or a folder's files, by a pattern) in the project: CSV with `name [unit]`
    headers (as a Table operator's), Parquet or Excel. Several files stack, with a
    `file` column saying which."""
    from puzzle.model.units import to_si

    ev = _ev()
    pl = _pl()
    root = ctx.root
    if root is None:  # the project folder: the store's parent
        store_root = getattr(ctx.store, "root", None)
        root = Path(store_root).parent if store_root not in (None, "memory") else Path(".")
    if not op.file:
        raise PuzzleError(f"'{op.name}': which file?", code="inconsistent")
    pattern = (Path(root) / op.file).resolve()
    if not str(pattern).startswith(str(Path(root).resolve())):
        raise PuzzleError(f"'{op.name}': the file must be inside the project", code="invalid")
    paths = sorted(Path(p) for p in glob.glob(str(pattern)))
    if not paths:
        raise PuzzleError(f"'{op.name}': no file '{op.file}' in the project", code="not_found")
    frames, kinds = [], {}
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in (".csv", ".txt", ".dat", ".tsv"):
            table = ev.parse_table(path.read_text(encoding="utf-8"))
            frame, k = table.frame, _kinds(table)
        elif suffix == ".parquet":
            frame, k = _with_units(pl.read_parquet(path), to_si)
        elif suffix in (".xlsx", ".xlsm"):
            frame, k = _with_units(pl.read_excel(path, engine="openpyxl"), to_si)
        else:
            raise PuzzleError(f"'{op.name}': {path.name} is not CSV, Parquet or Excel",
                              code="unsupported")  # fmt: skip
        kinds.update(k)
        if len(paths) > 1:
            frame = frame.with_columns(pl.lit(path.name).alias("file"))
        frames.append(frame)
    return _made(pl.concat(frames, how="diagonal_relaxed"), kinds)


def _with_units(frame, to_si):
    """Headers "name [unit]" become the name, and the column SI with its kind."""
    pl = _pl()
    from puzzle.model.units import known

    kinds, exprs = {}, []
    for c in frame.columns:
        name, _, unit = c.partition("[")
        name, unit = name.strip(), unit.rstrip("]").strip()
        if unit and known(unit) and frame[c].dtype.is_numeric():
            factor = to_si(1.0, unit)
            offset = to_si(0.0, unit)
            exprs.append((pl.col(c) * (factor - offset) + offset).alias(name))
            kinds[name] = _kind_of_unit(unit)
        else:
            exprs.append(pl.col(c).alias(name or c))
    return frame.select(exprs), kinds


# --- frame operators -------------------------------------------------------------------


def columns(ctx, op):
    """Pick, order and rename columns: `pick` in order ("x", "von_mises as vm"); `drop`."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    kinds, frame = _kinds(t), t.frame
    if op.pick:
        exprs, new = [], {}
        for spec in op.pick:
            name, _, alias = spec.partition(" as ")
            name, alias = name.strip(), alias.strip() or name.strip()
            _names(op, [name], t)
            exprs.append(_pl().col(name).alias(alias))
            new[alias] = kinds.get(name)
        frame, kinds = frame.select(exprs), new
    if op.drop:
        frame = frame.drop(_names(op, list(op.drop), t))
    return {"out": _made(frame, kinds)}


def filter_rows(ctx, op):
    """The rows where a condition holds: Puzzle's expression or polars'."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    if not op.condition.strip():
        return {"out": t}
    if op.syntax == "polars":
        frame = t.frame.filter(polars_expression(op, op.condition, t))
    else:
        mask, _ = _puzzle(op, op.condition, t)
        if np.ndim(mask) == 0:
            mask = np.full(t.frame.height, bool(mask))
        frame = t.frame.filter(_pl().Series(np.asarray(mask, bool)))
    return {"out": _made(frame, _kinds(t))}


def sort(ctx, op):
    """Rows in order of one or more columns, ascending or descending (`descending`)."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    by = _names(op, list(op.by) or [t.columns[0]], t)
    frame = t.frame.sort(by, descending=op.descending, nulls_last=True)
    return {"out": _made(frame, _kinds(t))}


def top_n(ctx, op):
    """The N rows with the largest (or smallest) value of a column, per group of `by`."""
    ev = _ev()
    t = _table(op, ev._input(ctx, op, "table"))
    col = _names(op, [op.column or t.columns[-1]], t)[0]
    count = ev._typed_number(ctx, op, "count")
    n = int(count.value) if count is not None else 10
    ordered = t.frame.sort(col, descending=op.largest, nulls_last=True)
    if op.by:
        _names(op, list(op.by), t)
        frame = ordered.group_by(list(op.by), maintain_order=True).head(n)
    else:
        frame = ordered.head(n)
    return {"out": _made(frame, _kinds(t))}


def new_column(ctx, op):
    """A column from an expression over the others: Puzzle's, its unit worked out and
    checked; or polars', its unit given (`unit`)."""
    pl = _pl()
    t = _table(op, _ev()._input(ctx, op, "table"))
    name = op.new_name.strip() or "value"
    kinds = _kinds(t)
    if op.syntax == "polars":
        expr = polars_expression(op, op.expression, t)
        frame = t.frame.with_columns(expr.alias(name))
        kinds[name] = _kind_of_unit(op.unit) if op.unit else None
        if op.unit:
            from puzzle.model.units import to_si

            frame = frame.with_columns((pl.col(name) * to_si(1.0, op.unit)).alias(name))
    else:
        values, kind = _puzzle(op, op.expression, t)
        values = np.broadcast_to(np.asarray(values, float), (t.frame.height,))
        frame = t.frame.with_columns(pl.Series(name, values))
        kinds[name] = kind
    return {"out": _made(frame, kinds)}


def convert_units(ctx, op):
    """A column shown in another unit of its kind: the value stays SI inside (the table's
    rule); the column's display unit is set, and an export writes it in that unit."""
    from puzzle.model.units import kind_of, known

    t = _table(op, _ev()._input(ctx, op, "table"))
    col = _names(op, [op.column or t.columns[-1]], t)[0]
    unit = op.unit.strip()
    if not unit or not known(unit):
        raise PuzzleError(f"'{op.name}': unknown unit '{unit}'", code="invalid")
    have = _kinds(t).get(col)
    if have is not None and kind_of(unit) != have:
        raise PuzzleError(f"'{op.name}': {col} is a {have}, and {unit} is not",
                          code="invalid")  # fmt: skip
    out = _made(t.frame, _kinds(t))
    out.units = dict(t.units, **{col: unit})
    return {"out": out}


def round_values(ctx, op):
    """Numbers rounded in their shown units (display or converted): `decimals` places,
    or that many significant figures (`significant`). Values stay SI inside."""
    pl = _pl()
    t = _table(op, _ev()._input(ctx, op, "table"))
    cols = _names(op, list(op.pick), t) if op.pick else [
        c for c in t.columns if t.frame[c].dtype.is_float()]  # fmt: skip
    exprs = []
    for c in cols:
        factor = _shown_factor(t, c)  # SI → shown
        x = pl.col(c) * factor
        if op.significant:
            mag = (x.abs().log10().floor() + 1).fill_nan(1).fill_null(1)
            scale = 10.0 ** (op.decimals - mag)
            x = (x * scale).round(0) / scale
        else:
            x = x.round(op.decimals)
        exprs.append((x / factor).alias(c))
    return {"out": _with_display(_made(t.frame.with_columns(exprs), _kinds(t)), t)}


def _with_display(new, old):
    new.units = dict(old.units)
    return new


def _shown_factor(t, col) -> float:
    """SI → the unit the column is shown in (its converted unit, else display units)."""
    from puzzle.model.units import DIMENSIONS, convert

    kind = _kinds(t).get(col)
    if kind is None:
        return 1.0
    unit = t.units.get(col) or _ev().display_unit(kind)
    return convert(1.0, DIMENSIONS[kind][1], unit) if unit else 1.0


def group(ctx, op):
    """Rows grouped by columns (`by`: part, step, point, solver), each group reduced:
    `aggregations` such as "max(von_mises)", "count()", "mean(stress_xx) as avg",
    "quantile(von_mises, 0.95)". Without `by`, the whole table is one group."""
    pl = _pl()
    t = _table(op, _ev()._input(ctx, op, "table"))
    if op.by:
        _names(op, list(op.by), t)
    kinds = _kinds(t)
    exprs, new = [], {c: kinds.get(c) for c in op.by}
    for spec in op.aggregations or ["count()"]:
        m = AGGREGATION.fullmatch(spec)
        if not m or m.group(1) not in AGGREGATIONS:
            raise PuzzleError(f"'{op.name}': '{spec}' isn't an aggregation; write "
                              f"{', '.join(a + '(column)' for a in AGGREGATIONS[:4])}, count() "
                              "or quantile(column, 0.95)", code="invalid")  # fmt: skip
        fn, col, q, alias = m.group(1), m.group(2), m.group(3), m.group(4)
        if fn == "count":
            e = pl.len()
            name = alias or "count"
        else:
            _names(op, [col], t)
            c = pl.col(col)
            e = c.quantile(float(q or 0.5)) if fn == "quantile" else getattr(c, fn)()
            name = alias or f"{fn} {col}"
        exprs.append(e.alias(name))
        new[name] = None if fn in KINDLESS else kinds.get(col)
    frame = (t.frame.group_by(list(op.by), maintain_order=True).agg(exprs) if op.by
             else t.frame.select(exprs))  # fmt: skip
    if op.by:
        frame = frame.sort(list(op.by))
    return {"out": _made(frame, new)}


def to_number(ctx, op):
    """One value of a column: its max, min, mean, sum, median, first or last; or, with
    `by`, the value of `column` in the row where `by`'s column is largest."""
    ev = _ev()
    t = _table(op, ev._input(ctx, op, "table"))
    col = _names(op, [op.column or t.columns[-1]], t)[0]
    s = t.frame[col].drop_nulls()
    if s.len() == 0:
        raise PuzzleError(f"'{op.name}': {col} has no values", code="not_found")
    kind = _kinds(t).get(col)
    if op.by:
        key = _names(op, [op.by[0]], t)[0]
        i = int(t.frame[key].arg_max())
        return {"out": ev.Number(float(t.frame[col][i]), kind, f"{col} where {key} is largest")}
    how = op.reduction if op.reduction in ("max", "min", "mean", "sum", "median", "first",
                                           "last", "count") else "max"  # fmt: skip
    value = float(s.len()) if how == "count" else float(getattr(s, how)())
    return {"out": ev.Number(value, None if how == "count" else kind, f"{how} {col}")}


# --- expressions -----------------------------------------------------------------------


def _puzzle(op, text: str, t):
    """Puzzle's expression over the table's columns (arrays), units checked."""
    from puzzle.operators.expression import evaluate

    values = {}
    for c, kind in zip(t.columns, t.kinds, strict=False):
        s = t.frame[c]
        name = c.replace(" ", "_")
        if s.dtype.is_numeric():
            values[name] = (s.cast(float).fill_null(math.nan).to_numpy(), kind)
        elif s.dtype == _pl().Boolean:
            values[name] = (s.cast(float).to_numpy(), None)
        elif s.dtype.is_temporal():  # dates as ISO text: started > "2026-11-01" compares
            values[name] = (np.asarray([None if v is None else v.isoformat()
                                        for v in s.to_list()], dtype=object), "text")  # fmt: skip
        else:
            values[name] = (np.asarray(s.to_list(), dtype=object), "text")
    try:
        return evaluate(text, values)
    except PuzzleError as exc:
        raise PuzzleError(f"'{op.name}': {exc.message}", code=exc.code) from None


# polars' expression API that a typed polars expression may use: no I/O, no Python.
POLARS_FUNCTIONS = {"col", "lit", "when", "max", "min", "sum", "mean", "len", "all", "any"}
POLARS_METHODS = {
    "abs", "sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan",
    "round", "floor", "ceil", "clip", "is_between", "is_in", "is_null", "is_not_null",
    "is_nan", "fill_null", "fill_nan", "max", "min", "mean", "median", "sum", "std", "count",
    "first", "last", "quantile", "cum_sum", "cum_max", "cum_min", "cum_count", "rolling_mean",
    "rolling_max", "rolling_min", "rolling_sum", "rank", "alias", "over", "then",
    "otherwise", "when", "pow", "diff", "shift", "n_unique", "unique", "sign",
    "contains", "starts_with", "ends_with", "to_lowercase", "to_uppercase", "str",
}  # fmt: skip


def polars_expression(op, text: str, t):
    """A polars expression typed as text, built by walking its syntax tree: only
    `pl.<function>` from an allow-list, methods from an allow-list, numbers, strings,
    lists and arithmetic. Never `eval`."""
    import operator

    pl = _pl()
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise PuzzleError(f"'{op.name}': not a polars expression: {exc.msg}",
                          code="invalid") from None  # fmt: skip
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
              ast.BitAnd: operator.and_, ast.BitOr: operator.or_}  # fmt: skip
    compare = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}  # fmt: skip

    def refuse(what: str):
        raise PuzzleError(f"'{op.name}': {what} isn't allowed in a polars expression here "
                          "(pl.col(...), pl.lit(...), pl.when(...) and expression methods "
                          "only)", code="invalid")  # fmt: skip

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            return [walk(e) for e in node.elts]
        if isinstance(node, ast.UnaryOp):
            v = walk(node.operand)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.Invert):
                return ~v
            if isinstance(node.op, ast.Not):
                return ~v
            return v
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.BoolOp):
            vals = [walk(v) for v in node.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(node.op, ast.And) else (out | v)
            return out
        if isinstance(node, ast.Compare):
            left, out = walk(node.left), None
            for o, right in zip(node.ops, node.comparators, strict=True):
                if type(o) not in compare:
                    refuse(type(o).__name__)
                r = walk(right)
                part = compare[type(o)](left, r)
                out = part if out is None else (out & part)
                left = r
            return out
        if isinstance(node, ast.Call):
            fn = node.func
            args = [walk(a) for a in node.args]
            kwargs = {k.arg: walk(k.value) for k in node.keywords if k.arg}
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id == "pl":  # fmt: skip
                if fn.attr not in POLARS_FUNCTIONS:
                    refuse(f"pl.{fn.attr}")
                if fn.attr == "col":
                    for a in args:
                        if isinstance(a, str) and a not in t.columns:
                            raise PuzzleError(f"'{op.name}': the table has no column '{a}'",
                                              code="not_found",
                                              details={"columns": t.columns})  # fmt: skip
                return getattr(pl, fn.attr)(*args, **kwargs)
            if isinstance(fn, ast.Attribute):
                target = walk(fn.value)
                if fn.attr not in POLARS_METHODS or not hasattr(target, fn.attr):
                    refuse(f".{fn.attr}()")
                return getattr(target, fn.attr)(*args, **kwargs)
            refuse("a call to " + ast.unparse(fn))
        if isinstance(node, ast.Attribute):  # a namespace such as .str
            target = walk(node.value)
            if node.attr not in POLARS_METHODS or not hasattr(target, node.attr):
                refuse(f".{node.attr}")
            return getattr(target, node.attr)
        refuse(type(node).__name__)

    got = walk(tree)
    if not isinstance(got, pl.Expr):
        got = pl.lit(got)
    return got


# --- exports ---------------------------------------------------------------------------


def write(table, path: Path, fmt: str) -> Path:
    """A table to CSV, Parquet or Excel, in its shown units, each header "name [unit]"."""
    frame = shown_frame(table)
    if fmt == "parquet":
        frame.write_parquet(path)
    elif fmt == "xlsx":
        frame.write_excel(path, worksheet="Puzzle", autofit=True)
    else:  # nine significant figures, as the Table operator's CSV
        import csv

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(frame.columns)
            for r in frame.rows():
                w.writerow(["" if v is None else f"{v:.9g}" if isinstance(v, float) else v
                            for v in r])  # fmt: skip
    return path


def shown_frame(table):
    """The table in its shown units (converted, else display units), headers with them."""
    from puzzle.model.units import DIMENSIONS, convert

    pl = _pl()
    exprs = []
    for c, kind in zip(table.columns, table.kinds, strict=False):
        if kind is None or not table.frame[c].dtype.is_numeric():
            exprs.append(pl.col(c))
            continue
        unit = table.units.get(c) or _ev().display_unit(kind)
        factor = convert(1.0, DIMENSIONS[kind][1], unit)
        offset = convert(0.0, DIMENSIONS[kind][1], unit)
        exprs.append((pl.col(c) * (factor - offset) + offset).alias(f"{c} [{unit}]"))
    return table.frame.select(exprs)
