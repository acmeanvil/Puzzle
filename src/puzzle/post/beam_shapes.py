"""Beam members drawn as their cross-sections (ADR 0055 decision 5).

`outline` gives a beam section's shape as polygons in the section's own axes (u along
its first axis, v along its second, metres, the centroid at the origin): Puzzle's own
rectangle, circle, tube and box, and the tables' I, channel, tee, angle, double angle,
rectangular and round hollow sections. The first axis lies across the flanges (an
I's flange width, a rectangle's width), so the web runs along the second axis; for
angles the legs lie along the two axes. The polygons are pure geometry, so they are
tested against the tables' areas without OpenGL.

`members` resolves, per meshed member of a line part with a section, the first axis
the solver gets (`puzzle.beams.first_axis`, on the same nodes as `with_orientations`),
and `solid` sweeps the outlines along the members into one surface for the view,
optionally with a value per node (results).

The frame is right-handed (first, second, member): the second axis is the member's
direction × the first, the member running from its line elements' first node to their
second, as CalculiX builds its beams' frame.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass, field

import numpy as np

# A region of a section: its outer loop and, for a hollow one, the hole's loop, both
# counter-clockwise (k, 2) and of the same length (the cap is a strip between them).
Region = tuple[np.ndarray, "np.ndarray | None"]
ROUND = 48  # points around a circle
ARC = 6  # segments in a fillet


def _shoelace(loop: np.ndarray) -> float:
    u, v = loop[:, 0], loop[:, 1]
    return 0.5 * float(np.dot(u, np.roll(v, -1)) - np.dot(np.roll(u, -1), v))


def area(regions: list[Region]) -> float:
    """The drawn outline's area (m²)."""
    return sum(_shoelace(o) - (_shoelace(h) if h is not None else 0.0) for o, h in regions)


def _centroid(regions: list[Region]) -> np.ndarray:
    def first_moment(loop):
        u, v = loop[:, 0], loop[:, 1]
        cross = u * np.roll(v, -1) - np.roll(u, -1) * v
        return (
            np.array([((u + np.roll(u, -1)) * cross).sum(), ((v + np.roll(v, -1)) * cross).sum()])
            / 6
        )

    total = sum(first_moment(o) - (first_moment(h) if h is not None else 0.0) for o, h in regions)
    return total / area(regions)


def _rounded(corners: list[tuple[float, float]], radii: list[float]) -> np.ndarray:
    """A counter-clockwise polygon with each corner rounded by its radius (0: sharp),
    convex or re-entrant alike."""
    pts = np.asarray(corners, float)
    out = []
    n = len(pts)
    for i in range(n):
        p, r = pts[i], radii[i]
        if r <= 0:
            out.append(p)
            continue
        d_in = p - pts[i - 1]
        d_out = pts[(i + 1) % n] - p
        d_in, d_out = d_in / np.linalg.norm(d_in), d_out / np.linalg.norm(d_out)
        a, b = p - d_in * r, p + d_out * r  # where the arc meets each side
        # The centre is r from both sides, on the inside of the turn.
        turn = d_in[0] * d_out[1] - d_in[1] * d_out[0]  # > 0 convex (left turn)
        normal = np.array([-d_in[1], d_in[0]]) * (1 if turn > 0 else -1)
        c = a + normal * r
        t0, t1 = math.atan2(*(a - c)[::-1]), math.atan2(*(b - c)[::-1])
        sweep = (t1 - t0 + math.pi) % (2 * math.pi) - math.pi
        for k in range(ARC + 1):
            t = t0 + sweep * k / ARC
            out.append(c + r * np.array([math.cos(t), math.sin(t)]))
    return np.asarray(out)


def _circle(radius: float) -> np.ndarray:
    t = np.linspace(0, 2 * math.pi, ROUND, endpoint=False)
    return radius * np.column_stack([np.cos(t), np.sin(t)])


