"""A threaded contact on a mesh (ADR 0076): `threads.ThreadPairs` for the adapters.

The screw is the inner of the two faces. Its bearing flank faces the way the screw goes
on past the engagement (pulled out of its nut or boss, as a tightened bolt is), unless
the thread's `bearing` says otherwise; the load flank (the form's first angle) is the
bearing one. A bonded thread ties its bearing flank; no_separation, frictionless and
frictional ones act on both flanks.
"""

from __future__ import annotations

import math

import numpy as np

from puzzle import threads
from puzzle.adapters.common import InputError
from puzzle.mesh import Mesh


def edge_shares(group, mesh: Mesh):
    """A 2D axisymmetric model's edge selection: each node's share ∫N 2πr ds (m²)
    -> (node ids, areas)."""
    gauss = np.array([-math.sqrt(3 / 5), 0.0, math.sqrt(3 / 5)])
    weights = np.array([5 / 9, 8 / 9, 5 / 9])
    share: dict[int, float] = {}
    for b in group.blocks(mesh):
        if b.dim != 1:
            continue
        x = mesh.node_coords(b.connectivity.ravel()).reshape(len(b.connectivity), -1, 3)
        quadratic = b.connectivity.shape[1] == 3
        for t, w in zip(gauss, weights, strict=True):
            if quadratic:  # Gmsh's line3: the two ends, then the middle
                n = np.array([t * (t - 1) / 2, t * (t + 1) / 2, 1 - t * t])
                dn = np.array([t - 0.5, t + 0.5, -2 * t])
            else:
                n, dn = np.array([(1 - t) / 2, (1 + t) / 2]), np.array([-0.5, 0.5])
            at = np.einsum("k,eki->ei", n, x)
            ds = np.linalg.norm(np.einsum("k,eki->ei", dn, x), axis=1) * w
            da = ds * 2 * math.pi * np.abs(at[:, 0])
            for k, column in enumerate(b.connectivity.T):
                for nid, a in zip(column, n[k] * da, strict=True):
                    share[int(nid)] = share.get(int(nid), 0.0) + float(a)
    if not share:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    top = max(share.values())
    ids = np.array(sorted(i for i, a in share.items() if a > 1e-12 * top), dtype=np.int64)
    return ids, np.array([share[int(i)] for i in ids])


def modulus(project, comp, selection: str) -> float:
    """Young's modulus on the part (or body) `selection` belongs to."""
    from puzzle.model.schema import Material

    part = selection.split("/", 1)[0]
    for a in comp.materials:
        if a.selection == part or a.selection.startswith(part + "/"):
            props = project.get(a.material, Material).properties
            if "youngs_modulus" in props:
                return props["youngs_modulus"].to_si()
    raise InputError(f"a thread on '{selection}' needs a youngs_modulus on its part's material")


# Each node's share of a face element's area. Second order: every node takes a share
# (`face_shares`); an eight-node quadrilateral's are its diagonal mass scaled to the
# area (Hinton, Rock and Zienkiewicz), corners 3/76, mid-edge nodes 16/76.
SHARES = {
    "tri3": [1 / 3] * 3,
    "tri6": [1 / 12] * 3 + [1 / 4] * 3,
    "quad4": [1 / 4] * 4,
    "quad8": [3 / 76] * 4 + [16 / 76] * 4,
}


