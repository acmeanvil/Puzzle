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


def over_selection(mesh: Mesh, step: StepResult, item: Item, selection: str, reduction: str):
    """The item over a selection's nodes (or elements, for per-element fields): max or
    min (with where), or the area- or volume-weighted mean or integral (faces and
    bodies, with each element's shape-function weights)."""
    from puzzle.results.query import _measures, _weights

    f = step.fields.get(item.field)
    if f is None:
        raise PuzzleError(f"no '{item.label}' in this result step", code="not_found")
    group = _group(mesh, selection)
    if f.location == "element":
        cells = np.concatenate([b.ids for b in group.blocks(mesh) if b.dim == group.dim] or [[]])
        keep = np.isin(f.ids, cells)
    else:
        keep = np.isin(f.ids, group.node_ids)
    if not keep.any():
        raise PuzzleError(f"'{selection}' has no {item.label} values", code="not_found")
    ids, values = f.ids[keep], compute(item, f.values[keep], f.components)
    out = {"selection": selection, "reduction": reduction, "count": int(keep.sum())}
    if reduction in ("max", "min"):
        i = int(np.nanargmax(values) if reduction == "max" else np.nanargmin(values))
        out["value"] = float(values[i])
        if f.location == "node":
            out["point"] = mesh.node_coords(ids[i : i + 1])[0].tolist()
        return out
    blocks = [b for b in group.blocks(mesh) if b.dim == group.dim and b.dim >= 2]
    if f.location != "node" or not blocks:
        if reduction == "integral":
            raise PuzzleError(
                f"an integral needs a face or body selection with nodal values; "
                f"'{selection}' is not one",
                code="unsupported",
            )
        out["value"] = float(np.nanmean(values))
        return out
    lookup = dict(zip(ids.tolist(), values.tolist(), strict=True))
    total = measure = 0.0
    for b in blocks:
        sizes = _measures(b, mesh)
        weights = _weights(b.element, b.connectivity.shape[1])
        at = np.vectorize(lambda n: lookup.get(int(n), np.nan))(b.connectivity)
        total += float(np.nansum(sizes[:, None] * weights[None, :] * at))
        measure += float(sizes.sum())
    out["measure"] = measure
    out["value"] = total if reduction == "integral" else total / measure
    return out


def reaction(mesh: Mesh, step: StepResult, selection: str, about=None) -> dict:
    """The force a selection's nodes take, and its moment about `about` (else the
    selection's centroid, area- or volume-weighted): sums of the solver's nodal
    reaction forces. A node shared with a loaded
    face carries that load too (as in every solver's reaction sum)."""
    f = step.fields.get("reaction_force")
    if f is None:
        raise PuzzleError(
            "these results have no reaction forces (CalculiX and Code_Aster write them for "
            "static structural steps)",
            code="unsupported",
        )
    group = _group(mesh, selection)
    keep = np.isin(f.ids, group.node_ids)
    if not keep.any():
        raise PuzzleError(f"'{selection}' has no reaction force values", code="not_found")
    forces = np.asarray(f.values[keep], float)
    xyz = mesh.node_coords(f.ids[keep])
    centre = np.asarray(about, float) if about is not None else _centroid(mesh, group, xyz)
    force = forces.sum(axis=0)
    moment = np.cross(xyz - centre, forces).sum(axis=0)
    return {
        "selection": selection,
        "value": float(np.linalg.norm(force)),
        "components": dict(zip(("x", "y", "z"), force.tolist(), strict=True)),
        "moment": dict(zip(("x", "y", "z"), moment.tolist(), strict=True)),
        "moment_total": float(np.linalg.norm(moment)),
        "about": centre.tolist(),
        "count": int(keep.sum()),
    }


