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
| sheet | points (a flat polygon: a sheet body, for shells) | face |
| curved_sheet | r, length, angle, x0, y0, z0 (a cylinder panel on x, centred on +z) | surface |
| cone | r1, r2, h, x, y, z, axis | wall, start, end |
| sphere | r, x, y, z | surface |
| extrude | profile, distance, plane, offset | start, end, side-1 ... (a sketch's edge names) |
| revolve | profile, angle (deg), axis (u or v), plane, offset | side-1 ..., start, end |
| hole | r, x, y (through, along `axis`, default z) | wall |
| fillet, chamfer | r or d, rule (edges) | edge-1 ... |
| move | dx, dy, dz | (names kept) |
| rotate | angle (deg), axis, x, y, z (a point on the axis) | (names kept) |
| pattern | feature, count, dx, dy, dz, or angle + axis for a circular one | <feature>#2/..., ... |
| split | axis or normal, at or x, y, z | section (the cut face inside); split faces keep names |
| imprint | hole (a wall's face name), r | annulus (rings at the hole's mouths); others keep names |
| enclosure | margin, or factor of the part size | +x ... of the box; body `<name>` |
| import, heal, remove, fragment | see puzzle.geometry.prep | preparing imported CAD (G2) |
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


def ops_of(geometry) -> list[Op]:
    """A project geometry's history as operations, suppressed steps left out."""
    return [Op(s.kind, s.name, dict(s.args)) for s in geometry.history if not s.suppressed]


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
    final, solids = _faces(state.shape), _subshapes(state.shape, TopAbs_SOLID)
    names = {n: [f for f in fs if any(f.IsSame(g) for g in final)] for n, fs in state.names.items()}
    bodies = {
        n: [b for b in bs if any(b.IsSame(g) for g in solids)] for n, bs in state.bodies.items()
    }
    return NamedShape(
        state.shape,
        {n: fs for n, fs in names.items() if fs},
        {n: b for n, b in bodies.items() if b},
    )


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
    "sheet": _sheet,
    "curved_sheet": _curved_sheet,
    "import": _import,
    "box": _box,
    "cylinder": _cylinder,
    "cone": _cone,
    "sphere": _sphere,
    "extrude": _extrude,
    "revolve": _revolve,
    "hole": _hole,
}


def _join(state, op, tool, tool_names: Names, mode: str, tool_bodies=None) -> None:
    """Combine a tool body with the part; its faces are named `<op>/<name>`."""
    mine = {f"{op.name}/{k}": v for k, v in tool_names.items()}
    body = op.args.get("body", "Body")
    if mode == "new" or state.shape is None:
        state.shape, state.names = tool, {**state.names, **mine} if state.shape is None else mine
        solids = _subshapes(tool, TopAbs_SOLID)
        state.bodies = {**({body: solids} if solids else {}), **(tool_bodies or {})}
        return
    algo = {"add": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}.get(
        mode
    )
    if algo is None:
        raise GeometryError(f"mode must be new, add, cut or intersect, not '{mode}'")
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


def _pattern(state, op, a):
    """Copies of an earlier tool operation: linear (dx, dy, dz per step) or circular
    (angle per step about `axis` through x, y, z); copy i is named `<feature>#<i>`."""
    feature = op.args["feature"]
    if feature not in state.tools:
        raise GeometryError(f"pattern '{op.name}': no earlier tool operation '{feature}'")
    kind, fa = state.tools[feature]
    for i in range(2, int(a["count"]) + 1):
        tool, names = TOOLS[kind](state, dict(fa))
        trsf = gp_Trsf()
        if "angle" in a:
            point = gp_Pnt(a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0))
            trsf.SetRotation(gp_Ax1(point, _axis_dir(a)), math.radians(a["angle"] * (i - 1)))
        else:
            k = i - 1
            trsf.SetTranslation(
                gp_Vec(a.get("dx", 0.0) * k, a.get("dy", 0.0) * k, a.get("dz", 0.0) * k)
            )
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


def _fragment(state, op, a):
    from puzzle.geometry.prep import fragment

    algo = fragment(state.shape)
    state.names, state.bodies = _carry(algo, state.names), _carry(algo, state.bodies)
    state.shape = algo.Shape()


MODIFIERS = {
    "heal": _heal,
    "remove": _remove,
    "fragment": _fragment,
    "fillet": _fillet,
    "chamfer": _chamfer,
    "move": _move,
    "rotate": _rotate,
    "pattern": _pattern,
    "split": _split,
    "imprint": _imprint,
    "enclosure": _enclosure,
}


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

        named = name_all(NamedShape(shape, state.names, state.bodies)).of["edge"]
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
