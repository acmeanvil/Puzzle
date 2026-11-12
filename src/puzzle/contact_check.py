"""Each contact's initial state, from the mesh, before any solve (ADR 0056).

For every node of a contact's source (slave) face: the nearest point on its target
(master) face, the gap (+) or penetration (−) along the target's outward normal there,
and how far off to the side that nearest point lies. Each node is then

- closed: penetrating, or a gap within 2 % of the target's element size there (two
  differently meshed curved faces never coincide exactly);
- near: a gap within a tenth of the search distance;
- open: a gap within the search distance (Ansys's pinball);
- far: farther than the search distance;
- overhanging: no target face under it (the nearest target point is off to the side
  by more than a tenth of the element size): the source face runs past the target's
  edge, where CalculiX can invert elements.

Shares are of the source face's area. The search distance is the contact's own
`search_distance`, or 1 % of the model's size (its bounding box's diagonal).

`checks` turns the states into warnings (`Problem`s) that name the contact and the
fix; they don't stop a run (a body held by a preloaded frictional joint is fine), but
are listed with the problems, given by the Advisor and the agent, and logged at the
start of a solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.errors import Problem
from puzzle.mesh.mesh import Mesh

STATUSES = ("closed", "near", "open", "far", "overhanging")
SEARCH_SHARE = 0.01  # default search distance: 1 % of the model's size
NEAR_SHARE = 0.1  # near: a gap within a tenth of the search distance
TOUCH = 0.02  # closed: a gap within 2 % of the target's element size
LATERAL = 0.1  # overhanging: the nearest target point off to the side by 10 % of h
HOLDING = ("fixed", "displacement", "remote_displacement", "elastic_support",
           "compression_only_support", "symmetry", "antisymmetry")  # fmt: skip
SLIDING = ("frictionless", "frictional", "rough")  # hold normally only, if at all

_FACES = {  # corner indices of each volume element's faces (outward normals)
    "tet4": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "tet10": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "hex8": [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
    "hex20": [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
}
_CORNERS = {"tri3": 3, "tri6": 3, "quad4": 4, "quad8": 4, "quad9": 4}


@dataclass
class Surface:
    """A face selection as flat triangles (corners only; quads split in two), with
    normals pointing out of the body each face bounds."""

    triangles: np.ndarray  # (n, 3, 3) corner coordinates
    normals: np.ndarray  # (n, 3) unit, outward
    corners: np.ndarray  # (n, 3) corner node ids
    nodes: np.ndarray  # every node of the selection (midside nodes too)
    curved: np.ndarray | None = None  # (n, 6, 3) six-node triangles' nodes, NaN for flat

    @property
    def areas(self) -> np.ndarray:
        t = self.triangles
        return 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)

    @property
    def sizes(self) -> np.ndarray:
        """Each triangle's element size h = √(2 A) (a right triangle's leg)."""
        return np.sqrt(2 * self.areas)

    def node_areas(self) -> dict[int, float]:
        """Each corner node's share of the area (a third of each triangle's)."""
        out: dict[int, float] = {}
        for tri, a in zip(self.corners, self.areas, strict=True):
            for n in tri:
                out[int(n)] = out.get(int(n), 0.0) + a / 3
        return out


def surface(mesh: Mesh, selection: str) -> Surface | None:
    """A face selection's triangles with outward normals; None when it isn't a face
    selection of this mesh."""
    group = mesh.groups.get(selection)
    if group is None or group.dim != 2:
        return None
    rows, curved = [], []
    for b in group.blocks(mesh):
        if b.dim != 2 or b.element not in _CORNERS:
            continue
        c = b.connectivity[:, : _CORNERS[b.element]]
        rows.append(c[:, :3])
        six = np.full((len(c), 6, 3), np.nan)
        if b.element == "tri6":
            six = mesh.node_coords(b.connectivity[:, :6].ravel()).reshape(-1, 6, 3)
        curved.append(six)
        if c.shape[1] == 4:
            rows.append(c[:, [0, 2, 3]])
            curved.append(np.full((len(c), 6, 3), np.nan))
    if not rows:
        return None
    corners = np.concatenate(rows).astype(np.int64)
    six = np.concatenate(curved)
    xyz = mesh.node_coords(corners.ravel()).reshape(-1, 3, 3)
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-300)
    inside = _inside_points(mesh, corners)  # a point inside each face's body
    known = ~np.isnan(inside[:, 0])
    away = (xyz.mean(axis=1) - inside) * known[:, None]
    flip = (normals * away).sum(axis=1) < 0
    normals[flip] *= -1
    return Surface(xyz, normals, corners, np.asarray(group.node_ids, dtype=np.int64),
                   six if not np.isnan(six).all() else None)  # fmt: skip


def _tri6(xi, eta):
    """Gmsh's six-node triangle (corners, then mid-edges 0-1, 1-2, 2-0): N, dN/dξ,
    dN/dη at each (ξ, η), each (m, 6)."""
    l1, l2, l3 = 1 - xi - eta, xi, eta
    n = np.stack([l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), l3 * (2 * l3 - 1),
                  4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1], axis=1)  # fmt: skip
    zero = np.zeros_like(xi)
    dxi = np.stack([-(4 * l1 - 1), 4 * l2 - 1, zero, 4 * (l1 - l2), 4 * l3, -4 * l3], axis=1)
    deta = np.stack([-(4 * l1 - 1), zero, 4 * l3 - 1, -4 * l2, 4 * l2, 4 * (l1 - l3)], axis=1)
    return n, dxi, deta


def on_curved(points: np.ndarray, target: Surface, q: np.ndarray, t: np.ndarray):
    """The nearest points on the target's own (second-order) faces, and the outward
    normals there: Gauss-Newton on each six-node triangle from the flat triangle's
    nearest point, kept inside the triangle. Flat faces keep q and their normal.
    On a curved face the flat triangles are off by the chord's sag (h² / 8R: 8 µm for
    0.8 mm elements on a 10 mm radius), more than a press fit's interference."""
    normals = target.normals[t].copy()
    q = q.copy()
    if target.curved is None:
        return q, normals
    six = target.curved[t]
    rows = np.flatnonzero(~np.isnan(six[:, 0, 0]))
    if not len(rows):
        return q, normals
    x6, p = six[rows], points[rows]
    a, b, c = x6[:, 0], x6[:, 1], x6[:, 2]
    # (ξ, η) of the flat point: q = a + ξ (b - a) + η (c - a)
    e1, e2, d = b - a, c - a, q[rows] - a
    g11, g12, g22 = (e1 * e1).sum(1), (e1 * e2).sum(1), (e2 * e2).sum(1)
    r1, r2 = (d * e1).sum(1), (d * e2).sum(1)
    det = np.maximum(g11 * g22 - g12**2, 1e-300)
    xi, eta = (g22 * r1 - g12 * r2) / det, (g11 * r2 - g12 * r1) / det
    for _ in range(6):
        n, dxi, deta = _tri6(xi, eta)
        x = np.einsum("mk,mki->mi", n, x6)
        tx, ty = np.einsum("mk,mki->mi", dxi, x6), np.einsum("mk,mki->mi", deta, x6)
        r = p - x
        a11, a12, a22 = (tx * tx).sum(1), (tx * ty).sum(1), (ty * ty).sum(1)
        b1, b2 = (tx * r).sum(1), (ty * r).sum(1)
        det = np.maximum(a11 * a22 - a12**2, 1e-300)
        xi = xi + (a22 * b1 - a12 * b2) / det
        eta = eta + (a11 * b2 - a12 * b1) / det
        xi, eta = np.clip(xi, 0, 1), np.clip(eta, 0, 1)
        over = xi + eta > 1
        xi[over], eta[over] = xi[over] / (xi[over] + eta[over]), eta[over] / (xi[over] + eta[over])
    n, dxi, deta = _tri6(xi, eta)
    q[rows] = np.einsum("mk,mki->mi", n, x6)
    normal = np.cross(np.einsum("mk,mki->mi", dxi, x6), np.einsum("mk,mki->mi", deta, x6))
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-300)
    flip = (normal * normals[rows]).sum(1) < 0
    normal[flip] *= -1
    normals[rows] = normal
    return q, normals


