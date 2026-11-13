"""Geometry as a feature history with persistent face names (ADR 0004, ADR 0014).

A history is a list of operations. Regenerating it builds the shape with OpenCASCADE
(OCP) and names every face by the operation that made it, e.g. "Plate/+z",
"Hole1/wall", "Round/edge-2". Names are carried through later operations by OCCT's
own history (Modified / Generated / IsDeleted):

- a face an operation leaves alone, or only trims, keeps its name;
- a face split in two keeps its name on both pieces (a selection follows the split);
- a face an operation removes loses its name (a selection on it is detached).

Edges for fillets are picked by rules evaluated at each regeneration, not by index.
Values may name parameters: a string is looked up in `params`.

Operations (lengths in metres; `mode` = new | add | cut | intersect joins a tool body to
the part, the first one starts it):

| kind | args | face names |
| --- | --- | --- |
| box | dx, dy, dz, x0, y0, z0 | -x, +x, -y, +y, -z, +z |
| cylinder | r, h, x, y, z, axis | wall, start, end |
| polyline | points (a line body, for beams) | none: edge-N, vertex-N by position |
| frame | nodes, members, or dxf, layer, scale (ADR 0079) | edges: members, groups |
| sheet | points (a flat polygon: a sheet body, for shells) | face |
| face | profile (sketch), plane, offset (a flat region: 2D, shells) | face; edges by side |
| section | part (a geometry), plane, offset, half (ADR 0048) | face; edges by 3D face |
| rectangle, circle, annulus, polygon, slot | 2D shapes (`planar.py`, ADR 0048) | face; sides |
| offset | line, t, side: a wall along a line (`planar.py`) | face; left, right, start, end |
| curved_sheet | r, length, angle, x0, y0, z0 (a cylinder panel on x, centred on +z) | surface |
| cone | r1, r2, h, x, y, z, axis | wall, start, end |
| sphere | r, x, y, z | surface |
| extrude | profile, distance, plane, offset | start, end, side-1 ... (a sketch's edge names) |
| revolve | profile, angle (deg), axis (u or v), plane, offset | side-1 ..., start, end |
| hole | r, x, y (through, along `axis`, default z) | wall |
| fillet, chamfer | r or d, rule (edges; a flat part's corners, `planar.py`) | edge-1 ... |
| move | dx, dy, dz | (names kept) |
| rotate | angle (deg), axis, x, y, z (a point on the axis) | (names kept) |
| pattern | feature, count, dx, dy, dz, or angle + axis for a circular one | <feature>#2/..., ... |
| spline | points, closed, start_tangent, end_tangent (a smooth line body) | none |
| sweep | profile, plane, offset, path (points; smooth) or along, frame | start, end, side-N |
| pipe | r, inner (a tube), path or along | wall, bore, start, end |
| loft | sections (each profile, plane, offset), ruled | start, end, the first's side-N |
| mirror | x, y, z and normal (or axis), keep (fuse the copy: a whole part) | <name>~m on the copy |
| scale | factor, or fx, fy, fz, about x, y, z | (names kept) |
| patch | rule (boundary edges) or points (a closed boundary), through | patch |
| thicken | t, both (half each side): a sheet made a solid | offset, side; the sheet's kept |
| subd | file (an OBJ quad cage) or points + faces, levels: Catmull–Clark to B-splines | face-N |
| deform | kind (twist, taper, bend, box), axis, angle, factor, toward, corners | (names kept) |
| split | axis or normal, at or x, y, z | section (the cut face inside); split faces keep names |
| imprint | hole (a wall's face name), r | annulus (rings at the hole's mouths); others keep names |
| split_face | profile (sketch), faces (default: on its plane), direction | region; others kept |
| enclosure | margin, or factor of the part size | +x ... of the box; body `<name>` |
| import, heal, remove, fragment | see puzzle.geometry.prep (import `flat`: faces onto XY) | G2 |
| keep | solids (1-based, file order) or bodies (names); body (the kept ones' name) | (names kept) |
| bolt | `puzzle.solid_bolts` dimensions (ADR 0077) | bearing, fillet, shank, thread, section |
| weld_bead | points, a, b (legs), closed, root (ADR 0078) | leg_a, leg_b, face, root, ... |
| sketch | sketch, plane and offset, or face (a planar face, axis-aligned) | (no faces: a profile) |

A sketch step (ADR 0017) adds no solid: extrude and revolve take it by name as their
profile, `{"sketch": "Outline"}`, on its plane. A profile may still hold a sketch
inline (`{"sketch": {...}}`, G5).

Bodies are named too: the part is "Body" unless an operation's `body` says otherwise,
and an enclosure adds its own; selections of entity "body" use these names.

`tests/test_naming.py` and `tests/test_geometry_ops.py` measure the naming across edits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import (
    BRepAlgoAPI_BuilderAlgo,
    BRepAlgoAPI_Common,
    BRepAlgoAPI_Cut,
    BRepAlgoAPI_Fuse,
    BRepAlgoAPI_Splitter,
)
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeRevol,
    BRepPrimAPI_MakeSphere,
)
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

try:  # OCP 8 moved OCCT's collections into one module
    from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
except ImportError:
    from OCP.TopTools import TopTools_ListOfShape


def _static(cls, name: str):
    """A static OCCT method: OCP 7 names it `Name_s`, OCP 8 plain `Name`."""
    return getattr(cls, f"{name}_s", None) or getattr(cls, name)


Names = dict[str, list]  # persistent name -> faces (several after a split)


@dataclass
class Op:
    """One history step: `kind` with its `args` (numbers, parameter names, lists)."""

    kind: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class NamedShape:
    shape: TopoDS_Shape
    names: Names  # face names
    bodies: Names = field(default_factory=dict)  # body names -> solids
    edges: Names = field(default_factory=dict)  # edges an operation named (a 2D face's sides)

    def faces(self) -> list:
        return _faces(self.shape)

    def solids(self) -> list:
        return _subshapes(self.shape, TopAbs_SOLID)

    def resolve(self, names: list[str]) -> tuple[list, list[str]]:
        """Faces (or bodies) for persistent names, and the names that no longer exist."""
        found, missing = [], []
        for n in names:
            hits = self.names.get(n) or self.bodies.get(n)
            if hits:
                found.extend(hits)
            else:
                missing.append(n)
        return found, missing


def ops_of(geometry, project=None, _seen=()) -> list[Op]:
    """A project geometry's history as operations, suppressed steps left out. A section
    of another part (ADR 0048) carries that part's own operations (or its file), so
    regenerating stays a function of the operations alone; it needs `project`."""
    out = []
    found = None
    for s in geometry.history:
        if s.suppressed:
            continue
        args = dict(s.args)
        if project is not None:  # ADR 0062 phase 3: points, profiles, list entries
            from puzzle.operators.geometry_data import substitute, values

            found = values(project) if found is None else found
            if found:
                args = substitute(args, found)
        if s.kind == "section":
            args["_part"] = _section_source(geometry, args.get("part"), project, _seen)
        out.append(Op(s.kind, s.name, args))
    return out


def _section_source(geometry, part, project, seen) -> dict:
    """What a section cuts: the named geometry's operations, or its file."""
    from uuid import UUID

    from puzzle.model.schema import Geometry

    if project is None or not part:
        raise GeometryError("a section names the part it cuts (its geometry)")
    other = project.objects.get(UUID(str(part)))
    if not isinstance(other, Geometry):
        raise GeometryError(f"a section names geometry {part}, which is not in the project")
    if other.id == geometry.id or other.id in seen:
        raise GeometryError(f"geometry '{geometry.name}' would be a section of itself")
    if other.history:
        return {"ops": ops_of(other, project, (*seen, geometry.id))}
    return {"file": other.source}