def face_shares(group, mesh: Mesh):
    """A face selection's nodes, each one's share of the area (m²) and its normal. Every
    node takes a share (six-node triangles: corners 1/12, mid-edge nodes 1/4): the
    consistent shares put none on the corners, and flank springs on the mid-edge nodes
    alone let the corners float, the forces alternating ring to ring (3D benchmark)."""
    share: dict[int, float] = {}
    normal: dict[int, np.ndarray] = {}
    centre = mesh.coords.mean(axis=0)
    for b in group.blocks(mesh):
        if b.element not in SHARES:
            continue
        conn = b.connectivity
        corners = 4 if b.element.startswith("quad") else 3
        c = mesh.node_coords(conn[:, :corners].ravel()).reshape(-1, corners, 3)
        if corners == 4:  # a revolved bolt's quadrilaterals (ADR 0077): half the diagonals' cross
            cross = 0.5 * np.cross(c[:, 2] - c[:, 0], c[:, 3] - c[:, 1])
            area = np.linalg.norm(cross, axis=1)
        else:
            cross = np.cross(c[:, 1] - c[:, 0], c[:, 2] - c[:, 0])
            area = 0.5 * np.linalg.norm(cross, axis=1)
        unit = cross / np.maximum(np.linalg.norm(cross, axis=1, keepdims=True), 1e-300)
        flip = np.einsum("ij,ij->i", unit, c.mean(axis=1) - centre) < 0
        unit[flip] *= -1
        weights = SHARES[b.element]
        for k, w in enumerate(weights):
            for nid, a, d in zip(conn[:, k], area, unit, strict=True):
                share[int(nid)] = share.get(int(nid), 0.0) + w * a
                normal[int(nid)] = normal.get(int(nid), 0.0) + w * a * d
    ids = np.array(sorted(share), dtype=np.int64)
    normals = np.array([normal[int(i)] for i in ids])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-300)
    return ids, np.array([share[int(i)] for i in ids]), normals


def _side(mesh: Mesh, selection: str, name: str):
    group = mesh.groups.get(selection)
    if group is None:
        raise InputError(f"thread '{name}': no selection '{selection}'", path=name)
    if mesh.planar:
        ids, areas = edge_shares(group, mesh)
        normals = None
    else:
        ids, areas, normals = face_shares(group, mesh)
    return ids, areas, normals, mesh.node_coords(ids)


def _frame(project, contact, sides, planar: bool) -> threads.Frame:
    from puzzle.model.schema import CoordinateSystem

    if planar:  # ADR 0048: Y the axis, X the radius
        frame = threads.Frame(np.zeros(3), np.array([0.0, 1.0, 0.0]))
        rs = [float(np.mean(np.abs(s[3][:, 0]))) for s in sides]
        zs = [s[3][:, 1] for s in sides]
        frame.radii = (rs[0], rs[1])
        frame.overlap = (float(max(zs[0].min(), zs[1].min())),
                         float(min(zs[0].max(), zs[1].max())))  # fmt: skip
        return frame
    (_, _, ns, ps), (_, _, nt, pt) = sides
    if contact.thread.cs is not None:
        cs = project.get(contact.thread.cs, CoordinateSystem)
        return threads.axis_frame((ps, ns), (pt, nt), axis=cs.orientation()[:, 2],
                                  origin=cs.centre())  # fmt: skip
    return threads.axis_frame((ps, ns), (pt, nt), axis=contact.thread.axis)


