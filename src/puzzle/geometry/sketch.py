"""Constrained 2D sketches (G5, ADR 0014): SolveSpace's solver through py-slvs.

A sketch is a profile for extrude and revolve (`{"sketch": {...}}`), lengths in
metres, on the profile's plane (u, v):

    {"points":   {"A": [0, 0], "B": [0.06, 0], ...},          positions (start values)
     "lines":    {"bottom": ["A", "B"], ...},                  from point to point
     "arcs":     {"round": ["C", "S", "E"]},                   centre, start, end (ccw)
     "circles":  {"bore": ["O", 0.004]},                       centre, radius
     "construction": ["axis"],                                 drawn, constrained, not cut
     "constraints": [["horizontal", "bottom"], ["length", "bottom", "L"], ...]}

Constraints (a value may name a parameter; lengths in m, angles in degrees):

    fix P · coincident P Q · horizontal L · vertical L · length L d · distance P Q d
    hdistance P Q d · vdistance P Q d · parallel L M · perpendicular L M · equal L M
    (two lines: lengths; two arcs/circles: radii) · angle L M deg · radius C r ·
    diameter C d · on P L|C (on a line or circle) · midpoint P L · tangent A L
    (an arc and a line meeting at an end) · symmetric P Q L

Points are solved from their stored positions, so a parameter change moves the
sketch continuously (the same branch of the solution); `solve` returns the new
positions, which the editor stores back. Lines, arcs and circles that share points
form closed loops: the largest is the outline, the others holes. Each profile edge
keeps its entity's name, so an extrusion's sides are `<step>/bottom`, `<step>/bore`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

KINDS = {
    "fix": 1,
    "coincident": 2,
    "horizontal": 1,
    "vertical": 1,
    "length": 2,
    "distance": 3,
    "hdistance": 3,
    "vdistance": 3,
    "parallel": 2,
    "perpendicular": 2,
    "equal": 2,
    "angle": 3,
    "radius": 2,
    "diameter": 2,
    "on": 2,
    "midpoint": 2,
    "tangent": 2,
    "symmetric": 3,
}
DIMENSIONS = {"length", "distance", "hdistance", "vdistance", "angle", "radius", "diameter"}


class SketchError(ValueError):
    pass


@dataclass
class Solved:
    points: dict[str, tuple[float, float]]
    radii: dict[str, float]  # circles' radii, solved
    dof: int  # degrees of freedom left (0 = fully constrained)
    status: str = "ok"  # ok | conflict | no_convergence
    failed: list[int] = field(default_factory=list)  # indices of conflicting constraints

    def spec(self, sketch: dict) -> dict:
        """The sketch with its positions (and circle radii) replaced by the solved ones."""
        out = {k: v for k, v in sketch.items()}
        out["points"] = {n: [round(u, 12), round(v, 12)] for n, (u, v) in self.points.items()}
        if "circles" in sketch:
            out["circles"] = {
                n: [c, round(self.radii.get(n, r if isinstance(r, float) else 0.0), 12)]
                for n, (c, r) in sketch["circles"].items()
            }
        return out


def resolve(sketch: dict, params: dict[str, float]) -> dict:
    """The sketch with parameter names in dimension values replaced by numbers."""

    def number(v, where: str):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str) and v in params:
            return float(params[v])
        raise SketchError(f"{where}: '{v}' is neither a number nor a parameter")

    out = dict(sketch)
    out["points"] = {
        n: [number(p[0], f"point {n}"), number(p[1], f"point {n}")]
        for n, p in sketch.get("points", {}).items()
    }
    out["circles"] = {
        n: [c[0], number(c[1], f"circle {n}")] for n, c in sketch.get("circles", {}).items()
    }
    constraints = []
    for i, c in enumerate(sketch.get("constraints", [])):
        kind = c[0]
        if kind not in KINDS:
            raise SketchError(f"constraint {i + 1}: unknown kind '{kind}'")
        refs = list(c[1 : 1 + KINDS[kind]])
        if kind in DIMENSIONS:
            refs[-1] = number(refs[-1], f"constraint {i + 1} ({kind})")
        constraints.append([kind, *refs])
    out["constraints"] = constraints
    return out


def solve(sketch: dict, params: dict[str, float] | None = None) -> Solved:
    """Solve a sketch (parameter names resolved from `params`)."""
    from py_slvs import slvs

    s = resolve(sketch, params or {})
    sys = slvs.System()
    g1, g2 = 1, 2
    origin = sys.addPoint3dV(0, 0, 0, group=g1)
    normal = sys.addNormal3dV(1, 0, 0, 0, group=g1)
    wp = sys.addWorkplane(origin, normal, group=g1)
    pts = {n: sys.addPoint2dV(wp, u, v, group=g2) for n, (u, v) in s["points"].items()}

    def point(name, where):
        if name not in pts:
            raise SketchError(f"{where}: no point '{name}'")
        return pts[name]

    lines = {
        n: sys.addLineSegment(point(a, f"line {n}"), point(b, f"line {n}"), group=g2)
        for n, (a, b) in s.get("lines", {}).items()
    }
    plane_normal = sys.addNormal2d(wp, group=g2)
    arcs = {
        n: sys.addArcOfCircle(wp, *(point(p, f"arc {n}") for p in (c, a, b)), group=g2)
        for n, (c, a, b) in s.get("arcs", {}).items()
    }
    circles, radius_params = {}, {}
    for n, (c, r) in s.get("circles", {}).items():
        dist = sys.addDistanceV(r, group=g2)
        radius_params[n] = dist
        circles[n] = sys.addCircle(point(c, f"circle {n}"), plane_normal, dist, group=g2)
    curves = {**arcs, **circles}

    def line(name, where):
        if name not in lines:
            raise SketchError(f"{where}: no line '{name}'")
        return lines[name]

    def curve(name, where):
        if name not in curves:
            raise SketchError(f"{where}: no arc or circle '{name}'")
        return curves[name]

    handles = []
    for i, (kind, *r) in enumerate(s["constraints"]):
        where = f"constraint {i + 1} ({kind})"
        if kind == "fix":
            h = sys.addWhereDragged(point(r[0], where), wp, group=g2)
        elif kind == "coincident":
            h = sys.addPointsCoincident(point(r[0], where), point(r[1], where), wp, group=g2)
        elif kind == "horizontal":
            h = sys.addLineHorizontal(line(r[0], where), wp, group=g2)
        elif kind == "vertical":
            h = sys.addLineVertical(line(r[0], where), wp, group=g2)
        elif kind == "length":
            a, b = s["lines"][r[0]] if r[0] in lines else (None, None)
            if a is None:
                raise SketchError(f"{where}: no line '{r[0]}'")
            h = sys.addPointsDistance(_positive(r[1], where), pts[a], pts[b], wp, group=g2)
        elif kind == "distance":
            a, b = point(r[0], where), point(r[1], where)
            h = sys.addPointsDistance(_positive(r[2], where), a, b, wp, group=g2)
        elif kind in ("hdistance", "vdistance"):
            h = _axis_distance(sys, slvs, kind, point(r[0], where), point(r[1], where), r[2], wp)
        elif kind == "parallel":
            h = sys.addParallel(line(r[0], where), line(r[1], where), wp, group=g2)
        elif kind == "perpendicular":
            h = sys.addPerpendicular(line(r[0], where), line(r[1], where), wp, group=g2)
        elif kind == "equal":
            if r[0] in lines and r[1] in lines:
                h = sys.addEqualLength(lines[r[0]], lines[r[1]], wp, group=g2)
            else:
                h = sys.addEqualRadius(curve(r[0], where), curve(r[1], where), group=g2)
        elif kind == "angle":
            h = sys.addAngle(r[2], False, line(r[0], where), line(r[1], where), wp, group=g2)
        elif kind in ("radius", "diameter"):
            d = _positive(r[1], where) * (2 if kind == "radius" else 1)
            h = sys.addDiameter(d, curve(r[0], where), group=g2)
        elif kind == "on":
            p = point(r[0], where)
            if r[1] in lines:
                h = sys.addPointOnLine(p, lines[r[1]], wp, group=g2)
            else:
                h = sys.addPointOnCircle(p, curve(r[1], where), group=g2)
        elif kind == "midpoint":
            h = sys.addMidPoint(point(r[0], where), line(r[1], where), wp, group=g2)
        elif kind == "tangent":
            arc_name, line_name = r
            arc, ln = curve(arc_name, where), line(line_name, where)
            _, start, end = s["arcs"][arc_name]
            a, b = s["lines"][line_name]
            at_end = end in (a, b)
            if not at_end and start not in (a, b):
                raise SketchError(f"{where}: arc '{arc_name}' and line '{line_name}' do not meet")
            h = sys.addArcLineTangent(at_end, arc, ln, group=g2)
        elif kind == "symmetric":
            h = sys.addSymmetricLine(
                point(r[0], where), point(r[1], where), line(r[2], where), wp, group=g2
            )
        handles.append(h)

    result = sys.solve(g2, True, True)
    status = {0: "ok", 1: "conflict", 2: "no_convergence", 3: "too_many_unknowns"}.get(
        result, "conflict"
    )
    failed = sorted({handles.index(h) for h in sys.Failed if h in handles}) if result else []
    solved = {
        n: (sys.getParam(sys.getEntityParam(h, 0)).val, sys.getParam(sys.getEntityParam(h, 1)).val)
        for n, h in pts.items()
    }
    radii = {n: sys.getParam(sys.getEntityParam(d, 0)).val for n, d in radius_params.items()}
    return Solved(solved, radii, int(sys.Dof), status, failed)


def _positive(v: float, where: str) -> float:
    if v <= 0:
        raise SketchError(f"{where}: a length must be positive, not {v:g}")
    return v


def _axis_distance(sys, slvs, kind, p, q, d, wp):
    """Distance along u (hdistance) or v (vdistance) between p and q, through a helper
    point level with p and in line with q. SolveSpace distances are unsigned: which
    side q stays on follows the starting positions."""
    helper = sys.addPoint2dV(wp, 0.0, 0.0, group=2)
    if kind == "hdistance":
        sys.addPointsHorizontal(p, helper, wp, group=2)  # helper at p's height
        sys.addPointsVertical(helper, q, wp, group=2)  # ... under q
    else:
        sys.addPointsVertical(p, helper, wp, group=2)
        sys.addPointsHorizontal(helper, q, wp, group=2)
    return (
        sys.addPointsDistance(abs(d), p, helper, wp, group=2)
        if d
        else sys.addPointsCoincident(p, helper, wp, group=2)
    )


def describe(solved: Solved, sketch: dict) -> str:
    """The solve in words, for the editor's status line."""
    if solved.status != "ok":
        numbers = ", ".join(str(i + 1) for i in solved.failed) or "some"
        return f"Constraints {numbers} conflict or cannot all hold"
    if solved.dof == 0:
        return "Fully constrained"
    return f"{solved.dof} degree{'s' if solved.dof != 1 else ''} of freedom left"


