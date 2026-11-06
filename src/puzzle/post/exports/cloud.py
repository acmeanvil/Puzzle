"""Point clouds (ADR 0043): x, y, z and one result per node as CSV, the way Ansys's
External Data, COMSOL's interpolation functions and Abaqus's mapped fields bring a
field onto their own mesh. Optionally one selection's nodes only; SI or display units,
each column's unit in its header."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError


def write(job):
    return point_cloud(job.mesh, job.results, job.step, job.quantity, job.selection, job.path,
                       job.units)  # fmt: skip


def point_cloud(mesh, results, step, quantity, selection, path: Path, units: str):
    """x, y, z and a result per node (a listed quantity, or all of a field's
    components), CSV with a header naming each column's unit."""
    from puzzle.post.derived import _items_of
    from puzzle.post.quantities import compute

    items = _items_of(results)
    if quantity is None:
        quantity = next((i.key for i in items if i.field in step.fields), None)
    item = next((i for i in items if i.key == quantity), None)
    if item is None or item.field not in step.fields:
        raise PuzzleError(
            f"no '{quantity}' in this step",
            code="not_found",
            details={"quantities": [i.key for i in items if i.field in step.fields]},
        )
    field = step.fields[item.field]
    if field.location != "node":
        raise PuzzleError(
            f"{item.label} is per element; a point cloud needs nodes", code="unsupported"
        )
    ids = field.ids
    if selection is not None:
        group = mesh.groups.get(selection)
        if group is None:
            raise PuzzleError(f"no selection '{selection}' in the mesh", code="not_found")
        ids = ids[np.isin(ids, group.node_ids)]
    values = field.at(ids)
    if item.components:  # a component, a magnitude or an equivalent: one column
        columns = [(item.label, compute(item, values, field.components))]
    else:
        columns = [
            (f"{item.label} {c}".strip(), values[:, k]) for k, c in enumerate(field.components)
        ]
    xyz = mesh.node_coords(ids)
    length, scale, unit = "m", 1.0, item.unit
    if units == "display":
        from puzzle.gui.viewport import _display
        from puzzle.settings import length_unit

        length, per_metre = length_unit()
        xyz = xyz * per_metre
        converted = []
        for name, col in columns:
            col, shown = _display(col, item.unit)
            converted.append((name, col))
            unit = shown
        columns = converted
    else:
        from puzzle.model.units import DIMENSIONS

        unit = DIMENSIONS[item.unit][1] if item.unit in DIMENSIONS else ""
    header = [f"x [{length}]", f"y [{length}]", f"z [{length}]"]
    header += [f"{name} [{unit}]" if unit else name for name, _ in columns]
    table = np.column_stack([xyz, *(c for _, c in columns)]) * scale
    path.write_text(
        ",".join(header)
        + "\n"
        + "\n".join(",".join(f"{v:.9g}" for v in row) for row in table)
        + "\n",
        encoding="utf-8",
    )
    return [path], {
        "result": item.label,
        "nodes": str(len(ids)),
        "on": selection or "the whole model",
        "units": "display units" if units == "display" else "SI",
    }
