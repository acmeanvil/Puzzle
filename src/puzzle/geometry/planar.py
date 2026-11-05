"""2D shapes and operations (ADR 0048 round 2): flat regions with every side named.

Each shape is a history tool, joined to the part by `mode` like any other (new, add,
cut, intersect, or `separate`: a region of its own sharing its edges). It lies on
`plane` (xy by default) at `offset`, its coordinates (u, v) the plane's own (x, y on
the XY plane, where 2D models live). The region is `<step>/face`.

| kind | args | edge names |
| --- | --- | --- |
| rectangle | dx, dy, x0, y0 (the corner at the lowest u, v) | -x, +x, -y, +y |
| circle | r, x, y | outer |
| annulus | r_inner, r_outer, x, y | inner, outer |
| polygon | sides, r (to the corners), x, y, angle (deg, of the first corner) | side-1 ... |
| slot | length (overall), width, x, y, angle (deg, of its length) | side-1, end-1, side-2, end-2 |
| offset | line (points u, v), t, side (left, right, both), closed | left, right, start, end |

`offset` is a wall of thickness `t` along a line, to its left, its right or half on
each side, with mitred corners: the quick way to draw a vessel's section. Its `left`
and `right` name every edge on that side; a closed line makes a ring (no ends).

Fillets and chamfers on a part made of faces only round or cut its **corners**
(`corners`): the rule `{"all": true}` (or the 3D default, the outer corners), `{"between":
["P/-x", "P/+y"]}` (the corner where two named edges meet), `{"near": [u, v]}` (the
corner nearest a point), or `{"outer": true}` (corners of outer boundaries, not of
holes). A corner shared by two regions is refused: rounding it would part them.

`lay_flat` lays an imported planar face on the XY plane at z = 0 (round 3).
"""

from __future__ import annotations

import math

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS

from puzzle.geometry.profile import NORMALS, PLANES, _same_edge, _wire


def _h():
    from puzzle.geometry import history

    return history


def _to3d(a):
    plane, offset = a.get("plane", "xy"), float(a.get("offset", 0.0))
    if plane not in PLANES:
        raise _h().GeometryError(f"plane must be one of {sorted(PLANES)}, not '{plane}'")
    return (lambda u, v: gp_Pnt(*PLANES[plane](u, v, offset))), gp_Dir(*NORMALS[plane])


def _line(p, q):
    return BRepBuilderAPI_MakeEdge(p, q).Edge()


def _arc(centre, r, a0, a1, to3d):
    """An arc about `centre` (u, v) from angle a0 to a1 (radians, counter-clockwise)."""
    from OCP.GC import GC_MakeArcOfCircle

    pts = [to3d(centre[0] + r * math.cos(t), centre[1] + r * math.sin(t))
           for t in (a0, 0.5 * (a0 + a1), a1)]  # fmt: skip
    return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(*pts).Value()).Edge()


def _region(loops: list[list[tuple[str, object]]], normal):
    """A face from named loops (the outline first, then holes), facing along the plane's
    `normal` whichever way the outline runs (a clockwise one would face away, and
    CalculiX refuses the elements meshed on it); the face and its names, each edge found
    again on the face (the orientation fix may copy them)."""
    from OCP.ShapeFix import ShapeFix_Face

    for flip in (False, True):
        wires = [_wire([e for _, e in loop]) for loop in loops]
        outline = _h()._static(TopoDS, "Wire")(wires[0].Reversed()) if flip else wires[0]
        maker = BRepBuilderAPI_MakeFace(outline, True)
        for w in wires[1:]:
            maker.Add(w)
        if not maker.IsDone():
            raise _h().GeometryError("the shape does not make a flat region")
        fix = ShapeFix_Face(maker.Face())
        fix.FixOrientation()
        fix.Perform()
        face = fix.Face()
        if facing(face).Dot(normal) > 0:
            break
    names: dict[str, list] = {"face": [face]}
    for loop in loops:
        for name, e in loop:
            names.setdefault(name, []).append(_same_edge(e, face))
    return face, names


