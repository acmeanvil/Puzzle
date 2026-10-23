"""Comparing a swept study's points (ADR 0018): one result across several runs.

A `Case` is one point's run (its label, mesh and results). `values` gives a listed
result (`post.quantities.Item`) for a case; `summary` its extremes per case and where
they are; `difference` one case minus (or divided by) a baseline on the baseline's mesh,
mapped there when the meshes differ (`transfer.map_field`). All in SI.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.post.quantities import Item, compute
from puzzle.results.model import Field, Results, StepResult


@dataclass
class Case:
    label: str
    mesh: Mesh
    results: Results
    run_id: str = ""


def _step(results: Results, field: str, step_index: int) -> StepResult:
    steps = [s for s in results.steps if field in s.fields]
    if not steps:
        raise PuzzleError(f"no '{field}' in these results", code="not_found")
    return steps[min(step_index, len(steps) - 1)] if step_index >= 0 else steps[step_index]


def values(case: Case, item: Item, step_index: int = -1) -> tuple[Field, np.ndarray]:
    """The case's field and the item's value at each of its ids."""
    f = _step(case.results, item.field, step_index).fields[item.field]
    return f, compute(item, f.values, f.components)


def summary(cases: list[Case], item: Item, step_index: int = -1) -> list[dict]:
    """Per case: minimum and maximum, and the position of each (nodal results)."""
    rows = []
    for case in cases:
        f, v = values(case, item, step_index)
        lo, hi = int(np.nanargmin(v)), int(np.nanargmax(v))
        row = {"label": case.label, "min": float(v[lo]), "max": float(v[hi])}
        if f.location == "node":
            index = {int(n): i for i, n in enumerate(case.mesh.node_ids)}
            row["min_at"] = case.mesh.coords[index[int(f.ids[lo])]].tolist()
            row["max_at"] = case.mesh.coords[index[int(f.ids[hi])]].tolist()
        rows.append(row)
    return rows


def common_range(cases: list[Case], item: Item, step_index: int = -1) -> tuple[float, float]:
    """One colour scale for side-by-side views: the extremes over every case."""
    rows = summary(cases, item, step_index)
    return min(r["min"] for r in rows), max(r["max"] for r in rows)


def difference(
    base: Case, other: Case, item: Item, step_index: int = -1, relative: bool = False
) -> Results:
    """`other` minus `base` (or other / base) for the item, on the base's mesh, as a
    one-field result (`difference`) the view shows like any other."""
    f0, v0 = values(base, item, step_index)
    f1 = _step(other.results, item.field, step_index).fields[item.field]
    if f0.location == "node":
        from puzzle.transfer import map_field

        mapped = map_field(f1, other.mesh, base.mesh)
        v0 = compute(item, f0.at(base.mesh.node_ids), f0.components)
        v1 = compute(item, mapped.values, mapped.components)
        ids = base.mesh.node_ids.copy()
    else:  # element values: only on the same elements
        if len(f0.ids) != len(f1.ids) or not np.array_equal(np.sort(f0.ids), np.sort(f1.ids)):
            raise PuzzleError(
                f"'{item.label}' is per element and the meshes differ: compare a nodal result",
                code="unsupported",
            )
        v1 = compute(item, f1.at(f0.ids), f1.components)
        ids = f0.ids.copy()
    if relative:
        with np.errstate(divide="ignore", invalid="ignore"):
            d = np.where(np.abs(v0) > 0, v1 / v0, np.nan)
    else:
        d = v1 - v0
    step = _step(base.results, item.field, step_index)
    field = Field("difference", f0.location, [""], ids, d[:, None])
    return Results(
        base.results.solver, [StepResult(1, step.kind, step.value, fields={"difference": field})]
    )


def difference_item(item: Item, base_label: str, relative: bool = False) -> Item:
    """How a difference is listed and shown: ratios have no unit, and a temperature
    change stays in kelvin (a display unit with an offset, °C or °F, would shift it)."""
    if relative:
        text, unit = f"{item.label}: ratio to {base_label}", None
    elif item.unit == "temperature":
        text, unit = f"{item.label}: change from {base_label} (K)", None
    else:
        text, unit = f"{item.label}: change from {base_label}", item.unit
    return Item("difference:value", text, "difference", "value", item.group, unit)
