"""A light weld on a mesh (ADR 0078 decision 2): node pairs across the joint, and the
stations along the path where its line force is read.

The weld runs along `Weld.path`, an edge of the path's part lying on the other part.
Its legs lie on the two `faces`: the path's part's (its leg `leg`) and the other's
(`leg2`, else `leg`). Every node of each face within its leg of the path is a footprint
node, placed by (s, d): its distance along the path and its distance from it. Each
footprint node is paired with the other footprint's node nearest the same place across
the weld (same s, the same share of its leg), both ways, so the pairs fill the fillet's
triangle and carry moments about the path as well as forces.

The pairs give the weld's stiffness, a pair k I as stiff as the weld metal: E times
the weld's length over the pairs, which with the pairs' spread makes the joint about as
stiff as a solid bead. Stations are the path's mesh nodes, so a curved path is followed
at two stations per element side on quadratic meshes; each pair belongs to the station
nearest its s.

The springs written are per station (`Coupling`, 2026-11-14): each leg's nodes near
the station (weighted by a hat in s over the neighbouring stations and by their shares
of the face's area) are fitted by a rigid motion, as an RBE3 spreads a load, and six
springs join the two fits' motions at one point P, the same in every station's
cross-section (the station plus the weld's mean pair offset across the path, in the
station's frame; ADR 0090 package 15: each station's own pairs' centroid wandered with
the mesh, and the load with it): three along x, y and z of E times the station's share
of the path (not by how many pairs it got, which on a tetrahedral mesh varies from
station to station), and three of the pairs' mean spread about their centroid per pair,
(|r|² I - r rᵀ) times those, along its principal axes. Each
spring is a stiffness on a linear combination of the legs' displacements (`coeffs`), so
no element joins two nodes apart: a rigid motion stretches none, and the weld passes
force and moment exactly. The x, y, z springs between pairs of nodes that don't
coincide (the first light welds') dropped each pair's couple, and a lap joint's weld
carried 23.5 of its 100 N·m (ADR 0078). A leg whose nodes near a station lie on one
line (a shell's edge) has no fitted rotation; such a weld keeps those pair springs
(`couplings` None, with a note). A station's force and moment come from the same
combinations (`post/weld_fields.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.adapters.common import InputError
from puzzle.mesh import Mesh


@dataclass
class Coupling:
    """A station's six springs (module docstring): spring j's stretch is
    g_j = Σ_n coeffs[j, n] · u_n over `nodes`, its energy ½ k_j g_j²; springs 0-2 are
    the relative motion at `point` along `dirs` 0-2 (x, y, z), 3-5 the relative turn
    about `dirs` 3-5. The other part's leg minus the path's part's."""

    point: np.ndarray  # (3,)
    nodes: np.ndarray  # (m,) node ids, both legs
    coeffs: np.ndarray  # (6, m, 3)
    k: np.ndarray  # (6,) N/m, then N·m/rad
    dirs: np.ndarray  # (6, 3)


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
    couplings: list | None = None  # per station a Coupling (None: no pairs there)


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
    # a node of the path's part about on it, nearer than its distance from the path. A
    # groove's two faces lie on each other by design: all of them is the weld.
    part = weld.path.split("/", 1)[0]
    if part in mesh.groups and weld.type != "groove":
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
    # each leg's direction at a station, then their sum: a pair with a node on the path
    # (no direction) had tilted the station's bisector by up to 15° toward the other leg
    dir_a, dir_b = np.zeros_like(sxyz), np.zeros_like(sxyz)
    np.add.at(dir_a, station_of, ua)
    np.add.at(dir_b, station_of, ub)
    bisector = unit(dir_a) + unit(dir_b)
    fallback = unit(ua.sum(axis=0)[None])[0] + unit(ub.sum(axis=0)[None])[0]
    empty = np.linalg.norm(bisector, axis=1) < 1e-12
    bisector[empty] = fallback
    bisector -= np.einsum("ij,ij->i", bisector, tangent)[:, None] * tangent
    bisector = unit(bisector)
    k = _modulus(project, comp, weld.faces) * welded / len(pa)
    a_ids = ia[pa[:, 0]]
    wp = WeldPairs(weld.name, a_ids, ib[pa[:, 1]], k, stations, sxyz, s_st, widths, tangent,
                   bisector, station_of, welded, mesh.node_coords(a_ids), closed)  # fmt: skip
    wp.couplings = _couplings(wp, mesh, weld.faces, (ia, sa, pa[:, 0]), (ib, sb, pa[:, 1]),
                              total)  # fmt: skip
    if wp.couplings is None:
        wp.notes.append("a leg's nodes near a station lie on one line (a shell's edge?): "
                        "node-pair springs, which pass no couple across the weld")  # fmt: skip
    return wp


