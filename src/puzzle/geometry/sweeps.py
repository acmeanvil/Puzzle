"""History operations from ADR 0065 phase 1: splines, sweeps, pipes, lofts, mirror and
scale. Each names its faces by ADR 0014's rules: a sweep's and a loft's `start`, `end`
and sides after the profile's edges (as an extrusion names them), a pipe's `wall`,
`bore`, `start` and `end`; a mirrored copy's faces `<name>~m`.

Paths (for sweeps and pipes) are a list of points (`path`; a spline through them when
`smooth`, else straight segments) or an earlier spline or polyline step (`along`).
"""

from __future__ import annotations

from OCP.BRepAdaptor import BRepAdaptor_CompCurve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_GTransform,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe, BRepOffsetAPI_ThruSections
from OCP.BRepTools import BRepTools
from OCP.collections import HArray1_gp_Pnt
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.GeomFill import GeomFill_IsCorrectedFrenet, GeomFill_IsFixed, GeomFill_IsFrenet
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_GTrsf, gp_Mat, gp_Pnt, gp_Trsf, gp_Vec, gp_XYZ

FRAMES = {"corrected": GeomFill_IsCorrectedFrenet, "frenet": GeomFill_IsFrenet,
          "fixed": GeomFill_IsFixed}  # fmt: skip


def _h():
    from puzzle.geometry import history

    return history


def _points(a, key: str = "points") -> list[gp_Pnt]:
    pts = a.get(key) or []
    return [gp_Pnt(*[float(c) for c in (list(p) + [0.0, 0.0, 0.0])[:3]]) for p in pts]


def spline_wire(points: list[gp_Pnt], closed: bool = False, start=None, end=None):
    """A smooth curve through the points (GeomAPI_Interpolate), optionally with end
    tangents, as a wire."""
    h = _h()
    if len(points) < 2:
        raise h.GeometryError("a spline needs two points or more")
    arr = HArray1_gp_Pnt(1, len(points))
    for i, p in enumerate(points, start=1):
        arr.SetValue(i, p)
    interp = GeomAPI_Interpolate(arr, bool(closed), 1e-9)
    if start is not None and end is not None and not closed:
        interp.Load(gp_Vec(*start), gp_Vec(*end), True)
    interp.Perform()
    if not interp.IsDone():
        raise h.GeometryError("the spline through those points failed (points repeated?)")
    edge = BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


def _spline(state, a):
    """A line body through points, smooth (ADR 0065 item 3): a sweep's path, or a
    curved beam (ADR 0027), meshed into line elements as a polyline is."""
    return spline_wire(_points(a), a.get("closed", False), a.get("start_tangent"),
                       a.get("end_tangent")), {}  # fmt: skip


def path_wire(state, a):
    """A sweep's or pipe's path: an earlier spline or polyline step by name (`along`),
    or points (`path`), a spline through them when `smooth` (default), else straight."""
    h = _h()
    if a.get("along"):
        found = state.tools.get(a["along"])
        if found is None or found[0] not in ("spline", "polyline"):
            raise h.GeometryError(f"no earlier spline or polyline '{a['along']}' to follow")
        kind, args = found
        return h.TOOLS[kind](state, dict(args))[0]
    pts = _points(a, "path")
    if len(pts) < 2:
        raise h.GeometryError("a path needs two points or more (or `along` a step)")
    if a.get("smooth", True) and len(pts) > 2:
        return spline_wire(pts)
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(p)
    return poly.Wire()


def _start(spine) -> tuple[gp_Pnt, gp_Vec]:
    curve = BRepAdaptor_CompCurve(spine)
    p, v = gp_Pnt(), gp_Vec()
    curve.D1(curve.FirstParameter(), p, v)
    return p, v


def _named_pipe(mk, labels_edges, h) -> dict:
    names = {"start": h._faces(mk.FirstShape()), "end": h._faces(mk.LastShape())}
    for label, e in labels_edges:
        names[label] = names.get(label, []) + h._list(mk.Generated(e))
    return {k: v for k, v in names.items() if v}


def _sweep(state, a):
    """A profile swept along a path (ADR 0065 item 4): drawn on its plane where the path
    starts, kept square to it (corrected Frenet, `frame`), as a solid."""
    from puzzle.geometry.profile import profile_face

    h = _h()
    face, edges, labels = profile_face(a["profile"], a.get("plane", "xy"),
                                       a.get("offset", 0.0), state.root, state.params)  # fmt: skip
    spine = path_wire(state, a)
    frame = FRAMES.get(a.get("frame", "corrected"))
    if frame is None:
        raise h.GeometryError(f"frame is one of {', '.join(FRAMES)}")
    mk = BRepOffsetAPI_MakePipe(spine, face, frame, False)
    mk.Build()
    if not mk.IsDone():
        raise h.GeometryError("the sweep failed (does the profile cross itself on a bend?)")
    return mk.Shape(), _named_pipe(mk, list(zip(labels, edges, strict=True)), h)