def along_path(mesh: Mesh, step: StepResult, item: Item, start, end, samples: int) -> dict:
    """The item at `samples` points from `start` to `end` (undeformed), interpolated in
    the elements; points outside the mesh are NaN. `distance` is along the path."""
    import pyvista as pv

    from puzzle.post.vtk import to_grid

    f = step.fields.get(item.field)
    if f is None:
        raise PuzzleError(f"no '{item.label}' in this result step", code="not_found")
    a, b = np.asarray(start, float), np.asarray(end, float)
    t = np.linspace(0.0, 1.0, samples)
    points = a[None, :] + t[:, None] * (b - a)[None, :]
    probe = pv.PolyData(points).sample(to_grid(mesh, step))
    if item.field not in probe.point_data:
        raise PuzzleError(f"'{item.label}' can't be read along a path", code="unsupported")
    comps = np.asarray(probe.point_data[item.field], float).reshape(samples, -1)
    values = compute(item, comps, f.components)
    inside = np.asarray(probe.point_data["vtkValidPointMask"], bool)
    values = np.where(inside, values, np.nan)
    if not inside.any():
        raise PuzzleError("the path does not pass through the model", code="invalid")
    distance = t * float(np.linalg.norm(b - a))
    i = int(np.nanargmax(np.abs(values)))
    return {
        "distance": distance.tolist(),
        "values": values.tolist(),
        "points": points.tolist(),
        "value": float(values[i]),
        "point": points[i].tolist(),
        "outside": int((~inside).sum()),
    }


def _centroid(mesh: Mesh, group, xyz) -> np.ndarray:
    """A face or body selection's centroid from its elements (the nodes' mean is
    pulled toward wherever the mesh is finer); for points and edges, the nodes' mean."""
    from puzzle.results.query import _measures

    total, moment = 0.0, np.zeros(3)
    for b in group.blocks(mesh):
        if b.dim != group.dim or b.dim < 2:
            continue
        sizes = _measures(b, mesh)
        corners = 3 if b.dim == 2 else 4
        centres = mesh.node_coords(b.connectivity[:, :corners].ravel())
        centres = centres.reshape(len(b.ids), corners, 3).mean(axis=1)
        total += float(sizes.sum())
        moment += (sizes[:, None] * centres).sum(axis=0)
    return moment / total if total > 0 else xyz.mean(axis=0)


def _group(mesh: Mesh, selection: str):
    group = mesh.groups.get(selection)
    if group is None:
        raise PuzzleError(
            f"no selection '{selection}' in the mesh",
            code="not_found",
            details={"selections": sorted(mesh.groups)},
        )
    return group


def _source(project, store, probe):
    """(mesh, results, items) of the probe's source's current run (or derived result)."""
    from puzzle.model.schema import ResultDefinition, Study
    from puzzle.post import derived

    src = project.objects.get(probe.source)
    if isinstance(src, ResultDefinition):
        d = derived.evaluate(project, store, src)
        return d.mesh, d.results, d.items
    if isinstance(src, Study):
        record = derived.run_for(project, store, src.id)
        results, mesh = store.read_results(record.run_id), store.read_mesh(record.mesh_id)
        return mesh, results, derived._items_of(results)
    raise PuzzleError(f"probe '{probe.name}': its source is gone", code="not_found")


def _item(probe, items):
    item = next((i for i in items if i.key == probe.quantity), None)
    if item is None:
        what = "reaction forces" if probe.type == "reaction" else f"'{probe.quantity}'"
        raise PuzzleError(f"probe '{probe.name}': the results have no {what}", code="not_found")
    return item


def at_step(mesh: Mesh, step: StepResult, item: Item, probe) -> dict:
    """One probe of any type, at one step."""
    if probe.type == "selection":
        return over_selection(mesh, step, item, probe.selection, probe.reduction)
    if probe.type == "reaction":
        return reaction(mesh, step, probe.selection, probe.about)
    if probe.type == "path":
        return along_path(mesh, step, item, probe.point, probe.end, probe.samples)
    return at_point(mesh, step, item, probe.point)


def evaluate(project, store, probe) -> dict:
    """A kept probe's value for its source's current run (or derived result)."""
    mesh, results, items = _source(project, store, probe)
    item = _item(probe, items)
    steps = [s for s in results.steps if item.field in s.fields]
    if not steps:
        raise PuzzleError(f"probe '{probe.name}': no step has {item.label}", code="not_found")
    step = steps[min(probe.step, len(steps) - 1)] if probe.step >= 0 else steps[probe.step]
    out = at_step(mesh, step, item, probe)
    return out | {"label": item.label, "unit": item.unit, "step": step.value}


def history(project, store, probe) -> list[dict]:
    """A kept probe at every step of its source's current run that has its quantity:
    [{"step": time, increment or frequency, "value": SI}] (ADR 0038's plots). A path
    gives its largest value along the path at each step."""
    mesh, results, items = _source(project, store, probe)
    item = _item(probe, items)
    out = []
    for step in results.steps:
        if item.field in step.fields:
            found = at_step(mesh, step, item, probe)
            out.append({"step": step.value, "mode": step.mode, "value": found["value"]})
    return out
