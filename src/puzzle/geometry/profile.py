"""2D profiles for extrude and revolve: points, circles or a DXF drawing, on a plane.

A profile spec (lengths in metres, values already resolved from parameters):

    {"points": [[0, 0], [0.08, 0], [0.08, 0.04], [0, 0.04]]}    a closed polygon
    {"circle": [cx, cy, r]}
    {"dxf": "bracket.dxf", "layer": "PROFILE", "scale": 0.001}  loops from a drawing
    plus optional "holes": [{"points": ...} | {"circle": ...}, ...]

The plane is "xy" (default), "yz" or "xz", at `offset` along its normal; profile
coordinates (u, v) map to (x, y), (y, z) or (x, z). The normal is u x v, so an
extrusion of positive distance goes along +z, +x or -y.

Edges come back in a stable order (the outer loop from its first segment, then each
hole), so an extrusion can name its sides `side-1`, `side-2`, ...
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


def profile_face(spec: dict, plane: str = "xy", offset: float = 0.0, root: Path | None = None):
    """The planar face of a profile, and its edges in order (outer loop, then holes)."""
    if plane not in PLANES:
        raise ProfileError(f"plane must be one of {sorted(PLANES)}, not '{plane}'")
    to3d = lambda u, v: gp_Pnt(*PLANES[plane](u, v, offset))  # noqa: E731
    if "dxf" in spec:
        loops = _dxf_loops(spec, to3d, plane, offset, root)
        outer, holes = loops[0], loops[1:]
    else:
        outer = _loop(spec, to3d, plane, offset)
        holes = [_loop(h, to3d, plane, offset) for h in spec.get("holes", [])]
    maker = BRepBuilderAPI_MakeFace(_wire(outer), True)
    for h in holes:
        maker.Add(_wire(h))
    if not maker.IsDone():
        raise ProfileError("the profile does not make a planar face (is it closed and flat?)")
    fix = ShapeFix_Face(maker.Face())
    fix.FixOrientation()  # holes wound either way
    fix.Perform()
    face = fix.Face()
    # The fix may copy edges: return the face's own, in the profile's order.
    return face, [_same_edge(e, face) for loop in [outer, *holes] for e in loop]


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
        elif kind in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
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
    loops = _chain(pieces, tol=1e-6)
    loops.sort(key=_extent, reverse=True)  # the outer loop encloses the others
    return loops


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