def _skew(v: np.ndarray) -> np.ndarray:
    """(n, 3, 3) the cross-product matrices: skew(a) @ b = a × b."""
    out = np.zeros((len(v), 3, 3))
    out[:, 0, 1], out[:, 0, 2], out[:, 1, 2] = -v[:, 2], v[:, 1], -v[:, 0]
    out[:, 1, 0], out[:, 2, 0], out[:, 2, 1] = v[:, 2], -v[:, 1], v[:, 0]
    return out


def _hat(s: np.ndarray, s_st: np.ndarray, i: int, closed: bool, total: float) -> np.ndarray:
    """Station i's weight of points at s: 1 at the station, 0 at its neighbours, linear
    between; past an open path's end station, 1."""
    n = len(s_st)
    if n == 1:
        return np.ones_like(s)
    d = s - s_st[i]
    if closed:
        d = (d + total / 2) % total - total / 2
        left = (s_st[i] - s_st[i - 1]) % total
        right = (s_st[(i + 1) % n] - s_st[i]) % total
    else:
        left = s_st[i] - s_st[i - 1] if i > 0 else np.inf
        right = s_st[i + 1] - s_st[i] if i < n - 1 else np.inf
    w = np.where(d < 0, 1 + d / max(left, 1e-300), 1 - d / max(right, 1e-300))
    return np.clip(w, 0.0, 1.0)


def _fit(xyz: np.ndarray, w: np.ndarray, point: np.ndarray):
    """A weighted rigid fit of nodes' motion (as RBE3): per node 3 × 3 blocks A_n, B_n
    with the fit's motion at `point` Σ A_n u_n and its turn Σ B_n u_n. None when the
    nodes lie on a line (no turn about it)."""
    W = w / w.sum()
    c = W @ xyz
    r = xyz - c
    J = (W * np.einsum("ij,ij->i", r, r)).sum() * np.eye(3) - np.einsum("i,ij,ik->jk", W, r, r)
    lam = np.linalg.eigvalsh(J)
    if lam[-1] <= 0 or lam[0] < 1e-6 * lam[-1]:
        return None
    B = W[:, None, None] * (np.linalg.inv(J)[None] @ _skew(r))
    A = W[:, None, None] * np.eye(3)[None] - _skew((point - c)[None])[0][None] @ B
    return A, B


