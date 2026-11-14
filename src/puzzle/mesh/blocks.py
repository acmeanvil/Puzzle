"""Hexahedra in bodies of revolution: blocks cut at mesh time (ADR 0072 section 5).

Gmsh meshes a volume with structured hexahedra only when it is block-like (six faces of
four sides, `setTransfiniteAutomatic`). A shaft, a pin, a tube, a stepped bush is not,
so it used to fall back to tetrahedra. Here such a body is cut, in Gmsh's model only
(never the part's geometry), into blocks that each are, as Ansys's MultiZone does:

- the faces must be planes square to one axis and cylinders about it (coaxial);
  `revolution` reads them from Gmsh's own surfaces (a cylinder's parametrisation runs
  round it in u and along it in v) and says why not otherwise (cones, tori, a tilted
  plane); a body with cones, tori or spheres has its profile revolved instead
  (`meridian`, `revolve`, below), and a prism its cross-section swept (`extrusion`,
  `sweep`); any other body gets tetrahedra;
- a body solid on the axis gets a butterfly: a square core of `CORE` times its smallest
  radius, its corners on four planes through the axis, which carry on out to the rim,
  so every block is four-sided and no hexahedron touches the axis with a wedge; a tube
  needs only the four planes;
- a stepped body is cut too by a coaxial cylinder at each smaller radius and a plane
  at each step, so the segments' blocks meet face to face;
- one of the four planes lies on the cylinders' seam (OCC's u = 0), so no block keeps a
  seam edge (a five-sided face).

The core's corners meet the planes at 135°, past Gmsh's 2.35 rad default for a corner,
so the blocks are made transfinite with `CORNER`. The faces the cuts split are re-found
by their pieces (`generate._pieces`), as joined bodies' faces are.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import gmsh
import numpy as np

CORE = 0.45  # the square core's half width over the smallest radius
CORNER = 2.6  # rad: the core's 135° corners count as corners
TOL = 1e-7


@dataclass
class Revolution:
    """A body of revolution: its axis, the radii and the axial stations of its faces."""

    origin: np.ndarray
    axis: np.ndarray
    seam: np.ndarray  # a unit vector square to the axis, toward the cylinders' seam
    radii: list[float]
    stations: list[float]  # along the axis from `origin`
    solid: bool  # material on the axis (a shaft), not a bore (a tube)
    why: str = ""
    notes: list[str] = field(default_factory=list)


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _cylinder(tag: int):
    """(point on its axis, axis, radius, seam point) from its parametrisation."""
    (u0, v0), (u1, v1) = (np.asarray(b) for b in gmsh.model.getParametrizationBounds(2, tag))
    v = (v0 + v1) / 2
    pts = [np.asarray(gmsh.model.getValue(2, tag, [u, v])) for u in (u0, u0 + math.pi / 2,
                                                                      u0 + math.pi)]  # fmt: skip
    d = np.asarray(gmsh.model.getDerivative(2, tag, [u0, v]), float).reshape(2, 3)
    axis = _unit(d[1])
    centre = (pts[0] + pts[2]) / 2
    radius = float(np.linalg.norm(pts[0] - centre))
    return centre, axis, radius, pts[0]


def revolution(volume: int) -> Revolution:
    """The volume as a body of revolution, or one with `why` set saying why it isn't."""
    faces = [t for _d, t in gmsh.model.getBoundary([(3, volume)], oriented=False,
                                                   combined=False)]  # fmt: skip
    kinds = {t: gmsh.model.getType(2, t) for t in faces}
    cylinders = [t for t, k in kinds.items() if k == "Cylinder"]
    planes = [t for t, k in kinds.items() if k == "Plane"]
    other = sorted({k for k in kinds.values() if k not in ("Cylinder", "Plane")})
    empty = Revolution(np.zeros(3), np.zeros(3), np.zeros(3), [], [], False)
    if other:
        empty.why = f"its faces include {', '.join(k.lower() for k in other)}"
        return empty
    if not cylinders:
        empty.why = "it has no cylindrical face"
        return empty
    found = [_cylinder(t) for t in cylinders]
    centre, axis, _r, seam_point = found[0]
    lo, hi = gmsh.model.getBoundingBox(3, volume)[:3], gmsh.model.getBoundingBox(3, volume)[3:]
    size = float(np.linalg.norm(np.subtract(hi, lo)))
    radii = []
    for c, a, r, _s in found:
        if abs(abs(a @ axis) - 1) > 1e-6:
            empty.why = "its cylinders aren't parallel"
            return empty
        off = (c - centre) - ((c - centre) @ axis) * axis
        if np.linalg.norm(off) > 1e-6 * size:
            empty.why = "its cylinders aren't coaxial"
            return empty
        radii.append(r)
    stations = []
    for t in planes:
        (u0, v0), _ = (np.asarray(b) for b in gmsh.model.getParametrizationBounds(2, t))
        n = np.asarray(gmsh.model.getNormal(t, [u0, v0]), float)
        if abs(abs(n @ axis) - 1) > 1e-6:
            empty.why = "a flat face isn't square to the axis"
            return empty
        p = np.asarray(gmsh.model.getValue(2, t, [u0, v0]), float)
        stations.append(float((p - centre) @ axis))
    seam = _unit((seam_point - centre) - ((seam_point - centre) @ axis) * axis)
    mid = centre + axis * (min(stations, default=0) + max(stations, default=0)) / 2
    solid = bool(gmsh.model.isInside(3, volume, list(mid)))
    return Revolution(centre, axis, seam, sorted(set(round(r, 12) for r in radii)),
                      sorted(set(round(s, 12) for s in stations)), solid)  # fmt: skip


