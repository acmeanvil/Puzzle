"""2D profiles for extrude and revolve: points, circles or a DXF drawing, on a plane.

A profile spec (lengths in metres, values already resolved from parameters):

    {"points": [[0, 0], [0.08, 0], [0.08, 0.04], [0, 0.04]]}    a closed polygon
    {"circle": [cx, cy, r]}
    {"dxf": "bracket.dxf", "layer": "PROFILE", "scale": 0.001}  loops from a drawing
    {"sketch": {...}}                                            a constrained sketch (G5)
    plus optional "holes": [{"points": ...} | {"circle": ...}, ...]

The plane is "xy" (default), "yz" or "xz", at `offset` along its normal; profile
coordinates (u, v) map to (x, y), (y, z) or (x, z). The normal is u x v, so an
extrusion of positive distance goes along +z, +x or -y.

Edges come back in a stable order (the outer loop from its first segment, then each
hole) with a name each: `side-1`, `side-2`, ..., or a sketch's own edge names
("bottom", "bore"), which an extrusion gives its sides.
"""

from __future__ import annotations

import math
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
from OCP.ShapeFix import ShapeFix_Face

PLANES = {
    # local (u, v, w) -> global (x, y, z); normal = u x v
    "xy": lambda u, v, w: (u, v, w),
    "yz": lambda u, v, w: (w, u, v),
    "xz": lambda u, v, w: (u, w, v),
}
NORMALS = {"xy": (0.0, 0.0, 1.0), "yz": (1.0, 0.0, 0.0), "xz": (0.0, -1.0, 0.0)}


class ProfileError(ValueError):
    pass


class _Unchained(ProfileError):
    """A drawing whose lines don't join into separate loops (a line shared by two
    regions, or running past a corner): split into cells instead."""

    def __init__(self, pieces):
        super().__init__("the DXF outline is not closed (a gap between segments)")
        self.pieces = pieces


def profile_face(
    spec: dict,
    plane: str = "xy",
    offset: float = 0.0,
    root: Path | None = None,
    params: dict[str, float] | None = None,
):
    """The planar face of a profile, its edges in order (outer loop, then holes), and
    each edge's name."""
    if plane not in PLANES:
        raise ProfileError(f"plane must be one of {sorted(PLANES)}, not '{plane}'")
    to3d = lambda u, v: gp_Pnt(*PLANES[plane](u, v, offset))  # noqa: E731
    if "sketch" in spec:
        loops, labels = _sketch_loops(spec["sketch"], to3d, plane, params or {})
    elif "dxf" in spec:
        try:
            loops = _dxf_loops(spec, to3d, plane, offset, root)
        except _Unchained as exc:  # lines shared or crossing (ADR 0048 round 3): cells
            return _cells(exc.pieces, plane)
        labels = None
    else:
        loops = [_loop(spec, to3d, plane, offset)]
        loops += [_loop(h, to3d, plane, offset) for h in spec.get("holes", [])]
        regions = [(0, list(range(1, len(loops))))]  # typed holes are holes
        labels = None
    if "sketch" in spec or "dxf" in spec:
        regions = _regions(loops)
    faces, order = [], []
    for outer, holes in regions:
        maker = BRepBuilderAPI_MakeFace(_wire(loops[outer]), True)
        for h in holes:
            maker.Add(_wire(loops[h]))
        if not maker.IsDone():
            raise ProfileError("the profile does not make a planar face (is it closed and flat?)")
        fix = ShapeFix_Face(maker.Face())
        fix.FixOrientation()  # holes wound either way
        fix.Perform()
        faces.append(fix.Face())
        order += [(len(faces) - 1, i) for i in (outer, *holes)]
    # The fix may copy edges: return the faces' own, in the profile's order.
    edges = [_same_edge(e, faces[f]) for f, i in order for e in loops[i]]
    names = [n for _, i in order for n in labels[i]] if labels else None
    return _one(faces), edges, names or [f"side-{i}" for i in range(1, len(edges) + 1)]


