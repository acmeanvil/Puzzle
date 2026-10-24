"""Which geometric faces and edges meet, and at what angle, from a mesh (ADR 0034).

Selection tools extend picks across smooth joins: "extend to tangent" grows a face
selection across edges whose dihedral angle is small (a fillet and the faces it
blends), "extend to chain" grows an edge selection along edges that continue each other.
Both work on the mesh's own surface triangles and lines, so no geometry kernel is
needed in the GUI. Angles are in degrees; 0 means smooth.
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

from puzzle.mesh.mesh import Mesh

CORNERS = {"tri3": 3, "tri6": 3, "quad4": 4, "quad8": 4, "quad9": 4}


def _index(mesh: Mesh):
    order = np.argsort(mesh.node_ids)

    def coords(ids):
        return mesh.coords[order[np.searchsorted(mesh.node_ids, ids, sorter=order)]]

    return coords


def face_neighbours(mesh: Mesh) -> dict[int, dict[int, float]]:
    """Face tag -> {neighbouring face tag: the largest angle between their surface
    normals along the shared edges}. Orientation is ignored (|n1·n2|): geometric faces
    need not be oriented alike."""
    coords = _index(mesh)
    by_edge: dict[tuple[int, int], list[tuple[int, np.ndarray]]] = defaultdict(list)
    for b in mesh.blocks:
        if b.dim != 2 or b.element not in CORNERS:
            continue
        corners = b.connectivity[:, : CORNERS[b.element]]
        pts = coords(corners.ravel()).reshape(len(corners), -1, 3)
        normals = np.cross(pts[:, 1] - pts[:, 0], pts[:, 2] - pts[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-300)
        k = corners.shape[1]
        for cell, n in zip(corners, normals, strict=True):
            for i in range(k):
                a, c = int(cell[i]), int(cell[(i + 1) % k])
                by_edge[(min(a, c), max(a, c))].append((b.entity[1], n))
    out: dict[int, dict[int, float]] = defaultdict(dict)
    for sides in by_edge.values():
        for i, (fa, na) in enumerate(sides):
            for fb, nb in sides[i + 1 :]:
                if fa == fb:
                    continue
                angle = float(np.degrees(np.arccos(min(1.0, abs(float(na @ nb))))))
                for x, y in ((fa, fb), (fb, fa)):
                    out[x][y] = max(out[x].get(y, 0.0), angle)
    return dict(out)


def tangent_faces(mesh: Mesh, seeds, max_angle: float = 20.0) -> set[int]:
    """The seed faces and every face reached across joins smoother than `max_angle`."""
    return _spread(face_neighbours(mesh), seeds, max_angle)


def edge_neighbours(mesh: Mesh) -> dict[int, dict[int, float]]:
    """Edge tag -> {edge tag meeting it at an end: the angle between their directions
    there}, 0 when one continues the other."""
    coords = _index(mesh)
    ends: dict[int, list[tuple[int, np.ndarray]]] = defaultdict(list)
    for b in mesh.blocks:
        if b.dim != 1:
            continue
        lines = b.connectivity[:, :2]
        if not len(lines):
            continue
        # The edge's two end nodes: nodes used once among its segments' corners.
        used, counts = np.unique(lines.ravel(), return_counts=True)
        for node in used[counts == 1]:
            seg = lines[np.any(lines == node, axis=1)][0]
            other = seg[1] if seg[0] == node else seg[0]
            p, q = coords(np.array([node, other]))
            d = q - p  # pointing into the edge from its end
            ends[int(node)].append((b.entity[1], d / max(np.linalg.norm(d), 1e-300)))
    out: dict[int, dict[int, float]] = defaultdict(dict)
    for meeting in ends.values():
        for i, (ea, da) in enumerate(meeting):
            for eb, db in meeting[i + 1 :]:
                if ea == eb:
                    continue
                # Continuing edges point opposite ways from their shared end.
                angle = float(np.degrees(np.arccos(np.clip(-float(da @ db), -1.0, 1.0))))
                for x, y in ((ea, eb), (eb, ea)):
                    out[x][y] = min(out[x].get(y, 180.0), angle)
    return dict(out)


def edge_chain(mesh: Mesh, seeds, max_angle: float = 20.0) -> set[int]:
    return _spread(edge_neighbours(mesh), seeds, max_angle)


def _spread(neighbours, seeds, max_angle) -> set[int]:
    found = set(seeds)
    todo = deque(found)
    while todo:
        for n, angle in neighbours.get(todo.popleft(), {}).items():
            if angle <= max_angle and n not in found:
                found.add(n)
                todo.append(n)
    return found


def centres(mesh: Mesh, dim: int) -> dict[int, np.ndarray]:
    """Each geometric entity's centre (the mean of its elements' nodes)."""
    coords = _index(mesh)
    out: dict[int, list] = defaultdict(list)
    for b in mesh.blocks:
        if b.dim == dim:
            out[b.entity[1]].append(coords(np.unique(b.connectivity.ravel())))
    return {tag: np.concatenate(pts).mean(axis=0) for tag, pts in out.items()}


def samples(mesh: Mesh, dim: int, n: int = 24) -> dict[int, np.ndarray]:
    """Up to `n` points on each entity (element centres, spread over it): points that
    are really on a face or an edge, unlike its mean, which a hole can leave in the air."""
    coords = _index(mesh)
    out: dict[int, list] = defaultdict(list)
    for b in mesh.blocks:
        if b.dim != dim:
            continue
        k = CORNERS.get(b.element, 2 if dim == 1 else b.connectivity.shape[1])
        corners = b.connectivity[:, :k]
        pts = coords(corners.ravel()).reshape(len(corners), k, 3).mean(axis=1)
        out[b.entity[1]].append(pts)
    picked = {}
    for tag, chunks in out.items():
        pts = np.concatenate(chunks)
        step = max(1, len(pts) // n)
        picked[tag] = pts[::step][:n]
    return picked


def cylinder_axis(mesh: Mesh, group_name: str):
    """A cylindrical face's axis from its mesh (ADR 0037: axes picked from a face):
    (a point on the axis, unit direction, radius), or None when the face isn't one.
    Every normal of a cylinder is square to its axis, so the axis is the direction
    the normals leave out; the centre is a least-squares circle in the plane across it."""
    group = mesh.groups.get(group_name)
    if group is None or group.dim != 2:
        return None
    coords = _index(mesh)
    tri = [
        coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        for b in group.blocks(mesh)
        if b.element in CORNERS
    ]
    if not tri:
        return None
    xyz = np.concatenate(tri)
    n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-300)
    values, vectors = np.linalg.eigh(n.T @ n)
    if values[0] > 1e-2 * values[2] or values[1] < 0.05 * values[2]:
        return None  # normals not square to one direction (not a cylinder), or a plane
    points = coords(group.node_ids)
    # Flat triangles tilt their normals a little; the nodes lie on the surface itself.
    # A quadric through them, x·Qx + b·x + c = 0, has Q ∝ I - a aᵀ: the axis a is Q's
    # null direction.
    centre, scale = points.mean(axis=0), float(np.ptp(points, axis=0).max()) or 1.0
    q = (points - centre) / scale
    x, y, z = q.T
    rows = np.column_stack([x * x, y * y, z * z, x * y, x * z, y * z, x, y, z, np.ones(len(q))])
    coef = np.linalg.svd(rows, full_matrices=False)[2][-1]
    Q = np.array(
        [
            [coef[0], coef[3] / 2, coef[4] / 2],
            [coef[3] / 2, coef[1], coef[5] / 2],
            [coef[4] / 2, coef[5] / 2, coef[2]],
        ]
    )
    w, v = np.linalg.eigh(Q)
    pick = int(np.argmin(np.abs(w)))
    axis = v[:, pick] if abs(v[:, pick] @ vectors[:, 0]) > 0.99 else vectors[:, 0]
    u = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    p2 = np.column_stack([points @ u, points @ v])
    # x² + y² = 2 a x + 2 b y + c, linear in (a, b, c)
    A = np.column_stack([2 * p2, np.ones(len(p2))])
    (a, b, c), *_ = np.linalg.lstsq(A, (p2**2).sum(axis=1), rcond=None)
    radius = float(np.sqrt(c + a * a + b * b))
    spread = np.abs(np.hypot(p2[:, 0] - a, p2[:, 1] - b) - radius).max()
    if spread > 1e-3 * radius:
        return None
    along = float((points @ axis).min())
    origin = a * u + b * v + along * axis
    if axis @ np.array([1.0, 1e-3, 1e-6]) < 0:  # a stable sign: +x, +y, +z first
        axis = -axis
    return origin, axis, radius