def facing(face) -> gp_Dir:
    """The way a flat face faces (its plane's normal, turned over if it is reversed)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_REVERSED

    n = BRepAdaptor_Surface(face).Plane().Axis().Direction()
    return n.Reversed() if face.Orientation() == TopAbs_REVERSED else n


def _positive(a, *keys):
    for k in keys:
        if float(a.get(k, 0.0)) <= 0:
            raise _h().GeometryError(f"{k} must be positive")


def _rectangle(state, a):
    _positive(a, "dx", "dy")
    to3d, normal = _to3d(a)
    x0, y0 = a.get("x0", 0.0), a.get("y0", 0.0)
    x1, y1 = x0 + a["dx"], y0 + a["dy"]
    c = [to3d(x0, y0), to3d(x1, y0), to3d(x1, y1), to3d(x0, y1)]
    return _region([[("-y", _line(c[0], c[1])), ("+x", _line(c[1], c[2])),
                     ("+y", _line(c[2], c[3])), ("-x", _line(c[3], c[0]))]], normal)  # fmt: skip


def _circle_edge(a, r, to3d, normal):
    return BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(to3d(a.get("x", 0.0), a.get("y", 0.0)),
                                                  normal), r)).Edge()  # fmt: skip


def _circle(state, a):
    _positive(a, "r")
    to3d, normal = _to3d(a)
    return _region([[("outer", _circle_edge(a, a["r"], to3d, normal))]], normal)


def _annulus(state, a):
    _positive(a, "r_inner", "r_outer")
    if a["r_inner"] >= a["r_outer"]:
        raise _h().GeometryError("an annulus's inner radius must be below its outer one")
    to3d, normal = _to3d(a)
    return _region([[("outer", _circle_edge(a, a["r_outer"], to3d, normal))],
                    [("inner", _circle_edge(a, a["r_inner"], to3d, normal))]], normal)  # fmt: skip


def _polygon(state, a):
    _positive(a, "r")
    n = int(a.get("sides", 6))
    if n < 3:
        raise _h().GeometryError("a polygon needs three sides or more")
    to3d, normal = _to3d(a)
    x, y, start = a.get("x", 0.0), a.get("y", 0.0), math.radians(a.get("angle", 0.0))
    c = [to3d(x + a["r"] * math.cos(start + 2 * math.pi * k / n),
              y + a["r"] * math.sin(start + 2 * math.pi * k / n)) for k in range(n)]  # fmt: skip
    return _region([[(f"side-{k + 1}", _line(c[k], c[(k + 1) % n])) for k in range(n)]], normal)


def _slot(state, a):
    _positive(a, "length", "width")
    L, w = a["length"], a["width"]
    if w >= L:
        raise _h().GeometryError("a slot's length must exceed its width")
    to3d, normal = _to3d(a)
    t = math.radians(a.get("angle", 0.0))
    d, n = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
    x, y, h, r = a.get("x", 0.0), a.get("y", 0.0), (L - w) / 2, w / 2
    c1, c2 = (x - h * d[0], y - h * d[1]), (x + h * d[0], y + h * d[1])

    def at(c, s):  # a point beside centre c, s = -1 below the axis, +1 above
        return to3d(c[0] + s * r * n[0], c[1] + s * r * n[1])

    return _region([[
        ("side-1", _line(at(c1, -1), at(c2, -1))),
        ("end-1", _arc(c2, r, t - math.pi / 2, t + math.pi / 2, to3d)),
        ("side-2", _line(at(c2, 1), at(c1, 1))),
        ("end-2", _arc(c1, r, t + math.pi / 2, t + 3 * math.pi / 2, to3d)),
    ]], normal)  # fmt: skip


# --- a wall along a line ------------------------------------------------------------------


def _line_points(a) -> list[tuple[float, float]]:
    line = a.get("line") or a.get("points") or []
    if isinstance(line, dict):
        line = line.get("points", [])
    pts = [(float(p[0]), float(p[1])) for p in line]
    out = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-12]
    return out


def offset_line(pts, h: float, closed: bool) -> list[tuple[float, float]]:
    """The line moved `h` to its left (negative: right), corners mitred."""
    n = len(pts)
    segs = n if closed else n - 1
    normals = []
    for i in range(segs):
        (u0, v0), (u1, v1) = pts[i], pts[(i + 1) % n]
        length = math.hypot(u1 - u0, v1 - v0)
        normals.append((-(v1 - v0) / length, (u1 - u0) / length))
    out = []
    for j in range(n):
        if not closed and j in (0, n - 1):
            m = normals[0] if j == 0 else normals[-1]
            out.append((pts[j][0] + h * m[0], pts[j][1] + h * m[1]))
            continue
        a, b = normals[(j - 1) % segs], normals[j % segs]
        m = (a[0] + b[0], a[1] + b[1])
        dot = m[0] * b[0] + m[1] * b[1]  # = 1 + cos(turn)
        if dot < 1e-6:
            raise _h().GeometryError("the line turns back on itself: a wall cannot follow it")
        s = h / dot
        out.append((pts[j][0] + s * m[0], pts[j][1] + s * m[1]))
    return out


def _crosses(poly: list[tuple[float, float]]) -> bool:
    """Whether a closed polygon's sides cross (other than neighbours meeting)."""

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    n = len(poly)
    sides = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            (p1, p2), (q1, q2) = sides[i], sides[j]
            d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
            d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
            if d1 * d2 < 0 and d3 * d4 < 0:
                return True
    return False