def _frame(rev: Revolution) -> tuple[np.ndarray, np.ndarray]:
    e1 = rev.seam
    return e1, np.cross(rev.axis, e1)


def _rectangle(occ, p0, p1, along, a, b) -> int:
    """A rectangle from p0 to p1 (both at the axial station a), swept along the axis to b."""
    corners = [p0 + along * a, p1 + along * a, p1 + along * b, p0 + along * b]
    pts = [occ.addPoint(*c) for c in corners]
    lines = [occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    return occ.addPlaneSurface([occ.addCurveLoop(lines)])


def cut(volume: int, rev: Revolution) -> list[int]:
    """Cut the volume into blocks (module docstring); its blocks' tags."""
    occ = gmsh.model.occ
    e1, e2 = _frame(rev)
    o, ax = rev.origin, rev.axis
    r_max = max(rev.radii)
    a = min(rev.stations) - 0.1 * (max(rev.stations) - min(rev.stations) + r_max)
    b = max(rev.stations) + 0.1 * (max(rev.stations) - min(rev.stations) + r_max)
    reach = 2.0 * r_max
    tools = []
    directions = [e1, e2, -e1, -e2]  # a plane on the seam first
    if rev.solid:
        c = CORE * min(rev.radii) * math.sqrt(2)  # the square core's corners, on the planes
        corners = [o + c * d for d in directions]
        for p0, p1 in zip(corners, corners[1:] + corners[:1], strict=True):
            tools.append(_rectangle(occ, p0, p1, ax, a, b))
        for p0, d in zip(corners, directions, strict=True):
            tools.append(_rectangle(occ, p0, o + reach * d, ax, a, b))
    else:
        for d in directions:
            tools.append(_rectangle(occ, o, o + reach * d, ax, a, b))
    for r in rev.radii[:-1] if rev.solid else rev.radii[1:-1]:  # coaxial cuts at the steps
        circle = occ.addCircle(*(o + ax * a), r, zAxis=list(ax), xAxis=list(e1))
        swept = occ.extrude([(1, circle)], *(ax * (b - a)))
        tools += [t for d, t in swept if d == 2]
    for s in rev.stations[1:-1]:  # planes at the steps
        disk = occ.addDisk(*(o + ax * s), reach, reach, zAxis=list(ax), xAxis=list(e1))
        tools.append(disk)
    occ.synchronize()
    return _split(volume, tools)


def _split(volume: int, tools: list[int]) -> list[int]:
    """Fragment the volume by the tool faces, dropping their pieces outside it."""
    occ = gmsh.model.occ
    out, _ = occ.fragment([(3, volume)], [(2, t) for t in tools])
    occ.synchronize()
    pieces = [t for d, t in out if d == 3]
    inside = set()
    for v in pieces:
        inside |= {t for _d, t in gmsh.model.getBoundary([(3, v)], oriented=False,
                                                         combined=False)}  # fmt: skip
    loose = [(2, t) for d, t in out if d == 2 and t not in inside]
    if loose:
        occ.remove(loose, recursive=True)
        occ.synchronize()
    return pieces


def odd_blocks(pieces: list[int]) -> list[int]:
    """Blocks that aren't six four-sided faces (the cut didn't make them block-like)."""
    out = []
    for v in pieces:
        faces = gmsh.model.getBoundary([(3, v)], oriented=False, combined=False)
        sides = [len(gmsh.model.getBoundary([f], combined=False, oriented=False)) for f in faces]
        if len(faces) != 6 or any(s != 4 for s in sides):
            out.append(v)
    return out


# Bodies of revolution with cones, tori or spheres (chamfers, fillets, domed ends): the
# cuts above leave blocks of triangular section there, so the body's meridian profile
# is cut out instead, meshed in quadrilaterals and revolved in four quarter turns, as
# modelled bolts are (`mesh/revolved.py`): hexahedra, and prisms along the axis.
ROUND = {"Cylinder", "Cone", "Torus", "Sphere"}  # Gmsh's surfaces whose u runs round
STRIP = 0.25  # the core strip next to the axis, a share of the profile's least radius


def _round_axis(tag: int):
    """(centre, axis, a point at u0) of a surface whose u parameter runs round its
    axis: the circle through three points at one v."""
    (u0, v0), (_u1, v1) = (np.asarray(b) for b in gmsh.model.getParametrizationBounds(2, tag))
    v = (v0 + v1) / 2
    p0, p1, p2 = (np.asarray(gmsh.model.getValue(2, tag, [u, v]), float)
                  for u in (u0, u0 + math.pi / 2, u0 + math.pi))  # fmt: skip
    centre = (p0 + p2) / 2
    return centre, _unit(np.cross(p0 - centre, p1 - centre)), p0


def meridian(volume: int) -> Revolution:
    """The volume as a body of revolution whose faces may be cones, tori and spheres
    too (`revolve`), or one with `why` set. `radii` holds the largest radius, `stations`
    the body's span along the axis."""
    faces = [t for _d, t in gmsh.model.getBoundary([(3, volume)], oriented=False,
                                                   combined=False)]  # fmt: skip
    kinds = {t: gmsh.model.getType(2, t) for t in faces}
    empty = Revolution(np.zeros(3), np.zeros(3), np.zeros(3), [], [], False)
    other = sorted({k for k in kinds.values() if k not in ROUND | {"Plane"}})
    if other:
        empty.why = f"its faces include {', '.join(k.lower() for k in other)}"
        return empty
    round_ = [t for t, k in kinds.items() if k in ROUND]
    if not round_:
        empty.why = "it has no face of revolution"
        return empty
    box = np.asarray(gmsh.model.getBoundingBox(3, volume))
    size = float(np.linalg.norm(box[3:] - box[:3]))
    centre, axis, seam_point = _round_axis(round_[0])
    for t in round_[1:]:
        c, a, _p = _round_axis(t)
        off = (c - centre) - ((c - centre) @ axis) * axis
        if abs(abs(a @ axis) - 1) > 1e-6 or np.linalg.norm(off) > 1e-6 * size:
            empty.why = "its faces of revolution don't share one axis"
            return empty
    for t in [t for t, k in kinds.items() if k == "Plane"]:
        (u0, v0), _ = (np.asarray(b) for b in gmsh.model.getParametrizationBounds(2, t))
        n = np.asarray(gmsh.model.getNormal(t, [u0, v0]), float)
        if abs(abs(n @ axis) - 1) > 1e-6:
            empty.why = "a flat face isn't square to the axis"
            return empty
    corners = np.array([[box[i], box[j], box[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5)])
    along = (corners - centre) @ axis
    radial = corners - centre - np.outer(along, axis)
    reach = float(np.max(np.linalg.norm(radial, axis=1)))
    seam = (seam_point - centre) - ((seam_point - centre) @ axis) * axis
    if np.linalg.norm(seam) < 1e-9 * size:  # a point on the axis (a cone's apex)
        seam = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    a, b = float(along.min()), float(along.max())
    solid = any(gmsh.model.isInside(3, volume, list(centre + axis * (a + f * (b - a))))
                for f in (0.1, 0.3, 0.5, 0.7, 0.9))  # fmt: skip
    return Revolution(centre, axis, _unit(seam), [reach], [a, b], solid)


def _tol(rev: Revolution) -> float:
    return 1e-6 * (rev.stations[1] - rev.stations[0] + rev.radii[0])


def _radius(rev: Revolution, x) -> float:
    d = np.asarray(x, float) - rev.origin
    return float(np.linalg.norm(d - (d @ rev.axis) * rev.axis))


def revolve(volume: int, rev: Revolution, sectors: int) -> tuple[list[int], list[int]]:
    """Replace the volume by its meridian profile (the half-plane through the axis
    toward `rev.seam`, cut along r = STRIP x its least radius when solid on the axis)
    revolved in four quarter turns of `sectors / 4` layers each, recombined, the
    quarters fragmented together (`revolved.build`). -> (its volumes, the profile's
    faces, for `prepare`)."""
    from puzzle.mesh.revolved import QUARTERS, layers

    occ = gmsh.model.occ
    o, ax, e1 = rev.origin, rev.axis, rev.seam
    a, b = rev.stations
    span = b - a
    reach = 1.5 * rev.radii[0] + 1e-3 * span
    lo, hi = a - 0.05 * span, b + 0.05 * span
    rect = _rectangle(occ, o, o + reach * e1, ax, lo, hi)
    out, _ = occ.intersect([(3, volume)], [(2, rect)], removeObject=True, removeTool=True)
    occ.synchronize()
    faces = [t for d, t in out if d == 2]
    if not faces:
        raise ValueError("the body's profile could not be cut out")
    if rev.solid:  # a strip along the axis, one element across (`prepare`)
        least = _least_radius(faces, rev)
        p, q = o + STRIP * least * e1 + ax * lo, o + STRIP * least * e1 + ax * hi
        line = occ.addLine(occ.addPoint(*p), occ.addPoint(*q))
        out, _ = occ.fragment([(2, f) for f in faces], [(1, line)])
        occ.synchronize()
        faces = [t for d, t in out if d == 2]
        edges = {
            abs(t)
            for _d, t in gmsh.model.getBoundary(
                [(2, f) for f in faces], combined=False, oriented=False
            )
        }
        loose = [(1, t) for d, t in out if d == 1 and t not in edges]
        if loose:
            occ.remove(loose, recursive=True)
            occ.synchronize()
    current = [(2, f) for f in faces]
    volumes: list[tuple[int, int]] = []
    n = layers(sectors)
    for _ in range(QUARTERS):
        made = occ.revolve(current, *o, *ax, math.pi / 2, numElements=[n], recombine=True)
        current = [made[j - 1] for j, e in enumerate(made) if e[0] == 3]
        volumes += [e for e in made if e[0] == 3]
    out, _ = occ.fragment(volumes, [])
    occ.synchronize()
    new = [t for d, t in out if d == 3]
    return new, _profile_faces(new, rev)


def _least_radius(faces, rev: Revolution) -> float:
    """The profile's least radius off the axis among its boundary's points."""
    radii = [r for f in faces
             for _d, p in gmsh.model.getBoundary([(2, f)], combined=False, oriented=False,
                                                 recursive=True)
             if (r := _radius(rev, gmsh.model.getValue(0, p, []))) > _tol(rev)]  # fmt: skip
    return min(radii) if radii else rev.radii[0]


def _profile_faces(volumes: list[int], rev: Revolution) -> list[int]:
    """The revolved volumes' faces in the profile's half-plane (the first quarter's
    start, which the last quarter's end joined)."""
    from puzzle.mesh.generate import _closure

    normal = np.cross(rev.axis, rev.seam)
    out = []
    for d, t in _closure([(3, v) for v in volumes]):
        if d != 2:
            continue
        c = np.asarray(gmsh.model.occ.getCenterOfMass(2, t)) - rev.origin
        if abs(c @ normal) < _tol(rev) and c @ rev.seam > _tol(rev):
            out.append(t)
    return out


def prepare(faces: list[int], rev: Revolution) -> None:
    """The profile's mesh settings: every face recombined, and the strip along the axis
    (when solid) one structured element across, its axis edge a copy of its other side,
    so the elements on the axis sweep into prisms, not hexahedra with a collapsed edge
    (as `revolved.prepare` does for bolts)."""
    tol = _tol(rev)
    for f in faces:
        gmsh.model.mesh.setRecombine(2, f)
    if not rev.solid:
        return
    for f in faces:
        sides = [abs(t) for _, t in gmsh.model.getBoundary([(2, f)], oriented=False)]
        ends = {t: [_radius(rev, gmsh.model.getValue(0, abs(v), []))
                    for _, v in gmsh.model.getBoundary([(1, t)], oriented=False)]
                for t in sides}  # fmt: skip
        on_axis = [t for t, r in ends.items() if max(r) < tol]
        if len(on_axis) != 1:
            continue
        width = max(max(r) for r in ends.values())
        other = [t for t, r in ends.items()
                 if t not in on_axis and all(abs(x - width) < tol for x in r)]  # fmt: skip
        across = [t for t in sides if t not in on_axis + other]
        if len(other) != 1 or len(across) != 2:
            continue
        shift = -width * rev.seam
        affine = [1, 0, 0, shift[0], 0, 1, 0, shift[1], 0, 0, 1, shift[2], 0, 0, 0, 1]
        for t in across:
            gmsh.model.mesh.setTransfiniteCurve(t, 2)
        gmsh.model.mesh.setPeriodic(1, on_axis, other, affine)
        gmsh.model.mesh.setTransfiniteSurface(f)


# Prismatic bodies that aren't blocks (a plate with holes, an extruded L or channel):
# the cross-section meshed in quadrilaterals and extruded in layers along the length,
# as Ansys's sweep method does: hexahedra, prisms where a triangle was left.


@dataclass
class Extrusion:
    """A body swept along `direction` from its `source` face to its far one."""

    direction: np.ndarray = field(default_factory=lambda: np.zeros(3))
    length: float = 0.0
    source: int = 0
    why: str = ""


def _samples(tag: int) -> np.ndarray:
    """(n, 2) parameters inside a face's bounds."""
    lo, hi = gmsh.model.getParametrizationBounds(2, tag)
    us, vs = np.linspace(lo[0], hi[0], 5)[1:-1], np.linspace(lo[1], hi[1], 5)[1:-1]
    return np.array([(u, v) for u in us for v in vs])


def extrusion(volume: int) -> Extrusion:
    """The volume as a prism: two flat faces, translates of each other along their
    normal, and every other face parallel to that direction, spanning the length; or
    one with `why` set. Of several such pairs, the one with the largest faces."""
    faces = [t for _d, t in gmsh.model.getBoundary([(3, volume)], oriented=False,
                                                   combined=False)]  # fmt: skip
    planes = [t for t in faces if gmsh.model.getType(2, t) == "Plane"]
    box = np.asarray(gmsh.model.getBoundingBox(3, volume))
    size = float(np.linalg.norm(box[3:] - box[:3]))
    tol = 1e-6 * size + 2e-7
    normal = {t: _unit(gmsh.model.getNormal(t, list(_samples(t)[0]))) for t in planes}
    centre = {t: np.asarray(gmsh.model.occ.getCenterOfMass(2, t)) for t in planes}
    area = {t: gmsh.model.occ.getMass(2, t) for t in planes}
    best = Extrusion(why="no two flat faces are translates of each other")
    for i, a in enumerate(planes):
        for b in planes[i + 1 :]:
            d = normal[a]
            if abs(abs(d @ normal[b]) - 1) > 1e-9 or abs(area[a] - area[b]) > 1e-6 * area[a]:
                continue
            gap = centre[b] - centre[a]
            length = float(gap @ d)
            if abs(length) < tol or np.linalg.norm(gap - length * d) > tol:
                continue
            d = d if length > 0 else -d
            length = abs(length)
            sides = [t for t in faces if t not in (a, b)]
            ok = True
            for t in sides:
                n = np.asarray(gmsh.model.getNormal(t, list(_samples(t).ravel())), float)
                if np.max(np.abs(n.reshape(-1, 3) @ d)) > 1e-6:
                    ok = False
                    break
                lo, hi = (np.asarray(x) for x in (gmsh.model.getBoundingBox(2, t)[:3],
                                                  gmsh.model.getBoundingBox(2, t)[3:]))  # fmt: skip
                corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                    for z in (lo[2], hi[2])]) - centre[a]  # fmt: skip
                along = corners @ d
                if along.min() > 10 * tol or along.max() < length - 10 * tol:
                    ok = False  # a side that doesn't span the length (a step)
                    break
            if ok and (best.why or area[a] > gmsh.model.occ.getMass(2, best.source)):
                best = Extrusion(d, length, a)
    return best


