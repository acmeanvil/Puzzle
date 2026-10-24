"""Remote loads (ADR 0037): the RBE3-equivalent traction and its nodal forces carry the
force and moment exactly; a rigid motion is u0 + theta x (x - p)."""

import numpy as np
import pytest

from puzzle.adapters.common import nodal_traction, remote_load
from puzzle.mesh.mesh import CellBlock, Group, Mesh


def _disc(n: int = 24, r: float = 0.01, order: int = 2) -> tuple[Mesh, Group]:
    """A flat disc face at z = 0.1 of tri6 (or tri3) elements: a fan around its centre."""
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rim = np.c_[r * np.cos(ang), r * np.sin(ang), np.full(n, 0.1)]
    pts = [np.array([0, 0, 0.1]), *rim]
    cells = []
    for i in range(n):
        a, b = 1 + i, 1 + (i + 1) % n
        if order == 1:
            cells.append([0, a, b])
            continue
        mids = []
        for p, q in ((0, a), (a, b), (b, 0)):
            pts.append((pts[p] + pts[q]) / 2)
            mids.append(len(pts) - 1)
        cells.append([0, a, b, *mids])
    ids = np.arange(1, len(pts) + 1)
    conn = ids[np.asarray(cells)]
    block = CellBlock("tri6" if order == 2 else "tri3", 2, (2, 1), np.arange(1, n + 1), conn)
    mesh = Mesh(ids, np.asarray(pts, float), [block])
    group = Group("P/End", 2, [(2, 1)], ids.copy())
    mesh.groups["P/End"] = group
    return mesh, group


@pytest.mark.parametrize("order", [1, 2])
def test_resultant_and_moment_are_exact(order):
    mesh, group = _disc(order=order)
    F, M, p = np.array([3.0, -2.0, 5.0]), np.array([0.4, -0.1, 7.0]), np.array([0.0, 0.02, 0.15])
    load = remote_load(group, mesh, F, M, p)
    forces = nodal_traction(group, mesh, load.at)
    xyz = mesh.node_coords(np.array(list(forces)))
    f = np.array(list(forces.values()))
    assert f.sum(axis=0) == pytest.approx(F, abs=1e-12)
    # About the remote point, the nodal forces' moment is the applied moment.
    assert np.cross(xyz - p, f).sum(axis=0) == pytest.approx(M, abs=1e-12)


def test_curved_faces_carry_the_moment_too():
    """Rim mid-nodes on the circle: the face is curved, integrated on its own geometry,
    so the applied twist matches the true polar moment (a polygon would be ~2 % short)."""
    mesh, group = _disc(n=12)
    pts = mesh.coords.copy()
    for cell in mesh.blocks[0].connectivity:
        mid = int(cell[4]) - 1  # the rim edge's mid-node
        xy = pts[mid, :2]
        pts[mid, :2] = 0.01 * xy / np.linalg.norm(xy)
    mesh.coords[:] = pts
    load = remote_load(group, mesh, [0, 0, 0], [0, 0, 1.0])
    forces = nodal_traction(group, mesh, load.at)
    xyz = mesh.node_coords(np.array(list(forces)))
    f = np.array(list(forces.values()))
    assert np.cross(xyz - load.centroid, f).sum(axis=0)[2] == pytest.approx(1.0, rel=1e-6)
    assert load.area == pytest.approx(np.pi * 0.01**2, rel=2e-3)  # near the true circle