def _rect(width: float, height: float, radius: float = 0.0) -> np.ndarray:
    w, h = width / 2, height / 2
    return _rounded([(w, -h), (w, h), (-w, h), (-w, -h)], [radius] * 4)


def _centred(regions: list[Region]) -> list[Region]:
    c = _centroid(regions)
    return [(o - c, h - c if h is not None else None) for o, h in regions]


def _i(b, d, tw, tf, r=0.0) -> list[Region]:
    w, h, t = b / 2, d / 2, tw / 2
    right = [(w, -h), (w, -h + tf), (t, -h + tf), (t, h - tf), (w, h - tf), (w, h)]
    corners = right + [(-u, v) for u, v in reversed(right)]  # mirrored: the left half
    radii = [0, 0, r, r, 0, 0, 0, 0, r, r, 0, 0]
    return [(_rounded(corners, radii), None)]


def _channel(b, d, tw, tf, r=0.0) -> list[Region]:
    """Its web's back at u = 0, the flanges toward +first."""
    h = d / 2
    corners = [(0, -h), (b, -h), (b, -h + tf), (tw, -h + tf), (tw, h - tf), (b, h - tf),
               (b, h), (0, h)]  # fmt: skip
    return _centred([(_rounded(corners, [0, 0, 0, r, r, 0, 0, 0]), None)])


def _tee(b, d, tw, tf, r=0.0) -> list[Region]:
    """The flange on top (toward +second), the stem below."""
    w, t = b / 2, tw / 2
    corners = [(t, -d), (t, -tf), (w, -tf), (w, 0), (-w, 0), (-w, -tf), (-t, -tf), (-t, -d)]
    return _centred([(_rounded(corners, [0, r, 0, 0, 0, 0, r, 0]), None)])


def _angle_loop(b, d, t, r=0.0, at=0.0) -> np.ndarray:
    """An angle's heel at (at, 0): a leg of b along +first, one of d along +second."""
    corners = [(at, 0), (at + b, 0), (at + b, t), (at + t, t), (at + t, d), (at, d)]
    return _rounded(corners, [0, 0, 0, r, 0, 0])


def _inches(text: str) -> float:
    """'1-3/8' -> 1.375, '3/4' -> 0.75, '4' -> 4."""
    whole, _, frac = text.partition("-") if "/" in text and "-" in text else ("", "", text)
    value = float(whole) if whole else 0.0
    if "/" in frac:
        num, den = frac.split("/")
        return value + float(num) / float(den)
    return value + float(frac)


def double_angle_gap(designation: str) -> float:
    """The gap between a double angle's backs (m), from its AISC designation:
    2L4X3X5/8X3/8LLBB is 3/8 in apart; with no fourth size they are back to back."""
    name = designation.upper().removeprefix("2L")
    for tail in ("LLBB", "SLBB"):
        name = name.removesuffix(tail)
    parts = name.split("X")
    try:
        return _inches(parts[3]) * 0.0254 if len(parts) > 3 else 0.0
    except ValueError:
        return 0.0


def library_outline(sec) -> list[Region]:
    """A table section's outline (`puzzle.sections.Section`)."""
    s, shape = sec.sizes, sec.shape
    r = s.get("r") or 0.0
    if shape == "I":
        return _i(s["bf"], s["d"], s["tw"], s["tf"], r)
    if shape == "C":
        return _channel(s["bf"], s["d"], s["tw"], s["tf"], r)
    if shape == "T":
        return _tee(s["bf"], s["d"], s["tw"], s["tf"], r)
    if shape in ("L", "2L"):
        t = s["t"]
        if shape == "L":  # the tables' x leg along the first axis: AISC's d, EN's bf
            if sec.standard == "aisc":
                first, second = s["d"], s.get("b") or s["d"]
            else:
                first, second = s.get("bf") or s["d"], s["d"]
            return _centred([(_angle_loop(first, second, t, r), None)])
        b, d = s.get("b") or s.get("bf") or s["d"], s["d"]
        gap = double_angle_gap(sec.designation)
        right = _angle_loop(b, d, t, r, gap / 2)
        left = right[::-1] * np.array([-1.0, 1.0])  # mirrored, still counter-clockwise
        return _centred([(right, None), (left, None)])
    if shape == "RHS":  # AISC's corners: 2t outside, t inside
        t = s.get("tdes") or s["t"]
        h, b = s.get("Ht") or s["d"], s.get("B") or s["bf"]
        return [(_rect(b, h, 2 * t), _rect(b - 2 * t, h - 2 * t, t))]
    if shape == "CHS":
        od = s["OD"]
        t = s.get("tdes") or s.get("t") or (od - s["ID"]) / 2
        return [(_circle(od / 2), _circle(od / 2 - t))]
    raise ValueError(f"no outline for a {shape} section")