def _couplings(wp: WeldPairs, mesh: Mesh, faces, side_a, side_b, total: float):
    """Each station's `Coupling` (module docstring); None if a leg can't be fitted. A
    node's weight in its leg's fit is its share of the face's area (`face_shares`), so
    the force a station spreads is a traction, whatever the nodes' spacing."""
    from puzzle.adapters.thread_pairs import face_shares

    xa_all, xb_all = mesh.node_coords(side_a[0]), mesh.node_coords(side_b[0])
    mid = (xa_all[side_a[2]] + xb_all[side_b[2]]) / 2
    legs = []
    for face, (ids, s, used) in zip(faces, (side_a, side_b), strict=True):
        keep = np.unique(used)
        known, area, _n = face_shares(mesh.groups[face], mesh)
        at = np.searchsorted(known, ids[keep])
        at = np.clip(at, 0, max(len(known) - 1, 0))
        found = (known[at] == ids[keep]) if len(known) else np.zeros(len(keep), bool)
        area = np.where(found, area[at] if len(known) else 0.0, 0.0)
        if not (area > 0).any():  # elements without shares (a shell's edge): alike
            area = np.ones(len(keep))
        legs.append((ids[keep], s[keep], mesh.node_coords(ids[keep]), area))
    out: list = []
    # Each station's stiffness by its share of the path, not by how many pairs it got (a
    # tetrahedral mesh's footprint nodes fall unevenly): E times the welded length in all.
    counts = np.bincount(wp.station_of, minlength=len(wp.stations))
    shares = np.where(counts > 0, wp.widths, 0.0)
    per_length = wp.k * len(wp.a) / max(float(shares.sum()), 1e-300)
    # Every station's springs act at the same place in its own cross-section and with
    # the same spread: the weld's averages over all its pairs, in each station's frame
    # (tangent, bisector, their cross product). The station's own pairs' centroid
    # wandered with the tetrahedral mesh, and with it the share of the load: the lap
    # joint's station line forces ran 0.6 to 1.95 P/L (spread 0.13 to 0.29 over meshes
    # of 1.5 to 3.4 mm); at the averages 0.83 to 1.23 P/L (`test_welds_solved.py`).
    frames = np.stack([wp.tangent, wp.bisector, np.cross(wp.tangent, wp.bisector)], axis=1)
    of = wp.station_of
    offset = np.einsum("pij,pj->pi", frames[of], mid - wp.xyz[of])
    offset = offset.mean(axis=0)
    offset[0] = 0.0  # along the path: at the station
    local = np.zeros((3, 3))
    for i in np.flatnonzero(counts):
        mine = of == i
        r = (mid[mine] - mid[mine].mean(axis=0)) @ frames[i].T
        local += ((r * r).sum() * np.eye(3) - r.T @ r) / mine.sum()
    local /= max(int((counts > 0).sum()), 1)  # per pair, as one station sees it
    for i in range(len(wp.stations)):
        mine = wp.station_of == i
        if not mine.any():
            out.append(None)
            continue
        point = wp.xyz[i] + offset @ frames[i]
        spread = frames[i].T @ local @ frames[i]
        lam, vec = np.linalg.eigh(spread)
        lam = np.maximum(lam, 1e-6 * max(lam[-1], 1e-300))
        dirs = np.vstack([np.eye(3), vec.T])
        kt = per_length * shares[i]
        k = np.concatenate([np.full(3, kt), lam * kt])
        nodes, rows = [], []
        for (ids, s, xyz, area), sign in zip(legs, (-1.0, 1.0), strict=True):
            w = _hat(s, wp.s, i, wp.closed, total) * area
            near = w > 1e-9
            fit = _fit(xyz[near], w[near], point) if near.sum() >= 3 else None
            if fit is None:
                return None
            A, B = fit
            # spring j's coefficients per node: dir_j · (A_n or B_n), signed by leg
            rows.append(sign * np.concatenate([np.einsum("jk,nkl->jnl", dirs[:3], A),
                                               np.einsum("jk,nkl->jnl", dirs[3:], B)]))  # fmt: skip
            nodes.append(ids[near])
        ids = np.concatenate(nodes)
        coeffs = np.concatenate(rows, axis=1)
        unique, back = np.unique(ids, return_inverse=True)
        if len(unique) < len(ids):  # a node on both legs: its terms added
            merged = np.zeros((6, len(unique), 3))
            np.add.at(merged, (slice(None), back), coeffs)
            ids, coeffs = unique, merged
        out.append(Coupling(point, ids.astype(np.int64), coeffs, k, dirs))
    return out


def stretches(c: Coupling, u: np.ndarray) -> np.ndarray:
    """(6,) each spring's stretch g_j from (m, 3) displacements of `c.nodes`."""
    return np.einsum("jnk,nk->j", c.coeffs, u)


def all_pairs(project, comp, mesh: Mesh) -> dict[str, WeldPairs]:
    """Every light weld's pairs, by weld name."""
    return {w.name: weld_pairs(project, comp, mesh, w) for w in comp.welds
            if w.formulation == "line"}  # fmt: skip
