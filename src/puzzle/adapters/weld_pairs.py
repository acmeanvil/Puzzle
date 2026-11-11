"""A light weld on a mesh (ADR 0078 decision 2): node pairs across the joint, and the
stations along the path where its line force is read.

The weld runs along `Weld.path`, an edge of the path's part lying on the other part.
Its legs lie on the two `faces`: the path's part's (its leg `leg`) and the other's
(`leg2`, else `leg`). Every node of each face within its leg of the path is a footprint
node, placed by (s, d): its distance along the path and its distance from it. Each
footprint node is paired with the other footprint's node nearest the same place across
the weld (same s, the same share of its leg), both ways, so the pairs fill the fillet's
triangle and carry moments about the path as well as forces.

Each pair is a spring in x, y and z (CalculiX SPRING2 node to node), as stiff as the
weld metal: E times the weld's length over the pairs, a stiffness per pair of E × ds,
which with the pairs' spread makes the joint about as stiff as a solid bead. A pair's
force (k (u_a - u_b), on the other part's node) is assigned to the station nearest its
s; a station's line force is its pairs' forces over its share of the path
(`post/weld_fields.py`). Stations are the path's mesh nodes, so a curved path is
followed at two stations per element side on quadratic meshes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.adapters.common import InputError
from puzzle.mesh import Mesh


@dataclass
class WeldPairs:
    name: str
    a: np.ndarray  # node ids on the path's part
    b: np.ndarray  # node ids on the other part
    k: float  # N/m per pair, in each of x, y, z
    stations: np.ndarray  # the path's node ids, in order along it
    xyz: np.ndarray  # (n, 3) their positions
    s: np.ndarray  # (n,) their distance along the path (m)
    widths: np.ndarray  # (n,) each station's share of the path (m)
    tangent: np.ndarray  # (n, 3) along the path
    bisector: np.ndarray  # (n, 3) from the root into the weld, between the legs
    station_of: np.ndarray  # (pairs,) each pair's station index
    length: float  # the welded length (m): the path's, or its segments'
    xa: np.ndarray = None  # (pairs, 3) the path's part's node of each pair
    closed: bool = False
    notes: list[str] = field(default_factory=list)


def _chain(group, mesh: Mesh) -> tuple[np.ndarray, bool]:
    """The path's corner nodes in order along it (an open chain, or a loop)."""
    edges = []
    for b in group.blocks(mesh):
        if b.dim == 1:
            edges += [(int(r[0]), int(r[1])) for r in b.connectivity]
    if not edges:
        raise InputError("a weld's path must be an edge selection", code="invalid")
    nbrs: dict[int, list[int]] = {}
    for a, b in edges:
        nbrs.setdefault(a, []).append(b)
        nbrs.setdefault(b, []).append(a)
    ends = [n for n, v in nbrs.items() if len(v) == 1]
    if any(len(v) > 2 for v in nbrs.values()) or len(ends) not in (0, 2):
        raise InputError("a weld's path must be one chain of edges (no branches)",
                         code="invalid")  # fmt: skip
    closed = not ends
    start = ends[0] if ends else min(nbrs)
    order, prev = [start], None
    while True:
        nxt = [n for n in nbrs[order[-1]] if n != prev]
        if not nxt or (closed and nxt[0] == start):
            break
        prev = order[-1]
        order.append(nxt[0])
        if len(order) > len(nbrs):
            break
    return np.array(order, dtype=np.int64), closed


def _project(points: np.ndarray, poly: np.ndarray, cum: np.ndarray):
    """(s along the polyline, distance from it, the nearest point) for each point."""
    a, b = poly[:-1], poly[1:]
    ab = b - a
    length2 = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300)
    t = np.clip(np.einsum("pij,ij->pi", points[:, None, :] - a[None], ab) / length2, 0, 1)
    near = a[None] + t[..., None] * ab[None]
    dist = np.linalg.norm(points[:, None, :] - near, axis=2)
    k = np.argmin(dist, axis=1)
    rows = np.arange(len(points))
    s = cum[k] + t[rows, k] * np.sqrt(length2[k])
    return s, dist[rows, k], near[rows, k]


def _modulus(project, comp, faces) -> float:
    from puzzle.adapters.thread_pairs import modulus

    return min(modulus(project, comp, f) for f in faces)


