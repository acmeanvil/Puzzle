"""What the fluid exports share (ADR 0043 step 4): the volume cells, the boundary
selections as face parts, which cell each boundary face belongs to, and field values
looked up by id without failing on ids a field lacks.

CFD results (Code_Saturne, OpenFOAM) are per cell (`location="element"`); a boundary
face has no cell value of its own, so the formats that want one per face get the
adjacent cell's (said in their notes)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import CellBlock, Mesh

# Corner nodes of each volume element's faces (Gmsh, and VTK, corner order).
FACES = {
    "tet": [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)],
    "hex": [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    "wedge": [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
    "pyramid": [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)],
}
CORNERS = {"tet": 4, "hex": 8, "wedge": 6, "pyramid": 5, "tri": 3, "quad": 4}


def family(element: str) -> str:
    return element.rstrip("0123456789")


# Fluid feature -> what kind of boundary it is (CGNS BCType names).
BC_TYPES = {
    "wall": "BCWall",
    "velocity_inlet": "BCInflow",
    "mass_flow_inlet": "BCInflow",
    "pressure_outlet": "BCOutflow",
    "symmetry": "BCSymmetryPlane",
}


@dataclass
class Volume:
    """The model's volume cells, in block order (the order every format writes)."""

    blocks: list[CellBlock]
    ids: np.ndarray  # (n,) element ids
    centroids: np.ndarray  # (n, 3) mean of the corner nodes
    corners: np.ndarray  # (n, 8) corner node ids, -1 padded


def volume(mesh: Mesh) -> Volume:
    blocks = [b for b in mesh.blocks if b.dim == 3]
    if not blocks:
        raise PuzzleError("this export needs a volume mesh (a 3D fluid model)", code="unsupported")
    ids = np.concatenate([b.ids for b in blocks])
    corners = np.full((len(ids), 8), -1, dtype=np.int64)
    centroids, start = np.empty((len(ids), 3)), 0
    for b in blocks:
        k, n = CORNERS[family(b.element)], len(b.ids)
        conn = b.connectivity[:, :k]
        corners[start : start + n, :k] = conn
        centroids[start : start + n] = mesh.node_coords(conn.ravel()).reshape(n, k, 3).mean(1)
        start += n
    return Volume(blocks, ids, centroids, corners)


def boundaries(mesh: Mesh) -> dict[str, list[CellBlock]]:
    """Each face selection (`Part/Selection`, dim 2) and its face elements."""
    out = {}
    for name, g in mesh.groups.items():
        if g.dim == 2 and "/" in name:
            blocks = [b for b in g.blocks(mesh) if b.dim == 2]
            if blocks:
                out[name] = blocks
    return out


def lookup(field, ids: np.ndarray) -> np.ndarray:
    """The field's values at `ids` (n, components), NaN where it has none."""
    order = np.argsort(field.ids)
    pos = np.clip(np.searchsorted(field.ids, ids, sorter=order), 0, len(field.ids) - 1)
    found = field.ids[order[pos]] == ids
    values = np.full((len(ids), field.values.shape[1]), np.nan)
    values[found] = field.values[order[pos[found]]]
    return values


def _keys(rows: np.ndarray) -> np.ndarray:
    """Sorted corner ids, -1 padded to four, as one comparable value per row."""
    rows = np.sort(rows, axis=1)
    rows = np.ascontiguousarray(rows[:, ::-1])  # -1 padding last, real ids first
    return rows.view([("", rows.dtype)] * rows.shape[1]).ravel()


@dataclass
class Faces:
    """Every volume cell's faces: their keys sorted, and the cell each belongs to."""

    keys: np.ndarray
    cells: np.ndarray  # index into Volume order


def cell_faces(vol: Volume) -> Faces:
    keys, cells, start = [], [], 0
    for b in vol.blocks:
        n = len(b.ids)
        for face in FACES[family(b.element)]:
            rows = np.full((n, 4), -1, dtype=np.int64)
            rows[:, : len(face)] = b.connectivity[:, list(face)]
            keys.append(_keys(rows))
            cells.append(np.arange(start, start + n))
        start += n
    keys, cells = np.concatenate(keys), np.concatenate(cells)
    order = np.argsort(keys, kind="stable")
    return Faces(keys[order], cells[order])


def adjacent(faces: Faces, blocks: list[CellBlock]) -> np.ndarray:
    """The volume cell (index) behind each boundary face of `blocks`, -1 where none."""
    rows = []
    for b in blocks:
        k = CORNERS[family(b.element)]
        r = np.full((len(b.ids), 4), -1, dtype=np.int64)
        r[:, :k] = b.connectivity[:, :k]
        rows.append(r)
    keys = _keys(np.vstack(rows))
    pos = np.clip(np.searchsorted(faces.keys, keys), 0, len(faces.keys) - 1)
    return np.where(faces.keys[pos] == keys, faces.cells[pos], -1)


def component_of(job):
    """The run's component, or None (a bare mesh and results, as in unit tests)."""
    from puzzle.model.schema import Component, Study

    project, record = job.project, job.record
    if project is None or record is None:
        return None
    study = project.objects.get(_uuid(record.study_id))
    if not isinstance(study, Study):
        return None
    comp = project.objects.get(study.component)
    return comp if isinstance(comp, Component) else None


def _uuid(value):
    from uuid import UUID

    try:
        return UUID(str(value))
    except ValueError:
        return value


def bc_types(job) -> dict[str, str]:
    """Selection -> CGNS BCType, from the component's fluid features."""
    comp = component_of(job)
    if comp is None:
        return {}
    return {
        f.selection: BC_TYPES[f.type]
        for p in comp.physics
        if p.type == "fluid_flow"
        for f in p.features
        if f.type in BC_TYPES
    }


def node_index(mesh: Mesh, ids: np.ndarray) -> np.ndarray:
    """Positions of node ids in `mesh.node_ids`."""
    order = np.argsort(mesh.node_ids)
    return order[np.searchsorted(mesh.node_ids, ids, sorter=order)]


def time_values(results) -> tuple[list[float], bool]:
    """Each step's time for a time set; the step count (0, 1, ...) when the values don't
    increase (several stationary steps), and whether that was needed."""
    values = [float(s.value) for s in results.steps]
    if all(b > a for a, b in zip(values, values[1:], strict=False)):
        return values, False
    return [float(i) for i in range(len(values))], True


UNITS_NOTE = "SI: m, kg, s, Pa, K (velocity m/s, temperature K)"
