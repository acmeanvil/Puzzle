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


def _patch(state, a):
    """A surface filling a closed boundary (ADR 0065 item 4, phase 3): the edges a `rule`
    picks (as a fillet's: `{"edges": ["A|B", ...]}`, `{"face": ...}`), or a closed
    polygon of `points`; it passes through `through` points if given. Capping a gap in
    imported CAD, or closing a region. Its face is `<step>/patch`."""
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_C0

    h = _h()
    fill = BRepFill_Filling()
    if a.get("rule"):
        if state.shape is None:
            raise h.GeometryError("a patch on edges needs a part to find them on")
        edges = h._edges_by_rule(state, a["rule"])
        if not edges:
            raise h.GeometryError("the patch's rule picks no edges")
    else:
        pts = _points(a)
        if len(pts) < 3:
            raise h.GeometryError("a patch needs a boundary: a rule's edges, or 3 points")
        poly = BRepBuilderAPI_MakePolygon()
        for q in pts:
            poly.Add(q)
        poly.Close()
        edges = h._subshapes(poly.Wire(), h.TopAbs_EDGE)
    for e in edges:
        fill.Add(e, GeomAbs_C0, True)
    for q in _points(a, "through"):
        fill.Add(q)
    fill.Build()
    if not fill.IsDone():
        raise h.GeometryError("the patch failed (is the boundary closed?)")
    face = fill.Face()
    return face, {"patch": [face]}


def _offset(shape, t: float, thicken: bool):
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Intersection

    mo = BRepOffset_MakeOffset()
    mo.Initialize(shape, t, 1e-7, BRepOffset_Skin, False, False, GeomAbs_Intersection,
                  thicken, False)  # fmt: skip
    mo.MakeOffsetShape()
    return mo


def _thicken(state, op, a):
    """A sheet (a face or shell) made a solid of thickness `t` (ADR 0065 item 4, phase
    3), by OCCT's offset in thickening mode: along its normal, or `both` ways (half
    each). The sheet's faces keep their names; the opposite face is `<step>/offset` and
    the rim faces `<step>/side`."""
    h = _h()
    t = float(a["t"])
    if t <= 0:
        raise h.GeometryError(f"thicken '{op.name}': the thickness must be positive")
    names = dict(state.names)
    shape = state.shape
    if a.get("both"):  # moved back half the thickness, then thickened through
        moved = _offset(shape, -t / 2, False)
        if not moved.IsDone():
            raise h.GeometryError(f"thicken '{op.name}': moving the sheet back failed")
        shape = moved.Shape()
        after = h._faces(shape)
        carried = {n: [g for f in fs for g in h._list(moved.Modified(f))]
                   for n, fs in names.items()}  # fmt: skip
        if not any(carried.values()) and len(after) == 1:  # one face: its names go with it
            carried = {n: after for n in names}
        names = {n: fs for n, fs in carried.items() if fs}
    mo = _offset(shape, t, True)
    if not mo.IsDone():
        raise h.GeometryError(f"thicken '{op.name}' failed (a bend tighter than the "
                              "thickness?)")  # fmt: skip
    solid = mo.Shape()
    faces = h._faces(solid)
    kept = {n: [f for f in faces if any(f.IsSame(g) for g in fs)] for n, fs in names.items()}
    kept = {n: fs for n, fs in kept.items() if fs}
    named = [g for fs in kept.values() for g in fs]
    new = [f for f in faces if not any(f.IsSame(g) for g in named)]
    if new:  # the largest new face is the offset side, the rest the rim
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        def area(f):
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(f, props)
            return props.Mass()

        new.sort(key=area, reverse=True)
        kept[f"{op.name}/offset"] = new[:1]
        if len(new) > 1:
            kept[f"{op.name}/side"] = new[1:]
    state.names, state.shape = kept, solid
    solids = h._subshapes(solid, h.TopAbs_SOLID)
    state.bodies = {n: solids for n in state.bodies} or {"Body": solids}


def _along(a) -> int:
    return "xyz".index(a.get("axis", "z"))


