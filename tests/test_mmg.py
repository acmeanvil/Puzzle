"""Anisotropic adaptation with MMG (ADR 0066 phase 4) on the unit cube of Kuhn tets,
to a sharp layer f = tanh((x − 0.5) / 0.03), in two passes: the elements in the layer come out thin
across it (along x) and long along it, the face x = 1 keeps its group (its nodes still
on that face), and second order adds edge midpoints. Skipped without the pixi `mmg`
environment."""

from __future__ import annotations

import numpy as np
import pytest
from test_estimates import cube

from puzzle.mesh import mmg
from puzzle.mesh.mesh import CellBlock, Group

pytestmark = pytest.mark.skipif(mmg.mmg_binary() is None, reason="mmg not available")


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
