"""Solid bolts from the fastener tables (ADR 0077): a standard bolt drawn as it is made.

A bolt is a body of revolution about its axis, drawn from its table row: the head (a
hex's flats idealised as its bearing circle, a socket head as its diameter), the
under-head fillet, the plain shank, a 30° run-out and the threaded part as a smooth
cylinder at the minor radius d3/2 (ADR 0076 measured it best for the thread contact).
Its local frame: the head's bearing face at z = 0, the head above (z up to k), the
shank down to z = -length. Faces are named `head_top`, `head_side`, `bearing`,
`fillet`, `shank`, `runout`, `thread`, `end`, and with a grip given a `section` across
the bolt at mid-grip for the pretension (in the thread when a short bolt's grip is).

Under-head fillet radii: ISO 885's (hex bolts and screws) and ISO 4762's r min, by
diameter; the ASME tables carry their own (B18.2.1 Table 10). Thread lengths follow
ISO 4014's rule b = 2d + 6 (length to 125 mm), 2d + 12 (to 200), 2d + 25 (longer).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ISO 885 / ISO 4762 under-head fillet radius r min (m) by nominal diameter (m).
FILLET_MIN = {
    0.003: 0.0001, 0.004: 0.0002, 0.005: 0.0002, 0.006: 0.00025, 0.008: 0.0004,
    0.010: 0.0004, 0.012: 0.0006, 0.014: 0.0006, 0.016: 0.0006, 0.018: 0.0006,
    0.020: 0.0008, 0.022: 0.0008, 0.024: 0.0008, 0.027: 0.001, 0.030: 0.001,
    0.033: 0.001, 0.036: 0.001, 0.039: 0.001, 0.042: 0.0012, 0.045: 0.0012,
    0.048: 0.0016, 0.052: 0.0016, 0.056: 0.002, 0.060: 0.002, 0.064: 0.002,
}  # fmt: skip
HEADS = {"hex": "hex", "socket": "socket"}  # head style -> fasteners' style
# A modelled bolt's bonds are ties between separate meshes: a conformal (shared-node) bond
# would fragment the bolt with its neighbour and mesh it as tetrahedra, not revolved.
TIED = "nonconformal"


def fillet_radius(d: float) -> float:
    """r min under the head (m): ISO 885's at the nearest tabulated diameter at or
    below d (a bolt between sizes takes the smaller's), else 0.04 d."""
    below = [k for k in FILLET_MIN if k <= d + 1e-9]
    return FILLET_MIN[max(below)] if below else 0.04 * d


def thread_length(d: float, length: float) -> float:
    """ISO 4014's thread length b for a bolt of nominal length `length` (m)."""
    extra = 0.006 if length <= 0.125 + 1e-9 else 0.012 if length <= 0.2 + 1e-9 else 0.025
    return min(2 * d + extra, length)


@dataclass(frozen=True)
class Dimensions:
    """What a solid bolt is drawn from (m)."""

    d: float  # nominal
    pitch: float
    head: str  # hex or socket
    head_radius: float  # the bearing circle (hex) or head diameter (socket) / 2
    head_height: float
    fillet: float
    shank_radius: float  # ds / 2
    minor_radius: float  # d3 / 2, the smooth thread's
    length: float  # under the head to the end
    thread: float  # b

    def args(self, grip: float | None = None) -> dict:
        """The `bolt` history step's arguments."""
        out = {k: getattr(self, k) for k in ("head_radius", "head_height", "fillet",
                                             "shank_radius", "minor_radius", "length",
                                             "thread")}  # fmt: skip
        if grip:
            out["section"] = -0.5 * grip
        return out


def dimensions(size: str, head: str = "hex", length: float | None = None,
               grip: float | None = None) -> Dimensions:  # fmt: skip
    """A standard bolt's dimensions: `size` from `bolts.SIZES` (M16, 1/2-13 UNC...),
    `head` hex or socket, `length` under the head (default: grip + 1.5 d engaged,
    rounded up to 5 mm)."""
    from puzzle import bolts, fasteners

    found = next((s for s in bolts.SIZES if s.name == size), None)
    if found is None:
        raise ValueError(f"no bolt size '{size}'")
    d, p = found.diameter, found.pitch
    metric = found.standard.startswith("iso")
    part = fasteners.head(HEADS.get(head, head), d, metric)
    if part is None:
        raise ValueError(f"no {head} head in the tables for {size}")
    if length is None:
        base = (grip or 4 * d) + 1.5 * d
        length = math.ceil(base / 0.005 - 1e-9) * 0.005
    if grip is not None and grip >= length:
        raise ValueError(f"a {length * 1e3:g} mm bolt is no longer than its "
                         f"{grip * 1e3:g} mm grip")  # fmt: skip
    d3 = d - 1.226869 * p
    fillet = fillet_radius(d)
    runout = (d - d3) / 2 / math.tan(math.radians(30))
    # A short bolt is threaded nearly to its head (ISO 4017): the thread stops where the
    # fillet and the run-out (and a sliver of shank) still fit under the head.
    thread = min(thread_length(d, length), length - fillet - runout - 0.05 * d)
    return Dimensions(
        d=d, pitch=p, head=head, head_radius=part.bearing / 2, head_height=part.height,
        fillet=fillet, shank_radius=d / 2, minor_radius=d3 / 2, length=length, thread=thread,
    )  # fmt: skip


def bolt_tool(state, a):
    """The `bolt` history step: a standard bolt as a body of revolution (module
    docstring), its faces named. -> (shape, names)"""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    from puzzle.geometry.history import GeometryError, _faces, _list

    rh, k, r = a["head_radius"], a["head_height"], a["fillet"]
    rs, rm, length, b = a["shank_radius"], a["minor_radius"], a["length"], a["thread"]
    if not (rh > rs + r and rs >= rm > 0 and length > b > 0 and k > 0):
        raise GeometryError(
            "a bolt needs its head wider than shank and fillet, its shank no thinner than "
            "the minor diameter, and its length over its thread length"
        )
    runout = (rs - rm) / math.tan(math.radians(30))
    z_thread = -(length - b)  # where the thread starts (full depth below the run-out)
    z_shank = z_thread + runout
    if z_shank > -r:
        raise GeometryError("the bolt's plain shank is too short for its fillet and run-out")

    def p(x, z):
        return gp_Pnt(x, 0.0, z)

    # Counter-clockwise in the xz half-plane (x = r): the axis, the end, up the thread.
    pieces = [
        ("end", (p(0, -length), p(rm, -length))),
        ("thread", (p(rm, -length), p(rm, z_thread))),
        ("runout", (p(rm, z_thread), p(rs, z_shank))),
        ("shank", (p(rs, z_shank), p(rs, -r))),
        ("fillet", None),
        ("bearing", (p(rs + r, 0.0), p(rh, 0.0))),
        ("head_side", (p(rh, 0.0), p(rh, k))),
        ("head_top", (p(rh, k), p(0, k))),
        ("axis", (p(0, k), p(0, -length))),
    ]
    edges, labels = [], []
    for name, ends in pieces:
        if name == "fillet":  # a quarter circle about (rs + r, -r), concave
            circle = gp_Circ(gp_Ax2(p(rs + r, -r), gp_Dir(0, 1, 0)), r)
            arc = GC_MakeArcOfCircle(circle, p(rs, -r), p(rs + r, 0.0), True).Value()
            edge = BRepBuilderAPI_MakeEdge(arc).Edge()
        else:
            edge = BRepBuilderAPI_MakeEdge(*ends).Edge()
        edges.append(edge)
        labels.append(name)
    wire = BRepBuilderAPI_MakeWire()
    for e in edges:
        wire.Add(e)
    if not wire.IsDone():
        raise GeometryError("the bolt's profile did not close")
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
    revol = BRepPrimAPI_MakeRevol(face, gp_Ax1(p(0, 0), gp_Dir(0, 0, 1)), 2 * math.pi)
    shape = revol.Shape()
    # The face's own edges (the wire builder copies them), each matched to its segment
    # by its midpoint.
    from OCP.TopAbs import TopAbs_EDGE

    from puzzle.geometry.history import _subshapes

    mids = [_midpoint(e) for e in edges]
    names: dict[str, list] = {}
    for e in _subshapes(face, TopAbs_EDGE):
        m = _midpoint(e)
        i = min(range(len(mids)), key=lambda j: m.Distance(mids[j]))
        if labels[i] != "axis":
            names.setdefault(labels[i], []).extend(_list(revol.Generated(e)))
    # OCC's revolve reports no face for a profile edge square to the axis: the flat
    # faces are found by their height (the end, the bearing face, the head's top).
    from OCP.TopAbs import TopAbs_FACE

    for label, z in (("end", -length), ("bearing", 0.0), ("head_top", k)):
        names[label] = [f for f in _subshapes(shape, TopAbs_FACE)
                        if _is_flat_across(f) and abs(_centre_z(f) - z) < 1e-9]  # fmt: skip
    if a.get("section") is not None:  # a cut across the shank for the pretension
        from OCP.gp import gp_Pln

        from puzzle.geometry.history import TopTools_ListOfShape

        z = float(a["section"])
        if not (-length < z < -r):  # the shank or the thread: a short bolt's grip is threaded
            raise GeometryError("the pretension section lies outside the bolt's shank")
        plane = gp_Pln(p(0, z), gp_Dir(0, 0, 1))
        tool = BRepBuilderAPI_MakeFace(plane, -2 * rh, 2 * rh, -2 * rh, 2 * rh).Face()
        split = BRepAlgoAPI_Splitter()
        args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        args.Append(shape)
        tools.Append(tool)
        split.SetArguments(args)
        split.SetTools(tools)
        split.Build()
        if not split.IsDone():
            raise GeometryError("the bolt's pretension section could not be cut")
        from puzzle.geometry.history import _carry

        names = _carry(split, names)
        shape = split.Shape()
        section = [f for f in _faces(shape)
                   if abs(_centre_z(f) - z) < 1e-9 and _is_flat_across(f)]  # fmt: skip
        names["section"] = section
    return shape, {k: v for k, v in names.items() if v}


def _midpoint(edge):
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    c = BRepAdaptor_Curve(edge)
    return c.Value((c.FirstParameter() + c.LastParameter()) / 2)


def _centre_z(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    props = GProp_GProps()
    _static(BRepGProp, "SurfaceProperties")(face, props)
    return props.CentreOfMass().Z()


def _is_flat_across(face) -> bool:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    s = BRepAdaptor_Surface(face)
    if s.GetType() != GeomAbs_Plane:
        return False
    return abs(abs(s.Plane().Axis().Direction().Z()) - 1) < 1e-9


def placement(head_point, direction) -> tuple[tuple, tuple]:
    """(translation, rotation in degrees about x then y then z) that put a bolt's local
    frame (head bearing at the origin, shank toward -z) at `head_point` with its shank
    along `direction` (unit, from the head into the joint)."""
    import numpy as np

    v = -np.asarray(direction, dtype=float)  # local +z (toward the head) in the world
    v /= np.linalg.norm(v)
    a = math.degrees(math.asin(max(-1.0, min(1.0, -v[1]))))
    b = math.degrees(math.atan2(v[0], v[2]))
    return tuple(float(x) for x in head_point), (a, b, 0.0)


def nut_tool(state, a, bore: str = "thread"):
    """The `nut` history step: a nut as a ring about z (its flats idealised as its
    bearing circle), the bearing face at z = 0, the nut up to z = height; faces
    `bearing`, `top`, `side`, `thread` (the bore, at the bolt's minor radius)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.TopAbs import TopAbs_FACE

    from puzzle.geometry.history import GeometryError, _subshapes

    ro, ri, h = a["outer_radius"], a["inner_radius"], a["height"]
    if not (ro > ri > 0 and h > 0):
        raise GeometryError("a nut needs its outer radius over its bore's, and a height")
    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    outer = BRepPrimAPI_MakeCylinder(axis, ro, h).Shape()
    ring = BRepAlgoAPI_Cut(outer, BRepPrimAPI_MakeCylinder(axis, ri, h).Shape()).Shape()
    names: dict[str, list] = {"bearing": [], "top": [], "side": [], bore: []}
    for f in _subshapes(ring, TopAbs_FACE):
        if _is_flat_across(f):
            names["bearing" if abs(_centre_z(f)) < 1e-9 else "top"].append(f)
        else:
            names[bore if _radius(f) < (ri + ro) / 2 else "side"].append(f)
    return ring, {k: v for k, v in names.items() if v}


def washer_tool(state, a):
    """The `washer` history step: a plain washer as a ring about z, its face on the
    plate (`bearing`) at z = 0 and the head's or nut's (`top`) at z = thickness; its
    bore (`bore`) clears the bolt, so nothing touches it."""
    return nut_tool(state, a, bore="bore")


def _radius(face) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    return BRepAdaptor_Surface(face).Cylinder().Radius()


# --- making a modelled bolt ----------------------------------------------------------


@dataclass
class Hole:
    """A hole face of the model, placed: a point on its axis, the unit axis, its radius
    and its two mouths (m along the axis from the point)."""

    point: object
    axis: object
    radius: float
    span: tuple[float, float]

    def mouths(self, along):
        """Its two mouths as points, ordered along the direction `along`."""
        import numpy as np

        pts = [np.asarray(self.point) + self.axis * s for s in self.span]
        return sorted(pts, key=lambda q: float(q @ along))


def _hole(project, comp, root, selection: str) -> Hole:
    import numpy as np

    from puzzle.geometry.contacts import _placed_faces
    from puzzle.geometry.holes import _cylinders
    from puzzle.model.schema import Geometry

    part, name = selection.split("/", 1)
    faces = _placed_faces(project, comp, root)
    geo = project.get(next(p for p in comp.parts if p.name == part).geometry, Geometry)
    sel = next((s for s in geo.selections if s.name == name), None)
    members = sel.members if sel is not None else [name]
    found = _cylinders({part: {m: faces[part][m] for m in members if m in faces[part]}})
    if not found:
        raise ValueError(f"'{selection}' holds no cylindrical face (a hole)")
    h = max(found, key=lambda c: c.length or 0.0)
    half = (h.length or 0.0) / 2
    s = float((np.asarray(h.centre) - h.point) @ h.axis)
    return Hole(h.point, np.asarray(h.axis), h.diameter / 2, (s - half, s + half))


def _face_at(project, comp, root, part: str, point, normal, tol: float):
    """The name of `part`'s flat face through `point` whose normal is along `normal`."""
    import numpy as np
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    from puzzle.geometry.contacts import _placed_faces

    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*(float(c) for c in point))).Vertex()
    best = None
    for name, shapes in _placed_faces(project, comp, root)[part].items():
        if not _square_to(shapes[0], np.asarray(normal)):
            continue
        gap = BRepExtrema_DistShapeShape(vertex, shapes[0]).Value()
        if gap < tol and (best is None or gap < best[0]):
            best = (gap, name)
    return best[1] if best else None