# Arguments that are never parameter names.
WORDS = {
    "rule",
    "axis",
    "mode",
    "plane",
    "feature",
    "profile",
    "body",
    "normal",
    "file",
    "unit",
    "sketch",  # resolved by the sketch solver itself (its dimensions may name parameters)
    "face",  # a sketch step's face to sketch on
    "hole",  # an imprint's hole wall (ADR 0050)
    "part",  # a section's geometry (its id)
    "_part",  # a section's part, filled in by ops_of
    "half",
    "side",  # an offset wall's side: left, right or both (ADR 0048 round 2)
    "faces",  # a split_face's faces, by name
    "direction",  # a split_face's projection: both, forward or backward
    "along",  # a sweep's or pipe's path: an earlier spline or polyline step (ADR 0065)
    "positions",  # a pattern's offsets: a Points operator's, put in by ops_of (ADR 0062)
    "frame",  # a sweep's frame along its path
    "kind",  # a deform's kind: twist, taper, bend or box
    "toward",  # a bend's direction
    "nodes",  # a frame's nodes and members, by name (ADR 0079)
    "members",
}


class GeometryError(ValueError):
    pass


def regenerate(ops: list[Op], params: dict[str, float] | None = None, root=None) -> NamedShape:
    """Build the shape of a history. `root` resolves relative paths (DXF profiles)."""
    from puzzle.geometry.session import occ_lock

    with occ_lock:
        return _regenerate(ops, params, root)


def _regenerate(ops: list[Op], params: dict[str, float] | None, root) -> NamedShape:
    params = params or {}

    def value(v, key=""):
        if key in WORDS and key != "profile":
            return v
        if isinstance(v, dict):
            return {k: value(x, k) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [value(x) for x in v]
        if isinstance(v, str):
            if v in params:
                return float(params[v])
            if key in ("dxf", "layer"):
                return v
            raise GeometryError(f"unknown parameter '{v}'")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v

    state = _State(root=root, params=params)
    for op in ops:
        a = {k: value(v, k) for k, v in op.args.items()}
        try:
            if op.kind == "sketch":
                state.sketches[op.name] = _sketch_plane(state, op, a)
                continue
            if op.kind in TOOLS:
                a = _with_sketch(state, op, a)
                built = TOOLS[op.kind](state, a)
                tool, names = built[0], built[1]
                bodies = built[2] if len(built) > 2 else None  # an import's part names
                state.tools[op.name] = (op.kind, a)
                mode = a.get("mode", "add" if state.shape else "new")
                _join(state, op, tool, names, mode, bodies)
            elif op.kind in MODIFIERS:
                MODIFIERS[op.kind](state, op, a)
            else:
                raise GeometryError(f"unknown operation '{op.kind}'")
        except GeometryError:
            raise
        except Exception as exc:  # noqa: BLE001 - OCCT errors carry little; say where
            raise GeometryError(f"step '{op.name}' ({op.kind}) failed: {exc}") from exc
        if state.shape is None or state.shape.IsNull():
            raise GeometryError(f"step '{op.name}' ({op.kind}) left no shape")
    if state.shape is None:
        raise GeometryError("no solid yet: extrude or revolve a sketch, or add a primitive")
    # Keep only what is in the final shape (OCCT may report intermediates).
    names, edges = _split_names(state.names, state.shape)
    solids = _subshapes(state.shape, TopAbs_SOLID)
    bodies = {
        n: [b for b in bs if any(b.IsSame(g) for g in solids)] for n, bs in state.bodies.items()
    }
    return NamedShape(state.shape, names, {n: b for n, b in bodies.items() if b}, edges)


def _split_names(names: Names, shape) -> tuple[Names, Names]:
    """Face names and edge names (both ride in one table through the operations, which
    carry any sub-shape alike), each kept to what is in `shape`."""
    out: dict[int, Names] = {TopAbs_FACE: {}, TopAbs_EDGE: {}}
    final = {k: _subshapes(shape, k) for k in out}
    for n, shapes in names.items():
        for kind, table in out.items():
            kept = [
                x for x in shapes if x.ShapeType() == kind and any(x.IsSame(g) for g in final[kind])
            ]
            if kept:
                table[n] = kept
    return out[TopAbs_FACE], out[TopAbs_EDGE]


@dataclass
class _State:
    shape: TopoDS_Shape | None = None
    names: Names = field(default_factory=dict)
    bodies: Names = field(default_factory=dict)
    tools: dict[str, tuple[str, dict]] = field(default_factory=dict)  # op name -> (kind, args)
    root: Any = None
    params: dict[str, float] = field(default_factory=dict)  # for sketches' dimensions
    sketches: dict[str, dict] = field(default_factory=dict)  # sketch step -> its profile args


# --- sketches -------------------------------------------------------------------------------

# A face's normal axis -> the sketch plane that has it, and the offset's coordinate.
FACE_PLANES = {2: "xy", 0: "yz", 1: "xz"}


def _sketch_plane(state, op, a) -> dict:
    """A sketch step's profile arguments: its sketch, on its plane at its offset, or on
    a named planar face (whose normal must lie along an axis, for now)."""
    if "sketch" not in a or not isinstance(a["sketch"], dict):
        raise GeometryError(f"sketch '{op.name}' has no sketch")
    plane, offset = a.get("plane", "xy"), a.get("offset", 0.0)
    if a.get("face"):
        faces = state.names.get(a["face"])
        if not faces:
            raise GeometryError(f"sketch '{op.name}': no face '{a['face']}' to sketch on")
        surface = BRepAdaptor_Surface(faces[0])
        if surface.GetType() != GeomAbs_Plane:
            raise GeometryError(f"sketch '{op.name}': face '{a['face']}' is not flat")
        d = surface.Plane().Axis().Direction()
        normal = [d.X(), d.Y(), d.Z()]
        axis = max(range(3), key=lambda i: abs(normal[i]))
        if abs(abs(normal[axis]) - 1.0) > 1e-9:
            raise GeometryError(
                f"sketch '{op.name}': face '{a['face']}' is not square to an axis (not yet)"
            )
        plane = FACE_PLANES[axis]
        offset = signature(faces[0])[2][axis] + offset
    return {"profile": {"sketch": a["sketch"]}, "plane": plane, "offset": offset}


def _with_sketch(state, op, a) -> dict:
    """A tool whose profile names a sketch step gets that sketch, its plane and offset."""
    profile = a.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("sketch"), str):
        name = profile["sketch"]
        if name not in state.sketches:
            raise GeometryError(f"{op.kind} '{op.name}': no earlier sketch '{name}'")
        return {**a, **state.sketches[name]}
    return a


# --- tools: bodies with their own face names ---------------------------------------------


def _axis_dir(a, default="z"):
    axis = a.get("axis", default)
    if isinstance(axis, (list, tuple)):
        return gp_Dir(*axis)
    return {"x": gp_Dir(1, 0, 0), "y": gp_Dir(0, 1, 0), "z": gp_Dir(0, 0, 1)}[axis]


def _ends(shape, base: gp_Pnt, direction: gp_Dir, length: float) -> dict[str, list]:
    """A solid of revolution's faces: the curved one(s) `wall`, the flat ends by position."""
    out: dict[str, list] = {"wall": [], "start": [], "end": []}
    d = (direction.X(), direction.Y(), direction.Z())
    for f in _faces(shape):
        if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane:
            out["wall"].append(f)
            continue
        c = signature(f)[2]
        along = sum((c[i] - (base.X(), base.Y(), base.Z())[i]) * d[i] for i in range(3))
        out["end" if along > 0.5 * length else "start"].append(f)
    return {k: v for k, v in out.items() if v}