def _inside_points(mesh: Mesh, corners: np.ndarray) -> np.ndarray:
    """The centroid of the volume element each triangle lies on (NaN for none, as a
    sheet's): its face points away from it."""
    out = np.full((len(corners), 3), np.nan)
    wanted = {frozenset(row[:3].tolist()): i for i, row in enumerate(corners)}
    on = np.unique(corners)
    for b in mesh.blocks:
        if b.dim != 3 or b.element not in _FACES:
            continue
        rows = np.flatnonzero(np.isin(b.connectivity, on).sum(axis=1) >= 3)
        if not len(rows):
            continue
        conn = b.connectivity[rows]
        centres = mesh.node_coords(conn.ravel()).reshape(len(rows), -1, 3).mean(axis=1)
        for face in _FACES[b.element]:
            keys = conn[:, face]
            for r, key in enumerate(keys):
                for tri in ((key[0], key[1], key[2]),) if len(face) == 3 else (
                    (key[0], key[1], key[2]), (key[0], key[2], key[3])
                ):  # fmt: skip
                    i = wanted.get(frozenset(int(n) for n in tri))
                    if i is not None:
                        out[i] = centres[r]
    return out


def closest_points(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """The nearest point to each p on triangle (a, b, c), row by row (Ericson's
    Real-Time Collision Detection, 5.1.5: the Voronoi regions of the corners and edges)."""
    ab, ac, ap, bp, cp = b - a, c - a, p - a, p - b, p - c

    def dot(u, v):
        return (u * v).sum(axis=-1)

    d1, d2, d3, d4, d5, d6 = (
        dot(ab, ap),
        dot(ac, ap),
        dot(ab, bp),
        dot(ac, bp),
        dot(ab, cp),
        dot(ac, cp),
    )
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        total = va + vb + vc
        v, w = vb / total, vc / total
        q = a + ab * v[:, None] + ac * w[:, None]  # inside the face
        regions = [  # later ones win: Ericson tests them first
            ((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0),
             lambda: b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))[:, None]),
            ((vb <= 0) & (d2 >= 0) & (d6 <= 0), lambda: a + ac * (d2 / (d2 - d6))[:, None]),
            ((d6 >= 0) & (d5 <= d6), lambda: c),
            ((vc <= 0) & (d1 >= 0) & (d3 <= 0), lambda: a + ab * (d1 / (d1 - d3))[:, None]),
            ((d3 >= 0) & (d4 <= d3), lambda: b),
            ((d1 <= 0) & (d2 <= 0), lambda: a),
        ]  # fmt: skip
        for where, point in regions:
            if where.any():
                q = np.where(where[:, None], point(), q)
    bad = ~np.isfinite(q).all(axis=1)  # a degenerate triangle: its nearest corner
    if bad.any():
        corners = np.stack([a[bad], b[bad], c[bad]], axis=1)
        k = np.argmin(np.linalg.norm(corners - p[bad, None], axis=2), axis=1)
        q[bad] = corners[np.arange(len(k)), k]
    return q