def outline(section) -> list[Region]:
    """A beam section's outline (`model.schema.BeamSection`), in metres."""
    if section.shape == "library":
        from puzzle import sections

        regions = library_outline(sections.get(section.standard, section.designation))
        if getattr(section, "mirror", False):  # a single angle's other leg on the other side
            flip = np.array([1.0, -1.0])
            regions = [(o[::-1] * flip, None if h is None else h[::-1] * flip) for o, h in regions]
        return regions
    si = {k: getattr(section, k).to_si() for k in ("width", "height", "diameter", "thickness")
          if getattr(section, k) is not None}  # fmt: skip
    if section.shape == "rectangle":
        return [(_rect(si["width"], si["height"]), None)]
    if section.shape == "box":
        t = si["thickness"]
        return [
            (_rect(si["width"], si["height"]), _rect(si["width"] - 2 * t, si["height"] - 2 * t))
        ]
    if section.shape == "circle":
        return [(_circle(si["diameter"] / 2), None)]
    if section.shape == "tube":
        r = si["diameter"] / 2
        return [(_circle(r), _circle(r - si["thickness"]))]
    raise ValueError(f"no outline for a {section.shape} section")


# --- members and the swept solid ----------------------------------------------------------


@dataclass
class Member:
    """One straight member of a line part with a section: its nodes in order along it,
    its line elements, and its section's axes."""

    selection: str
    shape: str
    regions: list[Region]
    node_ids: np.ndarray
    element_ids: np.ndarray
    first: np.ndarray  # unit, square to the member
    along: np.ndarray  # unit, from its first node to its last
    extra: dict = field(default_factory=dict)


def members(project, comp, mesh, root=None) -> tuple[list[Member], list[str]]:
    """(each member drawn with its section, why some aren't). A selection's section
    over the part's own wins on its members, as the more specific one."""
    from puzzle.beams import _along, first_axis

    sections = getattr(comp, "beam_sections", None) or []
    if not sections:
        return [], []
    if any(b.orient == "axes" for b in sections):
        from puzzle.axes import resolved_project

        # Drawing must not fail on a system it can't place: the member is left a line.
        with contextlib.suppress(Exception):
            project = resolved_project(project, root)
    found: dict[tuple[int, int], Member] = {}
    problems = []
    for b in sorted(sections, key=lambda b: "/" in b.selection):
        group = mesh.groups.get(b.selection)
        if group is None:
            continue
        try:
            regions = outline(b)
        except (KeyError, ValueError) as exc:
            problems.append(f"{b.selection}: {exc}")
            continue
        for blk in group.blocks(mesh):
            if blk.dim != 1 or not len(blk.ids):
                continue
            ids = np.unique(blk.connectivity)  # as `with_orientations` hands them over
            xyz = mesh.node_coords(ids)
            a, z = mesh.node_coords(blk.connectivity[0, :2])
            along = (z - a) / (np.linalg.norm(z - a) or 1.0)
            try:
                first = first_axis(project, mesh, b, xyz, f"beam section on '{b.selection}'",
                                   along=_along(mesh, blk))  # fmt: skip
            except ValueError as exc:  # InputError: the solver would refuse it too
                problems.append(str(exc))
                continue
            order = np.argsort(xyz @ along, kind="stable")
            found[blk.entity] = Member(b.selection, b.shape if b.shape != "library" else
                                       b.designation or "", regions, ids[order], blk.ids,
                                       np.asarray(first, float), along)  # fmt: skip
    return list(found.values()), problems


