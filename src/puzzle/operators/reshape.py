"""Tables combined and reshaped (ADR 0064 phase 2): join two tables on key columns,
stack several, pivot long to wide and back, keep distinct rows, fill missing values,
and running or rolling sums, means and extremes. Units travel as in `frames`: a joined
column keeps its kind, a stack needs the same kind per column, a pivot's columns take
their values' kind, and an unpivot needs one kind across what it folds.
"""

from __future__ import annotations

from typing import Any

from puzzle.errors import PuzzleError
from puzzle.operators.frames import _ev, _kinds, _made, _names, _pl, _table


def _second(ctx, op, key: str):
    got = _ev()._input(ctx, op, key)
    return None if got is None else _table(op, got)


def join(ctx, op) -> dict[str, Any]:
    """Two tables side by side, matched on `by` (node, point, step): `how` inner (rows
    in both), left (every row of the first) or full (every row of either). A column in
    both, not a key, gets the second table's name after it ("von_mises (Code_Aster)")
    or `suffix`."""
    a = _table(op, _ev()._input(ctx, op, "table"))
    b = _second(ctx, op, "other")
    if b is None:
        raise PuzzleError(f"'{op.name}': the second table isn't wired", code="inconsistent")
    keys = list(op.by) or [c for c in a.columns if c in b.columns][:1]
    if not keys:
        raise PuzzleError(f"'{op.name}': the tables share no column to join on",
                          code="inconsistent")  # fmt: skip
    _names(op, keys, a)
    _names(op, keys, b)
    ka, kb = _kinds(a), _kinds(b)
    for k in keys:
        if ka.get(k) != kb.get(k):
            raise PuzzleError(f"'{op.name}': {k} is a {ka.get(k)} in one and a {kb.get(k)} "
                              "in the other", code="invalid")  # fmt: skip
    suffix = op.suffix or f" ({_label(ctx, op, 'other')})"
    frame = a.frame.join(b.frame, on=keys, how=op.how, suffix=suffix,
                         coalesce=True)  # fmt: skip
    kinds = dict(ka)
    for c in b.columns:
        if c in keys:
            continue
        kinds[c + suffix if c in a.columns else c] = kb.get(c)
    return {"out": _made(frame, kinds)}


def _label(ctx, op, key: str) -> str:
    inp = op.inputs.get(key)
    obj = ctx.project.objects.get(inp.source) if inp is not None and inp.source else None
    return getattr(obj, "name", key)


def stack(ctx, op) -> dict[str, Any]:
    """Tables' rows one after another (a column of each matches by name; one missing is
    empty), with a column (`new_name`, default "from") naming each row's table."""
    pl = _pl()
    parts, kinds = [], {}
    name = op.new_name.strip() or "from"
    for key in ("table", "other", "third"):
        got = _second(ctx, op, key)
        if got is None:
            continue
        for c, k in _kinds(got).items():
            if c in kinds and kinds[c] != k and None not in (k, kinds[c]):
                raise PuzzleError(f"'{op.name}': {c} is a {kinds[c]} in one table and a "
                                  f"{k} in another", code="invalid")  # fmt: skip
            kinds.setdefault(c, k)
        parts.append(got.frame.with_columns(pl.lit(_label(ctx, op, key)).alias(name)))
    if not parts:
        raise PuzzleError(f"'{op.name}': wire the tables to stack", code="inconsistent")
    return {"out": _made(pl.concat(parts, how="diagonal_relaxed"), kinds)}