def normals(xyz, frame: threads.Frame, thread: threads.Spec, flank: str, load_up: bool,
            planar: bool) -> np.ndarray:  # fmt: skip
    """The screw's outward unit normal on `flank` at each point (the helix dropped in a
    2D axisymmetric model), the load flank facing the way the screw is pulled."""
    e_r, e_t, e_z = frame.basis(xyz)
    r, _, _ = frame.cylindrical(xyz)
    lead = 0.0 if planar else thread.hand * thread.lead
    alpha = math.radians(threads.flank_angle(thread, flank, load_up))
    sign = 1.0 if flank == "upper" else -1.0
    n = sign * e_z - sign * (lead / (2 * math.pi * r))[:, None] * e_t + math.tan(alpha) * e_r
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def thread_pairs(project, comp, mesh: Mesh, contact) -> threads.ThreadPairs:
    """The contact's pairs, normals, areas and stiffness on this mesh."""
    thread = threads.spec(contact.thread)
    planar = bool(mesh.planar)
    if planar and not mesh.axisymmetric:
        raise InputError(f"thread '{contact.name}': a thread needs a 3D or axisymmetric model",
                         code="unsupported", path=contact.name)  # fmt: skip
    sides = [_side(mesh, sel, contact.name) for sel in (contact.source, contact.target)]
    frame = _frame(project, contact, sides, planar)
    gap = abs(frame.radii[0] - frame.radii[1])
    if gap > 0.02 * thread.major:
        raise InputError(
            f"thread '{contact.name}': its faces' radii differ by {gap * 1e3:.3g} mm, more "
            "than 2 % of the diameter: they should be one cylinder", code="thread_radii",
            path=contact.name)  # fmt: skip
    screw = 0 if frame.radii[0] <= frame.radii[1] else 1
    s_ids, s_areas, _, s_xyz = sides[screw]
    t_ids, _, _, t_xyz = sides[1 - screw]
    lo, hi = frame.overlap
    _, _, z_all = frame.cylindrical(s_xyz)
    inside = (z_all >= lo - 1e-9) & (z_all <= hi + 1e-9)
    s_ids, s_areas, s_xyz = s_ids[inside], s_areas[inside], s_xyz[inside]
    limit = 0.25 * thread.pitch + 0.05 * thread.major
    a_ids, b_ids, _ = threads.pairs(s_xyz, s_ids, t_xyz, t_ids, frame, limit=limit)
    if not len(a_ids):
        raise InputError(f"thread '{contact.name}': no node pairs across its faces",
                         path=contact.name)  # fmt: skip
    area_of = dict(zip(s_ids.tolist(), s_areas.tolist(), strict=True))
    xyz = mesh.node_coords(a_ids)
    _, _, z = frame.cylindrical(xyz)
    screw_side = contact.source if screw == 0 else contact.target
    nut_side = contact.target if screw == 0 else contact.source
    body = mesh.groups.get(screw_side.split("/", 1)[0])
    pulled = 1.0  # toward where the screw's part goes on past the engagement
    if body is not None:
        _, _, zb = frame.cylindrical(mesh.node_coords(body.node_ids))
        pulled = 1.0 if float(np.mean(zb)) >= (lo + hi) / 2 else -1.0
    # The pull decides which side bears; `bearing` which of the form's angles is on it
    # (a buttress's 7° load flank, or its 45° one with "other").
    bearing = threads.bearing_flank(pulled)
    load_up = (pulled > 0) != (contact.thread.bearing == "other")
    flanks = (bearing,) if contact.type == "bonded" else ("upper", "lower")
    e_screw, e_nut = modulus(project, comp, screw_side), modulus(project, comp, nut_side)
    out = threads.ThreadPairs(source=a_ids, target=b_ids, z=z, clearance=thread.clearance,
                              flanks=flanks)  # fmt: skip
    out.screw_is_source = screw == 0
    out.pulled = pulled
    out.mouth = hi if pulled > 0 else lo
    out.pitch = thread.pitch
    out.e_r, _e_t, out.e_z = frame.basis(xyz)  # each pair's axes, for results
    out.compliance = contact.thread.compliance
    out.kind = contact.type
    cylinder = np.array([area_of[int(i)] for i in a_ids])
    for flank in flanks:
        alpha = math.radians(threads.flank_angle(thread, flank, load_up))
        out.normals[flank] = normals(xyz, frame, thread, flank, load_up, planar)
        out.areas[flank] = cylinder / math.cos(alpha)
        out.stiffness[flank] = threads.stiffness(out.areas[flank], thread, e_screw, e_nut,
                                                 contact.thread.compliance)  # fmt: skip
    if not planar:
        # In 3D a tie along the flanks alone leaves the screw free to turn but for the
        # helix's small part (L / 2πr): pulled, it unscrewed until the springs balanced,
        # the flank forces swinging hundreds of percent thread to thread. A thread is held
        # from turning by friction: a "turn" spring per pair, circumferential (square to
        # the bearing flank's normal), as stiff as that flank. It carries no flank load.
        _r, e_t, _z = frame.basis(xyz)
        n0 = out.normals[bearing]
        t = e_t - np.einsum("ij,ij->i", e_t, n0)[:, None] * n0
        out.normals[TURN] = t / np.linalg.norm(t, axis=1, keepdims=True)
        out.areas[TURN] = out.areas[bearing]
        out.stiffness[TURN] = out.stiffness[bearing]
        out.flanks = (*flanks, TURN)
    return out


TURN = "turn"  # the circumferential spring that holds a 3D thread from turning
