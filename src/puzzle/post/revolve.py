"""Axisymmetric results drawn revolved about their axis (ADR 0048 decision 6).

A 2D axisymmetric model lies in the XY plane with Y the axis and X the radius. For the
view it is turned about Y through a sweep angle (270° by default, as Ansys draws it,
Settings > Results > Axisymmetric results), so it reads as the part:

- the section's corner nodes are copied at each of N + 1 angles (about 5° apart), and
  each triangle becomes a wedge, each quadrilateral a hexahedron, per segment (mid-side
  nodes are dropped: the view draws flat faces anyway);
- scalars and tensors are copied (a tensor's slots keep meaning r, y and hoop);
- three-component vectors are turned with the section: (v_r, v_y, v_hoop) becomes
  v_r e_r + v_y e_y + v_hoop e_θ at each angle, so the deformed view warps outward
  where the section moves outward.

The revolved grid is for drawing only. Probes and picks read the section: a point of
the revolved view maps back by `to_section` (r = hypot(x, z), y). At angle 0 the
revolved grid is the section itself, so extremes found on it are section points.
"""

from __future__ import annotations

import math

import numpy as np

TRI, QUAD = (5, 22), (9, 23)  # VTK's linear and quadratic triangles and quadrilaterals
WEDGE, HEX = 13, 12
DEGREES_PER_SEGMENT = 5.0


def segments_for(angle: float) -> int:
    return max(4, round(abs(angle) / DEGREES_PER_SEGMENT))


def frames(angle: float, n: int):
    """The angles (rad) and each one's radial and hoop directions, about +Y."""
    t = np.radians(np.linspace(0.0, angle, n + 1))
    radial = np.stack([np.cos(t), np.zeros_like(t), -np.sin(t)], axis=1)
    hoop = np.stack([-np.sin(t), np.zeros_like(t), -np.cos(t)], axis=1)
    return t, radial, hoop


def revolve(grid, angle: float = 270.0, segments: int | None = None):
    """The 2D section `grid` (a pyvista UnstructuredGrid in the XY plane) turned about Y
    through `angle` degrees, with its point and cell data (see the module's doc)."""
    import pyvista as pv

    n = segments or segments_for(angle)
    types = np.asarray(grid.celltypes)
    offsets = np.asarray(grid.cell_offsets if hasattr(grid, "cell_offsets") else grid.offset)
    conn = np.asarray(grid.cell_connectivity)
    keep = np.flatnonzero(np.isin(types, TRI + QUAD))
    if not len(keep):
        raise ValueError("no 2D elements to revolve")
    _t, radial, hoop = frames(angle, n)
    pts = np.asarray(grid.points, float)
    m = len(pts)
    r, y = pts[:, 0], pts[:, 1]
    points = np.concatenate([r[:, None] * radial[k] + np.outer(y, [0.0, 1.0, 0.0])
                             for k in range(n + 1)])  # fmt: skip
    cells, kinds, parents = [], [], []
    for c in keep:
        corners = conn[offsets[c] : offsets[c + 1]][: 3 if types[c] in TRI else 4]
        kind = WEDGE if len(corners) == 3 else HEX
        for k in range(n):
            ring = np.concatenate([corners + k * m, corners + (k + 1) * m])
            cells.append(np.concatenate([[len(ring)], ring]))
            kinds.append(kind)
            parents.append(c)
    out = pv.UnstructuredGrid(np.concatenate(cells), np.array(kinds, np.uint8), points)
    for name in grid.point_data:
        a = np.asarray(grid.point_data[name])
        if a.ndim == 2 and a.shape[1] == 3:  # a vector in (r, y, hoop): turned
            out.point_data[name] = np.concatenate([
                a[:, :1] * radial[k] + a[:, 1:2] * [0.0, 1.0, 0.0] + a[:, 2:] * hoop[k]
                for k in range(n + 1)
            ])  # fmt: skip
        else:
            out.point_data[name] = np.concatenate([a] * (n + 1))
    parents = np.asarray(parents)
    for name in grid.cell_data:
        out.cell_data[name] = np.asarray(grid.cell_data[name])[parents]
    out.field_data["revolved"] = np.array([angle])
    return out


def to_section(point) -> np.ndarray:
    """A point of the revolved view back on the section: (hypot(x, z), y, 0)."""
    x, y, z = (float(v) for v in point)
    return np.array([math.hypot(x, z), y, 0.0])


def revolved_view(mesh) -> float | None:
    """The sweep angle to draw `mesh`'s results through, or None to draw them flat:
    axisymmetric meshes only, as Settings > Results > Axisymmetric results says."""
    if not getattr(mesh, "axisymmetric", False):
        return None
    from puzzle.settings import current

    s = current()
    if s.get("results.axisymmetric_view") != "revolved":
        return None
    return float(s.get("results.sweep_angle"))


def mark_dimension(mesh, component) -> None:
    """A stored mesh told what its component is (the store keeps no dimension): its
    faces are regions of a 2D model, and whether that model is axisymmetric."""
    dimension = getattr(component, "dimension", "3d")
    if dimension != "3d":
        mesh.planar = True
        mesh.axisymmetric = dimension == "2d_axisymmetric"