def _pipe(state, a):
    """A circle (radius `r`), or a tube (with `inner`), along a path (ADR 0065 item 4):
    tubing, wires, a weld bead. Faces `wall` (and `bore`), `start`, `end`."""
    h = _h()
    spine = path_wire(state, a)
    p, v = _start(spine)
    axis = gp_Ax2(p, gp_Dir(v))
    outer = BRepBuilderAPI_MakeEdge(gp_Circ(axis, float(a["r"]))).Edge()
    wire = BRepBuilderAPI_MakeWire(outer).Wire()
    mkf = BRepBuilderAPI_MakeFace(wire, True)
    inner = None
    if a.get("inner"):
        if a["inner"] >= a["r"]:
            raise h.GeometryError("a pipe's inner radius is smaller than its outer")
        inner = BRepBuilderAPI_MakeEdge(gp_Circ(axis, float(a["inner"]))).Edge()
        hole = BRepBuilderAPI_MakeWire(inner).Wire()
        hole.Reverse()
        mkf.Add(hole)
    mk = BRepOffsetAPI_MakePipe(spine, mkf.Face(), GeomFill_IsCorrectedFrenet, False)
    mk.Build()
    if not mk.IsDone():
        raise h.GeometryError("the pipe failed (a bend tighter than its radius?)")
    pairs = [("wall", outer)] + ([("bore", inner)] if inner is not None else [])
    return mk.Shape(), _named_pipe(mk, pairs, h)


def _loft(state, a):
    """A solid through two or more profiles (ADR 0065 item 4), each a profile with its
    plane and offset; smooth, or `ruled` (straight between sections). Faces `start`,
    `end`, and the sides after the first profile's edges."""
    from puzzle.geometry.profile import profile_face

    h = _h()
    sections = a.get("sections") or []
    if len(sections) < 2:
        raise h.GeometryError("a loft needs two profiles or more")
    mk = BRepOffsetAPI_ThruSections(True, bool(a.get("ruled", False)), 1e-6)
    first = None
    for sec in sections:
        face, edges, labels = profile_face(
            sec["profile"], sec.get("plane", "xy"), sec.get("offset", 0.0), state.root, state.params
        )
        mk.AddWire(BRepTools.OuterWire_s(face))
        if first is None:
            first = list(zip(labels, edges, strict=True))
    mk.Build()
    if not mk.IsDone():
        raise h.GeometryError("the loft failed (profiles with different corners twist)")
    names = {"start": h._faces(mk.FirstShape()), "end": h._faces(mk.LastShape())}
    for label, e in first:
        try:
            faces = h._list(mk.Generated(e))
        except Exception:  # noqa: BLE001 - OCCT gives no history for some edges
            faces = []
        if faces:
            names[label] = faces
    return mk.Shape(), {k: v for k, v in names.items() if v}


def _plane(a) -> tuple[gp_Pnt, gp_Dir]:
    h = _h()
    point = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
    if "normal" in a:
        return point, gp_Dir(*a["normal"])
    return point, h._axis_dir(a)


def _mirror(state, op, a):
    """The part reflected in a plane (ADR 0065 item 2): through x, y, z with `normal` (or
    `axis`). With `keep` (default) the copy is fused to the original, a whole part from
    its half, its faces `<name>~m`; else the part is replaced by its other hand."""
    h = _h()
    point, normal = _plane(a)
    trsf = gp_Trsf()
    trsf.SetMirror(gp_Ax2(point, normal))
    if not a.get("keep", True):
        h._transform(state, trsf)
        return
    mk = BRepBuilderAPI_Transform(state.shape, trsf, True)
    copy = {f"{n}~m": h._list([mk.ModifiedShape(f) for f in fs]) for n, fs in state.names.items()}
    run = BRepAlgoAPI_Fuse(state.shape, mk.Shape())
    if not run.IsDone():
        raise h.GeometryError(f"mirror '{op.name}': joining the copy failed")
    state.names = h._carry(run, {**state.names, **copy})
    solids = h._subshapes(run.Shape(), h.TopAbs_SOLID)
    state.bodies = {n: solids for n in state.bodies}  # the two halves: one body
    state.shape = run.Shape()
    h._unify(state)


def _scale(state, op, a):
    """The part scaled about x, y, z (ADR 0065 item 2): by `factor`, or `fx`, `fy`, `fz`
    (non-uniform: cylinders and spheres become NURBS, so radii are no longer exact)."""
    h = _h()
    point = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
    if "factor" in a:
        if a["factor"] <= 0:
            raise h.GeometryError(f"scale '{op.name}': the factor must be positive")
        trsf = gp_Trsf()
        trsf.SetScale(point, float(a["factor"]))
        h._transform(state, trsf)
        return
    f = [float(a.get(k, 1.0)) for k in ("fx", "fy", "fz")]
    if min(f) <= 0:
        raise h.GeometryError(f"scale '{op.name}': the factors must be positive")
    g = gp_GTrsf()
    g.SetVectorialPart(gp_Mat(f[0], 0, 0, 0, f[1], 0, 0, 0, f[2]))
    c = point.XYZ()
    g.SetTranslationPart(gp_XYZ(c.X() * (1 - f[0]), c.Y() * (1 - f[1]), c.Z() * (1 - f[2])))
    mk = BRepBuilderAPI_GTransform(state.shape, g, True)
    state.names = h._carry(mk, state.names)
    state.bodies = h._carry(mk, state.bodies)
    state.shape = mk.Shape()


TOOLS = {"spline": _spline, "sweep": _sweep, "pipe": _pipe, "loft": _loft}
MODIFIERS = {"mirror": _mirror, "scale": _scale}
