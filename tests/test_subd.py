"""SubD from a control cage (ADR 0065 phase 4): a cube cage (side 2 m) refined by
Catmull–Clark and fitted with B-spline patches. The result is one closed solid of six
faces with no free edges, and its volume matches the limit surface's (the cage
subdivided six times, faceted) within 0.05 %; three levels and four agree. The same
cage read from an OBJ file gives the same solid."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("OCP")

from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402

from puzzle.geometry import subd  # noqa: E402
from puzzle.geometry.history import GeometryError, Op, regenerate  # noqa: E402
from puzzle.geometry.measure import topology  # noqa: E402
from puzzle.geometry.meshfile import volume as faceted_volume  # noqa: E402

CAGE = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
FACES = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]


def volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def reference() -> float:
    pts, grids = CAGE, [np.array([[a, b], [d, c]]) for a, b, c, d in FACES]
    for _ in range(6):
        pts, grids = subd.subdivide(pts, grids)
    lim = subd.limit(pts, grids)
    tris = []
    for g in grids:
        for i in range(g.shape[0] - 1):
            for j in range(g.shape[1] - 1):
                a, b, c, d = g[i, j], g[i, j + 1], g[i + 1, j + 1], g[i + 1, j]
                tris += [[lim[a], lim[b], lim[c]], [lim[a], lim[c], lim[d]]]
    return faceted_volume(np.array(tris))


def test_a_cube_cage_becomes_a_smooth_solid(tmp_path):
    ref = reference()
    got = regenerate([Op("subd", "S", {"points": CAGE.tolist(), "faces": FACES, "levels": 3})])
    assert topology(got.shape) == {"solids": 1, "shells": 1, "faces": 6, "edges": 12,
                                   "free_edges": 0}  # fmt: skip
    assert volume(got.shape) == pytest.approx(ref, rel=5e-4)
    assert {f"S/face-{k}" for k in range(1, 7)} <= set(got.names)
    finer = subd.cage_solid(CAGE, FACES, 4)[0]
    assert volume(finer) == pytest.approx(volume(got.shape), rel=2e-4)
    obj = tmp_path / "cage.obj"
    verts = "".join(f"v {x} {y} {z}\n" for x, y, z in CAGE * 1000)
    quads = "".join("f " + " ".join(str(i + 1) for i in f) + "\n" for f in FACES)
    obj.write_text(verts + quads, encoding="utf-8")
    again = regenerate([Op("subd", "S", {"file": "cage.obj", "scale": 0.001, "levels": 3})],
                       root=tmp_path)  # fmt: skip
    assert volume(again.shape) == pytest.approx(volume(got.shape), rel=1e-6)  # mm, scaled to m
    with pytest.raises(GeometryError, match="quads"):
        subd.cage_solid(CAGE, [(0, 1, 3)], 2)