def _square_to(face, direction) -> bool:
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    s = BRepAdaptor_Surface(face)
    if s.GetType() != GeomAbs_Plane:
        return False
    d = s.Plane().Axis().Direction()
    return abs(abs(float(np.dot([d.X(), d.Y(), d.Z()], direction))) - 1) < 1e-6


def _nearest_size(d: float) -> str:
    from puzzle import bolts

    coarse = [s for s in bolts.SIZES if s.standard == "iso_coarse"]
    return min(coarse, key=lambda s: abs(s.diameter - d)).name


def _pitch(size: str) -> float:
    from puzzle import bolts

    return next(s.pitch for s in bolts.SIZES if s.name == size)


def make_commands(project, comp, index: int, root, *, bonded_heads: bool = False) -> list:
    """The batch drawing bolt `index` of `comp` as a modelled bolt (ADR 0077): its
    geometry and part on the hole stack's axis (and a nut for a through bolt), its
    grade as its material, the head's (and nut's) frictional contact on the plates
    (bonded with `bonded_heads`), its thread into a tapped hole (or the nut) as a
    threaded contact, its preload on a section across the shank, finer elements at
    the fillet and the thread, and the bolt marked `modelled`. One batch: one undo.

    Washers (the bolt's `washers`, its type's series) are parts of their own between
    the head or nut and the plate: bonded to the head or nut, which don't slip on them,
    and in the bearing contact with the plate; the grip and length grow by their
    thickness."""
    import numpy as np

    from puzzle import bolts, fasteners
    from puzzle.commands.model import Create, Edit, EditOp
    from puzzle.model.schema import (
        Contact,
        Feature,
        Geometry,
        HistoryStep,
        MaterialAssignment,
        NamedSelection,
        PartInstance,
        Quantity,
        Thread,
    )

    bolt = comp.connectors[index]
    solved = bolts.resolve(project, bolt)
    btype = project.objects.get(bolt.type) if bolt.type is not None else None
    size = getattr(btype, "size", None) or _nearest_size(solved.diameter.to_si())
    joint = solved.joint or "hole_walls"
    if joint not in ("tapped", "through_nut") or solved.target is None:
        raise ValueError(f"bolt '{bolt.name}': a modelled bolt needs a tapped or a "
                         f"through-and-nut joint; its joint is '{joint}' (a bolt's own "
                         "joint wins over its type's)")  # fmt: skip
    a_hole = _hole(project, comp, root, solved.source)
    b_hole = _hole(project, comp, root, solved.target)
    a_mid = np.asarray(a_hole.point) + a_hole.axis * np.mean(a_hole.span)
    b_mid = np.asarray(b_hole.point) + b_hole.axis * np.mean(b_hole.span)
    axis = (b_mid - a_mid) / np.linalg.norm(b_mid - a_mid)
    d = solved.diameter.to_si()
    metric = size.startswith("M")
    washers = solved.washers or "none"
    w_style = getattr(btype, "washer", "normal")
    w_part = None
    if washers != "none":
        w_part = fasteners.washer(w_style, d, metric)
        if w_part is None:
            raise ValueError(f"bolt '{bolt.name}': no {w_style.replace('_', ' ')} washer in "
                             f"the tables for {size}")  # fmt: skip
    head_washer = w_part is not None and washers in ("head", "both")
    nut_washer = w_part is not None and washers in ("nut", "both") and joint == "through_nut"
    t_head = w_part.height if head_washer else 0.0
    t_nut = w_part.height if nut_washer else 0.0
    plate_head = a_hole.mouths(axis)[0]  # the source's outer mouth: where the head bears
    head_point = plate_head - axis * t_head
    nut_part = None
    if joint == "tapped":
        mouth, bottom = b_hole.mouths(axis)
        grip = float((mouth - head_point) @ axis)
        engaged = btype.engagement.to_si() if btype is not None and btype.engagement else 1.5 * d
        length = grip + min(engaged, float((bottom - mouth) @ axis))
    else:
        far = b_hole.mouths(axis)[1]
        grip = float((far - head_point) @ axis) + t_nut
        nut_part = fasteners.nut("hex", d, metric)
        length = grip + (nut_part.height if nut_part else 0.8 * d) + 3 * _pitch(size)
    length = math.ceil(length / 0.005 - 1e-9) * 0.005
    dim = dimensions(size, solved.head or "hex", length=length, grip=grip)
    name = bolt.name
    faces = (("Bearing", "bearing"), ("Fillet", "fillet"), ("Thread", "thread"),
             ("Section", "section"), ("Shank", "shank"))  # fmt: skip
    geo = Geometry(
        name=f"{name} (bolt)",
        history=[HistoryStep(kind="bolt", name="B", args=dim.args(grip=grip))],
        selections=[NamedSelection(name=n, entity="face", members=[f"B/{f}"]) for n, f in faces],
    )
    translation, rotation = placement(head_point, axis)
    taken = {p.name for p in comp.parts}
    part_name = name if name not in taken else f"{name} (bolt)"
    commands: list = [Create(kind="geometry", data=geo.model_dump(mode="json"))]
    parts = [PartInstance(name=part_name, geometry=geo.id, translation=translation,
                          rotation=rotation)]  # fmt: skip
    materials = [MaterialAssignment(material=solved.material, selection=part_name)]
    contacts = []
    friction = getattr(btype, "head_friction", 0.15)
    tol = 0.05 * d

    side = np.cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0])
    side /= np.linalg.norm(side)

    def bearing_contact(source: str, face_part: str, point, label: str, hole: Hole,
                        outer: float) -> None:  # fmt: skip
        # A point on the plate under the head (between the hole's edge and the head's
        # bearing circle): the axis's own point is in the hole.
        probe = np.asarray(point) + side * (hole.radius + outer) / 2
        face = _face_at(project, comp, root, face_part, probe, axis, tol)
        if face is None:
            raise ValueError(f"bolt '{name}': no flat face of '{face_part}' at its {label}")
        holder = next(p for p in comp.parts if p.name == face_part)
        selection = NamedSelection(name=f"Under {name} {label}", entity="face", members=[face])
        commands.append(
            Edit(
                object=str(project.get(holder.geometry, Geometry).id),
                ops=[
                    EditOp(op="append", path="selections", value=selection.model_dump(mode="json"))
                ],
            )
        )
        kind = "bonded" if bonded_heads else "frictional"
        contacts.append(Contact(name=f"{name} {label}", type=kind, source=source,
                                target=f"{face_part}/{selection.name}",
                                friction=None if bonded_heads else friction,
                                meshing=TIED if bonded_heads else None))  # fmt: skip

    def washer(label: str, plate_point, toward) -> str:
        """A washer on the plate at `plate_point`, its top facing `toward` (the head or
        nut); its part's name."""
        w_args = {"outer_radius": w_part.bearing / 2, "inner_radius": w_part.inner / 2,
                  "height": w_part.height}  # fmt: skip
        w_geo = Geometry(
            name=f"{name} ({label} washer)",
            history=[HistoryStep(kind="washer", name="W", args=w_args)],
            selections=[
                NamedSelection(name=n, entity="face", members=[f"W/{f}"])
                for n, f in (("Bearing", "bearing"), ("Top", "top"))
            ],  # fmt: skip
        )
        commands.append(Create(kind="geometry", data=w_geo.model_dump(mode="json")))
        w_name = f"{name} {label} washer"
        w_translation, w_rotation = placement(plate_point, -np.asarray(toward))
        parts.append(PartInstance(name=w_name, geometry=w_geo.id, translation=w_translation,
                                  rotation=w_rotation))  # fmt: skip
        materials.append(MaterialAssignment(material=solved.material, selection=w_name))
        return w_name

    plate_a = solved.source.split("/")[0]
    if head_washer:
        w_name = washer("head", plate_head, -axis)
        contacts.append(Contact(name=f"{name} head on washer", type="bonded", meshing=TIED,
                                source=f"{part_name}/Bearing", target=f"{w_name}/Top"))  # fmt: skip
        bearing_contact(f"{w_name}/Bearing", plate_a, plate_head, "head washer", a_hole,
                        w_part.bearing / 2)  # fmt: skip
    else:
        bearing_contact(f"{part_name}/Bearing", plate_a, head_point, "head", a_hole,
                        dim.head_radius)  # fmt: skip
    thread = solved.thread or Thread(form="iso", size=size)
    if joint == "tapped":
        contacts.append(Contact(name=f"{name} thread", type="bonded",
                                source=f"{part_name}/Thread", target=solved.target,
                                thread=thread))  # fmt: skip
    else:
        nut_args = {"outer_radius": (nut_part.bearing if nut_part else 1.5 * d) / 2,
                    "inner_radius": dim.minor_radius,
                    "height": nut_part.height if nut_part else 0.8 * d}  # fmt: skip
        nut_geo = Geometry(
            name=f"{name} (nut)",
            history=[HistoryStep(kind="nut", name="N", args=nut_args)],
            selections=[
                NamedSelection(name=n, entity="face", members=[f"N/{f}"])
                for n, f in (("Bearing", "bearing"), ("Thread", "thread"))
            ],  # fmt: skip
        )
        commands.append(Create(kind="geometry", data=nut_geo.model_dump(mode="json")))
        far = b_hole.mouths(axis)[1]
        nut_name = f"{name} nut"
        # It faces back up the bolt, on its washer when it has one.
        n_translation, n_rotation = placement(far + axis * t_nut, -axis)
        parts.append(PartInstance(name=nut_name, geometry=nut_geo.id,
                                  translation=n_translation, rotation=n_rotation))  # fmt: skip
        materials.append(MaterialAssignment(material=solved.material, selection=nut_name))
        plate_b = solved.target.split("/")[0]
        if nut_washer:
            w_name = washer("nut", far, axis)
            contacts.append(Contact(name=f"{name} nut on washer", type="bonded", meshing=TIED,
                                    source=f"{nut_name}/Bearing",
                                    target=f"{w_name}/Top"))  # fmt: skip
            bearing_contact(f"{w_name}/Bearing", plate_b, far, "nut washer", b_hole,
                            w_part.bearing / 2)  # fmt: skip
        else:
            bearing_contact(f"{nut_name}/Bearing", plate_b, far, "nut", b_hole,
                            nut_args["outer_radius"])  # fmt: skip
        contacts.append(Contact(name=f"{name} nut thread", type="bonded",
                                source=f"{part_name}/Thread", target=f"{nut_name}/Thread",
                                thread=thread))  # fmt: skip
    ops = [EditOp(op="append", path="parts", value=p.model_dump(mode="json")) for p in parts]
    ops += [EditOp(op="append", path="materials", value=m.model_dump(mode="json"))
            for m in materials]  # fmt: skip
    ops += [EditOp(op="append", path="contacts", value=c.model_dump(mode="json"))
            for c in contacts]  # fmt: skip
    solid = next((i for i, ph in enumerate(comp.physics) if ph.type == "solid_mechanics"), None)
    if solved.pretension is not None and solid is not None:
        force = Quantity(value=solved.pretension.to_si(), unit="N")
        feature = Feature(type="bolt_pretension", selection=f"{part_name}/Section",
                          values={"F": force})  # fmt: skip
        ops.append(EditOp(op="append", path=f"physics.{solid}.features",
                          value=feature.model_dump(mode="json")))  # fmt: skip
    ops.append(EditOp(op="set", path=f"connectors.{index}.formulation", value="modelled"))
    ops.append(EditOp(op="set", path=f"connectors.{index}.parts",
                      value=[p.name for p in parts]))  # fmt: skip
    commands.append(Edit(object=str(comp.id), ops=ops))
    recipe = project.objects.get(comp.mesh)
    if recipe is not None:
        sizes = [(f"{part_name}/Fillet", max(dim.fillet / 3, dim.d / 200)),
                 (f"{part_name}/Thread", dim.pitch / 4)]  # fmt: skip
        # Two elements through a washer's thickness.
        sizes += [(f"{p.name}/Top", w_part.height / 2) for p in parts
                  if p.name.endswith(" washer")]  # fmt: skip
        controls = [
            {"kind": "size", "selection": sel, "size": {"value": round(v * 1e3, 4), "unit": "mm"}}
            for sel, v in sizes
        ]
        commands.append(Edit(object=str(recipe.id), ops=[
            EditOp(op="append", path="controls", value=c) for c in controls]))  # fmt: skip
    return commands