def sweep(volume: int, ext: Extrusion, size: float | None) -> tuple[list[int], list[int]]:
    """Replace the volume by its source face extruded along its length, in layers about
    `size` long (else as many as the face's width over five), recombined. -> (its
    volume, the face meshed in 2D, for `prepare_sweep`)."""
    occ = gmsh.model.occ
    width = math.sqrt(occ.getMass(2, ext.source))
    step = size or width / 5
    layers = max(1, round(ext.length / step))
    face = occ.copy([(2, ext.source)])
    occ.remove([(3, volume)], recursive=True)
    out = occ.extrude(face, *(ext.direction * ext.length), numElements=[layers],
                      recombine=True)  # fmt: skip
    occ.synchronize()
    return [t for d, t in out if d == 3], [t for _d, t in face]


# Bodies swept round an arc (an elbow, a bent tube or bar of any section): two flat end
# faces, one the other turned about an axis that every other face is a surface of
# revolution about (cylinders, cones, tori about it, planes square to it). The end face
# is revolved through the angle in layers (`bend_sweep`), as `sweep` extrudes a prism's.
# A sweep along a free curve (a spline) has no such axis and gets tetrahedra.


@dataclass
class Bend:
    """A body swept from its `source` face by `angle` (rad) about `axis` at `origin`."""

    origin: np.ndarray = field(default_factory=lambda: np.zeros(3))
    axis: np.ndarray = field(default_factory=lambda: np.zeros(3))
    angle: float = 0.0
    radius: float = 0.0  # of the source face's centroid
    source: int = 0
    why: str = ""