def nearest(points: np.ndarray, target: Surface) -> tuple[np.ndarray, np.ndarray]:
    """For each point, the nearest point on the target surface and that triangle's index.

    Exact: a KD-tree over the triangles' centres gives candidates, and any triangle
    closer than the best found must have its centre within that distance plus its
    own reach (centre to farthest corner), which is checked."""
    from scipy.spatial import cKDTree

    tris = target.triangles
    centres = tris.mean(axis=1)
    reach = float(np.linalg.norm(tris - centres[:, None], axis=2).max())
    tree = cKDTree(centres)
    k = min(12, len(tris))
    dist, index = tree.query(points, k=k)
    dist, index = dist.reshape(len(points), k), index.reshape(len(points), k)
    best_q = np.empty_like(points)
    best_d = np.full(len(points), np.inf)
    best_t = np.zeros(len(points), dtype=np.int64)
    for j in range(k):
        t = index[:, j]
        q = closest_points(points, tris[t, 0], tris[t, 1], tris[t, 2])
        d = np.linalg.norm(points - q, axis=1)
        better = d < best_d
        best_q[better], best_d[better], best_t[better] = q[better], d[better], t[better]
    # Where a triangle beyond the k-th centre could still be closer, look wider.
    unsure = np.flatnonzero(best_d + reach > dist[:, -1]) if k < len(tris) else []
    for i in unsure:
        for t in tree.query_ball_point(points[i], best_d[i] + reach):
            q = closest_points(points[i : i + 1], tris[t : t + 1, 0], tris[t : t + 1, 1],
                               tris[t : t + 1, 2])[0]  # fmt: skip
            d = float(np.linalg.norm(points[i] - q))
            if d < best_d[i]:
                best_q[i], best_d[i], best_t[i] = q, d, t
    return best_q, best_t


