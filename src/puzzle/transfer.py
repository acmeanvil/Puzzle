"""Move a result field from one study's mesh to another's (ADR 0009 transfers).

A transfer reads a nodal field (e.g. temperature) from the source study's results
and maps it onto the target study's mesh nodes:

- identical meshes (same node ids and coordinates): copied node by node;
- otherwise `mapping="interpolate"`: linear interpolation inside the source mesh's
  tetrahedra (their corner nodes), the nearest source node for target nodes outside
  it; or `mapping="nearest"`: the nearest source node.

The mapped field reaches the target adapter as an `Input` (role "load",
"initial_condition" or "prestress").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field, Results


@dataclass(frozen=True)
class Input:
    """A field handed to a study by a transfer, on the target mesh's nodes."""

    field: Field
    role: str  # "load", "initial_condition", "prestress"
    selection: str | None = None  # where it applies; None: everywhere
    source: str = ""  # the transfer's name, for messages


def pick(results: Results, field: str, step: int = -1) -> Field:
    """The field at a solve-step result: `step` counts result steps, -1 the last."""
    steps = [s for s in results.steps if field in s.fields]
    if not steps:
        raise PuzzleError(
            f"the source results have no '{field}' field",
            code="not_found",
            details={"fields": sorted({n for s in results.steps for n in s.fields})},
        )
    try:
        return steps[step].fields[field]
    except IndexError:
        raise PuzzleError(
            f"the source has {len(steps)} '{field}' steps, not step {step}", code="not_found"
        ) from None


def map_field(field: Field, source: Mesh, target: Mesh, mapping: str = "interpolate") -> Field:
    """`field` (nodal, on `source`) on every node of `target`."""
    if field.location != "node":
        raise PuzzleError(
            f"only nodal fields transfer so far, not {field.location} {field.name}",
            code="unsupported",
        )
    values = field.at(source.node_ids)  # one row per source mesh node, in its order
    if _same(source, target):
        return Field(field.name, "node", field.components, target.node_ids.copy(), values)
    if mapping == "conservative":
        raise PuzzleError("conservative mapping is not implemented yet", code="unsupported")
    if mapping == "nearest":
        mapped = values[_nearest(source.coords, target.coords)]
    else:
        mapped = _interpolate(source, values, target.coords)
    return Field(field.name, "node", field.components, target.node_ids.copy(), mapped)


def _same(a: Mesh, b: Mesh) -> bool:
    return (
        len(a.node_ids) == len(b.node_ids)
        and np.array_equal(a.node_ids, b.node_ids)
        and np.allclose(a.coords, b.coords, rtol=0, atol=1e-12)
    )


def _cell_size(points: np.ndarray, per_cell: float = 8.0) -> float:
    span = np.ptp(points, axis=0).max() or 1.0
    return span * (per_cell / max(len(points), 1)) ** (1 / 3)


def _nearest(points: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Index of the nearest point for each query (grid buckets, growing rings)."""
    h = _cell_size(points)
    origin = points.min(axis=0)
    keys = np.floor((points - origin) / h).astype(np.int64)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for i, k in enumerate(map(tuple, keys)):
        buckets.setdefault(k, []).append(i)
    out = np.empty(len(queries), dtype=np.int64)
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, k) in enumerate(zip(queries, qkeys, strict=True)):
        ring = 1
        while True:
            near = [
                i
                for dx in range(-ring, ring + 1)
                for dy in range(-ring, ring + 1)
                for dz in range(-ring, ring + 1)
                for i in buckets.get((k[0] + dx, k[1] + dy, k[2] + dz), ())
            ]
            if near:
                cand = np.asarray(near)
                d = np.linalg.norm(points[cand] - q, axis=1)
                # A point in the next ring could be nearer than one found at the edge.
                if d.min() <= ring * h:
                    out[qi] = cand[int(np.argmin(d))]
                    break
            ring += 1
    return out


def _interpolate(source: Mesh, values: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Linear interpolation in the source tetrahedra's corners; nearest node outside."""
    tets = [b for b in source.blocks if b.dim == 3 and b.element.startswith("tet")]
    if not tets:
        return values[_nearest(source.coords, queries)]
    order = np.argsort(source.node_ids)

    def rows(ids: np.ndarray) -> np.ndarray:
        return order[np.searchsorted(source.node_ids, ids, sorter=order)]

    corners = np.concatenate([rows(b.connectivity[:, :4]) for b in tets])  # (n, 4) rows
    xyz = source.coords[corners]  # (n, 4, 3)
    lo, hi = xyz.min(axis=1), xyz.max(axis=1)
    h = _cell_size(source.coords, per_cell=2.0)
    origin = source.coords.min(axis=0)
    # Bucket every tetrahedron in each grid cell its bounding box touches.
    buckets: dict[tuple[int, int, int], list[int]] = {}
    klo = np.floor((lo - origin) / h).astype(np.int64)
    khi = np.floor((hi - origin) / h).astype(np.int64)
    for t in range(len(corners)):
        for i in range(klo[t, 0], khi[t, 0] + 1):
            for j in range(klo[t, 1], khi[t, 1] + 1):
                for k in range(klo[t, 2], khi[t, 2] + 1):
                    buckets.setdefault((i, j, k), []).append(t)
    # Barycentric coordinates via the inverse of each tetrahedron's edge matrix.
    edges = np.stack([xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 0]], axis=2)
    inverse = np.linalg.inv(edges)
    out = np.empty((len(queries), values.shape[1]))
    missing = []
    tol = 1e-9
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, key) in enumerate(zip(queries, qkeys, strict=True)):
        cand = buckets.get(tuple(key))
        found = False
        if cand:
            cand = np.asarray(cand)
            local = np.einsum("nij,nj->ni", inverse[cand], q - xyz[cand, 0])
            weights = np.column_stack([1 - local.sum(axis=1), local])
            inside = np.flatnonzero((weights >= -tol).all(axis=1))
            if len(inside):
                t, w = cand[inside[0]], weights[inside[0]]
                out[qi] = w @ values[corners[t]]
                found = True
        if not found:
            missing.append(qi)
    if missing:
        idx = np.asarray(missing)
        out[idx] = values[_nearest(source.coords, queries[idx])]
    return out
