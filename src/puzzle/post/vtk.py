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
    "pyramid5": (14, None),
}


def von_mises(stress: np.ndarray) -> np.ndarray:
    """Von Mises equivalent stress from (n, 6) components xx, yy, zz, xy, yz, zx."""
    sxx, syy, szz, sxy, syz, szx = stress.T
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


def to_grid(mesh: Mesh, step: StepResult | None = None):
    """A pyvista UnstructuredGrid of the volume elements, with the step's nodal fields.

    Vector fields also get a `<name>_magnitude` array, and stress gets `von_mises`.
    """
    import pyvista as pv

    cells, types = [], []
    order = np.argsort(mesh.node_ids)
    for b in mesh.blocks:
        if b.dim != 3:
            continue
        if b.element not in VTK_CELLS:
            raise ValueError(f"viewing {b.element} elements is not supported yet")
        vtk_type, reorder = VTK_CELLS[b.element]
        conn = b.connectivity[:, reorder] if reorder else b.connectivity
        index = order[np.searchsorted(mesh.node_ids, conn, sorter=order)]
        n = index.shape[1]
        cells.append(np.hstack([np.full((len(index), 1), n), index]).ravel())
        types.append(np.full(len(index), vtk_type, dtype=np.uint8))
    if not cells:
        raise ValueError("the mesh has no volume elements to show")
    grid = pv.UnstructuredGrid(np.concatenate(cells), np.concatenate(types), mesh.coords)
    grid.point_data["node_id"] = mesh.node_ids
    grid.cell_data["element_id"] = np.concatenate([b.ids for b in mesh.blocks if b.dim == 3])
    if step is not None:
        for f in step.fields.values():
            if f.location == "node":
                _add(grid, mesh, f)
            elif f.location == "element":
                _add_cells(grid, f)
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
        + "\n  </Collection>\n</VTKFile>\n"
    )
    return [pvd, *files]