def bend(volume: int) -> Bend:
    """The volume as a section swept round an arc (above), or one with `why` set."""
    faces = [t for _d, t in gmsh.model.getBoundary([(3, volume)], oriented=False,
                                                   combined=False)]  # fmt: skip
    kinds = {t: gmsh.model.getType(2, t) for t in faces}
    other = sorted({k for k in kinds.values() if k not in ROUND | {"Plane"}})
    if other:
        return Bend(why=f"its faces include {', '.join(k.lower() for k in other)}")
    round_ = [t for t, k in kinds.items() if k in ROUND]
    if not round_:
        return Bend(why="it has no face of revolution")
    box = np.asarray(gmsh.model.getBoundingBox(3, volume))
    size = float(np.linalg.norm(box[3:] - box[:3]))
    tol = 1e-6 * size + 2e-7
    centre, axis, _p = _round_axis(round_[0])
    for t in round_[1:]:
        c, a, _p = _round_axis(t)
        off = (c - centre) - ((c - centre) @ axis) * axis
        if abs(abs(a @ axis) - 1) > 1e-6 or np.linalg.norm(off) > tol:
            return Bend(why="its faces of revolution don't share one axis")
    ends = []
    for t in [t for t, k in kinds.items() if k == "Plane"]:
        n = _unit(gmsh.model.getNormal(t, list(_samples(t)[0])))
        c = np.asarray(gmsh.model.occ.getCenterOfMass(2, t))
        if abs(abs(n @ axis) - 1) < 1e-6:
            continue  # square to the axis: a side
        if abs(n @ axis) > 1e-6 or abs((c - centre) @ n) > tol:
            return Bend(why="a flat face neither holds the axis nor is square to it")
        ends.append((t, c))
    if len(ends) != 2:
        return Bend(why=f"{len(ends)} flat faces hold the axis; a bend has two ends")
    (a, ca), (b, cb) = ends
    area = gmsh.model.occ.getMass(2, a)
    if abs(area - gmsh.model.occ.getMass(2, b)) > 1e-6 * area:
        return Bend(why="its two ends differ")
    if abs((ca - cb) @ axis) > tol:
        return Bend(why="its ends are at different heights along the axis (a helix)")
    ra, rb = ((c - centre) - ((c - centre) @ axis) * axis for c in (ca, cb))
    if abs(np.linalg.norm(ra) - np.linalg.norm(rb)) > tol or np.linalg.norm(ra) < tol:
        return Bend(why="its two ends aren't one turned onto the other")
    angle = math.atan2(float(axis @ np.cross(ra, rb)), float(ra @ rb))
    # a point of the end face (its centroid may be in a bore), turned half way: in the
    # body unless the bend goes the long way round
    lo, hi = gmsh.model.getParametrizationBounds(2, a)
    grid = [(u, v) for u in np.linspace(lo[0], hi[0], 9)[1:-1]
            for v in np.linspace(lo[1], hi[1], 9)[1:-1]]  # fmt: skip
    xyz = np.asarray(gmsh.model.getValue(2, a, [x for uv in grid for x in uv])).reshape(-1, 3)
    on = next((x for x in xyz if gmsh.model.isInside(2, a, list(x))), ca)
    halfway = centre + _turn(on - centre, axis, angle / 2)
    if not gmsh.model.isInside(3, volume, list(halfway)):
        angle -= math.copysign(2 * math.pi, angle)  # the long way round
    return Bend(centre, axis, angle, float(np.linalg.norm(ra)), a)