@dataclass
class ContactState:
    """One contact's initial state (lengths in metres, shares of the source's area)."""

    name: str
    component: str
    status: str  # closed, near, open, far or overhanging; "" when it can't be checked
    search_distance: float
    shares: dict[str, float] = field(default_factory=dict)
    largest_gap: float | None = None  # among the nodes within the search distance
    largest_penetration: float = 0.0
    nearest: float | None = None  # the least distance between the two sides
    nodes: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64), repr=False)
    gaps: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)  # + gap, − in
    codes: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int8), repr=False)
    note: str = ""  # why it wasn't checked
    source: str = ""  # the source (slave) selection the nodes are on

    def node_status(self) -> list[str]:
        return [STATUSES[c] for c in self.codes]

    def to_dict(self) -> dict:
        out = {
            "name": self.name,
            "component": self.component,
            "status": self.status,
            "search_distance_m": self.search_distance,
            "shares": {k: round(v, 4) for k, v in self.shares.items() if v},
            "largest_gap_m": self.largest_gap,
            "largest_penetration_m": self.largest_penetration,
            "nearest_m": self.nearest,
            "source_nodes": len(self.nodes),
        }
        return out | ({"note": self.note} if self.note else {})


def model_size(mesh: Mesh) -> float:
    xyz = np.asarray(mesh.coords, dtype=float)
    return float(np.linalg.norm(xyz.max(axis=0) - xyz.min(axis=0))) if len(xyz) else 0.0


def search_distance(contact, mesh: Mesh) -> float:
    own = getattr(contact, "search_distance", None)
    return own.to_si() if own is not None else SEARCH_SHARE * model_size(mesh)


def state(contact, mesh: Mesh, component: str = "") -> ContactState:
    """A contact's initial state on this mesh."""
    reach = search_distance(contact, mesh)
    out = ContactState(contact.name, component, "", reach, source=contact.source)
    source, target = surface(mesh, contact.source), surface(mesh, contact.target)
    if source is None or target is None:
        missing = contact.source if source is None else contact.target
        out.note = f"'{missing}' is not a face selection of this mesh"
        return out
    nodes = source.nodes
    p = mesh.node_coords(nodes)
    q, t = nearest(p, target)
    q, n = on_curved(p, target, q, t)
    d = p - q
    gap = (d * n).sum(axis=1)
    lateral = np.sqrt(np.maximum((d * d).sum(axis=1) - gap**2, 0.0))
    h = target.sizes[t]
    distance = np.linalg.norm(d, axis=1)
    codes = np.where(
        (lateral > LATERAL * h) & (np.abs(gap) <= reach), 4,
        np.where(distance > reach, 3,
                 np.where(gap <= TOUCH * h, 0, np.where(gap <= NEAR_SHARE * reach, 1, 2))),
    ).astype(np.int8)  # fmt: skip
    out.nodes, out.gaps, out.codes = nodes, gap, codes
    weights = source.node_areas()
    w = np.array([weights.get(int(i), 0.0) for i in nodes])
    total = w.sum() or 1.0
    out.shares = {s: float(w[codes == k].sum() / total) for k, s in enumerate(STATUSES)}
    within = (codes != 3) & (codes != 4)
    gaps = gap[within & (gap > 0)]
    out.largest_gap = float(gaps.max()) if len(gaps) else (0.0 if within.any() else None)
    out.largest_penetration = float(max(-gap[within].min(), 0.0)) if within.any() else 0.0
    out.nearest = float(distance.min())
    if (codes == 3).all():
        out.status = "far"
    elif (codes == 4).any():
        out.status = "overhanging"
    elif (codes == 0).any():
        out.status = "closed"
    elif (codes == 1).any():
        out.status = "near"
    else:
        out.status = "open"
    return out


