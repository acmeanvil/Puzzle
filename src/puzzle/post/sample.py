"""Read results at points: what benchmarks and probes need."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field


@dataclass(frozen=True)
class Sample:
    values: np.ndarray  # one value per component, averaged over the matched nodes
    node_ids: np.ndarray  # the nodes averaged
    distance: float  # from the point to the farthest matched node (0 for an exact node)


def sample(
    mesh: Mesh, field: Field, point: Sequence[float | None], *, tol: float | None = None
) -> Sample:
    """The field at `point`, as the mean over the nodes there.

    A None coordinate is ignored, so (2.0, 0.0, None) averages every node on the line
    x = 2, y = 0 (e.g. through a plate's thickness). Nodes within `tol` of the point
    count; with no node that close, the nearest node is used and `distance` says how
    far away it is. The default tolerance is 1e-6 of the model size.
    """
    axes = [i for i, c in enumerate(point) if c is not None]
    if not axes:
        raise ValueError("give at least one coordinate")
    target = np.array([point[i] for i in axes], dtype=float)
    coords = mesh.node_coords(field.ids)[:, axes]
    dist = np.linalg.norm(coords - target, axis=1)
    if tol is None:
        span = mesh.coords.max(axis=0) - mesh.coords.min(axis=0)
        tol = 1e-6 * float(np.linalg.norm(span))
    hits = np.flatnonzero(dist <= tol)
    if not len(hits):
        hits = np.array([int(np.argmin(dist))])
    return Sample(
        values=field.values[hits].mean(axis=0),
        node_ids=field.ids[hits],
        distance=float(dist[hits].max()),
    )


def component(field: Field, name: str) -> int:
    """The column of a named component, e.g. "yy" in a stress field."""
    try:
        return field.components.index(name)
    except ValueError:
        raise KeyError(
            f"{field.name} has no '{name}' component; it has {field.components}"
        ) from None