def deformation(kind: str, a, bounds):
    """The displacement field of a deformation over the shape's bounds: (n, 3) points ->
    (n, 3) displacements.

    - twist: `angle` (deg) about the axis through x, y, z, growing linearly from the
      shape's start to its end along the axis;
    - taper: the section scaled linearly to `factor` at the far end, about the axis;
    - bend: the shape along the axis bent by `angle` (deg) toward `toward` (an axis
      across it), as a beam bends round an arc of radius length / angle;
    - box: a free-form box, the bounds' eight corners moved by `corners` (eight [dx, dy,
      dz], in x, y, z order of the corners: x fastest), the rest trilinearly.
    """
    import numpy as np

    lo, hi = np.array(bounds[:3]), np.array(bounds[3:])
    k = _along(a)
    centre = np.array([a.get("x", (lo[0] + hi[0]) / 2), a.get("y", (lo[1] + hi[1]) / 2),
                       a.get("z", (lo[2] + hi[2]) / 2)])  # fmt: skip
    length = max(hi[k] - lo[k], 1e-12)

    def move(p):
        s = (p[:, k] - lo[k]) / length  # 0 to 1 along the axis
        d = np.zeros_like(p)
        if kind == "twist":
            th = np.radians(float(a["angle"])) * s
            i, j = [m for m in range(3) if m != k]
            u, v = p[:, i] - centre[i], p[:, j] - centre[j]
            d[:, i] = u * np.cos(th) - v * np.sin(th) - u
            d[:, j] = u * np.sin(th) + v * np.cos(th) - v
        elif kind == "taper":
            f = 1 + (float(a["factor"]) - 1) * s
            for m in range(3):
                if m != k:
                    d[:, m] = (p[:, m] - centre[m]) * (f - 1)
        elif kind == "bend":
            j = "xyz".index(a.get("toward", "y" if k != 1 else "z"))
            phi_total = np.radians(float(a["angle"]))
            if abs(phi_total) < 1e-12:
                return d
            r = length / phi_total
            phi = s * phi_total
            y = p[:, j] - centre[j]
            d[:, k] = lo[k] + (r - y) * np.sin(phi) - p[:, k]
            d[:, j] = centre[j] + r - (r - y) * np.cos(phi) - p[:, j]
        elif kind == "box":
            c = np.asarray(a["corners"], float).reshape(8, 3)
            t = (p - lo) / np.where(hi > lo, hi - lo, 1.0)
            for n in range(8):
                ix, iy, iz = n & 1, (n >> 1) & 1, (n >> 2) & 1
                w = ((t[:, 0] if ix else 1 - t[:, 0]) * (t[:, 1] if iy else 1 - t[:, 1])
                     * (t[:, 2] if iz else 1 - t[:, 2]))  # fmt: skip
                d += w[:, None] * c[n]
        else:
            raise _h().GeometryError(f"deform is twist, taper, bend or box, not '{kind}'")
        return d

    return move


def _deform(state, op, a):
    """The part deformed (ADR 0065 item 8): twist, taper, bend or a free-form box
    (`kind`), its faces turned to NURBS and their poles moved (the morphed export's
    method). Faces keep their names; radii stop being exact."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    from puzzle.errors import PuzzleError
    from puzzle.post.exports.morphed import morph

    h = _h()
    conv = BRepBuilderAPI_NurbsConvert(state.shape, True)
    names, bodies = h._carry(conv, state.names), h._carry(conv, state.bodies)
    bounds = h._bounds(state.shape)
    move = deformation(a.get("kind", "twist"), a, bounds)
    try:
        moved = morph(conv.Shape(), move, max(h._size(state.shape) / 12, 1e-9), convert=False)
    except PuzzleError as exc:
        raise h.GeometryError(f"deform '{op.name}': {exc.message}") from None
    state.shape, state.names, state.bodies = moved, names, bodies


TOOLS = {"spline": _spline, "sweep": _sweep, "pipe": _pipe, "loft": _loft, "patch": _patch}
MODIFIERS = {"mirror": _mirror, "scale": _scale, "thicken": _thicken, "deform": _deform}