def _offset(state, a):
    """A wall of thickness `t` along a line (see the module's doc)."""
    pts = _line_points(a)
    closed = bool(a.get("closed", False))
    if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-12:
        pts = pts[:-1]
    if len(pts) < (3 if closed else 2):
        raise _h().GeometryError("a wall needs a line of two points or more (three if closed)")
    _positive(a, "t")
    t, side = float(a["t"]), a.get("side", "left")
    shifts = {"left": (t, 0.0), "right": (0.0, -t), "both": (t / 2, -t / 2)}
    if side not in shifts:
        raise _h().GeometryError(f"side must be left, right or both, not '{side}'")
    left, right = (offset_line(pts, h, closed) if h else list(pts) for h in shifts[side])
    _same_way(pts, left, closed)
    _same_way(pts, right, closed)
    to3d, normal = _to3d(a)
    L, R = [to3d(*p) for p in left], [to3d(*p) for p in right]
    n = len(pts)
    if closed:
        if _crosses(left) or _crosses(right):
            raise _h().GeometryError("the wall crosses itself: make it thinner")
        ring = lambda P, name: [(name, _line(P[i], P[(i + 1) % n])) for i in range(n)]  # noqa: E731
        outer, inner = ring(L, "left"), ring(R, "right")
        if abs(_signed_area(left)) < abs(_signed_area(right)):
            outer, inner = inner, outer
        return _region([outer, inner], normal)
    poly = left + right[::-1]
    if _crosses(poly):
        raise _h().GeometryError("the wall crosses itself: make it thinner")
    loop = [("left", _line(L[i], L[i + 1])) for i in range(n - 1)]
    loop.append(("end", _line(L[-1], R[-1])))
    loop += [("right", _line(R[i + 1], R[i])) for i in reversed(range(n - 1))]
    loop.append(("start", _line(R[0], L[0])))
    return _region([loop], normal)


def _signed_area(poly) -> float:
    pairs = zip(poly, poly[1:] + poly[:1], strict=True)
    return 0.5 * sum(p[0] * q[1] - q[0] * p[1] for p, q in pairs)