def _box(state, a):
    origin = (a.get("x0", 0.0), a.get("y0", 0.0), a.get("z0", 0.0))
    size = (a["dx"], a["dy"], a["dz"])
    shape = BRepPrimAPI_MakeBox(gp_Pnt(*origin), *size).Shape()
    # Name each side by its outward direction ("-x" ... "+z"), from geometry:
    # OCCT's Left/Front/... faces do not follow the axes as their names suggest.
    centre = [o + d / 2 for o, d in zip(origin, size, strict=True)]
    names = {}
    for f in _faces(shape):
        c = signature(f)[2]
        offsets = [(c[i] - centre[i]) / size[i] for i in range(3)]
        axis = max(range(3), key=lambda i: abs(offsets[i]))
        names[f"{'-+'[offsets[axis] > 0]}{'xyz'[axis]}"] = [f]
    return shape, names


def _cylinder(state, a):
    base, d = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)), _axis_dir(a)
    shape = BRepPrimAPI_MakeCylinder(gp_Ax2(base, d), a["r"], a["h"]).Shape()
    return shape, _ends(shape, base, d, a["h"])


def _cone(state, a):
    base, d = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)), _axis_dir(a)
    shape = BRepPrimAPI_MakeCone(gp_Ax2(base, d), a["r1"], a["r2"], a["h"]).Shape()
    return shape, _ends(shape, base, d, a["h"])


def _sphere(state, a):
    centre = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
    shape = BRepPrimAPI_MakeSphere(centre, a["r"]).Shape()
    return shape, {"surface": _faces(shape)}


def _extrude(state, a):
    from puzzle.geometry.profile import NORMALS, profile_face

    plane = a.get("plane", "xy")
    face, edges, labels = profile_face(
        a["profile"], plane, a.get("offset", 0.0), state.root, state.params
    )
    n = NORMALS[plane]
    mk = BRepPrimAPI_MakePrism(face, gp_Vec(*(c * a["distance"] for c in n)))
    shape = mk.Shape()
    names = {"start": _faces(mk.FirstShape()), "end": _faces(mk.LastShape())}
    for label, e in zip(labels, edges, strict=True):
        names[label] = names.get(label, []) + _list(mk.Generated(e))
    return shape, {k: v for k, v in names.items() if v}


def _revolve(state, a):
    from puzzle.geometry.profile import PLANES, profile_face

    plane, offset = a.get("plane", "xy"), a.get("offset", 0.0)
    face, edges, labels = profile_face(a["profile"], plane, offset, state.root, state.params)
    local = {"u": (1, 0), "v": (0, 1)}[a.get("axis", "v")]
    origin = PLANES[plane](0.0, 0.0, offset)
    direction = PLANES[plane](*local, 0.0)
    angle = a.get("angle", 360.0)
    mk = BRepPrimAPI_MakeRevol(
        face, gp_Ax1(gp_Pnt(*origin), gp_Dir(*direction)), math.radians(angle)
    )
    shape = mk.Shape()
    names = {}
    for label, e in zip(labels, edges, strict=True):
        names[label] = names.get(label, []) + _list(mk.Generated(e))
    if angle < 360.0:
        names["start"], names["end"] = _faces(mk.FirstShape()), _faces(mk.LastShape())
    return shape, {k: v for k, v in names.items() if v}


def _hole(state, a):
    """A through hole along `axis` (default z): a cylinder spanning the part."""
    if state.shape is None:
        raise GeometryError("a hole needs a part to cut")
    d = _axis_dir(a)
    i = [abs(d.X()), abs(d.Y()), abs(d.Z())].index(1.0)
    b = _bounds(state.shape)
    lo, hi = b[i], b[i + 3]
    margin = 0.1 * (hi - lo)  # relative: the model may be in mm or m
    centre = [a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)]
    if i == 2:
        centre = [a["x"], a["y"], lo - margin]
    else:
        centre[i] = lo - margin
    tool = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*centre), gp_Dir(*[1.0 if k == i else 0.0 for k in range(3)])),
        a["r"],
        hi - lo + 2 * margin,
    ).Shape()
    a.setdefault("mode", "cut")
    return tool, {"wall": _lateral(tool)}


def _import(state, a):
    """A CAD file as the part: faces `face-N` in file order, bodies by STEP part name."""
    from pathlib import Path

    from puzzle.geometry.prep import read_cad

    file = Path(a["file"])
    if not file.is_absolute() and state.root is not None:
        file = Path(state.root) / file
    shape, parts = read_cad(file, a.get("unit"), a.get("scale", 1.0))
    names = {f"face-{i}": [f] for i, f in enumerate(_faces(shape), start=1)}
    if not _subshapes(shape, TopAbs_SOLID):  # flat faces (ADR 0048): edges by file order too
        names |= {f"edge-{i}": [e] for i, e in enumerate(_subshapes(shape, TopAbs_EDGE), 1)}
    if a.get("flat"):  # ADR 0048 round 3: flat faces laid on the XY plane for a 2D model
        from puzzle.geometry.planar import lay_flat

        shape, names = lay_flat(shape, names)
    return shape, names, parts


def _polyline(state, a):
    """A line body (ADR 0027 beams): straight segments through the points (m). It has
    no faces; its edges and points are named by position (edge-1, point-1, ...)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon

    points = a.get("points") or []
    if len(points) < 2:
        raise GeometryError("a polyline needs two points or more")
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        x, y, z = (list(p) + [0.0, 0.0, 0.0])[:3]
        poly.Add(gp_Pnt(float(x), float(y), float(z)))
    if a.get("closed"):
        poly.Close()
    return poly.Wire(), {}


def _frame(state, a):
    """A frame of line members (ADR 0079, `frames.lines`): an edge per member between
    its nodes, members at a node sharing its vertex (joined there); each member's edge
    named by the member, each group's edges by the group (a DXF's layer)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.frames.lines import FrameError, from_args

    try:
        lines = from_args(a, state.root)
    except FrameError as exc:
        raise GeometryError(str(exc)) from None
    vertices = {
        n: BRepBuilderAPI_MakeVertex(gp_Pnt(*xyz)).Vertex() for n, xyz in lines.nodes.items()
    }
    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    names: Names = {}
    for m in lines.members:
        edge = BRepBuilderAPI_MakeEdge(vertices[m.i], vertices[m.j]).Edge()
        builder.Add(compound, edge)
        names[m.name] = [edge]
        if m.group:
            names.setdefault(m.group, []).append(edge)
    return compound, names


