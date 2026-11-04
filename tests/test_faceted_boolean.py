"""Booleans on faceted parts (ADR 0065 phase 4, manifold3d): two cubes of side 1 m
overlapping by half a cube; union 1.5 m³, difference 0.5 m³, overlap 0.5 m³ by hand;
written as STL and read back as a faceted part."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("manifold3d")

from puzzle.geometry import meshfile  # noqa: E402


def cube(x0=0.0, side=1.0):
    c = np.array([[x0 + side * i, side * j, side * k] for i in (0, 1) for j in (0, 1)
                  for k in (0, 1)], float)  # fmt: skip
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, cc, d in quads:  # outward (counter-clockwise from outside)
        tris += [[c[a], c[b], c[cc]], [c[a], c[cc], c[d]]]
    return np.array(tris)


def test_union_cut_and_overlap(tmp_path):
    a, b = cube(), cube(0.5)
    assert meshfile.volume(a) == pytest.approx(1.0)
    for mode, want in (("add", 1.5), ("cut", 0.5), ("intersect", 0.5)):
        got = meshfile.boolean(a, b, mode)
        assert meshfile.volume(got) == pytest.approx(want, rel=1e-6), mode
    path = meshfile.write_stl(meshfile.boolean(a, b, "add"), tmp_path / "both.stl")
    back = meshfile.triangles(path, unit="m")
    assert meshfile.volume(back) == pytest.approx(1.5, rel=1e-6)
    with pytest.raises(meshfile.MeshFileError, match="mode is one of"):
        meshfile.boolean(a, b, "glue")
    with pytest.raises(meshfile.MeshFileError, match="not a closed solid"):
        meshfile.boolean(a[:-2], b, "add")  # a cube with a hole