def _turn(v, axis, angle):
    """v turned by `angle` about the unit `axis` (Rodrigues)."""
    return (v * math.cos(angle) + np.cross(axis, v) * math.sin(angle)
            + axis * (axis @ v) * (1 - math.cos(angle)))  # fmt: skip


def bend_sweep(volume: int, b: Bend, size: float | None) -> tuple[list[int], list[int]]:
    """Replace the volume by its source face revolved through the bend's angle, in
    layers about `size` long along the centroid's arc, recombined. -> (its volumes,
    the face meshed in 2D)."""
    occ = gmsh.model.occ
    arc = abs(b.angle) * b.radius
    step = size or math.sqrt(occ.getMass(2, b.source)) / 5
    layers = max(1, round(arc / step))
    face = occ.copy([(2, b.source)])
    occ.remove([(3, volume)], recursive=True)
    out = occ.revolve(face, *b.origin, *b.axis, b.angle, numElements=[layers],
                      recombine=True)  # fmt: skip
    occ.synchronize()
    return [t for d, t in out if d == 3], [t for _d, t in face]


# Bodies that are blocks only once cut (a bar stepped in two directions, a block with
# a pocket, a cross): cut on the planes of their re-entrant flat faces (a plane with
# corners of the body on both sides), as Ansys's decomposition for MultiZone does.
# When every piece is a block, they are meshed transfinite together (shared faces, so
# conformal); otherwise the body gets tetrahedra.