def _sheet(state, a):
    """A flat sheet body (ADR 0027 shells): the polygon through the points (m), closed.
    It has one face, "face"; its edges are named by that face (face, face#2, ...)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon

    points = a.get("points") or []
    if len(points) < 3:
        raise GeometryError("a sheet needs three points or more")
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        x, y, z = (list(p) + [0.0, 0.0, 0.0])[:3]
        poly.Add(gp_Pnt(float(x), float(y), float(z)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire(), True)
    if not face.IsDone():
        raise GeometryError("the sheet's points must lie in one plane")
    shape = face.Face()
    return shape, {"face": [shape]}


def _face(state, a):
    """A flat region from a profile (ADR 0048 2D models, or a sheet for shells): "face",
    and each side named after the sketch's edge ("<op>/outer", ...; typed profiles name
    them side-1, side-2, ...). A side keeps its name through later operations."""
    from puzzle.geometry.profile import profile_face

    face, edges, labels = profile_face(
        a["profile"], a.get("plane", "xy"), a.get("offset", 0.0), state.root, state.params
    )
    names: Names = {"face": _faces(face)}
    for label, e in zip(labels, edges, strict=True):
        names.setdefault(label, []).append(e)
    if len(names["face"]) > 1:  # several regions (a DXF's): fused so touching ones share edges
        return _share_edges(face, names)
    return face, names


def _share_edges(shape, names: Names):
    """Regions side by side made to share the edges where they touch, each kept a face
    of its own (ADR 0048 round 3), the names carried."""
    algo = BRepAlgoAPI_BuilderAlgo()
    args = TopTools_ListOfShape()
    for f in _faces(shape):
        args.Append(f)
    algo.SetArguments(args)
    algo.Build()
    if not algo.IsDone():
        raise GeometryError("the regions could not be joined")
    return algo.Shape(), _carry(algo, names)


def _section(state, a):
    """A section of another part (ADR 0048): its cut by a plane, laid in the XY plane at
    z = 0 (the plane's own u and v becoming x and y), the half at u >= 0 only with
    `half` (an axisymmetric section: the part's axis on the plane's v). The region is
    "face"; each edge is named after the 3D face it cuts ("<op>/Tube/wall"), so loads
    on it follow the 3D part through its edits."""
    from OCP.gp import gp_Ax3

    from puzzle.geometry.profile import NORMALS, PLANES

    source = a.get("_part") or {}
    if "ops" in source:
        part = _regenerate(source["ops"], state.params, state.root)
        solid, names = part.shape, part.names
    else:
        solid, names, _ = _import(state, {"file": source.get("file", "")})
    plane, offset = a.get("plane", "xy"), float(a.get("offset", 0.0))
    origin = gp_Pnt(*PLANES[plane](0.0, 0.0, offset))
    frame = gp_Ax3(origin, gp_Dir(*NORMALS[plane]), gp_Dir(*PLANES[plane](1.0, 0.0, 0.0)))
    size = 10 * max(_size(solid), 1e-9)
    low = 0.0 if a.get("half") else -size
    cutter = BRepBuilderAPI_MakeFace(gp_Pln(frame), low, size, -size, size).Face()
    common = BRepAlgoAPI_Common(solid, cutter)
    common.Build()
    if not common.IsDone() or not _faces(common.Shape()):
        raise GeometryError("the plane does not cut the part")
    edges: Names = {}
    for name, faces in names.items():
        made = [
            e for f in faces for e in _list(common.Generated(f)) if e.ShapeType() == TopAbs_EDGE
        ]
        if made:
            edges[name] = made
    flat = gp_Trsf()
    flat.SetTransformation(frame)  # into the plane's own (u, v, w): w = 0
    move = BRepBuilderAPI_Transform(common.Shape(), flat, True)
    shape = move.Shape()
    out: Names = {"face": _faces(shape)}
    for name, es in edges.items():
        out[name] = [_static(TopoDS, "Edge")(move.ModifiedShape(e)) for e in es]
    return shape, out


def _curved_sheet(state, a):
    """A cylindrical panel (ADR 0027 shells): radius r, `length` along x from (x0, y0,
    z0) on its axis, spanning `angle` degrees centred on +z (a barrel roof)."""
    import math

    from OCP.gp import gp_Dir

    half = math.radians(a["angle"]) / 2
    base = gp_Pnt(a.get("x0", 0.0), a.get("y0", 0.0), a.get("z0", 0.0))
    frame = gp_Ax2(base, gp_Dir(1, 0, 0), gp_Dir(0, math.sin(half), math.cos(half)))
    solid = BRepPrimAPI_MakeCylinder(frame, a["r"], a["length"], 2 * half).Shape()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    wall = [f for f in _faces(solid) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder]
    if len(wall) != 1:
        raise GeometryError("the curved sheet could not be made")
    return wall[0], {"surface": wall}


TOOLS = {
    "polyline": _polyline,
    "frame": _frame,
    "sheet": _sheet,
    "face": _face,
    "section": _section,
    "curved_sheet": _curved_sheet,
    "import": _import,
    "box": _box,
    "cylinder": _cylinder,
    "cone": _cone,
    "sphere": _sphere,
    "extrude": _extrude,
    "revolve": _revolve,
    "hole": _hole,
    "bolt": lambda state, a: _bolt(state, a),
    "nut": lambda state, a: _nut(state, a),
    "washer": lambda state, a: _washer(state, a),
    "weld_bead": lambda state, a: _weld_bead(state, a),
}


def _weld_bead(state, a):
    """A modelled fillet's bead (ADR 0078 step 4): its section (the root at the path,
    the legs `a` and `b` as vectors from it, m) swept along the path `points`: two
    points a prism, more a ruled loft through a section at each (`a` and `b` then one
    vector each, or one for all), `closed` back to the first. With `root` (m, along each
    leg from the path) the section's root corner is cut back to a small `root` face, so
    the two legs share no edge: tied to the two parts, they would otherwise join them at
    the root line, which a fillet's unfused root doesn't. Faces: `leg_a` (on the path's
    part), `leg_b` (on the other part), `face` (the bead's exposed face), `root`, and
    `start`, `end` on an open path."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

    points = [list(map(float, p)) for p in (a.get("points") or [])]
    closed = bool(a.get("closed", False))
    if len(points) < 2 or (closed and len(points) < 3):
        raise GeometryError("a weld bead runs along two points or more (three if closed)")

    def per_point(key):
        v = a[key]
        rows = [list(map(float, x)) for x in v] if isinstance(v[0], (list, tuple)) else None
        if rows is not None and len(rows) != len(points):
            raise GeometryError(f"a weld bead's '{key}': one vector, or one per point")
        return rows or [list(map(float, v))] * len(points)

    va, vb = per_point("a"), per_point("b")
    gap = float(a.get("root", 0.0))

    def section(p, la, lb):
        def at(v, share):
            return gp_Pnt(*(p[c] + share * v[c] for c in range(3)))

        na, nb = (math.sqrt(sum(c * c for c in v)) for v in (la, lb))
        if gap >= 0.5 * min(na, nb):
            raise GeometryError("a weld bead's root gap must be under half its legs")
        if gap > 0:
            return [at(la, gap / na), at(la, 1.0), at(lb, 1.0), at(lb, gap / nb)]
        return [gp_Pnt(*p), at(la, 1.0), at(lb, 1.0)]

    sides = ["leg_a", "face", "leg_b", "root"] if gap > 0 else ["leg_a", "face", "leg_b"]
    corners = [section(p, la, lb) for p, la, lb in zip(points, va, vb, strict=True)]

    def wire(cs):
        poly = BRepBuilderAPI_MakePolygon()
        for c in cs:
            poly.Add(c)
        poly.Close()
        return poly.Wire()

    names: Names = {}
    if len(points) == 2 and not closed:
        face = BRepBuilderAPI_MakeFace(wire(corners[0]), True)
        if not face.IsDone():
            raise GeometryError("a weld bead's legs must not be parallel")
        mk = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(gp_Pnt(*points[0]), gp_Pnt(*points[1])))
        names["start"], names["end"] = [TopoDS.Face(mk.FirstShape())], [TopoDS.Face(mk.LastShape())]
    else:
        # A ruled solid per segment, fused (their shared faces go): one loft closed on
        # itself kept its seam's two caps inside the solid, which Gmsh can't mesh.
        rings = corners + ([corners[0]] if closed else [])
        shape = None
        for k in range(len(rings) - 1):
            mk = BRepOffsetAPI_ThruSections(True, True)
            mk.AddWire(wire(rings[k]))
            mk.AddWire(wire(rings[k + 1]))
            mk.Build()
            if not mk.IsDone():
                raise GeometryError("the weld bead's sections could not be lofted")
            shape = mk.Shape() if shape is None else BRepAlgoAPI_Fuse(shape, mk.Shape()).Shape()
        mk = None
        names = {}
    if mk is not None:
        shape = mk.Shape()
    # Each side of the section sweeps a face per segment: named by the side whose
    # middle, averaged over the segment's two ends, is nearest the face's centre.
    rings = corners + ([corners[0]] if closed else [])
    mids = []
    for k in range(len(rings) - 1):
        for n, side in enumerate(sides):
            m = [0.0, 0.0, 0.0]
            for cs in (rings[k], rings[k + 1]):
                p0, p1 = cs[n], cs[(n + 1) % len(sides)]
                for c, get in enumerate((gp_Pnt.X, gp_Pnt.Y, gp_Pnt.Z)):
                    m[c] += (get(p0) + get(p1)) / 4
            mids.append((side, gp_Pnt(*m)))
    if mk is None and not closed:  # the open path's two ends, by their sections' centres
        for key, cs in (("start", corners[0]), ("end", corners[-1])):
            m = [sum(get(c) for c in cs) / len(cs) for get in (gp_Pnt.X, gp_Pnt.Y, gp_Pnt.Z)]
            mids.append((key, gp_Pnt(*m)))
    for f in _faces(shape):
        if any(f.IsSame(c) for k in ("start", "end") for c in names.get(k, [])):
            continue
        props = GProp_GProps()
        _static(BRepGProp, "SurfaceProperties")(f, props)
        centre = props.CentreOfMass()
        side = min(mids, key=lambda sm: centre.Distance(sm[1]))[0]
        names.setdefault(side, []).append(TopoDS.Face(f))
    if not all(k in names for k in sides):
        raise GeometryError("the weld bead's faces could not be named")
    return shape, names


