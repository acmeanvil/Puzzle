"""Vectorised transfers and face look-ups (ADR 0090 package 14, BE-17).

The Python loops per query, per tetrahedron's grid cells and per element face are
arrays now. Measured on the maintainer's machine, a jittered box of 64k nodes and
356k tetrahedra onto 133k points: interpolation 4.1 s before, 1.1 s after; nearest
3.7 s, 0.14 s; the face look-up's build 1.9 s, 0.34 s. Answers are the loops' to the
bit: the loops are kept here as the reference (the same tetrahedron taken where a
point lies on several; for nearest, the lowest numbered of points equally near,
where the loops took the first of their search order).
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from puzzle import transfer
from puzzle.adapters import common
from puzzle.mesh.mesh import CellBlock, Mesh


def box(n: int, jitter: float = 0.3, seed: int = 0) -> Mesh:
    """A unit box of n³ cubes, each cut into six tetrahedra, inner nodes moved."""
    rng = np.random.default_rng(seed)
    g = np.linspace(0, 1, n + 1)
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    xyz = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    inner = ((xyz > 0) & (xyz < 1)).all(axis=1)
    xyz[inner] += jitter / n * (rng.random((inner.sum(), 3)) - 0.5)

    def at(i, j, k):
        return (i * (n + 1) + j) * (n + 1) + k

    i, j, k = (a.ravel() for a in np.meshgrid(*[np.arange(n)] * 3, indexing="ij"))
    c = [
        at(i + a, j + b, k + d)
        for a, b, d in [
            (0, 0, 0),
            (1, 0, 0),
            (1, 1, 0),
            (0, 1, 0),
            (0, 0, 1),
            (1, 0, 1),
            (1, 1, 1),
            (0, 1, 1),
        ]
    ]
    six = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)]
    conn = np.concatenate([np.column_stack([c[a], c[b], c[e], c[d]]) for a, b, e, d in six])
    mesh = Mesh(node_ids=np.arange(1, len(xyz) + 1, dtype=np.int64), coords=xyz)
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.arange(1, len(conn) + 1), conn + 1)]
    return mesh


# --- the loops as they were (the reference) --------------------------------------------


def _nearest_loops(points, queries):
    h = transfer._cell_size(points)
    origin = points.min(axis=0)
    keys = np.floor((points - origin) / h).astype(np.int64)
    buckets: dict = {}
    for i, k in enumerate(map(tuple, keys)):
        buckets.setdefault(k, []).append(i)
    out = np.empty(len(queries), dtype=np.int64)
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, k) in enumerate(zip(queries, qkeys, strict=True)):
        ring = 1
        while True:
            near = [i for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)
                    for dz in range(-ring, ring + 1)
                    for i in buckets.get((k[0] + dx, k[1] + dy, k[2] + dz), ())]  # fmt: skip
            if near:
                cand = np.asarray(near)
                d = np.linalg.norm(points[cand] - q, axis=1)
                if d.min() <= ring * h:
                    out[qi] = cand[int(np.argmin(d))]
                    break
            ring += 1
    return out


def _interpolate_loops(source, values, queries):
    tets = [b for b in source.blocks if b.dim == 3 and b.element.startswith("tet")]
    order = np.argsort(source.node_ids)
    corners = np.concatenate([order[np.searchsorted(source.node_ids, b.connectivity[:, :4],
                                                    sorter=order)] for b in tets])  # fmt: skip
    xyz = source.coords[corners]
    lo, hi = xyz.min(axis=1), xyz.max(axis=1)
    h = transfer._cell_size(source.coords, per_cell=2.0)
    origin = source.coords.min(axis=0)
    buckets: dict = {}
    klo = np.floor((lo - origin) / h).astype(np.int64)
    khi = np.floor((hi - origin) / h).astype(np.int64)
    for t in range(len(corners)):
        for i in range(klo[t, 0], khi[t, 0] + 1):
            for j in range(klo[t, 1], khi[t, 1] + 1):
                for k in range(klo[t, 2], khi[t, 2] + 1):
                    buckets.setdefault((i, j, k), []).append(t)
    edges = np.stack([xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 0]], axis=2)
    inverse = np.linalg.inv(edges)
    out = np.empty((len(queries), values.shape[1]))
    missing = []
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, key) in enumerate(zip(queries, qkeys, strict=True)):
        cand = buckets.get(tuple(key))
        found = False
        if cand:
            cand = np.asarray(cand)
            local = np.einsum("nij,nj->ni", inverse[cand], q - xyz[cand, 0])
            weights = np.column_stack([1 - local.sum(axis=1), local])
            inside = np.flatnonzero((weights >= -1e-9).all(axis=1))
            if len(inside):
                t, w = cand[inside[0]], weights[inside[0]]
                out[qi] = w @ values[corners[t]]
                found = True
        if not found:
            missing.append(qi)
    if missing:
        idx = np.asarray(missing)
        out[idx] = values[_nearest_loops(source.coords, queries[idx])]
    return out


def _faces_loops(blocks, surface):
    lookup: dict = {}
    for b in blocks:
        for face_no, corners in enumerate(common.FACES[b.element], start=1):
            for eid, key in zip(b.ids, b.connectivity[:, corners], strict=True):
                lookup.setdefault(frozenset(key.tolist()), []).append((int(eid), face_no))
    return [lookup[frozenset(r.tolist())][-1] for r in surface.connectivity[:, :3]]


# --- the tests ------------------------------------------------------------------------


def test_interpolation_and_nearest_are_the_loops_answers():
    source, target = box(8, seed=1), box(11, seed=2)
    queries = target.coords * 1.04 - 0.02  # some outside: nearest there
    values = np.column_stack([np.sin(3 * source.coords[:, 0]), source.coords @ [1.0, 2, 3]])
    assert np.array_equal(transfer._interpolate(source, values, queries),
                          _interpolate_loops(source, values, queries))  # fmt: skip
    assert np.array_equal(transfer._nearest(source.coords, queries),
                          _nearest_loops(source.coords, queries))  # fmt: skip
    # A linear field comes through exactly inside.
    inside = ((queries > 0) & (queries < 1)).all(axis=1)
    got = transfer._interpolate(source, values, queries)[inside, 1]
    assert np.allclose(got, queries[inside] @ [1.0, 2, 3], rtol=0, atol=1e-12)


def test_nearest_takes_the_lowest_of_points_equally_near():
    points = np.array([[0.0, 0, 0], [2, 0, 0], [1, 5, 0], [0, 0, 0]])
    assert transfer._nearest(points, np.array([[1.0, 0, 0], [0, 0, 0]])).tolist() == [0, 0]


def test_face_look_up_is_the_dicts_answer_and_keeps_both_parents():
    mesh = box(6)
    conn = mesh.blocks[0].connectivity
    faces = np.concatenate([np.sort(conn[:, f], axis=1) for f in common.FACES["tet4"]])
    rows, count = np.unique(faces, axis=0, return_counts=True)
    outer = CellBlock("tri3", 2, (2, 1), np.arange(int((count == 1).sum())), rows[count == 1])
    look = common.FaceLookup.build(mesh.blocks)
    assert look.of(outer) == _faces_loops(mesh.blocks, outer)
    # A face inside: both parents, in the order written, the last one taken.
    shared = CellBlock("tri3", 2, (2, 2), np.array([1]), rows[count == 2][:1][:, ::-1])
    assert look.of(shared) == _faces_loops(mesh.blocks, shared)
    assert len(look._hits(rows[count == 2][0])) == 2
    with pytest.raises(common.InputError):
        look.of(CellBlock("tri3", 2, (2, 3), np.array([1]), np.array([[1, 2, 10_000]])))


def test_at_sixty_thousand_nodes_they_take_seconds_not_minutes():
    source, target = box(30, seed=1), box(39, seed=2)  # 30k nodes, 162k tets; 64k points
    values = source.coords.copy()
    transfer._nearest(source.coords[:10], source.coords[:10])  # scipy loaded first
    t = time.perf_counter()
    transfer._interpolate(source, values, target.coords)
    interpolate = time.perf_counter() - t
    t = time.perf_counter()
    transfer._nearest(source.coords, target.coords)
    nearest = time.perf_counter() - t
    t = time.perf_counter()
    common.FaceLookup.build(source.blocks)
    faces = time.perf_counter() - t
    # 0.5 s, 0.06 s and 0.13 s here; the loops took 1.9 s, 1.8 s and 1.0 s
    assert interpolate < 3.0 and nearest < 1.0 and faces < 1.5, (interpolate, nearest, faces)
