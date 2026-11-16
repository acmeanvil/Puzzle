"""Anisotropic adaptation with MMG (ADR 0066 phase 4) on the unit cube of Kuhn tets,
to a sharp layer f = tanh((x − 0.5) / 0.03), in two passes: the elements in the layer come out thin
across it (along x) and long along it, the face x = 1 keeps its group (its nodes still
on that face), and second order adds edge midpoints. Skipped without the pixi `mmg`
environment. The metric itself, against Frey and Alauzet's (2005) bound for linear
tetrahedra, |u − Π u| ≤ c_3 max_e eᵀ|H|e with c_3 = 9/32, needs no MMG."""

from __future__ import annotations

import numpy as np
import pytest
from test_estimates import cube

from puzzle.mesh import mmg
from puzzle.mesh.mesh import CellBlock, Group

needs_mmg = pytest.mark.skipif(mmg.mmg_binary() is None, reason="mmg not available")


def test_the_metric_of_a_quadratic_field():
    """u = x² + 4 y² on the unit cube: H = diag(2, 8, 0), so for an error ε = 0.01 × its
    range (5) the metric's eigenvalues are 9/32 × (2, 8) / ε, and the hmax floor 1/hmax²
    along z (the stretch cap of 50 doesn't bind)."""
    mesh = cube(8, quadratic=False)
    tets = mesh.blocks[0].connectivity - 1
    x, y = mesh.coords[:, 0], mesh.coords[:, 1]
    m = mmg.metric(mesh.coords, tets, x**2 + 4 * y**2, 0.01, 1e-3, 1.0)
    inside = np.all((mesh.coords > 0.3) & (mesh.coords < 0.7), axis=1)  # recovery exact there
    eps = 0.01 * 5.0
    assert np.allclose(m[inside, 0, 0], 9 / 32 * 2 / eps, rtol=1e-9)
    assert np.allclose(m[inside, 1, 1], 9 / 32 * 8 / eps, rtol=1e-9)
    assert np.allclose(m[inside, 2, 2], 1.0, rtol=1e-9)


def with_boundary(n=6):
    mesh = cube(n)
    tets = mesh.blocks[0].connectivity
    faces: dict[tuple, int] = {}
    for t in tets:
        for a, b, c in ((0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)):
            key = tuple(sorted((t[a], t[b], t[c])))
            faces[key] = faces.get(key, 0) + 1
    outer = np.array([k for k, v in faces.items() if v == 1])
    pos = mesh.coords[outer - 1]
    right = np.isclose(pos[:, :, 0], 1.0).all(axis=1)
    start = len(mesh.blocks[0].ids) + 1
    mesh.blocks.append(CellBlock("tri3", 2, (2, 1), np.arange(start, start + right.sum()),
                                 outer[right]))  # fmt: skip
    start += right.sum()
    mesh.blocks.append(CellBlock("tri3", 2, (2, 2), np.arange(start, start + (~right).sum()),
                                 outer[~right]))  # fmt: skip
    nodes = np.unique(outer[right])
    mesh.groups["B/Right"] = Group("B/Right", 2, [(2, 1)], nodes)
    mesh.groups["B/Body"] = Group("B/Body", 3, [(3, 1)], mesh.node_ids)
    return mesh


@needs_mmg
@pytest.mark.parametrize("order", [1, 2])
def test_a_layer_gives_thin_long_elements(tmp_path, order):
    mesh = with_boundary()
    for k in range(2):  # the first pass finds the layer; the second resolves it
        f = np.tanh((mesh.coords[:, 0] - 0.5) / 0.03)
        mesh = mmg.adapt_mesh(mesh, f, tmp_path / f"pass{k}", target=0.01, hmin=0.004,
                              hmax=0.25, order=order if k else 1)  # fmt: skip
    new = mesh
    pos = {int(n): i for i, n in enumerate(new.node_ids)}
    (tets,) = [b for b in new.blocks if b.dim == 3]
    assert tets.element == ("tet10" if order == 2 else "tet4")
    x = new.coords[np.vectorize(pos.__getitem__)(tets.connectivity[:, :4])]
    ext = x.max(axis=1) - x.min(axis=1)
    centre = x.mean(axis=1)
    layer = np.abs(centre[:, 0] - 0.5) < 0.02
    across, along = ext[layer, 0].mean(), ext[layer, 1:].mean()
    assert along / across > 3, (across, along)
    far = np.abs(centre[:, 0] - 0.5) > 0.3
    assert ext[far].mean() > 3 * across  # coarse away from it
    right = new.groups["B/Right"]
    assert len(right.node_ids) > 4
    assert np.allclose(new.coords[np.vectorize(pos.__getitem__)(right.node_ids), 0], 1.0)
    assert mmg.quality(new) > 0