def pivot(ctx, op) -> dict[str, Any]:
    """Long to wide: a row per `by` (the index columns), a column per value of `column`
    (step, solver), filled from `pick` (the values; default every other number), the
    first where several meet (or `reduction`: max, min, mean, sum)."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    on = _names(op, [op.column or t.columns[-1]], t)[0]
    index = _names(op, list(op.by), t) if op.by else [t.columns[0]]
    values = _names(op, list(op.pick), t) if op.pick else [
        c for c in t.columns if c not in index and c != on and t.frame[c].dtype.is_numeric()]  # fmt: skip
    how = op.reduction if op.reduction in ("max", "min", "mean", "sum", "first",
                                           "last", "median") else "first"  # fmt: skip
    frame = t.frame.pivot(on=on, index=index, values=values, aggregate_function=how)
    kinds = _kinds(t)
    new = {c: kinds.get(c) for c in index}
    for c in frame.columns:
        if c in index:
            continue
        base = next((v for v in values if c == v or c.startswith(v + "_")), values[0])
        name = c if len(values) > 1 else f"{values[0]} · {on} {c}"
        new[name] = kinds.get(base)
        if name != c:
            frame = frame.rename({c: name})
    return {"out": _made(frame, new)}


def unpivot(ctx, op) -> dict[str, Any]:
    """Wide to long: `pick` (the columns folded; default every number not in `by`) into
    two columns, `new_name` (their names, default "variable") and "value", a row per
    `by` per folded column. The folded columns must be one kind."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    index = _names(op, list(op.by), t) if op.by else []
    folded = _names(op, list(op.pick), t) if op.pick else [
        c for c in t.columns if c not in index and t.frame[c].dtype.is_numeric()]  # fmt: skip
    kinds = _kinds(t)
    found = {kinds.get(c) for c in folded}
    if len(found) > 1:
        raise PuzzleError(f"'{op.name}': the columns folded are of different kinds "
                          f"({', '.join(sorted(str(k) for k in found))})", code="invalid")  # fmt: skip
    name = op.new_name.strip() or "variable"
    frame = t.frame.unpivot(on=folded, index=index or None, variable_name=name,
                            value_name="value")  # fmt: skip
    new = {c: kinds.get(c) for c in index} | {name: None, "value": found.pop() if found else None}
    return {"out": _made(frame, new)}


def distinct(ctx, op) -> dict[str, Any]:
    """Rows kept once: whole rows alike, or alike on `by` (the first kept)."""
    t = _table(op, _ev()._input(ctx, op, "table"))
    subset = _names(op, list(op.by), t) if op.by else None
    return {"out": _made(t.frame.unique(subset=subset, keep="first", maintain_order=True),
                         _kinds(t))}  # fmt: skip


def fill_missing(ctx, op) -> dict[str, Any]:
    """Missing values filled in `pick` (default every column): from the row before
    (forward) or after (backward), the column's mean, zero, or the `value` input; or
    the rows with any missing dropped (`fill` "drop")."""
    pl = _pl()
    ev = _ev()
    t = _table(op, ev._input(ctx, op, "table"))
    cols = _names(op, list(op.pick), t) if op.pick else t.columns
    if op.fill == "drop":
        return {"out": _made(t.frame.drop_nulls(subset=cols), _kinds(t))}
    kinds = _kinds(t)
    exprs = []
    for c in cols:
        x = pl.col(c)
        if t.frame[c].dtype.is_float():
            x = x.fill_nan(None)
        if op.fill == "forward":
            x = x.fill_null(strategy="forward")
        elif op.fill == "backward":
            x = x.fill_null(strategy="backward")
        elif op.fill == "mean":
            x = x.fill_null(strategy="mean")
        elif op.fill == "zero":
            x = x.fill_null(0)
        else:
            given = ev._typed_number(ctx, op, "value")
            if given is None:
                raise PuzzleError(f"'{op.name}': filling with a value needs the value",
                                  code="inconsistent")  # fmt: skip
            ev._same_kind(op, given.kind, kinds.get(c))
            x = x.fill_null(given.value)
        exprs.append(x.alias(c))
    return {"out": _made(t.frame.with_columns(exprs), kinds)}


def rolling(ctx, op) -> dict[str, Any]:
    """A running (`window` 0: cumulative) or rolling (`window` rows) sum, mean, max or
    min of `column`, in the order of `by` (time, step), per group of `pick` if given,
    as a new column (`new_name`, default "<reduction> <column>")."""
    pl = _pl()
    t = _table(op, _ev()._input(ctx, op, "table"))
    col = _names(op, [op.column or t.columns[-1]], t)[0]
    how = op.reduction if op.reduction in ("sum", "mean", "max", "min") else "sum"
    name = op.new_name.strip() or (f"{'running' if op.window == 0 else 'rolling'} {how} {col}")
    frame = t.frame.sort(_names(op, list(op.by), t)) if op.by else t.frame
    c = pl.col(col)
    if op.window == 0:
        x = {"sum": c.cum_sum(), "max": c.cum_max(), "min": c.cum_min(),
             "mean": c.cum_sum() / pl.int_range(1, pl.len() + 1)}[how]  # fmt: skip
    else:
        w = int(op.window)
        x = {"sum": c.rolling_sum(w, min_samples=1), "mean": c.rolling_mean(w, min_samples=1),
             "max": c.rolling_max(w, min_samples=1),
             "min": c.rolling_min(w, min_samples=1)}[how]  # fmt: skip
    if op.pick:
        x = x.over(_names(op, list(op.pick), t))
    kinds = _kinds(t)
    kinds[name] = kinds.get(col)
    return {"out": _made(frame.with_columns(x.alias(name)), kinds)}