def _nut(state, a):
    """A nut for a modelled bolt (ADR 0077, `puzzle.solid_bolts`)."""
    from puzzle.solid_bolts import nut_tool

    return nut_tool(state, a)


def _washer(state, a):
    """A plain washer for a modelled bolt (ADR 0077, `puzzle.solid_bolts`)."""
    from puzzle.solid_bolts import washer_tool

    return washer_tool(state, a)


def _bolt(state, a):
    """A standard bolt from the fastener tables (ADR 0077, `puzzle.solid_bolts`)."""
    from puzzle.solid_bolts import bolt_tool

    return bolt_tool(state, a)


def _join(state, op, tool, tool_names: Names, mode: str, tool_bodies=None) -> None:
    """Combine a tool body with the part; its faces are named `<op>/<name>`."""
    mine = {f"{op.name}/{k}": v for k, v in tool_names.items()}
    body = op.args.get("body", "Body")
    if mode == "new" or state.shape is None:
        state.shape, state.names = tool, {**state.names, **mine} if state.shape is None else mine
        solids = _subshapes(tool, TopAbs_SOLID)
        state.bodies = {**({body: solids} if solids else {}), **(tool_bodies or {})}
        return
    # "separate" (a 2D model's regions, ADR 0048): fused so they share their edges, but
    # not merged into one face, so each stays a region of its own (own material).
    algo = {"add": BRepAlgoAPI_Fuse, "separate": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut,
            "intersect": BRepAlgoAPI_Common}.get(mode)  # fmt: skip
    if algo is None:
        raise GeometryError(f"mode must be new, add, separate, cut or intersect, not '{mode}'")
    run = algo(state.shape, tool)
    if not run.IsDone():
        raise GeometryError(f"the {mode} in '{op.name}' failed")
    tool_solids = {f"{body}": _subshapes(tool, TopAbs_SOLID)} if mode == "add" else {}
    state.names = _carry(run, {**state.names, **mine})
    bodies = _carry(
        run, {**state.bodies, **{k: v for k, v in tool_solids.items() if k not in state.bodies}}
    )
    if mode == "add" and body in state.bodies:  # the tool joined the existing body
        bodies[body] = _subshapes(run.Shape(), TopAbs_SOLID)
    state.shape, state.bodies = run.Shape(), bodies
    if mode == "add":
        _unify(state)


def _unify(state) -> None:
    """Merge faces a fuse left split along the seam (two coplanar halves of a side), so
    the mesh sees one face; a merged face keeps every name it had."""
    usd = ShapeUpgrade_UnifySameDomain(state.shape, True, True, False)
    usd.Build()
    history = usd.History()
    state.names = _carry_history(history, state.names)
    state.bodies = _carry_history(history, state.bodies)
    state.shape = usd.Shape()


# --- modifiers: change the part ----------------------------------------------------------


def _fillet(state, op, a):
    mk = BRepFilletAPI_MakeFillet(state.shape)
    edges = _edges_by_rule(state, a.get("rule", {}))
    if not edges:
        raise GeometryError(f"fillet '{op.name}': its rule matches no edges")
    for e in edges:
        mk.Add(a["r"], e)
    _finish_edges(state, op, mk, edges)


def _chamfer(state, op, a):
    mk = BRepFilletAPI_MakeChamfer(state.shape)
    edges = _edges_by_rule(state, a.get("rule", {}))
    if not edges:
        raise GeometryError(f"chamfer '{op.name}': its rule matches no edges")
    for e in edges:
        mk.Add(a["d"], e)
    _finish_edges(state, op, mk, edges)


def _finish_edges(state, op, mk, edges) -> None:
    mk.Build()
    if not mk.IsDone():
        raise GeometryError(f"{op.kind} '{op.name}' failed (radius too large for the part?)")
    names = _carry(mk, state.names)
    for i, e in enumerate(edges, start=1):
        gen = _list(mk.Generated(e))
        if gen:
            names[f"{op.name}/edge-{i}"] = gen
    state.bodies = _carry(mk, state.bodies)
    state.names, state.shape = names, mk.Shape()


def _transform(state, trsf) -> None:
    mk = BRepBuilderAPI_Transform(state.shape, trsf, True)
    state.names = {n: _list(mk.ModifiedShape(f) for f in fs) for n, fs in state.names.items()}
    state.bodies = {n: _list(mk.ModifiedShape(b) for b in bs) for n, bs in state.bodies.items()}
    state.shape = mk.Shape()


def _move(state, op, a):
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(a.get("dx", 0.0), a.get("dy", 0.0), a.get("dz", 0.0)))
    _transform(state, trsf)


def _rotate(state, op, a):
    trsf = gp_Trsf()
    point = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
    trsf.SetRotation(gp_Ax1(point, _axis_dir(a)), math.radians(a["angle"]))
    _transform(state, trsf)


def _pattern_moves(state, a) -> list:
    """Each copy's transformation after the original (ADR 0062 phase 3 adds the last
    three): circular (`angle` per step about `axis` through x, y, z); linear (dx, dy, dz
    per step, and a second direction dx2, dy2, dz2 for `count2` rows); at `positions`
    (offsets from the original, e.g. a Points operator's); or `along` an earlier
    polyline or spline, `count` copies at equal lengths from its start."""
    out = []
    if a.get("positions"):
        for p in a["positions"][1:] if a.get("skip_first", True) else a["positions"]:
            x, y, z = (list(p) + [0.0, 0.0, 0.0])[:3]
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(float(x), float(y), float(z)))
            out.append(trsf)
        return out
    count = int(a.get("count", 2))
    if a.get("along"):
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_AbscissaPoint

        from puzzle.geometry.sweeps import path_wire

        curve = BRepAdaptor_CompCurve(path_wire(state, {"along": a["along"]}))
        length = GCPnts_AbscissaPoint.Length_s(curve)
        first = curve.Value(curve.FirstParameter())
        for i in range(1, count):
            at = GCPnts_AbscissaPoint(curve, length * i / (count - 1), curve.FirstParameter())
            p = curve.Value(at.Parameter())
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(first, p))
            out.append(trsf)
        return out
    rows = int(a.get("count2", 1))
    for j in range(rows):
        for i in range(count):
            if i == 0 and j == 0:
                continue
            trsf = gp_Trsf()
            if "angle" in a:
                point = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
                trsf.SetRotation(gp_Ax1(point, _axis_dir(a)), math.radians(a["angle"] * i))
            else:
                trsf.SetTranslation(gp_Vec(
                    a.get("dx", 0.0) * i + a.get("dx2", 0.0) * j,
                    a.get("dy", 0.0) * i + a.get("dy2", 0.0) * j,
                    a.get("dz", 0.0) * i + a.get("dz2", 0.0) * j))  # fmt: skip
            out.append(trsf)
    return out


