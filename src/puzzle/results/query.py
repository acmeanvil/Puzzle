"""Results as summaries, not arrays: what a person scans and an agent reads (ADR 0015).

    summary(results)                          every step and field, in a few numbers
    stats(results, mesh, "stress", ...)       extremes, mean and where they occur
    value_at(results, mesh, "temperature", p) the field at a point
    integrate(results, mesh, "temperature", "Bar/Body")   integral and mean over a selection

Vector fields reduce to their magnitude and stress to von Mises unless a component
is named. Values are SI.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field, Results, StepResult


def summary(results: Results) -> list[dict[str, Any]]:
    out = []
    for step in results.steps:
        entry: dict[str, Any] = {"step": step.index, "kind": step.kind}
        if step.mode is not None:
            entry |= {"mode": step.mode, "frequency_hz": step.value}
        elif step.kind == "transient":
            entry["time_s"] = step.value
        entry["fields"] = {}
        for name, f in step.fields.items():
            if f.location == "global":
                entry["fields"][name] = float(f.values[0, 0])
                continue
            values, label = _scalar(f, None)
            entry["fields"][name] = {
                "of": label,
                "min": float(values.min()),
                "max": float(values.max()),
            }
        out.append(entry)
    return out


def stats(
    results: Results,
    mesh: Mesh,
    field: str,
    *,
    step: int | None = None,
    selection: str | None = None,
    component: str | None = None,
) -> dict[str, Any]:
    """Min, max and mean of a field (or one component), and where the extremes are."""
    f, chosen = _field(results, field, step)
    ids, values, label = _restricted(f, mesh, selection, component)
    imin, imax = int(np.argmin(values)), int(np.argmax(values))
    where = _locations(f, mesh, ids[[imin, imax]])
    return {
        "field": field,
        "of": label,
        "step": chosen.index,
        "location": f.location,
        "selection": selection,
        "count": int(len(values)),
        "min": float(values[imin]),
        "min_at": {"id": int(ids[imin]), "xyz": where[0]},
        "max": float(values[imax]),
        "max_at": {"id": int(ids[imax]), "xyz": where[1]},
        "mean": float(values.mean()),
    }


def value_at(
    results: Results,
    mesh: Mesh,
    field: str,
    point: list[float | None],
    *,
    step: int | None = None,
) -> dict[str, Any]:
    from puzzle.post.sample import sample

    f, chosen = _field(results, field, step)
    if f.location != "node":
        raise PuzzleError(
            f"{field} is per {f.location}; sampling needs nodal values", code="unsupported"
        )
    s = sample(mesh, f, point)
    return {
        "field": field,
        "step": chosen.index,
        "point": point,
        "components": f.components,
        "values": [float(v) for v in s.values],
        "nodes": int(len(s.node_ids)),
        "distance": s.distance,
    }


def integrate(
    results: Results,
    mesh: Mesh,
    field: str,
    selection: str,
    *,
    step: int | None = None,
    component: str | None = None,
) -> dict[str, Any]:
    """Integral and mean of a nodal field over a body (volume) or face (area) selection.

    Uses each element's shape-function weights for linear and quadratic tetrahedra and
    triangles; other elements fall back to equal corner weights.
    """
    f, chosen = _field(results, field, step)
    if f.location != "node":
        raise PuzzleError(
            f"{field} is per {f.location}; integration needs nodal values", code="unsupported"
        )
    group = mesh.groups.get(selection)
    if group is None:
        raise PuzzleError(
            f"no selection '{selection}' in the mesh",
            code="not_found",
            details={"selections": sorted(mesh.groups)},
        )
    values, label = _scalar(f, component)
    lookup = dict(zip(f.ids.tolist(), range(len(f.ids)), strict=True))
    total, measure = 0.0, 0.0
    blocks = [b for b in group.blocks(mesh) if b.dim == group.dim]
    for b in blocks:
        sizes = _measures(b, mesh)
        weights = _weights(b.element, b.connectivity.shape[1])
        rows = np.vectorize(lookup.__getitem__)(b.connectivity)
        total += float((sizes[:, None] * weights[None, :] * values[rows]).sum())
        measure += float(sizes.sum())
    if not blocks or measure == 0.0:
        raise PuzzleError(f"'{selection}' has no elements to integrate over", code="invalid")
    kind = "volume" if group.dim == 3 else "area"
    return {
        "field": field,
        "of": label,
        "step": chosen.index,
        "selection": selection,
        "integral": total,
        kind: measure,
        "mean": total / measure,
    }


# --- helpers ---------------------------------------------------------------------------


def _field(results: Results, name: str, step: int | None) -> tuple[Field, StepResult]:
    steps = results.steps if step is None else [s for s in results.steps if s.index == step]
    for s in reversed(steps):
        if name in s.fields:
            return s.fields[name], s
    names = sorted({n for s in results.steps for n in s.fields})
    raise PuzzleError(
        f"no '{name}' field" + (f" at step {step}" if step else ""),
        code="not_found",
        details={"fields": names},
    )


def _scalar(f: Field, component: str | None) -> tuple[np.ndarray, str]:
    if component is not None:
        if component not in f.components:
            raise PuzzleError(
                f"{f.name} has no component '{component}'",
                code="not_found",
                details={"components": f.components},
            )
        return f.values[:, f.components.index(component)], component
    if f.name == "stress" and f.values.shape[1] == 6:
        from puzzle.post.vtk import von_mises

        return von_mises(f.values), "von_mises"
    if f.values.shape[1] == 1:
        return f.values[:, 0], "value"
    return np.linalg.norm(f.values, axis=1), "magnitude"


def _restricted(f: Field, mesh: Mesh, selection: str | None, component: str | None):
    values, label = _scalar(f, component)
    ids = f.ids
    if selection is not None:
        group = mesh.groups.get(selection)
        if group is None:
            raise PuzzleError(
                f"no selection '{selection}' in the mesh",
                code="not_found",
                details={"selections": sorted(mesh.groups)},
            )
        if f.location == "node":
            keep = np.isin(ids, group.node_ids)
        else:
            elements = np.concatenate([b.ids for b in group.blocks(mesh)])
            keep = np.isin(ids, elements)
        ids, values = ids[keep], values[keep]
        if not len(ids):
            raise PuzzleError(f"{f.name} has no values on '{selection}'", code="not_found")
    return ids, values, label


def _locations(f: Field, mesh: Mesh, ids: np.ndarray) -> list[list[float]]:
    if f.location == "node":
        return mesh.node_coords(ids).tolist()
    centres = []
    for i in ids:
        for b in mesh.blocks:
            hit = np.flatnonzero(b.ids == i)
            if len(hit):
                centres.append(mesh.node_coords(b.connectivity[hit[0]]).mean(axis=0).tolist())
                break
    return centres


def _measures(b, mesh: Mesh) -> np.ndarray:
    """Element volumes (3D) or areas (2D), from the corner nodes."""
    corners = {"tet": 4, "tri": 3, "hex": 8, "quad": 4, "wedge": 6, "pyramid": 5}
    family = next(k for k in corners if b.element.startswith(k))
    xyz = mesh.node_coords(b.connectivity[:, : corners[family]].ravel()).reshape(
        len(b.ids), corners[family], 3
    )
    if family == "tri":
        return 0.5 * np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)
    if family == "quad":
        return _measures_split(xyz, [(0, 1, 2), (0, 2, 3)], _tri_area)
    if family == "tet":
        return _tet_volume(xyz[:, 0], xyz[:, 1], xyz[:, 2], xyz[:, 3])
    splits = {
        "hex": [(0, 1, 3, 4), (1, 2, 3, 6), (1, 4, 5, 6), (3, 4, 6, 7), (1, 3, 4, 6)],
        "wedge": [(0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5)],
        "pyramid": [(0, 1, 2, 4), (0, 2, 3, 4)],
    }[family]
    return _measures_split(xyz, splits, lambda *p: _tet_volume(*p))


def _measures_split(xyz, splits, fn) -> np.ndarray:
    return sum(np.abs(fn(*(xyz[:, i] for i in s))) for s in splits)


def _tri_area(a, b, c) -> np.ndarray:
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def _tet_volume(a, b, c, d) -> np.ndarray:
    return np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0


def _weights(element: str, nodes: int) -> np.ndarray:
    """Integrals of each node's shape function, as fractions of the element's measure."""
    if element == "tet4":
        return np.full(4, 0.25)
    if element == "tet10":
        return np.array([-0.05] * 4 + [0.2] * 6)
    if element == "tri3":
        return np.full(3, 1 / 3)
    if element == "tri6":
        return np.array([0.0] * 3 + [1 / 3] * 3)
    corners = {"hex": 8, "quad": 4, "wedge": 6, "pyramid": 5}
    n = next(v for k, v in corners.items() if element.startswith(k))
    w = np.zeros(nodes)
    w[:n] = 1.0 / n
    return w