def beam_commands(project, comp, index: int) -> list:
    """The batch turning modelled bolt `index` back into a beam bolt: its parts, their
    geometries, materials, contacts (and the plates' selections made for them), its
    pretension feature and mesh sizes removed, the bolt a beam again."""
    from puzzle.commands.model import Delete, Edit, EditOp

    bolt = comp.connectors[index]
    if bolt.formulation != "modelled":
        return []
    mine = set(bolt.parts)
    owned = {str(p.geometry) for p in comp.parts if p.name in mine}

    def on_mine(selection: str | None) -> bool:
        return bool(selection) and selection.split("/", 1)[0] in mine

    contacts = [c for c in comp.contacts if not (on_mine(c.source) or on_mine(c.target))]
    plates = {}
    for c in comp.contacts:
        if on_mine(c.source) and not on_mine(c.target) and "/Under " in c.target:
            part, sel = c.target.split("/", 1)
            plates.setdefault(part, []).append(sel)
    physics = []
    for ph in comp.physics:
        data = ph.model_dump(mode="json")
        data["features"] = [f for f in data["features"] if not on_mine(f.get("selection"))]
        physics.append(data)
    ops = [
        EditOp(op="set", path="parts", value=[p.model_dump(mode="json") for p in comp.parts
                                              if p.name not in mine]),
        EditOp(op="set", path="materials", value=[m.model_dump(mode="json") for m in comp.materials
                                                  if not on_mine(m.selection)]),
        EditOp(op="set", path="contacts", value=[c.model_dump(mode="json") for c in contacts]),
        EditOp(op="set", path="physics", value=physics),
        EditOp(op="set", path=f"connectors.{index}.formulation", value="beam"),
        EditOp(op="set", path=f"connectors.{index}.parts", value=[]),
    ]  # fmt: skip
    commands: list = [Edit(object=str(comp.id), ops=ops)]
    for part, sels in plates.items():
        holder = next((p for p in comp.parts if p.name == part), None)
        geo = project.objects.get(holder.geometry) if holder else None
        if geo is not None:
            kept = [s.model_dump(mode="json") for s in geo.selections if s.name not in sels]
            commands.append(Edit(object=str(geo.id), ops=[EditOp(op="set", path="selections",
                                                                 value=kept)]))  # fmt: skip
    recipe = project.objects.get(comp.mesh)
    if recipe is not None:
        kept = [c.model_dump(mode="json") for c in recipe.controls
                if not on_mine(getattr(c, "selection", None))]  # fmt: skip
        if len(kept) != len(recipe.controls):
            commands.append(Edit(object=str(recipe.id), ops=[EditOp(op="set", path="controls",
                                                                    value=kept)]))  # fmt: skip
    commands += [Delete(object=gid) for gid in sorted(owned)]
    return commands
