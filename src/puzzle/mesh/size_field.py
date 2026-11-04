"""Sizes from data (ADR 0066 item 3, phase 3): a mesh size known at points, from a table
(x, y, z and size) or from an earlier run's error estimate (each element's size for a
target error), handed to the mesher:

- Gmsh: a background mesh. The points' tetrahedra (the earlier run's own elements, or
  a Delaunay tetrahedralisation of a table's points) carry the sizes at their corners
  as a list-based view ("SS"), read by a PostView field, min-combined with the
  recipe's other controls so typed controls still hold.
- Netgen: its mesh-size file of points with sizes (`meshsizefilename`), which
  restricts Netgen's local size near each point. Coarser than Gmsh's field: it can
  only make the mesh finer than the recipe's size there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError


@dataclass
class Sizes:
    """Sizes at points (m) and, for Gmsh, the tetrahedra joining them."""

    points: np.ndarray  # (n, 3)
    sizes: np.ndarray  # (n,)
    tets: np.ndarray  # (m, 4) indices into points

    def clamp(self, smallest: float, largest: float) -> Sizes:
        return Sizes(self.points, np.clip(self.sizes, smallest, largest), self.tets)


def from_points(points, sizes) -> Sizes:
    """A table's points and sizes, joined into tetrahedra by Delaunay."""
    from scipy.spatial import Delaunay

    pts = np.asarray(points, float)
    h = np.asarray(sizes, float).reshape(-1)
    if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) != len(h):
        raise PuzzleError("sizes need x, y, z and size for each point", code="invalid")
    if len(pts) < 4:
        raise PuzzleError("sizes from data need four points or more", code="invalid")
    if (h <= 0).any() or not np.isfinite(h).all():
        raise PuzzleError("every size is a positive length", code="invalid")
    try:
        tets = Delaunay(pts).simplices
    except Exception as exc:  # noqa: BLE001 - flat or repeated points: say so plainly
        raise PuzzleError(f"the points don't span a volume ({exc})", code="invalid") from exc
    return Sizes(pts, h, np.asarray(tets, np.int64))


def from_estimate(mesh, est, target: float, *, finer: float = 4.0, coarser: float = 2.0,
                  smallest: float = 0.0, largest: float = np.inf) -> Sizes:  # fmt: skip
    """Each element's new size for `target` (ADR 0066 item 4: h ξ^(−1/p), at most
    `finer` times finer or `coarser` times coarser in one pass, within the recipe's
    sizes), spread to the corner nodes (each node the smallest of its elements)."""
    h_new = est.sizes(target)
    h_new = np.clip(h_new, est.size / finer, est.size * coarser)
    h_new = np.clip(h_new, smallest, largest)
    by_id = {int(e): k for k, e in enumerate(est.element_ids)}
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    corners, values = [], []
    for b in mesh.blocks:
        if b.dim != 3 or b.element not in ("tet4", "tet10"):
            continue
        keep = np.array([int(e) in by_id for e in b.ids])
        if not keep.any():
            continue
        corners.append(np.vectorize(pos.__getitem__)(b.connectivity[keep, :4]))
        values.append(h_new[[by_id[int(e)] for e in b.ids[keep]]])
    if not corners:
        raise PuzzleError("the estimate has no tetrahedra to size from", code="unsupported")
    tets = np.concatenate(corners)
    per = np.concatenate(values)
    node_h = np.full(len(mesh.node_ids), np.inf)
    for k in range(4):
        np.minimum.at(node_h, tets[:, k], per)
    used = np.unique(tets)
    remap = np.full(len(mesh.node_ids), -1)
    remap[used] = np.arange(len(used))
    return Sizes(mesh.coords[used], node_h[used], remap[tets])


def gmsh_field(sizes: Sizes) -> int:
    """A PostView field over the sizes' tetrahedra (call inside a Gmsh session)."""
    import gmsh

    x = sizes.points[sizes.tets]  # (m, 4, 3)
    v = sizes.sizes[sizes.tets]  # (m, 4)
    data = np.concatenate([x[:, :, 0], x[:, :, 1], x[:, :, 2], v], axis=1)
    view = gmsh.view.add("puzzle sizes")
    gmsh.view.addListData(view, "SS", len(sizes.tets), data.reshape(-1).tolist())
    field = gmsh.model.mesh.field.add("PostView")
    gmsh.model.mesh.field.setNumber(field, "ViewTag", view)
    return field


def netgen_file(sizes: Sizes, path: Path) -> Path:
    """Netgen's mesh-size file: the points with their sizes, then no lines."""
    lines = [str(len(sizes.points))]
    lines += [f"{x:.9g} {y:.9g} {z:.9g} {h:.9g}" for (x, y, z), h in
              zip(sizes.points, sizes.sizes, strict=True)]  # fmt: skip
    lines.append("0")
    path.write_text("\n".join(lines) + "\n")
    return path


def resolve(control, project, root) -> Sizes:
    """A `size_field` control's sizes: from its `run`'s error estimate (each element's
    size for `target`), or from its `table` operator's x, y, z and size columns (SI)."""
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    store = ResultStore(Path(root) / RESULTS_DIR) if root is not None else None
    if control.run:
        from puzzle.post import estimates

        if store is None:
            raise PuzzleError("sizes from a run need the project's results", code="invalid")
        record = next((r for r in store.runs() if r.run_id == control.run), None)
        if record is None:
            raise PuzzleError(f"sizes from run {control.run}: it is gone (deleted?); "
                              "run the study again", code="not_found")  # fmt: skip
        est = estimates.of_run(project, store, control.run, field_name=control.field)
        return from_estimate(store.read_mesh(record.mesh_id), est, control.target,
                             finer=control.finer, coarser=control.coarser)  # fmt: skip
    from puzzle.operators import evaluate as ev

    got = ev.source(ev.Context(project, store), control.table, "out")
    if not isinstance(got, ev.Table):
        raise PuzzleError("sizes from data need a table (x, y, z, size)", code="invalid")
    missing = [c for c in ("x", "y", "z", "size") if c not in got.columns]
    if missing:
        raise PuzzleError(f"the size table has no {', '.join(missing)} column",
                          code="invalid")  # fmt: skip
    f = got.frame
    pts = np.column_stack([f[c].to_numpy().astype(float) for c in ("x", "y", "z")])
    return from_points(pts, f["size"].to_numpy().astype(float))