def _pattern(state, op, a):
    """Copies of an earlier tool operation (`_pattern_moves`); copy i is named
    `<feature>#<i>`, from 2."""
    feature = op.args["feature"]
    if feature not in state.tools:
        raise GeometryError(f"pattern '{op.name}': no earlier tool operation '{feature}'")
    kind, fa = state.tools[feature]
    for i, trsf in enumerate(_pattern_moves(state, a), start=2):
        tool, names = TOOLS[kind](state, dict(fa))
        mk = BRepBuilderAPI_Transform(tool, trsf, True)
        moved = {n: _list(mk.ModifiedShape(f) for f in fs) for n, fs in names.items()}
        copy = Op(kind, f"{feature}#{i}", {"mode": fa.get("mode", "add")})
        _join(state, copy, mk.Shape(), moved, fa.get("mode", "add"))


def _split(state, op, a):
    """A plane splits the faces it crosses; a split face keeps its name on both pieces,
    and the face the cut leaves inside the part is "<op>/section"."""
    if "normal" in op.args:
        normal = gp_Dir(*a["normal"])
        origin = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
    else:
        axis = op.args["axis"]
        normal = _axis_dir({"axis": axis})
        origin = gp_Pnt(*(a["at"] if c == axis else 0.0 for c in "xyz"))
    size = 10 * max(_size(state.shape), 1e-9)
    plane = BRepBuilderAPI_MakeFace(gp_Pln(origin, normal), -size, size, -size, size)
    sp = BRepAlgoAPI_Splitter()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(state.shape)
    tools.Append(plane.Face())
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    state.names, state.bodies = _carry(sp, state.names), _carry(sp, state.bodies)
    state.shape = sp.Shape()
    # The cut face inside the part (a bolt's pretension section): "<op>/section".
    cut = _list(sp.Modified(plane.Face()))
    inside = [f for f in _faces(state.shape) if any(f.IsSame(c) for c in cut)]
    if inside:
        state.names[f"{op.name}/section"] = inside