def states(component, mesh: Mesh) -> list[ContactState]:
    """Every contact of a component, on its mesh."""
    return [state(c, mesh, component.name) for c in component.contacts]


# --- checks before the run ----------------------------------------------------------


def shown_length(metres: float) -> str:
    """A length in the display unit (ADR 0024), for messages."""
    from puzzle.model.units import convert, pretty
    from puzzle.settings import current

    unit = current().display_units()["length"]
    if abs(metres) < 1e-12:  # round-off (coincident faces): none
        metres = 0.0
    return f"{convert(metres, 'm', unit):.3g} {pretty(unit)}"


def checks(project, component, mesh: Mesh | None = None, solver: str | None = None,
           found: list[ContactState] | None = None) -> list[Problem]:  # fmt: skip
    """Warnings about a component's contacts, each naming the contact and the fix.
    Without a mesh only the ones that need none (how the bodies are held)."""
    out = held_by_contact_only(project, component)
    if mesh is None or not component.contacts:
        return out
    found = found if found is not None else states(component, mesh)
    by_name = {s.name: s for s in found}
    for c in component.contacts:
        s = by_name.get(c.name)
        if s is None or not s.status:
            continue
        if s.status == "far":
            out.append(Problem(
                "contact_far", c.name,
                f"contact '{c.name}': its sides are {shown_length(s.nearest)} apart at the "
                f"nearest, "
                f"beyond its search distance ({shown_length(s.search_distance)}): "
                + ("a bonded contact would join faces that don't touch"
                   if c.type in ("bonded", "no_separation")
                   else "it never closes, and a body held by it is free")
                + ". Check the two faces, or raise the search distance if the gap is meant",
                {"component": component.name, "nearest_m": s.nearest},
            ))  # fmt: skip
        if s.shares.get("overhanging", 0) > 0:
            out.append(Problem(
                "contact_overhang", c.name,
                f"contact '{c.name}': {s.shares['overhanging']:.0%} of its source face "
                f"({c.source}) lies past the edge of its target ({c.target}), with no target "
                "face under it; CalculiX can invert elements there. Make the smaller face "
                "the source (swap the sides), or trim the face",
                {"component": component.name, "share": s.shares["overhanging"]},
            ))  # fmt: skip
        out += _slave_clashes(project, component, c, mesh, solver)
        line = nonconforming(c, mesh, s) if solver in (None, "code_aster") else None
        if line:
            out.append(Problem(
                "contact_nonconforming", c.name,
                f"contact '{c.name}': its source face ({c.source}) is curved and meets its "
                "target along a line or at a point (as a cylinder or ball on a plate; gaps "
                f"across it from {shown_length(line['gap_min_m'])} to "
                f"{shown_length(line['gap_max_m'])}). Code_Aster's contact fails to pair "
                "such faces, so it refuses them; run the study on CalculiX",
                {"component": component.name} | line,
            ))  # fmt: skip
        if c.type == "frictional" and solver in (None, "code_aster"):
            share = shared_outline(mesh, c.source, c.target)
            if share > 0.5:
                out.append(Problem(
                    "contact_shared_outline", c.name,
                    f"contact '{c.name}': its source and target faces have the same outline "
                    f"({share:.0%} of the source's edge lies on the target's); Code_Aster's 3D "
                    "friction is known to fail there (singular friction multipliers on the "
                    "rim). Run it on CalculiX, or make one face smaller than the other",
                    {"component": component.name, "share": share},
                ))  # fmt: skip
    return out


