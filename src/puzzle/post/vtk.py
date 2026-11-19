"""Turn Puzzle meshes and results into VTK grids (pyvista), and export them for ParaView."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field, Results, StepResult

# Puzzle element -> (VTK cell type, node reorder from Gmsh order or None).
# VTK's quadratic tetra ends with edges (1-3, 2-3), like CalculiX; Gmsh ends with (2-3, 1-3).
VTK_CELLS = {
    "tet4": (10, None),
    "tet10": (24, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "hex8": (12, None),
    "wedge6": (13, None),
    # Revolved bolts (ADR 0077): VTK orders the edges as CalculiX does.
    "hex20": (25, [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15]),
    "wedge15": (26, [0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11]),
    # Lagrange bricks and prisms drawn by their serendipity nodes (the face and centre
    # nodes, which a flat-shaded view can't show, left out of the cells).
    "hex27": (25, [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15]),
    "wedge18": (26, [0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11]),
    "pyramid5": (14, None),
    "line2": (3, None),  # beams (ADR 0027); Gmsh and VTK both put the mid node last
    "line3": (21, None),
    "tri3": (5, None),  # shells (ADR 0027): corners, then edges, in both
    "tri6": (22, None),
    "quad4": (9, None),
    "quad8": (23, None),
    "quad9": (28, None),  # corners, mid-edges, centre in both
}


def beam_lines(grid, skip=None):
    """The grid's beam elements alone (None without any), drawn again as thick lines:
    a line cell renders one pixel wide otherwise. `skip`: element ids drawn otherwise
    (as their section's outline, ADR 0055)."""
    lines = grid.extract_cells_by_type([3, 21])
    if skip is not None and len(skip) and lines.n_cells and "element_id" in lines.cell_data:
        keep = ~np.isin(np.asarray(lines.cell_data["element_id"]), np.asarray(skip))
        lines = lines.extract_cells(np.flatnonzero(keep)) if not keep.all() else lines
    return lines if lines.n_cells else None


def von_mises(stress: np.ndarray) -> np.ndarray:
    """Von Mises equivalent stress from (n, 6) components xx, yy, zz, xy, yz, zx."""
    sxx, syy, szz, sxy, syz, szx = stress.T
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


def grid_blocks(mesh: Mesh) -> list:
    """The blocks `to_grid` makes cells of, in its order: volumes, beams and shells, and
    a 2D model's regions (ADR 0048), which are its elements."""
    return [b for b in mesh.blocks if b.dim == 3] + mesh.structural_blocks() + (
        mesh.region_blocks())  # fmt: skip


def to_grid(mesh: Mesh, step: StepResult | None = None):
    """A pyvista UnstructuredGrid of the volume, beam, shell and 2D region elements, with
    the step's nodal fields.

    Vector fields also get a `<name>_magnitude` array, and stress gets `von_mises`.
    """
    shown = grid_blocks(mesh)
    over = (mesh.node_ids, mesh.coords, *(a for b in shown for a in (b.connectivity, b.ids)))
    # The cells are the same for every result of a mesh, and the fields for every
    # quantity of a step: both are kept on the mesh (`Mesh.kept`) and handed out as
    # shallow copies, which share the arrays and take the caller's own. Building them
    # anew was a third of each click on a result (35 of 115 ms at 200k nodes).
    bare = mesh.kept("vtk cells", over, lambda: _bare_grid(mesh, shown))
    if step is None:
        return _handed(bare)

    def with_fields():
        grid = _handed(bare)
        for f in step.fields.values():
            if not len(f.ids):  # nothing stored there (older runs kept empty fields)
                continue
            if f.location == "node":
                _add(grid, mesh, f)
            elif f.location == "element":
                _add_cells(grid, f)
        return grid

    fields = tuple(step.fields.values())
    arrays = tuple(a for f in fields for a in (f.ids, f.values))
    return _handed(mesh.kept("vtk step", (*over, *fields, *arrays), with_fields))


def skin(mesh: Mesh, grid, name: str):
    """The outer surface of `grid` (a result on `mesh`, deformed or not) carrying the
    array `name`, or None when the grid is not this mesh's own (cut, revolved, shown
    whole). The surface's faces are found once per mesh and kept; given the volume, the
    renderer found them anew for every result shown (47 of each click's 115 ms at
    200k nodes)."""
    if grid.n_points != len(mesh.node_ids) or grid.n_cells != sum(
        len(b.ids) for b in grid_blocks(mesh)
    ):
        return None
    shown = grid_blocks(mesh)
    over = (mesh.node_ids, mesh.coords, *(a for b in shown for a in (b.connectivity, b.ids)))

    def outer():
        # Each surface point's and face's place in the grid, by the node and element
        # ids the grid carries (VTK's own "original ids" are not the grid's points on
        # second-order cells, whose faces it cuts into flat ones).
        bare = to_grid(mesh)
        surface = bare.extract_surface(algorithm="dataset_surface")
        order = np.argsort(mesh.node_ids)
        ids = np.asarray(surface.point_data["node_id"])
        points = order[np.searchsorted(mesh.node_ids, ids, sorter=order)]
        if not np.array_equal(mesh.node_ids[points], ids) or not np.allclose(
            np.asarray(surface.points), np.asarray(bare.points)[points]
        ):
            return None  # points VTK made itself: draw the volume
        elements = np.asarray(bare.cell_data["element_id"])
        by_id = np.argsort(elements)
        mine = np.asarray(surface.cell_data["element_id"])
        cells = by_id[np.searchsorted(elements, mine, sorter=by_id)]
        return surface, points, cells

    found = mesh.kept("vtk skin", over, outer)
    if found is None:
        return None
    kept, points, cells = found
    out = _handed(kept)
    out.points = np.asarray(grid.points)[points]
    if name in grid.point_data:
        out.point_data[name] = np.asarray(grid.point_data[name])[points]
    elif name in grid.cell_data:
        out.cell_data[name] = np.asarray(grid.cell_data[name])[cells]
    else:
        return None
    return out


def _handed(kept):
    """A kept grid for a caller: its own grid, point and cell data lists and points
    object over the same arrays, so arrays it adds and points it sets (pyvista's
    `grid.points = ...` writes into the points object) never reach the kept one. No
    caller changes an array in place."""
    import vtk

    grid = kept.copy(deep=False)
    points = vtk.vtkPoints()
    points.SetData(kept.GetPoints().GetData())
    grid.SetPoints(points)
    return grid


def _bare_grid(mesh: Mesh, shown):
    import pyvista as pv

    cells, types = [], []
    order = np.argsort(mesh.node_ids)
    for b in shown:
        if b.element not in VTK_CELLS:
            raise ValueError(f"viewing {b.element} elements is not supported yet")
        vtk_type, reorder = VTK_CELLS[b.element]
        conn = b.connectivity[:, reorder] if reorder else b.connectivity
        index = order[np.searchsorted(mesh.node_ids, conn, sorter=order)]
        n = index.shape[1]
        cells.append(np.hstack([np.full((len(index), 1), n), index]).ravel())
        types.append(np.full(len(index), vtk_type, dtype=np.uint8))
    if not cells:
        raise ValueError("the mesh has no volume or beam elements to show")
    grid = pv.UnstructuredGrid(np.concatenate(cells), np.concatenate(types), mesh.coords)
    grid.point_data["node_id"] = mesh.node_ids
    grid.cell_data["element_id"] = np.concatenate([b.ids for b in shown])
    return grid


def _add_cells(grid, f: Field) -> None:
    """A per-element field as cell data, in the grid's cell order."""
    cell_ids = grid.cell_data["element_id"]
    order = np.argsort(f.ids)
    pos = np.clip(np.searchsorted(f.ids, cell_ids, sorter=order), 0, len(f.ids) - 1)
    found = f.ids[order[pos]] == cell_ids
    values = np.full((len(cell_ids), f.values.shape[1]), np.nan)
    values[found] = f.values[order[pos[found]]]
    if values.shape[1] == 1:
        grid.cell_data[f.name] = values[:, 0]
        return
    grid.cell_data[f.name] = values
    if values.shape[1] == 3:
        grid.cell_data[f"{f.name}_magnitude"] = np.linalg.norm(values, axis=1)


def _add(grid, mesh: Mesh, f: Field) -> None:
    order = np.argsort(f.ids)
    pos = np.searchsorted(f.ids, mesh.node_ids, sorter=order)
    pos = np.clip(pos, 0, len(f.ids) - 1)
    found = f.ids[order[pos]] == mesh.node_ids
    values = np.full((len(mesh.node_ids), f.values.shape[1]), np.nan)
    values[found] = f.values[order[pos[found]]]
    if values.shape[1] == 1:
        grid.point_data[f.name] = values[:, 0]
        return
    grid.point_data[f.name] = values
    if f.name == "stress":
        grid.point_data["von_mises"] = von_mises(values)
    elif values.shape[1] == 3:
        grid.point_data[f"{f.name}_magnitude"] = np.linalg.norm(values, axis=1)


def pick_step(
    results: Results, field: str, *, step: int | None = None, mode: int | None = None
) -> StepResult:
    """The step to show: a given mode or solve step, else the last step that has `field`."""
    candidates = [s for s in results.steps if field in s.fields]
    if mode is not None:
        candidates = [s for s in candidates if s.mode == mode]
    if step is not None:
        candidates = [s for s in candidates if s.index == step]
    if not candidates:
        raise KeyError(f"no '{field}' results for the requested step or mode")
    return candidates[-1]


def export(mesh: Mesh, results: Results, path: Path) -> list[Path]:
    """Write one .vtu per result step plus a .pvd series ParaView opens as one dataset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = path.with_suffix("")
    files, entries = [], []
    for i, s in enumerate(results.steps):
        out = Path(f"{stem}_{i:03d}.vtu")
        to_grid(mesh, s).save(out)
        files.append(out)
        label = f"mode {s.mode}" if s.mode else f"step {s.index}"
        attrs = f'timestep="{i}" part="0" file={quoteattr(out.name)} name={quoteattr(label)}'
        entries.append(f"    <DataSet {attrs}/>")
    pvd = stem.with_suffix(".pvd")
    pvd.write_text(
        '<?xml version="1.0"?>\n<VTKFile type="Collection" version="0.1">\n  <Collection>\n'
        + "\n".join(entries)
        + "\n  </Collection>\n</VTKFile>\n",
        encoding="utf-8",
    )
    return [pvd, *files]