def _imprint(state, op, a):
    """Bearing rings (ADR 0050): the circle of radius `r` about a hole's axis imprinted
    on the faces at both mouths of the hole (`hole`: the wall's face name), so the
    ring under a bolt's head or nut is a face of its own: "<op>/annulus" (both mouths'
    rings). The faces split keep their names. Nothing is cut: the solid is unchanged."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax3, gp_Circ

    walls = state.names.get(a["hole"])
    if not walls:
        raise GeometryError(f"imprint '{op.name}': no face '{a['hole']}'")
    surface = BRepAdaptor_Surface(walls[0])
    if surface.GetType() != GeomAbs_Cylinder:
        raise GeometryError(f"imprint '{op.name}': '{a['hole']}' is not a hole's wall")
    cyl = surface.Cylinder()
    ax = cyl.Axis()
    o, d = ax.Location(), ax.Direction()
    origin, axis = (o.X(), o.Y(), o.Z()), (d.X(), d.Y(), d.Z())
    r = float(a["r"])
    if r <= cyl.Radius():
        raise GeometryError(f"imprint '{op.name}': the ring must be wider than the hole")
    along = []
    for w in walls:  # the wall's extent along its axis: the two mouths
        exp = TopExp_Explorer(w, TopAbs_VERTEX)
        while exp.More():
            p = _static(TopoDS, "Vertex")(exp.Current())
            from OCP.BRep import BRep_Tool

            q = _static(BRep_Tool, "Pnt")(p)
            along.append(sum((c - c0) * k for c, c0, k in zip((q.X(), q.Y(), q.Z()), origin,
                                                               axis, strict=True)))  # fmt: skip
            exp.Next()
    if not along:
        raise GeometryError(f"imprint '{op.name}': '{a['hole']}' has no ends")
    disks = []
    for t in (min(along), max(along)):
        centre = gp_Pnt(*(c0 + t * k for c0, k in zip(origin, axis, strict=True)))
        frame = gp_Ax2(centre, gp_Dir(*axis))
        edge = BRepBuilderAPI_MakeEdge(gp_Circ(frame, r)).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        disks.append((BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(frame)), wire).Face(), centre))
    sp = BRepAlgoAPI_Splitter()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(state.shape)
    for disk, _c in disks:
        tools.Append(disk)
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    if not sp.IsDone():
        raise GeometryError(f"imprint '{op.name}' failed")
    state.names, state.bodies = _carry(sp, state.names), _carry(sp, state.bodies)
    state.shape = sp.Shape()
    tol = 1e-6 * max(_size(state.shape), 1e-9)
    rings = []
    for f in _faces(state.shape):  # the planar faces on a mouth's plane, inside the circle
        if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane:
            continue
        props = GProp_GProps()
        _static(BRepGProp, "SurfaceProperties")(f, props)
        c = props.CentreOfMass()
        for _disk, centre in disks:
            rel = (c.X() - centre.X(), c.Y() - centre.Y(), c.Z() - centre.Z())
            height = sum(x * k for x, k in zip(rel, axis, strict=True))
            radial = math.sqrt(max(sum(x * x for x in rel) - height**2, 0.0))
            if abs(height) < tol and radial < r and props.Mass() < math.pi * r**2:
                rings.append(f)
                break
    if not rings:
        raise GeometryError(f"imprint '{op.name}': no flat face at the hole's mouths to "
                            "imprint (the hole opens onto a curved face?)")  # fmt: skip
    state.names[f"{op.name}/annulus"] = rings


def _split_face(state, op, a):
    """A sketch projected along its plane's normal splits the faces it falls on
    (SpaceClaim's project-to-body): the faces named in `faces`, or by default those
    lying on the sketch's plane. Only faces are split; the solid is unchanged. The
    pieces inside the sketch's outline are "<op>/region"; split faces keep their names
    on every piece. `direction`: both (default), forward (along the normal) or backward,
    for a projection that would otherwise reach both sides of a curved part."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepFeat import BRepFeat_SplitShape

    from puzzle.geometry.profile import NORMALS, profile_face

    a = _with_sketch(state, op, a)
    if "profile" not in a:
        raise GeometryError(f"split_face '{op.name}' has no sketch to project")
    plane, offset = a.get("plane", "xy"), a.get("offset", 0.0)
    sketch, _edges, _labels = profile_face(a["profile"], plane, offset, state.root, state.params)
    n = NORMALS[plane]
    targets = _split_targets(state, op, a, sketch, n)
    reach = 4 * max(_size(state.shape), 1e-9) + abs(offset)
    back, ahead = {"both": (reach, reach), "forward": (0.0, reach), "backward": (reach, 0.0)}[
        a.get("direction", "both")
    ]
    shift = gp_Trsf()
    shift.SetTranslation(gp_Vec(*(-back * c for c in n)))
    start = BRepBuilderAPI_Transform(sketch, shift, True).Shape()
    prism = BRepPrimAPI_MakePrism(start, gp_Vec(*((back + ahead) * c for c in n))).Shape()
    splitter = BRepFeat_SplitShape(state.shape)
    crossed = []
    for f in targets:
        section = BRepAlgoAPI_Section(f, prism, False)
        section.ComputePCurveOn1(True)
        section.Approximation(True)
        section.Build()
        edges = _subshapes(section.Shape(), TopAbs_EDGE) if section.IsDone() else []
        for e in edges:
            splitter.Add(e, f)
        if edges:
            crossed.append(f)
    if not crossed:
        raise GeometryError(f"split_face '{op.name}': the sketch falls on none of its faces")
    splitter.Build()
    if not splitter.IsDone():
        raise GeometryError(f"split_face '{op.name}' failed")
    pieces = [p for f in crossed for p in (_list(splitter.Modified(f)) or [f])]
    state.names, state.bodies = _carry(splitter, state.names), _carry(splitter, state.bodies)
    state.shape = splitter.Shape()
    inside = [p for p in pieces if _projects_inside(p, sketch, n, offset)]
    if not inside:
        raise GeometryError(f"split_face '{op.name}': no face lies inside the sketch")
    state.names[f"{op.name}/region"] = [
        f for f in _faces(state.shape) if any(f.IsSame(p) for p in inside)
    ]


def _split_targets(state, op, a, sketch, n) -> list:
    """The faces a split_face works on: those it names, else those on the sketch's plane."""
    named = a.get("faces") or []
    if isinstance(named, dict):  # the step dialog's form: {"face": [...]}, or {} for the default
        named = named.get("face") or []
    if isinstance(named, str):
        named = [named]
    if named:
        out = []
        for name in named:
            faces = state.names.get(name)
            if not faces:
                raise GeometryError(f"split_face '{op.name}': no face '{name}'")
            out += faces
        return out
    level = sum(c * k for c, k in zip(signature(sketch)[2], n, strict=True))
    tol = 1e-6 * max(_size(state.shape), 1e-9)
    out = []
    for f in _faces(state.shape):
        surface = BRepAdaptor_Surface(f)
        if surface.GetType() != GeomAbs_Plane:
            continue
        d = surface.Plane().Axis().Direction()
        along = abs(d.X() * n[0] + d.Y() * n[1] + d.Z() * n[2])
        height = sum(c * k for c, k in zip(signature(f)[2], n, strict=True))
        if abs(along - 1.0) < 1e-9 and abs(height - level) < tol:
            out.append(f)
    if not out:
        raise GeometryError(
            f"split_face '{op.name}': no face lies on the sketch's plane; name the faces"
        )
    return out


def _projects_inside(face, sketch, n, offset) -> bool:
    """Whether a point on `face` (not its centre of mass, which may lie off a curved
    face) falls inside the sketch's outline when moved along `n` onto its plane."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN

    umin, umax, vmin, vmax = _static(BRepTools, "UVBounds")(face)
    surface = _static(BRep_Tool, "Surface")(face)
    level = sum(c * k for c, k in zip(signature(sketch)[2], n, strict=True))
    tol = 1e-7
    for i in range(1, 8):
        for j in range(1, 8):
            u, v = umin + (umax - umin) * i / 8, vmin + (vmax - vmin) * j / 8
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tol).State() != TopAbs_IN:
                continue
            p = surface.Value(u, v)
            xyz = (p.X(), p.Y(), p.Z())
            h = sum(c * k for c, k in zip(xyz, n, strict=True)) - level
            on = gp_Pnt(*(c - h * k for c, k in zip(xyz, n, strict=True)))
            return BRepClass_FaceClassifier(sketch, on, tol).State() == TopAbs_IN
    return False


def _enclosure(state, op, a):
    """An air (or fluid) box around the part, sharing the interface faces with it, so
    the mesh is conformal: body `<op name>`, its outer faces `<op name>/+x` ..."""
    b = _bounds(state.shape)
    span = max(b[3] - b[0], b[4] - b[1], b[5] - b[2])
    m = a.get("margin", a.get("factor", 0.5) * span)
    box, box_names = _box(
        state,
        {
            "x0": b[0] - m,
            "y0": b[1] - m,
            "z0": b[2] - m,
            "dx": b[3] - b[0] + 2 * m,
            "dy": b[4] - b[1] + 2 * m,
            "dz": b[5] - b[2] + 2 * m,
        },
    )
    algo = BRepAlgoAPI_BuilderAlgo()
    shapes = TopTools_ListOfShape()
    shapes.Append(state.shape)
    shapes.Append(box)
    algo.SetArguments(shapes)
    algo.Build()
    if not algo.IsDone():
        raise GeometryError(f"enclosure '{op.name}' failed")
    part_solids = [s for b in state.bodies.values() for s in b]
    names = _carry(algo, {**state.names, **{f"{op.name}/{k}": v for k, v in box_names.items()}})
    bodies = _carry(algo, state.bodies)
    result = algo.Shape()
    kept = [s for bs in bodies.values() for s in bs]
    bodies[op.name] = [
        s for s in _subshapes(result, TopAbs_SOLID) if not any(s.IsSame(k) for k in kept)
    ]
    del part_solids
    state.shape, state.names, state.bodies = result, names, bodies


def _heal(state, op, a):
    from puzzle.geometry.prep import heal

    tolerance = a.get("tolerance", 1e-6 * max(_size(state.shape), 1e-9))
    healed, mapped = heal(state.shape, tolerance)
    state.names = {n: [p for f in fs for p in mapped(f)] for n, fs in state.names.items()}
    solids = _subshapes(healed, TopAbs_SOLID)
    if len(state.bodies) <= 1:  # one body (or none yet): whatever solids the healing made
        state.bodies = {next(iter(state.bodies), "Body"): solids} if solids else {}
    else:
        state.bodies = {n: [p for b in bs for p in mapped(b)] for n, bs in state.bodies.items()}
    state.names = {n: fs for n, fs in state.names.items() if fs}
    state.shape = healed


def _remove(state, op, a):
    from puzzle.geometry.prep import faces_by_rule, remove_faces

    faces = faces_by_rule(state, a.get("rule", {}))
    if not faces:
        raise GeometryError(f"remove '{op.name}': its rule matches no faces")
    algo = remove_faces(state.shape, faces)
    state.names, state.bodies = _carry(algo, state.names), _carry(algo, state.bodies)
    state.shape = algo.Shape()


def _keep(state, op, a):
    """Keep some bodies, drop the rest (one part per body of an imported assembly):
    `solids`, 1-based in the shape's order (the plain import's body-N), or `bodies` by
    name; the kept ones become the body `body` (default "Body"). Face names are kept."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.geometry.naming import _Index

    solids = _subshapes(state.shape, TopAbs_SOLID)
    if a.get("solids"):
        try:
            chosen = [solids[int(i) - 1] for i in a["solids"]]
        except (IndexError, ValueError) as exc:
            raise GeometryError(
                f"keep '{op.name}': no solid {a['solids']} ({len(solids)} here)"
            ) from exc
    else:
        wanted = a.get("bodies") or []
        if isinstance(wanted, str):  # typed in the step's form
            wanted = [b.strip() for b in wanted.split(",") if b.strip()]
        missing = [b for b in wanted if b not in state.bodies]
        if missing or not wanted:
            raise GeometryError(f"keep '{op.name}': no body {missing or wanted}")
        chosen = [s for b in wanted for s in state.bodies[b]]
    if not chosen:
        raise GeometryError(f"keep '{op.name}': nothing to keep")
    held = _Index()
    for solid in chosen:
        for f in _subshapes(solid, TopAbs_FACE):
            held.get(f).append(True)
    names = {n: [f for f in fs if held.get(f)] for n, fs in state.names.items()}
    if len(chosen) == 1:
        shape = chosen[0]
    else:
        builder, shape = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(shape)
        for solid in chosen:
            builder.Add(shape, solid)
    state.shape = shape
    state.names = {n: fs for n, fs in names.items() if fs}
    state.bodies = {a.get("body", "Body"): chosen}


def _fragment(state, op, a):
    from puzzle.geometry.prep import fragment

    algo = fragment(state.shape)
    state.names, state.bodies = _carry(algo, state.names), _carry(algo, state.bodies)
    state.shape = algo.Shape()


