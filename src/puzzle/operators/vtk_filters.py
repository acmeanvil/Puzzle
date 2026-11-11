"""ParaView's filters as operators (ADR 0070 level 2, ADR 0071 step 5), run on VTK here.

ParaView's filters are VTK's, and Puzzle already holds VTK (pyvista), so Slice, Clip and
Contour run in Puzzle's own process on a field (a Filter's, or a field operator's), with
no ParaView needed: they work where ParaView isn't installed (macOS before 14) and in
the agent's and scripts' runs alike. Those ParaView has under other names are Puzzle
operators already, found by ParaView's names in the palette: Threshold is Filter,
Calculator is Field formula, Integrate Variables is Reduce's integral, Plot Over Line is
a Path result tool.

- **Slice**: the field on a plane (`target`, a construction plane), as a table of the
  cut's points (x, y, z and the value), interpolated in the elements it crosses.
- **Clip**: the field kept on one side of the plane (`keep`: above, on the side its
  normal faces; below, the other), a field still on the mesh, so it shows in the view
  and feeds Reduce or a Check.
- **Contour**: where the field takes a value (an iso-surface; a line in 2D), as a table
  of points with x, y, z.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from puzzle.errors import PuzzleError


def _plane(ctx, op) -> tuple[np.ndarray, np.ndarray]:
    from puzzle.model.schema import ConstructionGeometry

    c = ctx.project.objects.get(op.target) if op.target else None
    if not isinstance(c, ConstructionGeometry) or c.type != "plane":
        raise PuzzleError(f"'{op.name}': pick its plane (a construction plane)",
                          code="missing_input")  # fmt: skip
    origin, normal = c.frame(ctx.project)
    normal = np.asarray(normal, float)
    size = np.linalg.norm(normal)
    if not size:
        raise PuzzleError(f"'{op.name}': its plane has no normal", code="invalid")
    return np.asarray(origin, float), normal / size


def _field_in(ctx, op):
    from puzzle.operators.evaluate import FieldValue, _input

    got = _input(ctx, op, "field")
    if not isinstance(got, FieldValue):
        raise PuzzleError(f"'{op.name}': wire a field into it (a Filter's, or a field "
                          "operator's)", code="missing_input")  # fmt: skip
    return got


def grid_of(fv):
    """The field's mesh as a VTK grid with the field as its one array, "value"."""
    from puzzle.post.vtk import to_grid

    grid = to_grid(fv.mesh)
    values = np.asarray(fv.values, float)
    values = values[:, 0] if values.ndim > 1 else values
    if fv.location == "node":
        ids = np.asarray(grid.point_data["node_id"])
        at = np.full(len(ids), np.nan)
        order = np.argsort(fv.ids)
        pos = np.clip(np.searchsorted(fv.ids, ids, sorter=order), 0, len(fv.ids) - 1)
        found = fv.ids[order[pos]] == ids
        at[found] = values[order[pos[found]]]
        grid.point_data["value"] = at
    else:
        ids = np.asarray(grid.cell_data["element_id"])
        at = np.full(len(ids), np.nan)
        order = np.argsort(fv.ids)
        pos = np.clip(np.searchsorted(fv.ids, ids, sorter=order), 0, len(fv.ids) - 1)
        found = fv.ids[order[pos]] == ids
        at[found] = values[order[pos[found]]]
        grid.cell_data["value"] = at
        grid = grid.cell_data_to_point_data()
    return grid


def _points_table(points, values, kind: str | None, name: str):
    from puzzle.operators.evaluate import Table

    pts = np.asarray(points, float).reshape(-1, 3)
    rows = [[*p, v] for p, v in zip(pts.tolist(), np.asarray(values, float).tolist(),
                                    strict=True)]  # fmt: skip
    return Table(["x", "y", "z", name], rows, ["length", "length", "length", kind])


def slice_op(ctx, op) -> dict[str, Any]:
    fv = _field_in(ctx, op)
    origin, normal = _plane(ctx, op)
    cut = grid_of(fv).slice(normal=normal, origin=origin)
    if cut.n_points == 0:
        raise PuzzleError(f"'{op.name}': its plane misses the model", code="invalid")
    name = fv.label or fv.item.label
    return {"out": _points_table(cut.points, cut.point_data["value"], fv.item.unit, name)}


def clip_op(ctx, op) -> dict[str, Any]:
    from dataclasses import replace

    fv = _field_in(ctx, op)
    origin, normal = _plane(ctx, op)
    if fv.location == "node":
        where = fv.mesh.node_coords(fv.ids)
    else:  # an element's side by its corners' mean
        centre = {}
        for b in fv.mesh.blocks:
            corners = fv.mesh.node_coords(b.connectivity.ravel()).reshape(len(b.ids), -1, 3)
            centre.update(zip(b.ids.tolist(), corners.mean(axis=1), strict=True))
        where = np.asarray([centre.get(int(i), (np.nan,) * 3) for i in fv.ids])
    side = (where - origin) @ normal
    keep = side >= -1e-12 if op.keep != "below" else side <= 1e-12
    values = np.where(keep, np.asarray(fv.values, float), np.nan)
    label = f"{fv.label or fv.item.label} (clipped)"
    return {"out": replace(fv, values=values, label=label)}


def contour_op(ctx, op) -> dict[str, Any]:
    from puzzle.operators.evaluate import _input, _number, _same_kind

    fv = _field_in(ctx, op)
    level = _number(op, _input(ctx, op, "level"), "level")
    if level is None:
        raise PuzzleError(f"'{op.name}': give the value to contour at", code="missing_input")
    _same_kind(op, fv.item.unit, level.kind)
    grid = grid_of(fv)
    surface = grid.contour([level.value], scalars="value")
    if surface.n_points == 0:
        raise PuzzleError(f"'{op.name}': the field never reaches {level.value:g}",
                          code="invalid")  # fmt: skip
    name = fv.label or fv.item.label
    values = np.full(surface.n_points, level.value)
    return {"out": _points_table(surface.points, values, fv.item.unit, name)}


def evaluators() -> dict:
    return {"slice": slice_op, "clip": clip_op, "contour": contour_op}
