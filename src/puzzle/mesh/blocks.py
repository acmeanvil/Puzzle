"""Hexahedra in bodies of revolution: blocks cut at mesh time (ADR 0072 section 5).

Gmsh meshes a volume with structured hexahedra only when it is block-like (six faces of
four sides, `setTransfiniteAutomatic`). A shaft, a pin, a tube, a stepped bush is not,
so it used to fall back to tetrahedra. Here such a body is cut, in Gmsh's model only
(never the part's geometry), into blocks that each are, as Ansys's MultiZone does:

- the faces must be planes square to one axis and cylinders about it (coaxial);
  `revolution` reads them from Gmsh's own surfaces (a cylinder's parametrisation runs
  round it in u and along it in v) and says why not otherwise (cones, tori, a tilted
  plane: the mesher then meshes tetrahedra, as before);
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