# --- loops: the profile's outline and holes ----------------------------------------------


def loops(sketch: dict, solved: Solved) -> list[list[tuple[str, str, tuple]]]:
    """Closed loops of (kind, name, geometry), largest first. Geometry: a line's two
    points, an arc's centre, start and end, a circle's centre and radius (all in u, v)."""
    p = solved.points
    skip = set(sketch.get("construction", []))
    pieces = []
    for n, (a, b) in sketch.get("lines", {}).items():
        if n not in skip:
            pieces.append(("line", n, a, b, (p[a], p[b])))
    for n, (c, a, b) in sketch.get("arcs", {}).items():
        if n not in skip:
            pieces.append(("arc", n, a, b, (p[c], p[a], p[b])))
    out = [
        [("circle", n, (p[c], solved.radii.get(n, r)))]
        for n, (c, r) in sketch.get("circles", {}).items()
        if n not in skip
    ]
    ends: dict[str, list[int]] = {}
    for i, (_, _, a, b, _) in enumerate(pieces):
        ends.setdefault(a, []).append(i)
        ends.setdefault(b, []).append(i)
    loose = [n for n, at in ends.items() if len(at) != 2]
    if loose:
        raise SketchError(
            f"the sketch is not closed at point{'s' if len(loose) > 1 else ''} "
            + ", ".join(sorted(loose))
            + " (each outline point must join exactly two edges)"
        )
    used = set()
    for i in range(len(pieces)):
        if i in used:
            continue
        loop, at, here = [], i, pieces[i][2]
        while at not in used:
            used.add(at)
            kind, name, a, b, geo = pieces[at]
            loop.append((kind, name, geo))
            here = b if here == a else a
            at = next((j for j in ends[here] if j != at), at)
        out.append(loop)
    out.sort(key=lambda lp: _area(lp), reverse=True)
    return out


def _area(loop) -> float:
    """The area a loop encloses, from its points (arcs by their chords and ends)."""
    if loop[0][0] == "circle":
        return math.pi * loop[0][2][1] ** 2
    pts = []
    for kind, _, geo in loop:
        pts += [geo[0], geo[1]] if kind == "line" else [geo[1], geo[2]]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