def _reentrant_planes(volume: int) -> list[tuple[np.ndarray, float]]:
    """(normal, offset) of the planes of re-entrant flat faces, each once."""
    faces = [t for _d, t in gmsh.model.getBoundary([(3, volume)], oriented=False,
                                                   combined=False)]  # fmt: skip
    corners = gmsh.model.getBoundary([(3, volume)], recursive=True)
    points = np.array([gmsh.model.getValue(0, t, []) for _d, t in corners])
    box = np.asarray(gmsh.model.getBoundingBox(3, volume))
    tol = 1e-6 * float(np.linalg.norm(box[3:] - box[:3])) + 2e-7
    planes: list[tuple[np.ndarray, float]] = []
    for t in faces:
        if gmsh.model.getType(2, t) != "Plane":
            continue
        n = _unit(gmsh.model.getNormal(t, list(_samples(t)[0])))
        k = int(np.argmax(np.abs(n)))
        n = n if n[k] > 0 else -n  # one sign per plane
        d = float(n @ np.asarray(gmsh.model.occ.getCenterOfMass(2, t)))
        side = points @ n - d
        if side.max() < tol or side.min() > -tol:
            continue  # the body on one side: convex there
        if not any(np.allclose(n, m, atol=1e-9) and abs(d - e) < tol for m, e in planes):
            planes.append((n, d))
    return planes


