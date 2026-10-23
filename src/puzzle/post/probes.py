"""Results read at a point (ADR 0032): for the view's probes and for kept probes.

One function, `at_point`, interpolates a step's fields inside the element that holds
the point (VTK's own shape functions on the undeformed grid, so a quadratic tet is
read quadratically), then computes the listed result from the interpolated components.
The view samples where the user double-clicked; a kept probe (`ResultProbe`) is
evaluated by `evaluate` for its source's current run. Values are SI.
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.post.quantities import Item, compute
from puzzle.results.model import StepResult


def at_point(mesh: Mesh, step: StepResult, item: Item, xyz) -> dict:
    """The item's value at `xyz` (undeformed, metres), and the field's components there.
    Outside the mesh, the nearest point of it is used and `distance` says how far."""
    import pyvista as pv

    from puzzle.post.vtk import to_grid

    grid = to_grid(mesh, step)
    f = step.fields.get(item.field)
    if f is None:
        raise PuzzleError(f"no '{item.label}' in this result step", code="not_found")
    point = np.asarray(xyz, float).reshape(1, 3)
    distance = 0.0
    if grid.find_containing_cell(point[0]) < 0:  # outside: the nearest surface point
        surface = grid.extract_surface()
        idx = surface.find_closest_point(point[0])
        nearest = np.asarray(surface.points[idx])
        distance = float(np.linalg.norm(nearest - point[0]))
        point = nearest.reshape(1, 3)
    probe = pv.PolyData(point).sample(grid)
    data = probe.point_data
    if item.field not in data:
        raise PuzzleError(f"'{item.label}' can't be read at a point", code="unsupported")
    components = np.asarray(data[item.field], float).reshape(1, -1)
    value = float(compute(item, components, f.components)[0])
    return {
        "value": value,
        "components": dict(zip(f.components, components[0].tolist(), strict=True)),
        "point": point[0].tolist(),
        "distance": distance,
    }


def evaluate(project, store, probe) -> dict:
    """A kept probe's value for its source's current run (or derived result)."""
    from puzzle.model.schema import ResultDefinition, Study
    from puzzle.post import derived

    src = project.objects.get(probe.source)
    if isinstance(src, ResultDefinition):
        d = derived.evaluate(project, store, src)
        mesh, results, items = d.mesh, d.results, d.items
    elif isinstance(src, Study):
        record = derived.run_for(project, store, src.id)
        results = store.read_results(record.run_id)
        mesh = store.read_mesh(record.mesh_id)
        items = derived._items_of(results)
    else:
        raise PuzzleError(f"probe '{probe.name}': its source is gone", code="not_found")
    item = next((i for i in items if i.key == probe.quantity), None)
    if item is None:
        raise PuzzleError(
            f"probe '{probe.name}': the results have no '{probe.quantity}'", code="not_found"
        )
    steps = [s for s in results.steps if item.field in s.fields]
    if not steps:
        raise PuzzleError(f"probe '{probe.name}': no step has {item.label}", code="not_found")
    step = steps[min(probe.step, len(steps) - 1)] if probe.step >= 0 else steps[probe.step]
    out = at_point(mesh, step, item, probe.point)
    return out | {"label": item.label, "unit": item.unit, "step": step.value}