CURVED_SPREAD = 2.0  # a source face whose normals turn by more (degrees) is curved
GAP_SPREAD = 0.25  # gaps that differ by more than this share of the element size: not alike


def nonconforming(contact, mesh: Mesh, s: ContactState | None = None) -> dict | None:
    """A curved source face meeting a face of another shape (a cylinder or ball on a
    plate, Hertz's line and point contacts): its normals turn by more than
    `CURVED_SPREAD` and the gaps across it differ by more than `GAP_SPREAD` of the
    target's element size. A press fit (one curve inside a like one) and flat faces
    don't count. Code_Aster's continuous contact fails to pair such faces before its
    first increment (tests/integration/test_hertz.py), so it refuses them."""
    if contact.type not in ("frictionless", "frictional") or contact.thread is not None:
        return None
    s = s if s is not None else state(contact, mesh)
    source, target = surface(mesh, contact.source), surface(mesh, contact.target)
    if not s.status or source is None or target is None:
        return None
    within = (s.codes != 3) & (s.codes != 4)
    if within.sum() < 3:
        return None
    mean = source.normals.mean(axis=0)
    mean /= max(float(np.linalg.norm(mean)), 1e-300)
    turn = float(np.degrees(np.arccos(np.clip(source.normals @ mean, -1.0, 1.0))).max())
    gaps = s.gaps[within]
    spread = float(gaps.max() - gaps.min())
    h = float(np.median(target.sizes))
    if turn <= CURVED_SPREAD or spread <= GAP_SPREAD * h:
        return None
    return {"turn_deg": turn, "gap_min_m": float(gaps.min()), "gap_max_m": float(gaps.max())}


def _slave_clashes(project, component, contact, mesh: Mesh, solver) -> list[Problem]:
    """Source nodes also held, tied by another contact, or in a bolt's spider."""
    from puzzle.adapters.common import fastener_selections

    if contact.effective_meshing == "conformal" or contact.source not in mesh.groups:
        return []
    slave = np.asarray(mesh.groups[contact.source].node_ids)

    def shared(selections):
        return sorted({s for s in selections if s in mesh.groups
                       and np.intersect1d(slave, mesh.groups[s].node_ids).size})  # fmt: skip

    features = [f for p in component.physics if p.type == "solid_mechanics" for f in p.features]
    # With friction any hold on a source node clashes (Code_Aster duplicates a multiplier);
    # without, only a node held in every direction: a symmetry or plane-strain condition
    # on a source face's edge is fine (the press fit and Hertz models have them).
    holding = HOLDING if contact.type == "frictional" else ("fixed",)
    held = shared(f.selection for f in features if f.type in holding)
    others = shared(o.source for o in component.contacts
                    if o is not contact and o.effective_meshing == "nonconformal")  # fmt: skip
    spiders = shared(fastener_selections(component.connectors))
    out = []
    if held or others:
        what = ", ".join(held + others)
        out.append(Problem(
            "contact_slave_held", contact.name,
            f"contact '{contact.name}': nodes of its source face ({contact.source}) are also "
            f"{'held' if held else 'the source of another contact'} ({what}). A node held "
            "twice makes Code_Aster's contact multipliers singular and floods CalculiX's log; "
            "hold the part away from the interface, or swap the contact's sides",
            {"component": component.name, "with": held + others},
        ))  # fmt: skip
    if spiders and solver in (None, "code_aster"):  # CalculiX takes them as they are
        out.append(Problem(
            "contact_slave_bolted", contact.name,
            f"contact '{contact.name}': its source face shares nodes with bolt ends "
            f"({', '.join(spiders)}); Code_Aster leaves those nodes out of the contact "
            "(SANS_GROUP_NO), so the face's rim at the holes is not in contact there",
            {"component": component.name, "with": spiders},
        ))  # fmt: skip
    return out