def decompose(volume: int) -> list[int]:
    """Cut the volume on its re-entrant planes (above); its pieces' tags."""
    occ = gmsh.model.occ
    box = np.asarray(gmsh.model.getBoundingBox(3, volume))
    middle, reach = (box[:3] + box[3:]) / 2, float(np.linalg.norm(box[3:] - box[:3]))
    tools = []
    for n, d in _reentrant_planes(volume):
        at = middle - (middle @ n - d) * n
        x = _unit(np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0]))
        tools.append(occ.addDisk(*at, reach, reach, zAxis=list(n), xAxis=list(x)))
    occ.synchronize()
    return _split(volume, tools)


def decomposes(volume: int) -> str:
    """Why cutting the volume on its re-entrant planes wouldn't leave only blocks, or
    "" when it would (tried on a copy, removed after)."""
    if not _reentrant_planes(volume):
        return "it has no re-entrant flat face to cut on"
    (copy,) = gmsh.model.occ.copy([(3, volume)])
    gmsh.model.occ.synchronize()
    pieces = [copy[1]]
    try:
        pieces = decompose(copy[1])
        odd = odd_blocks(pieces)
    finally:
        gmsh.model.occ.remove([(3, v) for v in pieces], recursive=True)
        gmsh.model.occ.synchronize()
    if odd:
        return f"cut on its re-entrant faces, {len(odd)} of {len(pieces)} pieces aren't blocks"
    return ""