def _regions(loops: list[list]) -> list[tuple[int, list[int]]]:
    """Loops as regions: each outline with the holes directly inside it. A loop inside
    an odd number of others is a hole of the smallest one around it; loops side by
    side (two bolt holes sketched as circles) are regions of their own."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN

    alone = [BRepBuilderAPI_MakeFace(_wire(lp), True).Face() for lp in loops]
    inside: dict[int, list[int]] = {}
    for i, lp in enumerate(loops):
        p = _midpoint(lp[0])
        inside[i] = [
            j
            for j, face in enumerate(alone)
            if j != i and BRepClass_FaceClassifier(face, p, 1e-9).State() == TopAbs_IN
        ]
    regions = {i: [] for i in inside if len(inside[i]) % 2 == 0}
    for i, around in inside.items():
        if len(around) % 2 == 1:
            parent = min(
                (j for j in around if len(inside[j]) == len(around) - 1),
                key=lambda j: _extent(loops[j]),
            )
            regions[parent].append(i)
    return sorted(regions.items(), key=lambda r: -_extent(loops[r[0]]))


def _one(faces: list):
    """A face, or several side by side as one compound (extrusions take either)."""
    if len(faces) == 1:
        return faces[0]
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for f in faces:
        builder.Add(compound, f)
    return compound


def _sketch_loops(sketch: dict, to3d, plane: str, params: dict[str, float]):
    """A solved sketch's loops as edges, with their entity names. A sketch that does
    not solve is refused, naming the constraints at fault."""
    from puzzle.geometry.sketch import describe, loops, solve

    solved = solve(sketch, params)
    if solved.status != "ok":
        raise ProfileError(f"the sketch does not solve: {describe(solved, sketch)}")
    normal = gp_Dir(*NORMALS[plane])
    edges, names = [], []
    for loop in loops(sketch, solved):
        built, labels = [], []
        for kind, name, geo in loop:
            if kind == "line":
                (u0, v0), (u1, v1) = geo
                built.append(BRepBuilderAPI_MakeEdge(to3d(u0, v0), to3d(u1, v1)).Edge())
            elif kind == "arc":
                (cu, cv), (su, sv), (eu, ev) = geo
                r = math.hypot(su - cu, sv - cv)
                circle = gp_Circ(gp_Ax2(to3d(cu, cv), normal), r)
                arc = GC_MakeArcOfCircle(circle, to3d(su, sv), to3d(eu, ev), True).Value()
                built.append(BRepBuilderAPI_MakeEdge(arc).Edge())
            elif kind == "spline":  # ADR 0065 phase 3: smooth through its points
                from OCP.TopAbs import TopAbs_EDGE
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS

                from puzzle.geometry.sweeps import spline_wire

                wire = spline_wire([to3d(u, v) for u, v in geo])
                built.append(TopoDS.Edge(TopExp_Explorer(wire, TopAbs_EDGE).Current()))
            else:
                (cu, cv), r = geo
                built.append(
                    BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(to3d(cu, cv), normal), r)).Edge()
                )
            labels.append(name)
        edges.append(built)
        names.append(labels)
    return edges, names


def _same_edge(edge, face):
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    target = _midpoint(edge)
    ex, best, best_d = TopExp_Explorer(face, TopAbs_EDGE), edge, float("inf")
    while ex.More():
        cast = getattr(TopoDS, "Edge_s", None) or TopoDS.Edge
        candidate = cast(ex.Current())
        d = _midpoint(candidate).Distance(target)
        if d < best_d:
            best, best_d = candidate, d
        ex.Next()
    return best


def _midpoint(edge):
    c = BRepAdaptor_Curve(edge)
    return c.Value(0.5 * (c.FirstParameter() + c.LastParameter()))


def _loop(spec: dict, to3d, plane: str, offset: float) -> list:
    if "circle" in spec:
        cx, cy, r = spec["circle"]
        centre = to3d(cx, cy)
        normal = gp_Dir(*NORMALS[plane])
        return [BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(centre, normal), r)).Edge()]
    points = [tuple(p) for p in spec["points"]]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise ProfileError("a polygon profile needs at least three points")
    edges = []
    for (u0, v0), (u1, v1) in zip(points, points[1:] + points[:1], strict=True):
        if math.isclose(u0, u1, abs_tol=1e-15) and math.isclose(v0, v1, abs_tol=1e-15):
            continue
        edges.append(BRepBuilderAPI_MakeEdge(to3d(u0, v0), to3d(u1, v1)).Edge())
    return edges


def _wire(edges: list):
    mk = BRepBuilderAPI_MakeWire()
    for e in edges:
        mk.Add(e)
    if not mk.IsDone():
        raise ProfileError("the profile's edges do not join into a closed loop")
    return mk.Wire()


# --- DXF ------------------------------------------------------------------------------

DXF_UNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1}  # $INSUNITS -> m


def _dxf_loops(spec: dict, to3d, plane: str, offset: float, root: Path | None) -> list[list]:
    """Closed loops of a DXF drawing's lines, arcs, circles and polylines, largest first.

    Units: `scale` if given, else the drawing's $INSUNITS (mm assumed when unset).
    """
    import ezdxf
    from ezdxf import path as dxfpath

    file = Path(spec["dxf"])
    if not file.is_absolute() and root is not None:
        file = root / file
    if not file.exists():
        raise ProfileError(f"no DXF file {file}")
    doc = ezdxf.readfile(file)
    scale = spec.get("scale") or DXF_UNITS.get(doc.header.get("$INSUNITS", 4), 0.001)
    layer = spec.get("layer")
    pieces = []  # (start, end, edge)
    for entity in doc.modelspace():
        if layer and entity.dxf.layer != layer:
            continue
        kind = entity.dxftype()
        if kind == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            pieces.append(_segment(a, b, scale, to3d))
        elif kind == "ARC":
            c, r = entity.dxf.center, entity.dxf.radius
            a0, a1 = math.radians(entity.dxf.start_angle), math.radians(entity.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            pts = [(c.x + r * math.cos(t), c.y + r * math.sin(t)) for t in (a0, (a0 + a1) / 2, a1)]
            pieces.append(_arc(pts, scale, to3d))
        elif kind == "CIRCLE":
            c, r = entity.dxf.center, entity.dxf.radius
            edge = _loop({"circle": [c.x * scale, c.y * scale, r * scale]}, to3d, plane, offset)
            pieces.append((None, None, edge[0]))
        elif kind == "SPLINE":  # ADR 0065 phase 3: kept smooth, one edge
            pieces.append(_spline(entity, scale, to3d, dxfpath))
        elif kind in ("LWPOLYLINE", "POLYLINE", "ELLIPSE"):
            # Flattened to segments: bulges, splines and ellipses become short lines.
            p = dxfpath.make_path(entity)
            pts = [(v.x, v.y) for v in p.flattening(distance=1e-3 / scale)]
            closed = bool(getattr(entity, "closed", False)) or getattr(entity, "is_closed", False)
            if closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            for a, b in zip(pts, pts[1:], strict=False):
                pieces.append(_segment(a, b, scale, to3d))
    if not pieces:
        raise ProfileError(
            f"no lines, arcs or circles in {file.name}" + (f" on layer {layer}" if layer else "")
        )
    try:
        loops = _chain(pieces, tol=1e-6)
    except ProfileError:
        raise _Unchained(pieces) from None
    loops.sort(key=_extent, reverse=True)  # the outer loop encloses the others
    return loops


def _spline(entity, scale, to3d, dxfpath):
    """A DXF spline as one smooth edge through its fit points (or, without them, through
    points along it), so a profile keeps its curve instead of short lines."""
    from puzzle.geometry.sweeps import spline_wire

    fit = [(v[0], v[1]) for v in getattr(entity, "fit_points", [])]
    if len(fit) < 2:
        fit = [(v.x, v.y) for v in dxfpath.make_path(entity).flattening(distance=1e-4 / scale)]
        step = max(1, len(fit) // 200)  # a smooth curve needs few points
        fit = fit[::step] + ([fit[-1]] if (len(fit) - 1) % step else [])
    closed = bool(getattr(entity, "closed", False))
    if closed and fit[0] == fit[-1]:
        fit = fit[:-1]
    pts = [to3d(u * scale, v * scale) for u, v in fit]
    wire = spline_wire(pts, closed=closed)
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    edge = TopoDS.Edge(TopExp_Explorer(wire, TopAbs_EDGE).Current())
    if closed:
        return None, None, edge
    return pts[0], pts[-1], edge


def _segment(a, b, scale, to3d):
    pa, pb = to3d(a[0] * scale, a[1] * scale), to3d(b[0] * scale, b[1] * scale)
    return pa, pb, BRepBuilderAPI_MakeEdge(pa, pb).Edge()


def _arc(pts, scale, to3d):
    p = [to3d(u * scale, v * scale) for u, v in pts]
    edge = BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[0], p[1], p[2]).Value()).Edge()
    return p[0], p[2], edge


def _chain(pieces, tol: float) -> list[list]:
    """Join edges end to end into closed loops (a circle is a loop of its own)."""
    loops = [[e] for s, _, e in pieces if s is None]
    open_ = [(s, t, e) for s, t, e in pieces if s is not None]
    close = lambda p, q: p.Distance(q) <= tol  # noqa: E731
    while open_:
        start, end, edge = open_.pop(0)
        loop = [edge]
        while not close(end, start):
            hit = next(
                (i for i, (s, t, _) in enumerate(open_) if close(s, end) or close(t, end)), None
            )
            if hit is None:
                raise ProfileError("the DXF outline is not closed (a gap between segments)")
            s, t, e = open_.pop(hit)
            loop.append(e)
            end = t if close(s, end) else s
        loops.append(loop)
    return loops


def _extent(loop) -> float:
    pts = []
    for e in loop:
        c = BRepAdaptor_Curve(e)
        for t in (c.FirstParameter(), 0.5 * (c.FirstParameter() + c.LastParameter())):
            p = c.Value(t)
            pts.append((p.X(), p.Y(), p.Z()))
    return max(math.dist(p, q) for p in pts for q in pts) if len(pts) > 1 else 0.0


def _cells(pieces, plane: str):
    """Every region a drawing's lines enclose (ADR 0048 round 3), for drawings whose
    regions share a line drawn once, or whose lines run on past a corner: a plane
    larger than the drawing is split by every line, and the cells not touching its
    border are the regions. Each region's edges are named after the drawing's line they
    lie on (side-N, in the drawing's order); dangling lines are dropped. A loop inside
    another is a region too here (an insert), not a hole."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.gp import gp_Ax3, gp_Pln
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_INTERNAL

    from puzzle.geometry.history import TopTools_ListOfShape, _bounds, _faces, _list, _subshapes

    edges = [e for _s, _t, e in pieces]
    box = _bounds(_one_compound(edges))
    size = max(box[3] - box[0], box[4] - box[1], box[5] - box[2], 1e-9)
    centre = gp_Pnt((box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2)
    frame = gp_Ax3(centre, gp_Dir(*NORMALS[plane]), gp_Dir(*PLANES[plane](1.0, 0.0, 0.0)))
    base = BRepBuilderAPI_MakeFace(gp_Pln(frame), -size, size, -size, size).Face()
    border = _subshapes(base, TopAbs_EDGE)
    split = BRepAlgoAPI_Splitter()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(base)
    for e in edges:
        tools.Append(e)
    split.SetArguments(args)
    split.SetTools(tools)
    split.Build()
    if not split.IsDone():
        raise ProfileError("the drawing's lines could not be split into regions")
    outer = [x for b in border for x in (_list(split.Modified(b)) or [b])]
    cells = []
    for f in _faces(split.Shape()):
        if any(e.IsSame(o) for e in _subshapes(f, TopAbs_EDGE) for o in outer):
            continue
        cells.append(_without_internal(f))
    if not cells:
        raise ProfileError("the drawing's lines enclose no region")
    names, order = [], []
    for f in cells:
        for e in _subshapes(f, TopAbs_EDGE):
            if e.Orientation() == TopAbs_INTERNAL or any(e.IsSame(o) for o in order):
                continue
            for k, piece in enumerate(edges, start=1):
                if any(e.IsSame(m) for m in (_list(split.Modified(piece)) or [piece])):
                    order.append(e)
                    names.append(f"side-{k}")
                    break
    return _one(cells), order, names


def _without_internal(face):
    """A split cell without the dangling lines left inside it (a drawing's construction
    lines), rebuilt from its outline and holes."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_INTERNAL, TopAbs_WIRE
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.history import _static, _subshapes

    edges = _subshapes(face, TopAbs_EDGE)
    if not any(e.Orientation() == TopAbs_INTERNAL for e in edges):
        return face
    outer = _static(BRepTools, "OuterWire")(face)
    maker = None
    for w in [outer] + [x for x in _subshapes(face, TopAbs_WIRE) if not x.IsSame(outer)]:
        kept = [e for e in _subshapes(w, TopAbs_EDGE) if e.Orientation() != TopAbs_INTERNAL]
        if not kept:
            continue
        mk = BRepBuilderAPI_MakeWire()
        for e in kept:
            mk.Add(e)
        if not mk.IsDone() or not mk.Wire().Closed():
            continue
        wire = _static(TopoDS, "Wire")(mk.Wire())
        if maker is None:
            maker = BRepBuilderAPI_MakeFace(wire, True)
        else:
            maker.Add(wire)
    return maker.Face() if maker is not None and maker.IsDone() else face


def _one_compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for s in shapes:
        builder.Add(compound, s)
    return compound