def weld_pairs(project, comp, mesh: Mesh, weld) -> WeldPairs:
    """`WeldPairs` for one light weld (module docstring)."""
    for sel in (weld.path, *weld.faces):
        if sel not in mesh.groups:
            raise InputError(f"weld '{weld.name}': no selection '{sel}' in the mesh",
                             code="invalid")  # fmt: skip
    if weld.path.split("/", 1)[0] == weld.faces[1].split("/", 1)[0]:
        raise InputError(f"weld '{weld.name}': its two faces must be on different parts",
                         code="invalid")  # fmt: skip
    corners, closed = _chain(mesh.groups[weld.path], mesh)
    poly = mesh.node_coords(corners)
    if closed:
        poly = np.vstack([poly, poly[:1]])
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])
    total = float(cum[-1])
    if total <= 0:
        raise InputError(f"weld '{weld.name}': its path has no length", code="invalid")
    stations = np.unique(mesh.groups[weld.path].node_ids.astype(np.int64))
    sxyz = mesh.node_coords(stations)
    s_st, _d, _near = _project(sxyz, poly, cum)
    order = np.argsort(s_st)
    stations, sxyz, s_st = stations[order], sxyz[order], s_st[order]
    if closed and len(s_st) > 1 and total - s_st[-1] < 1e-9 * total:
        stations, sxyz, s_st = stations[:-1], sxyz[:-1], s_st[:-1]

    legs = (weld.leg.to_si(), (weld.leg2 or weld.leg).to_si())
    sides = []
    for face, leg in zip(weld.faces, legs, strict=True):
        ids = np.unique(mesh.groups[face].node_ids.astype(np.int64))
        xyz = mesh.node_coords(ids)
        s, d, near = _project(xyz, poly, cum)
        keep = d <= leg * 1.02 + 1e-9 * total
        if not keep.any():
            raise InputError(
                f"weld '{weld.name}': no node of {face} lies within its leg of the path; "
                "is the path on the joint between the two faces?", code="invalid")  # fmt: skip
        sides.append((ids[keep], s[keep], d[keep] / leg, xyz[keep] - near[keep], xyz[keep]))
    (ia, sa, da, va, _xa), (ib, sb, db, vb, xb) = sides
    # The other part's leg lies outside the path's part: its nodes under the path's
    # part (the seat of a lap, the footprint of a bracket) are no weld. Such a node has
    # a node of the path's part about on it, nearer than its distance from the path.
    part = weld.path.split("/", 1)[0]
    if part in mesh.groups:
        from scipy.spatial import cKDTree as _Tree

        own = mesh.node_coords(np.unique(mesh.groups[part].node_ids.astype(np.int64)))
        gap = _Tree(own).query(xb)[0]
        outside = (gap > 0.5 * db * legs[1]) | (db * legs[1] < 1e-6 * total)
        if not outside.any():
            raise InputError(f"weld '{weld.name}': no node of {weld.faces[1]} lies outside "
                             f"{part} within the leg", code="invalid")  # fmt: skip
        ib, sb, db, vb = ib[outside], sb[outside], db[outside], vb[outside]
    scale = min(legs)
    from scipy.spatial import cKDTree

    def place(s, d):
        return np.column_stack([s / scale, d])

    tb, ta = cKDTree(place(sb, db)), cKDTree(place(sa, da))
    pairs = {(i, int(j)) for i, j in enumerate(tb.query(place(sa, da))[1])}
    pairs |= {(int(i), j) for j, i in enumerate(ta.query(place(sb, db))[1])}
    pa = np.array(sorted(pairs), dtype=np.int64)
    s_pair = (sa[pa[:, 0]] + sb[pa[:, 1]]) / 2
    welded = total
    if weld.pattern == "intermittent":
        seg, pitch = weld.segment.to_si(), weld.pitch.to_si()
        on = np.mod(s_pair, pitch) <= seg
        pa, s_pair = pa[on], s_pair[on]
        whole, rest = divmod(total, pitch)
        welded = whole * seg + min(rest, seg)
        if not len(pa):
            raise InputError(f"weld '{weld.name}': no segment falls on the mesh",
                             code="invalid")  # fmt: skip
    # Each pair's station: the one nearest along the path; a station's share of it.
    edges = (s_st[1:] + s_st[:-1]) / 2
    station_of = np.searchsorted(edges, s_pair)
    if closed:
        wrap = (s_pair > (s_st[-1] + total) / 2) | (s_pair < s_st[0] / 2)
        station_of[wrap & (s_pair > s_st[-1])] = 0
    bounds = np.concatenate([[0.0], edges, [total]])
    widths = np.diff(bounds)
    if closed and len(s_st) > 1:  # the first and last stations share the seam
        gap = total - s_st[-1] + s_st[0]
        widths[0] = (s_st[1] - s_st[0]) / 2 + gap / 2
        widths[-1] = (s_st[-1] - s_st[-2]) / 2 + gap / 2
    # The frame: the path's tangent, and the bisector between the legs' directions.
    tangent = np.zeros_like(sxyz)
    for i in range(len(s_st)):
        k = min(max(np.searchsorted(cum, s_st[i]) - 1, 0), len(poly) - 2)
        t = poly[k + 1] - poly[k]
        tangent[i] = t / np.linalg.norm(t)

    def unit(v):
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return np.divide(v, n, out=np.zeros_like(v), where=n > 0)

    ua, ub = unit(va[pa[:, 0]]), unit(vb[pa[:, 1]])
    bisector = np.zeros_like(sxyz)
    np.add.at(bisector, station_of, ua + ub)
    fallback = (ua + ub).sum(axis=0)
    empty = np.linalg.norm(bisector, axis=1) < 1e-12
    bisector[empty] = fallback
    bisector -= np.einsum("ij,ij->i", bisector, tangent)[:, None] * tangent
    bisector = unit(bisector)
    k = _modulus(project, comp, weld.faces) * welded / len(pa)
    a_ids = ia[pa[:, 0]]
    return WeldPairs(weld.name, a_ids, ib[pa[:, 1]], k, stations, sxyz, s_st, widths, tangent,
                     bisector, station_of, welded, mesh.node_coords(a_ids), closed)  # fmt: skip


def all_pairs(project, comp, mesh: Mesh) -> dict[str, WeldPairs]:
    """Every light weld's pairs, by weld name."""
    return {w.name: weld_pairs(project, comp, mesh, w) for w in comp.welds
            if w.formulation == "line"}  # fmt: skip