def _outline(s: Surface) -> np.ndarray:
    """The surface's boundary edges (used by one triangle only), as (n, 2, 3) points."""
    edges = np.concatenate([s.corners[:, [0, 1]], s.corners[:, [1, 2]], s.corners[:, [2, 0]]])
    keys = np.sort(edges, axis=1)
    unique, index, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    xyz = np.concatenate([s.triangles[:, [0, 1]], s.triangles[:, [1, 2]], s.triangles[:, [2, 0]]])
    return xyz[index[counts == 1]]


def shared_outline(mesh: Mesh, source: str, target: str) -> float:
    """The share of the source face's outline nodes lying on the target's outline."""
    s, t = surface(mesh, source), surface(mesh, target)
    if s is None or t is None:
        return 0.0
    ours, theirs = _outline(s), _outline(t)
    if not len(ours) or not len(theirs):
        return 0.0
    points = np.unique(ours.reshape(-1, 3), axis=0)
    a, b = theirs[:, 0], theirs[:, 1]
    tol = LATERAL * float(np.median(t.sizes))
    on = np.zeros(len(points), dtype=bool)
    for start in range(0, len(points), max(1, 2_000_000 // len(a))):
        p = points[start : start + max(1, 2_000_000 // len(a))]
        ab = b - a
        u = np.clip(((p[:, None] - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-300), 0, 1)
        d = np.linalg.norm(p[:, None] - (a + u[..., None] * ab), axis=2).min(axis=1)
        on[start : start + len(p)] = d <= tol
    return float(on.mean())


def held_by_contact_only(project, component) -> list[Problem]:
    """Bodies with no path to ground but through sliding contacts, or none at all.

    Supports ground the part they're on; bonds, no-separation contacts and bolts join
    parts; frictionless and frictional contacts don't (they hold only once closed and
    pressed)."""
    from puzzle.suppress import suppressed_parts

    solid = [p for p in component.physics if p.type == "solid_mechanics"]
    if not solid or len(component.parts) < 2:
        return []
    off = set(suppressed_parts(project, component))
    parts = [p.name for p in component.parts if p.name not in off]

    def part(selection):
        return selection.split("/", 1)[0] if selection else None

    grounded = {part(f.selection) for p in solid for f in p.features if f.type in HOLDING}
    joins = [(part(c.source), part(c.target)) for c in component.contacts
             if c.type not in SLIDING]  # fmt: skip
    for b in component.connectors:
        if getattr(b, "ground", False) or b.target is None:
            grounded.add(part(b.source))
        else:
            joins.append((part(b.source), part(b.target)))
    slides = [(part(c.source), part(c.target)) for c in component.contacts
              if c.type in SLIDING]  # fmt: skip
    if not grounded:
        return []  # the whole component is free: Project.problems says so (unconstrained)

    def reach(start, links):
        seen = set(start)
        while True:
            more = {b for a, b in links if a in seen} | {a for a, b in links if b in seen}
            if more <= seen:
                return seen
            seen |= more

    held = reach(grounded, joins)
    touching = reach(held, joins + slides)
    out = []
    for name in parts:
        if name in held:
            continue
        if name in touching:
            out.append(Problem(
                "contact_only_hold", name,
                f"body '{name}' is held only through frictionless or frictional contact: "
                "until the contact closes and is pressed it is free, and the first solve "
                "is singular. The solve adds weak springs to hold it until then (the "
                "study's Weak springs; ADR 0080), or add a support, a bond or a bolt",
                {"component": component.name},
            ))  # fmt: skip
        else:
            out.append(Problem(
                "free_body", name,
                f"body '{name}' of '{component.name}' is neither held nor joined to a held "
                "body: add a support, a contact or a bolt",
                {"component": component.name},
            ))  # fmt: skip
    return out


def solvers_of(project, component) -> list[str]:
    """The solvers of the studies on this component, in order, each once."""
    from puzzle.model.schema import Study

    return list(dict.fromkeys(
        s.solver for s in project.of_type(Study) if s.component == component.id and s.solver
    ))  # fmt: skip


def report(project, component, mesh: Mesh | None, solver: str | None = None) -> dict:
    """The Contact tool's Setup and Initial state, and the checks, as plain data (for
    the agent and the CLI). `solver`: whose input to describe and check against;
    default every solver of the component's studies."""
    from puzzle.adapters import available_adapters

    solvers = [solver] if solver else solvers_of(project, component)
    found = states(component, mesh) if mesh is not None else []
    by_name = {s.name: s for s in found}
    given = {}
    for name in solvers:
        try:
            given[name] = available_adapters()[name]().contact_summary(project, component, mesh)
        except Exception:  # noqa: BLE001 - an adapter that can't say: nothing to show
            given[name] = {}
    contacts = []
    for c in component.contacts:
        row = {
            "name": c.name, "type": c.type, "source": c.source, "target": c.target,
            "friction": c.friction, "meshing": c.effective_meshing, "stiffness": c.stiffness,
            "solver_input": {n: g[c.name] for n, g in given.items() if c.name in g},
        }  # fmt: skip
        if c.name in by_name:
            row["initial"] = by_name[c.name].to_dict()
        contacts.append(row)
    warnings: list[dict] = []
    for name in solvers or [None]:
        for w in checks(project, component, mesh, name, found):
            if w.to_dict() not in warnings:
                warnings.append(w.to_dict())
    return {
        "component": component.name,
        "contacts": contacts,
        "warnings": warnings,
        "meshed": mesh is not None,
    }


def adjusted(component, mesh: Mesh) -> tuple[Mesh, dict[str, tuple[int, float]]]:
    """The mesh with each adjusted or offset contact's source nodes moved, for every
    solver alike: adjust (Ansys's adjust to touch, CalculiX's ADJUST) puts each onto
    its nearest target point; offset puts it `offset` from there along the target's
    outward normal (− an interference: inside the target). Moved are the nodes within
    the search distance with a target face under them; points are found on the
    target's own curved faces (`on_curved`). Moving nodes rather than writing
    CalculiX's *CLEARANCE: that sets the gap, and its contact pressures came out three
    times Lamé's on a press fit (the displacements right), while the moved geometry
    gave Lamé's to 0.2 %. Returns the mesh (a copy when anything moved) and per
    contact (nodes moved, the largest move in m)."""
    import dataclasses

    moved: dict[str, tuple[int, float]] = {}
    coords = None
    for c in component.contacts:
        interface = getattr(c, "interface", "as_is")
        if interface not in ("adjust", "offset"):
            continue
        offset = c.offset.to_si() if interface == "offset" else 0.0
        source, target = surface(mesh, c.source), surface(mesh, c.target)
        if source is None or target is None:
            continue
        reach = search_distance(c, mesh)
        current = mesh if coords is None else dataclasses.replace(mesh, coords=coords)
        p = current.node_coords(source.nodes)
        q, t = nearest(p, target)
        q, n = on_curved(p, target, q, t)
        d = p - q
        gap = (d * n).sum(axis=1)
        lateral = np.sqrt(np.maximum((d * d).sum(axis=1) - gap**2, 0.0))
        move = (np.linalg.norm(d, axis=1) <= reach) & (lateral <= LATERAL * target.sizes[t])
        if not move.any():
            moved[c.name] = (0, 0.0)
            continue
        if coords is None:
            coords = np.array(mesh.coords, dtype=float, copy=True)
        order = np.argsort(mesh.node_ids)
        rows = order[np.searchsorted(mesh.node_ids, source.nodes[move], sorter=order)]
        goal = q + offset * n
        coords[rows] = goal[move]
        moved[c.name] = (int(move.sum()),
                         float(np.linalg.norm((goal - p)[move], axis=1).max()))  # fmt: skip
    if coords is None:
        return mesh, moved
    return dataclasses.replace(mesh, coords=coords), moved


def moved_words(c) -> str:
    """What adjust and offset do to the mesh before it is written, for the Setup tab."""
    interface = getattr(c, "interface", "as_is")
    if interface == "adjust":
        return "; source nodes moved onto the target first (adjust to touch)"
    if interface == "offset":
        what = "apart" if c.offset.to_si() >= 0 else "into the target (interference)"
        return f"; source nodes moved to start {shown_length(abs(c.offset.to_si()))} {what}"
    return ""