MODIFIERS = {
    "heal": _heal,
    "remove": _remove,
    "fragment": _fragment,
    "keep": _keep,
    "fillet": _fillet,
    "chamfer": _chamfer,
    "move": _move,
    "rotate": _rotate,
    "pattern": _pattern,
    "split": _split,
    "imprint": _imprint,
    "split_face": _split_face,
    "enclosure": _enclosure,
}


def _more_operations() -> None:
    """ADR 0065's operations (splines, sweeps, pipes, lofts, mirror, scale), kept in
    their own module."""
    from puzzle.geometry import subd, sweeps

    TOOLS.update(sweeps.TOOLS)
    TOOLS["subd"] = subd._subd
    MODIFIERS.update(sweeps.MODIFIERS)
    # ADR 0048 round 2: 2D shapes, and fillets and chamfers of a flat part's corners.
    from puzzle.geometry import planar

    TOOLS.update(planar.TOOLS)
    for kind in ("fillet", "chamfer"):
        MODIFIERS[kind] = planar._corner_op(kind, MODIFIERS[kind])


_more_operations()


# --- naming through operations --------------------------------------------------------


def _carry(algo, names: Names) -> Names:
    """Each name's faces after `algo`: unchanged, modified (maybe several), or gone."""
    out: Names = {}
    for name, faces in names.items():
        kept = []
        for f in faces:
            if algo.IsDeleted(f):
                continue
            modified = _list(algo.Modified(f))
            kept += modified or [f]
        if kept:
            out[name] = kept
    return out


def _carry_history(history, names: Names) -> Names:
    """As `_carry`, for a BRepTools_History (ShapeUpgrade_UnifySameDomain)."""
    out: Names = {}
    for name, shapes in names.items():
        kept = []
        for f in shapes:
            if history.IsRemoved(f):
                continue
            modified = _list(history.Modified(f))
            kept += modified or [f]
        unique = []
        for k in kept:
            if not any(k.IsSame(u) for u in unique):
                unique.append(k)
        if unique:
            out[name] = unique
    return out


def _list(shapes: TopTools_ListOfShape) -> list:
    down = {
        TopAbs_FACE: "Face",
        TopAbs_SOLID: "Solid",
        TopAbs_EDGE: "Edge",
        TopAbs_VERTEX: "Vertex",
    }
    return [_static(TopoDS, down[s.ShapeType()])(s) if s.ShapeType() in down else s for s in shapes]


def _subshapes(shape, kind) -> list:
    out, ex = [], TopExp_Explorer(shape, kind)
    while ex.More():
        sub = ex.Current()
        if not any(sub.IsSame(g) for g in out):
            out.append(_list([sub])[0])
        ex.Next()
    return out


def _size(shape) -> float:
    b = _bounds(shape)
    return math.dist(b[:3], b[3:])


def _faces(shape) -> list:
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        f = _static(TopoDS, "Face")(ex.Current())
        if not any(f.IsSame(g) for g in out):
            out.append(f)
        ex.Next()
    return out


def _lateral(cylinder) -> list:
    return [f for f in _faces(cylinder) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder]


def _bounds(shape) -> tuple[float, float, float, float, float, float]:
    """The tight bounding box: OCCT's default one is padded by ~1e-7 (CLAUDE.md)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    _static(BRepBndLib, "AddOptimal")(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()  # OCP 8 cannot return Get()'s struct
    return lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()


def _edges_by_rule(state, rule: dict) -> list:
    """Edges matching a rule, in a stable order (by midpoint). Rules combine:

    {"parallel": "z"}            straight edges along an axis
    {"outer": True}              ... on the part's outer bounding box (corner edges)
    {"face": "Plate/+z"}         edges of the named face(s) (a name or a list)
    {"between": ["A", "B"]}      edges shared by faces of both names
    {"curved": True}             circles and other curves only
    {"all": True}                every edge
    {"edges": ["A|B", ...]}      edges picked by name (the faces that meet there, G3)
    """
    shape = state.shape
    if "edges" in rule:
        from puzzle.geometry.naming import name_all

        faces, edges = _split_names(state.names, shape)
        named = name_all(NamedShape(shape, faces, state.bodies, edges)).of["edge"]
        missing = [n for n in rule["edges"] if n not in named]
        if missing:
            raise GeometryError(f"no edge {', '.join(missing)} on the part at this step")
        return [e for n in rule["edges"] for e in named[n]]
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    tol = max(1e-6 * _size(shape), 1e-9)
    candidates = _subshapes(shape, TopAbs_EDGE)
    if "face" in rule:
        named = rule["face"] if isinstance(rule["face"], list) else [rule["face"]]
        faces = [f for n in named for f in state.names.get(n, [])]
        candidates = [e for e in candidates if any(_on(e, f) for f in faces)]
    if "between" in rule:
        a, b = ([f for f in state.names.get(n, [])] for n in rule["between"])
        candidates = [
            e for e in candidates if any(_on(e, f) for f in a) and any(_on(e, f) for f in b)
        ]
    found = []
    for e in candidates:
        c = BRepAdaptor_Curve(e)
        if rule.get("curved") and c.GetType() == GeomAbs_Line:
            continue
        if "parallel" in rule:
            if c.GetType() != GeomAbs_Line:
                continue
            axis = "xyz".index(rule["parallel"])
            d = c.Line().Direction()
            if abs((d.X(), d.Y(), d.Z())[axis]) < 1 - 1e-9:
                continue
        mid = c.Value(0.5 * (c.FirstParameter() + c.LastParameter()))
        if rule.get("outer"):
            on = [
                min(abs(mid.X() - x0), abs(mid.X() - x1)) < tol,
                min(abs(mid.Y() - y0), abs(mid.Y() - y1)) < tol,
                min(abs(mid.Z() - z0), abs(mid.Z() - z1)) < tol,
            ]
            if sum(on) < 2:
                continue
        found.append((e, (round(mid.X(), 9), round(mid.Y(), 9), round(mid.Z(), 9))))
    return [e for e, _ in sorted(found, key=lambda t: t[1])]


def _on(edge, face) -> bool:
    return any(edge.IsSame(e) for e in _subshapes(face, TopAbs_EDGE))


# --- signatures, the second tier --------------------------------------------------------


def signature(face) -> tuple[str, float, tuple[float, float, float]]:
    """Surface kind, area and centroid: what re-finds a face without history."""
    kind = {GeomAbs_Plane: "Plane", GeomAbs_Cylinder: "Cylinder"}.get(
        BRepAdaptor_Surface(face).GetType(), "Other"
    )
    props = GProp_GProps()
    _static(BRepGProp, "SurfaceProperties")(face, props)
    c = props.CentreOfMass()
    return kind, props.Mass(), (c.X(), c.Y(), c.Z())


def body_signature(solid) -> tuple[float, tuple[float, float, float]]:
    """Volume and centre of mass: what matches a named body to the meshed file's."""
    props = GProp_GProps()
    _static(BRepGProp, "VolumeProperties")(solid, props)
    c = props.CentreOfMass()
    return props.Mass(), (c.X(), c.Y(), c.Z())


def match_signatures(old: list, new_faces: list, *, tol: float, area_tol: float = 0.02) -> list:
    """For each old signature, the new face with the same kind, an area within
    `area_tol` and the nearest centroid within `tol` (or None)."""
    import math

    new = [(f, signature(f)) for f in new_faces]
    out = []
    for kind, area, centre in old:
        best, best_d = None, tol
        for f, (k, a, c) in new:
            if k != kind or abs(a - area) > area_tol * max(area, 1e-30):
                continue
            d = math.dist(c, centre)
            if d <= best_d:
                best, best_d = f, d
        out.append(best)
    return out


def write_brep(shape, path) -> None:
    from OCP.BRepTools import BRepTools

    from puzzle.geometry.session import occ_lock

    with occ_lock:
        _static(BRepTools, "Write")(shape, str(path))
