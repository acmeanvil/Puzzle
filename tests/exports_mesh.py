"""A small synthetic mesh and results for the export writers' unit tests (no solver):
a cube of six tetrahedra (first or second order) with ids that don't start at 1 or
run on, a part "Box", the face selections "Box/Bottom" and "Box/Top", a point
selection, and steps with nodal, element and global fields of known values."""

from __future__ import annotations

import itertools

import numpy as np

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult

CORNERS = np.array(list(itertools.product([0.0, 1.0], repeat=3)))[:, ::-1]  # x fastest
# The cube's six tetrahedra around its diagonal 0-7 (positive volume, Gmsh order).
TETS = [(0, 1, 3, 7), (0, 3, 2, 7), (0, 2, 6, 7), (0, 6, 4, 7), (0, 4, 5, 7), (0, 5, 1, 7)]
TET_EDGES = [(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)]  # Gmsh's tet10 edge order


def cube(order: int = 1) -> Mesh:
    coords = [tuple(c) for c in CORNERS]
    tets = []
    for t in TETS:
        t = list(t)
        if np.linalg.det(CORNERS[t[1:]] - CORNERS[t[0]]) < 0:
            t[1], t[2] = t[2], t[1]
        if order == 2:
            for a, b in TET_EDGES:
                mid = tuple((np.array(coords[t[a]]) + coords[t[b]]) / 2)
                if mid not in coords:
                    coords.append(mid)
                t.append(coords.index(mid))
        tets.append(t)
    coords = np.array(coords)
    node_ids = 100 + 3 * np.arange(len(coords))  # neither 1..n nor contiguous
    tets = node_ids[np.array(tets)]
    element = "tet4" if order == 1 else "tet10"
    volume = CellBlock(element, 3, (3, 1), 1000 + 7 * np.arange(len(tets)), tets)
    blocks, groups = [volume], {"Box": Group("Box", 3, [(3, 1)], np.sort(node_ids))}
    for tag, (name, z) in enumerate([("Bottom", 0.0), ("Top", 1.0)], 1):
        faces = _faces_at(tets, node_ids, coords, z, order)
        ids = 5000 + 100 * tag + np.arange(len(faces))
        blocks.append(CellBlock("tri3" if order == 1 else "tri6", 2, (2, tag), ids, faces))
        nodes = np.unique(faces)
        groups[f"Box/{name}"] = Group(f"Box/{name}", 2, [(2, tag)], nodes)
    corner = node_ids[[7]]
    blocks.append(CellBlock("vertex", 0, (0, 1), np.array([9000]), corner[:, None]))
    groups["Box/Corner"] = Group("Box/Corner", 0, [(0, 1)], corner)
    return Mesh(node_ids, coords, blocks, groups)


def _faces_at(tets, node_ids, coords, z, order):
    """The tetrahedra's faces lying in the plane z, as Gmsh surface elements."""
    xyz = dict(zip(node_ids.tolist(), coords, strict=True))
    out = []
    for t in tets:
        for face in [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]:
            corners = [int(t[k]) for k in face]
            if all(abs(xyz[n][2] - z) < 1e-12 for n in corners):
                if order == 2:
                    mids = []
                    for a, b in [(0, 1), (1, 2), (2, 0)]:
                        want = (xyz[corners[a]] + xyz[corners[b]]) / 2
                        mids.append(next(n for n in t[4:] if np.allclose(xyz[int(n)], want)))
                    corners += [int(m) for m in mids]
                out.append(corners)
    return np.array(out, np.int64)


def displacement(mesh: Mesh, k: float) -> np.ndarray:
    """A known displacement field: u = k (x + 2y, -y z, 3 x z) 1e-3 m."""
    x, y, z = mesh.coords.T
    return 1e-3 * k * np.column_stack([x + 2 * y, -y * z, 3 * x * z])


def results(mesh: Mesh, kind: str = "transient", values=(0.5, 1.25)) -> Results:
    volume = mesh.blocks[0]
    steps = []
    for i, v in enumerate(values, 1):
        k = float(i)
        fields = {
            "displacement": Field("displacement", "node", ["x", "y", "z"], mesh.node_ids,
                                  displacement(mesh, k)),
            "temperature": Field("temperature", "node", [""], mesh.node_ids,
                                 (293.15 + 10 * k * mesh.coords[:, 0])[:, None]),
            "stress": Field("stress", "element", ["xx", "yy", "zz", "xy", "yz", "zx"],
                            volume.ids, k * 1e6 * (np.arange(len(volume.ids))[:, None] + 1)
                            * np.array([1, 2, 3, 4, 5, 6])),
            "energy": Field("energy", "global", [""], np.array([0]), np.array([[k * 2.5]])),
        }  # fmt: skip
        mode = i if kind in ("modal", "buckling") else None
        index = i if kind == "stationary" else 1  # output times and modes of one step
        steps.append(StepResult(index, kind, float(v), mode, fields))
    return Results("synthetic", steps)