def _same_way(pts, moved, closed) -> None:
    """Each moved side runs the way its side of the line does: one reversed means the
    wall is thicker than a short side or a tight corner allows."""
    n = len(pts)
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        d = (pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
        m = (moved[j][0] - moved[i][0], moved[j][1] - moved[i][1])
        if d[0] * m[0] + d[1] * m[1] <= 0:
            raise _h().GeometryError(
                "the wall is thicker than one of the line's sides or corners allows"
            )


# --- corners of flat parts ----------------------------------------------------------------


def is_flat(state) -> bool:
    """A part made of faces only (a 2D model's regions, or a sheet)."""
    h = _h()
    return (
        state.shape is not None
        and not h._subshapes(state.shape, h.TopAbs_SOLID)
        and bool(h._faces(state.shape))
    )


def _corner_point(v):
    from OCP.BRep import BRep_Tool

    return _h()._static(BRep_Tool, "Pnt")(v)


def _corners_by_rule(state, rule: dict) -> list:
    """The part's corners a rule picks (see the module's doc), in a stable order."""
    from OCP.TopAbs import TopAbs_VERTEX

    h = _h()
    faces = h._faces(state.shape)
    every = h._subshapes(state.shape, TopAbs_VERTEX)
    if "between" in rule:
        a, b = rule["between"]
        sets = []
        for name in (a, b):
            edges = [e for e in state.names.get(name, []) if e.ShapeType() == h.TopAbs_EDGE]
            if not edges:
                raise h.GeometryError(f"no edge '{name}' on the part at this step")
            sets.append([v for e in edges for v in h._subshapes(e, TopAbs_VERTEX)])
        picked = [v for v in sets[0] if any(v.IsSame(w) for w in sets[1])]
    elif "near" in rule:
        u, v = (list(rule["near"]) + [0.0])[:2]
        target = gp_Pnt(u, v, 0.0) if len(rule["near"]) < 3 else gp_Pnt(*rule["near"][:3])
        picked = [min(every, key=lambda x: _corner_point(x).Distance(target))] if every else []
    elif rule.get("outer"):
        from OCP.BRepTools import BRepTools

        outer = []
        for f in faces:
            outer += h._subshapes(h._static(BRepTools, "OuterWire")(f), TopAbs_VERTEX)
        picked = [v for v in every if any(v.IsSame(w) for w in outer)]
    elif rule.get("all"):
        picked = every
    else:
        raise h.GeometryError(
            'a flat part\'s corners are picked by {"all": true}, {"outer": true}, '
            '{"between": [edge, edge]} or {"near": [x, y]}'
        )
    unique = []
    for v in picked:
        if not any(v.IsSame(u) for u in unique):
            unique.append(v)
    return unique


def _sharp(v, face) -> bool:
    """Whether the edges meeting at a corner of `face` meet at an angle (not tangent)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_VERTEX

    h = _h()
    edges = [e for e in h._subshapes(face, h.TopAbs_EDGE)
             if any(v.IsSame(w) for w in h._subshapes(e, TopAbs_VERTEX))]  # fmt: skip
    if len(edges) != 2:
        return False
    dirs = []
    for e in edges:
        c = BRepAdaptor_Curve(e)
        t = h._static(BRep_Tool, "Parameter")(v, e)
        p, d = gp_Pnt(), gp_Vec()
        c.D1(t, p, d)
        if d.Magnitude() < 1e-15:
            return False
        dirs.append(d.Normalized())
    return abs(abs(dirs[0].Dot(dirs[1])) - 1.0) > 1e-6


def corners(state, op, a, kind: str) -> None:
    """Round (`fillet`, radius `r`) or cut (`chamfer`, distance `d`) a flat part's
    corners; each new edge is `<op>/edge-i`, the other names carried."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet2d
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.TopAbs import TopAbs_VERTEX

    h = _h()
    size = a["r"] if kind == "fillet" else a["d"]
    picked = _corners_by_rule(state, a.get("rule") or {"all": True})
    faces = h._faces(state.shape)
    holders = {}
    for i, v in enumerate(picked):
        on = [f for f in faces if any(v.IsSame(w) for w in h._subshapes(f, TopAbs_VERTEX))]
        if len(on) > 1:
            p = _corner_point(v)
            raise h.GeometryError(
                f"{kind} '{op.name}': the corner at ({p.X():.6g}, {p.Y():.6g}) is shared by "
                "two regions; rounding it would part them"
            )
        if on and _sharp(v, on[0]):
            holders.setdefault(faces.index(on[0]), []).append((i, v))
    if not holders:
        raise h.GeometryError(f"{kind} '{op.name}': its rule picks no sharp corners")
    reshape, names, made = BRepTools_ReShape(), dict(state.names), {}
    for fi, vs in holders.items():
        face = faces[fi]
        mk = BRepFilletAPI_MakeFillet2d(face)
        new_edges = []
        for i, v in vs:
            if kind == "fillet":
                new_edges.append((i, mk.AddFillet(v, size)))
            else:
                e1, e2 = [e for e in h._subshapes(face, h.TopAbs_EDGE)
                          if any(v.IsSame(w) for w in h._subshapes(e, TopAbs_VERTEX))]  # fmt: skip
                new_edges.append((i, mk.AddChamfer(e1, e2, size, size)))
        mk.Build()
        if not mk.IsDone():
            raise h.GeometryError(f"{kind} '{op.name}' failed (too large for the corner?)")
        new_face = h._static(TopoDS, "Face")(mk.Shape())
        old_edges = h._subshapes(face, h.TopAbs_EDGE)
        for n, shapes in names.items():
            kept = []
            for s in shapes:
                if s.IsSame(face):
                    kept.append(new_face)
                elif s.ShapeType() == h.TopAbs_EDGE and any(s.IsSame(e) for e in old_edges):
                    kept.append(_descendant(mk, s, new_face))
                else:
                    kept.append(s)
            names[n] = [k for k in kept if k is not None]
        for i, e in new_edges:
            made[i] = _same_edge(e, new_face)
        reshape.Replace(face, new_face)
    shape = reshape.Apply(state.shape)
    names = {n: [reshape.Value(s) for s in ss] for n, ss in names.items()}
    for k, i in enumerate(sorted(made), start=1):
        names[f"{op.name}/edge-{k}"] = [reshape.Value(made[i])]
    state.names = {n: [h._list([s])[0] for s in ss] for n, ss in names.items() if ss}
    state.shape = shape


def _descendant(mk, edge, face):
    """An edge after a 2D fillet: itself, its trimmed descendant, or found on the face."""
    try:
        if mk.IsModified(edge):
            return _same_edge(mk.DescendantEdge(edge), face)
    except Exception:  # noqa: BLE001 - OCCT raises for edges it never saw
        pass
    return _same_edge(edge, face)


def _corner_op(kind, original):
    def run(state, op, a):
        if is_flat(state):
            return corners(state, op, a, kind)
        return original(state, op, a)

    run.__doc__ = original.__doc__
    return run


# --- planar imports (round 3) -------------------------------------------------------------


def lay_flat(shape, names):
    """Imported flat faces moved onto the XY plane at z = 0: faces square to z keep x
    and y, square to x or y take the YZ or XZ plane's (u, v), others their own plane's
    axes. All the faces must lie in one plane."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.gp import gp_Ax3, gp_Trsf

    h = _h()
    faces = h._faces(shape)
    if not faces or h._subshapes(shape, h.TopAbs_SOLID):
        raise h.GeometryError("only flat faces can be laid on the XY plane (the file has solids)")
    planes = []
    for f in faces:
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Plane:
            raise h.GeometryError("a face in the file is not flat")
        planes.append(s.Plane())
    p0 = planes[0]
    n0 = p0.Axis().Direction()
    for p in planes[1:]:
        if not p.Axis().Direction().IsParallel(n0, 1e-6) or p0.Distance(p.Location()) > 1e-7:
            raise h.GeometryError("the file's faces do not lie in one plane")
    trsf = gp_Trsf()
    normal = [n0.X(), n0.Y(), n0.Z()]
    axis = max(range(3), key=lambda i: abs(normal[i]))
    loc = p0.Location()
    if abs(abs(normal[axis]) - 1.0) < 1e-9:
        plane = {2: "xy", 0: "yz", 1: "xz"}[axis]
        if plane == "xy":
            trsf.SetTranslation(gp_Vec(0.0, 0.0, -loc.Z()))
        else:
            w = [loc.X(), loc.Y(), loc.Z()][axis]
            frame = gp_Ax3(gp_Pnt(*PLANES[plane](0.0, 0.0, w)), gp_Dir(*NORMALS[plane]),
                           gp_Dir(*PLANES[plane](1.0, 0.0, 0.0)))  # fmt: skip
            trsf.SetTransformation(frame)
    else:
        trsf.SetTransformation(p0.Position())
    move = BRepBuilderAPI_Transform(shape, trsf, True)
    moved = {n: [h._list([move.ModifiedShape(s)])[0] for s in ss] for n, ss in names.items()}
    return move.Shape(), moved


TOOLS = {
    "rectangle": _rectangle,
    "circle": _circle,
    "annulus": _annulus,
    "polygon": _polygon,
    "slot": _slot,
    "offset": _offset,
}
