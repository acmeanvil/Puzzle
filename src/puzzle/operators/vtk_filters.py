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


# --- vectors: Stream Tracer and Glyph -------------------------------------------------


def _vectors(ctx, op):
    """The vector field `op.quantity` names (its field: "velocity", "displacement"), at
    the step picked, as a VTK grid with point vectors "vectors"; its name and kind."""
    from puzzle.operators.evaluate import ResultValue, _input
    from puzzle.post.vtk import to_grid

    got = _input(ctx, op, "result")
    if not isinstance(got, ResultValue):
        raise PuzzleError(f"'{op.name}': wire a study's result into it", code="missing_input")
    name = (op.quantity or "velocity").split(":", 1)[0]
    steps = [s for s in got.results.steps if name in s.fields]
    if not steps:
        raise PuzzleError(f"'{op.name}': {got.label} has no {name}", code="not_found")
    step = steps[min(op.step, len(steps) - 1)] if op.step >= 0 else steps[op.step]
    field = step.fields[name]
    if field.values.ndim != 2 or field.values.shape[1] != 3:
        raise PuzzleError(f"'{op.name}': {name} isn't a vector", code="invalid")
    grid = to_grid(got.mesh, step)
    if name in grid.cell_data and name not in grid.point_data:  # CFD: per cell
        grid = grid.cell_data_to_point_data()
    if name not in grid.point_data:
        raise PuzzleError(f"'{op.name}': {name} isn't on the mesh's nodes", code="invalid")
    grid.point_data["vectors"] = np.asarray(grid.point_data[name], float)
    item = next((i for i in got.items if i.field == name), None)
    return grid, name, getattr(item, "unit", None)


def _count(ctx, op, default: int) -> int:
    from puzzle.operators.evaluate import _input, _number

    got = _number(op, _input(ctx, op, "seeds"), "seeds")
    return max(1, int(round(got.value))) if got is not None else default


def stream_op(ctx, op) -> dict[str, Any]:
    """Stream Tracer: streamlines of a vector field from seeds along a construction path
    (`target`), as a table of their points: x, y, z, the line, its speed there."""
    import pyvista as pv

    from puzzle.model.schema import ConstructionGeometry
    from puzzle.operators.evaluate import Table

    grid, name, kind = _vectors(ctx, op)
    c = ctx.project.objects.get(op.target) if op.target else None
    if not isinstance(c, ConstructionGeometry) or c.type != "path":
        raise PuzzleError(f"'{op.name}': pick the line its seeds lie on (a construction "
                          "path)", code="missing_input")  # fmt: skip
    start, end = (np.asarray(v, float) for v in c.frame(ctx.project))
    seeds = pv.Line(start, end, resolution=max(1, _count(ctx, op, 10) - 1))
    # Steps in length, 1/200 of the model, its bounds around it: VTK's default (half a
    # cell) stopped at the seed on coarse meshes, and pyvista's minimum step (0.01 in
    # the unit) over the initial one stops it at once.
    h = grid.length / 200
    lines = grid.streamlines_from_source(seeds, vectors="vectors",
                                         integration_direction="both",
                                         max_length=4 * grid.length, step_unit="l",
                                         initial_step_length=h, min_step_length=h / 10,
                                         max_step_length=10 * h, max_steps=20000)  # fmt: skip
    if lines.n_points == 0:
        raise PuzzleError(f"'{op.name}': no streamline starts on its line (outside the "
                          "flow, or the field is zero there)", code="invalid")  # fmt: skip
    speed = np.linalg.norm(np.asarray(lines.point_data["vectors"]), axis=1)
    ids = (np.asarray(lines.point_data["SeedIds"]) if "SeedIds" in lines.point_data
           else np.zeros(lines.n_points, int))  # fmt: skip
    rows = [[*pt, int(i), float(v)] for pt, i, v in
            zip(np.asarray(lines.points).tolist(), ids.tolist(), speed, strict=True)]  # fmt: skip
    return {"out": Table(["x", "y", "z", "line", f"{name} magnitude"], rows,
                         ["length", "length", "length", None, kind])}  # fmt: skip


def glyph_op(ctx, op) -> dict[str, Any]:
    """Glyph: a vector field's arrows as a table (at most `seeds` of the nodes, evenly
    spread): x, y, z, the vector's components and its size, for plots and the view's
    pins."""
    from puzzle.operators.evaluate import Table

    grid, name, kind = _vectors(ctx, op)
    every = max(1, grid.n_points // _count(ctx, op, 200))
    pts = np.asarray(grid.points)[::every]
    vec = np.asarray(grid.point_data["vectors"])[::every]
    size = np.linalg.norm(vec, axis=1)
    rows = [[*pt, *v, float(m)] for pt, v, m in zip(pts.tolist(), vec.tolist(), size,
                                                     strict=True)]  # fmt: skip
    columns = ["x", "y", "z", f"{name}_x", f"{name}_y", f"{name}_z", f"{name} magnitude"]
    return {"out": Table(columns, rows, ["length"] * 3 + [kind] * 4)}


def evaluators() -> dict:
    return {
        "slice": slice_op,
        "clip": clip_op,
        "contour": contour_op,
        "stream": stream_op,
        "glyph": glyph_op,
    }