def _cap(region: Region) -> np.ndarray:
    """Triangles (t, 3) over a region's loop indices: a hollow one's strip between its
    loops (the hole's indices follow the outer's), else ear clipping."""
    outer, hole = region
    k = len(outer)
    if hole is not None:
        i = np.arange(k)
        j = (i + 1) % k
        return np.concatenate([np.column_stack([i, j, k + j]), np.column_stack([i, k + j, k + i])])
    return _ears(outer)


def _ears(loop: np.ndarray) -> np.ndarray:
    """A simple counter-clockwise polygon's triangles (ear clipping; n is small)."""
    left = list(range(len(loop)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(left) > 3 and guard < 10 * len(loop):
        guard += 1
        n = len(left)
        for m in range(n):
            i, j, k = left[m - 1], left[m], left[(m + 1) % n]
            a, b, c = loop[i], loop[j], loop[k]
            if cross(a, b, c) <= 1e-30:
                continue  # a reflex (or flat) corner is no ear
            inside = False
            for q in left:
                if q in (i, j, k):
                    continue
                p = loop[q]
                if cross(a, b, p) > 0 and cross(b, c, p) > 0 and cross(c, a, p) > 0:
                    inside = True
                    break
            if not inside:
                tris.append((i, j, k))
                del left[m]
                break
        else:  # nothing clipped (degenerate): fan the rest
            break
    for m in range(1, len(left) - 1):
        tris.append((left[0], left[m], left[m + 1]))
    return np.asarray(tris, int)


def solid(chosen: list[Member], positions, values=None):
    """The members' outlines swept along them as one surface (pyvista PolyData).

    `positions(node_ids)` gives the nodes' places (the undeformed mesh, or a result's
    warped grid), `values(node_ids)` a value per node to colour by (point data
    "value"), or None."""
    import pyvista as pv

    points, faces, data = [], [], []
    base = 0
    for m in chosen:
        p = np.asarray(positions(m.node_ids), float)
        first = m.first
        second = np.cross(m.along, first)
        v = None if values is None else np.asarray(values(m.node_ids), float)
        n = len(p)
        for region in m.regions:
            loops = [region[0]] if region[1] is None else [region[0], region[1]]
            ring = np.concatenate(loops)  # (k, 2): outer, then the hole
            k = len(ring)
            xyz = p[:, None, :] + ring[None, :, 0, None] * first + ring[None, :, 1, None] * second
            points.append(xyz.reshape(-1, 3))
            if v is not None:
                data.append(np.repeat(v, k))
            # Sides: each loop's edges between consecutive nodes.
            start = 0
            for n_loop, loop in enumerate(loops):
                c = len(loop)
                i = start + np.arange(c)
                j = start + (np.arange(c) + 1) % c
                if n_loop:  # the hole's walls face into it: the other way round
                    i, j = j, i
                for s in range(n - 1):
                    lo, hi = base + s * k, base + (s + 1) * k
                    quads = np.column_stack([lo + i, lo + j, hi + j, hi + i])
                    faces.append(np.hstack([np.full((c, 1), 4), quads]).ravel())
                start += c
            tris = _cap(region)
            for s, flip in ((0, True), (n - 1, False)):
                t = base + s * k + (tris[:, ::-1] if flip else tris)
                faces.append(np.hstack([np.full((len(t), 1), 3), t]).ravel())
            base += n * k
    if not points:
        return None
    mesh = pv.PolyData(np.concatenate(points), np.concatenate(faces))
    if data:
        mesh.point_data["value"] = np.concatenate(data)
    return mesh
