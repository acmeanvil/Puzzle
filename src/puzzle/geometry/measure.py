"""Exact measurements of named geometry (ADR 0042): what Ansys's Selection Information
and SpaceClaim's Measure give, computed by OpenCASCADE from the CAD shape (not from a
mesh, so they don't depend on the mesh size).

`measure(named, picks)` takes a part's named entities (`naming.Named`) and a list of
(entity, name) picks:

- one point: its coordinates;
- one edge: length, and radius and centre for a circle;
- one face: area and centroid, the normal for a plane, radius and axis for a cylinder;
- one body: volume, surface area, centroid and bounding box;
- two picks: the minimum distance between them and where it occurs, the distance
  between their centres with dx, dy, dz, and the angle between two planes or two
  straight edges;
- more: total length, area and volume.

Everything is SI. `rows` turns a measurement into (label, value, unit) rows for the GUI,
the CLI and MCP; each client shows them in its display units.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopoDS import TopoDS_Compound

from puzzle.geometry.history import _static
from puzzle.geometry.naming import CURVES, SURFACES, Named

KINDS = {"vertex": "Point", "edge": "Edge", "face": "Face", "body": "Body"}


class MeasureError(ValueError):
    pass


@dataclass
class Entity:
    """One pick's measurements (SI)."""

    entity: str
    name: str
    kind: str  # Point, Line, Circle, Plane, Cylinder, ..., Solid
    centre: tuple[float, float, float]
    length: float | None = None
    area: float | None = None
    volume: float | None = None
    radius: float | None = None
    normal: tuple[float, float, float] | None = None  # planes (outward)
    axis: tuple[float, float, float] | None = None  # cylinders, circles
    direction: tuple[float, float, float] | None = None  # straight edges
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    # ADR 0065 item 9: what an analyst asks of the geometry beyond sizes
    least_radius: float | None = None  # faces: the sharpest curvature's radius
    topology: dict | None = None  # bodies: solids, shells, faces, edges, free edges
    sharp_edges: int | None = None  # bodies: edges where faces meet at a corner (G0)
    oriented: tuple[float, float, float] | None = None  # bodies: the oriented box's sizes


@dataclass
class Measurement:
    entities: list[Entity]
    pair: dict = field(default_factory=dict)  # two picks: distances and angle
    totals: dict = field(default_factory=dict)  # several picks of one kind

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return {
            "entities": [
                {k: v for k, v in asdict(e).items() if v is not None} for e in self.entities
            ],
            **({"pair": self.pair} if self.pair else {}),
            **({"totals": self.totals} if self.totals else {}),
        }


def _compound(shapes: list):
    from OCP.BRep import BRep_Builder

    if len(shapes) == 1:
        return shapes[0]
    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for s in shapes:
        builder.Add(compound, s)
    return compound


def _xyz(p) -> tuple[float, float, float]:
    return (p.X(), p.Y(), p.Z())


def _props(kind: str, shape):
    props = GProp_GProps()
    fn = {"edge": "LinearProperties", "face": "SurfaceProperties", "body": "VolumeProperties"}
    _static(BRepGProp, fn[kind])(shape, props)
    return props


def _entity(entity: str, name: str, shapes: list) -> Entity:
    shape = _compound(shapes)
    if entity == "vertex":
        p = _static(BRep_Tool, "Pnt")(shapes[0])
        return Entity(entity, name, "Point", _xyz(p))
    props = _props(entity, shape)
    out = Entity(entity, name, "Solid", _xyz(props.CentreOfMass()))
    if entity == "edge":
        out.length = props.Mass()
        curve = BRepAdaptor_Curve(shapes[0])
        out.kind = CURVES.get(curve.GetType(), "Curve") if len(shapes) == 1 else "Edges"
        if len(shapes) == 1 and curve.GetType() == GeomAbs_Circle:
            circle = curve.Circle()
            out.radius = circle.Radius()
            out.centre = _xyz(circle.Location())
            out.axis = _xyz(circle.Axis().Direction())
        elif len(shapes) == 1 and curve.GetType() == GeomAbs_Line:
            out.direction = _xyz(curve.Line().Direction())
    elif entity == "face":
        out.area = props.Mass()
        surface = BRepAdaptor_Surface(shapes[0])
        out.kind = SURFACES.get(surface.GetType(), "Surface") if len(shapes) == 1 else "Faces"
        if len(shapes) == 1 and surface.GetType() == GeomAbs_Plane:
            n = np.array(_xyz(surface.Plane().Axis().Direction()))
            if shapes[0].Orientation() == TopAbs_REVERSED:
                n = -n
            out.normal = tuple(float(v) for v in n)
        elif len(shapes) == 1 and surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            out.radius = cylinder.Radius()
            out.axis = _xyz(cylinder.Axis().Direction())
        out.least_radius = least_radius(shapes)
    else:
        out.volume = props.Mass()
        out.area = _props("face", shape).Mass()
        box = Bnd_Box()
        _static(BRepBndLib, "AddOptimal")(shape, box, False, False)
        lo, hi = box.CornerMin(), box.CornerMax()
        out.bounds = (_xyz(lo), _xyz(hi))
        out.topology = topology(shape)
        out.sharp_edges = sharp_edges(shape)
        out.oriented = oriented_box(shape)
    return out


def least_radius(faces: list, samples: int = 9) -> float | None:
    """The smallest radius of curvature over faces (sampled on a grid of their
    parameters): a fillet's radius, the tightest bend; None where all are flat."""
    from OCP.BRepLProp import BRepLProp_SLProps

    worst = 0.0
    for f in faces:
        s = BRepAdaptor_Surface(f)
        if s.GetType() == GeomAbs_Plane:
            continue
        u0, u1, v0, v1 = s.FirstUParameter(), s.LastUParameter(), s.FirstVParameter(), \
            s.LastVParameter()  # fmt: skip
        props = BRepLProp_SLProps(s, 2, 1e-9)
        for i in range(samples):
            for j in range(samples):
                props.SetParameters(u0 + (u1 - u0) * (i + 0.5) / samples,
                                    v0 + (v1 - v0) * (j + 0.5) / samples)  # fmt: skip
                if props.IsCurvatureDefined():
                    worst = max(worst, abs(props.MaxCurvature()), abs(props.MinCurvature()))
    return 1.0 / worst if worst > 1e-12 else None


def topology(shape) -> dict:
    """How many solids, shells, faces and edges, and free edges (on one face only: a
    hole in the skin)."""
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeFaces,
    )
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp

    from puzzle.geometry.history import _subshapes

    edge_faces = EdgeFaces()
    _static(TopExp, "MapShapesAndAncestors")(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    free = sum(1 for i in range(1, edge_faces.Extent() + 1)
               if edge_faces.FindFromIndex(i).Size() == 1)  # fmt: skip
    return {"solids": len(_subshapes(shape, TopAbs_SOLID)),
            "shells": len(_subshapes(shape, TopAbs_SHELL)),
            "faces": len(_subshapes(shape, TopAbs_FACE)),
            "edges": len(_subshapes(shape, TopAbs_EDGE)), "free_edges": free}  # fmt: skip


def sharp_edges(shape) -> int:
    """Edges where their two faces meet at a corner, not tangent (continuity G0)."""
    from OCP.BRep import BRep_Tool
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeFaces,
    )
    from OCP.GeomAbs import GeomAbs_C0
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    m = EdgeFaces()
    _static(TopExp, "MapShapesAndAncestors")(shape, TopAbs_EDGE, TopAbs_FACE, m)
    count = 0
    for i in range(1, m.Extent() + 1):
        faces = list(m.FindFromIndex(i))
        if len(faces) != 2:
            continue
        e = _static(TopoDS, "Edge")(m.FindKey(i))
        a, b = (_static(TopoDS, "Face")(f) for f in faces)
        if a.IsSame(b):  # a seam
            continue
        if _static(BRep_Tool, "Continuity")(e, a, b) == GeomAbs_C0:
            count += 1
    return count


def oriented_box(shape) -> tuple[float, float, float]:
    """The sizes of the smallest box round the shape in its own directions, largest
    first (a tilted plate's true length, width and thickness)."""
    from OCP.Bnd import Bnd_OBB

    obb = Bnd_OBB()
    _static(BRepBndLib, "AddOBB")(shape, obb, False, True, False)
    sizes = sorted((2 * obb.XHSize(), 2 * obb.YHSize(), 2 * obb.ZHSize()), reverse=True)
    return tuple(float(v) for v in sizes)


def _angle(a: Entity, b: Entity) -> float | None:
    """Degrees between two planes' normals or two straight edges (0 to 90 for edges)."""
    if a.normal is not None and b.normal is not None:
        c = float(np.clip(np.dot(a.normal, b.normal), -1.0, 1.0))
        return math.degrees(math.acos(c))
    if a.direction is not None and b.direction is not None:
        c = abs(float(np.clip(np.dot(a.direction, b.direction), -1.0, 1.0)))
        return math.degrees(math.acos(c))
    return None


def measure(named: Named, picks: list[tuple[str, str]]) -> Measurement:
    """Measure the picked (entity, name) members of a part's named geometry."""
    items = []
    for entity, name in picks:
        members = named.of.get(entity, {}).get(name)
        if not members:
            raise MeasureError(f"no {entity} '{name}' in this geometry")
        items.append((entity, name, members))
    return measure_shapes(items)


def measure_placed(project, comp, root, picks: list[tuple[str, str, str]], cache=None):
    """Measure (part, entity, name) picks of a component where its parts are placed
    (translation and rotation applied), so distances between parts are true. `cache`
    (geometry id -> Named) saves regenerating a part for every pick."""
    from OCP.TopLoc import TopLoc_Location

    from puzzle.geometry.contacts import _placement
    from puzzle.geometry.naming import named_geometry
    from puzzle.model.schema import Geometry

    cache = {} if cache is None else cache
    parts = {p.name: p for p in comp.parts}
    several = len({part for part, _, _ in picks}) > 1
    items = []
    for part_name, entity, name in picks:
        part = parts.get(part_name)
        if part is None:
            raise MeasureError(f"no part '{part_name}' in {comp.name}")
        key = (part.geometry, _key(project.get(part.geometry, Geometry)))
        if key not in cache:
            cache[key] = named_geometry(project, project.get(part.geometry, Geometry), root)
        members = cache[key].of.get(entity, {}).get(name)
        if not members:
            raise MeasureError(f"no {entity} '{name}' in part {part_name}")
        if any(part.translation) or any(part.rotation):
            where = TopLoc_Location(_placement(part.translation, part.rotation))
            members = [_cast(entity, m.Moved(where)) for m in members]
        items.append((entity, f"{part_name}/{name}" if several else name, members))
    return measure_shapes(items)


def _cast(entity: str, shape):
    """A moved shape back to its own type (Moved returns a plain TopoDS_Shape)."""
    from OCP.TopoDS import TopoDS

    kind = {"vertex": "Vertex", "edge": "Edge", "face": "Face"}.get(entity)
    return _static(TopoDS, kind)(shape) if kind else shape


def _key(geometry) -> str:
    """A geometry's content (its history and file), so an edit regenerates it."""
    return geometry.model_dump_json(exclude={"selections"})


def measure_shapes(items: list[tuple[str, str, list]]) -> Measurement:
    """Measure (entity, label, shapes) items: the core of `measure`."""
    if not items:
        raise MeasureError("pick something to measure")
    found = [_entity(entity, label, shapes) for entity, label, shapes in items]
    shapes = [_compound(list(shapes)) for _, _, shapes in items]
    out = Measurement(found)
    if len(found) == 2:
        a, b = found
        extrema = BRepExtrema_DistShapeShape(shapes[0], shapes[1])
        if extrema.IsDone() and extrema.NbSolution():
            out.pair["minimum_distance"] = extrema.Value()
            out.pair["from"] = _xyz(extrema.PointOnShape1(1))
            out.pair["to"] = _xyz(extrema.PointOnShape2(1))
        d = np.subtract(b.centre, a.centre)
        out.pair["centre_distance"] = float(np.linalg.norm(d))
        out.pair["dx"], out.pair["dy"], out.pair["dz"] = (float(v) for v in d)
        angle = _angle(a, b)
        if angle is not None:
            out.pair["angle"] = angle
    if len(found) > 1:
        for key in ("length", "area", "volume"):
            values = [getattr(e, key) for e in found if getattr(e, key) is not None]
            if len(values) > 1:
                out.totals[key] = float(sum(values))
    return out


# --- rows for display --------------------------------------------------------------------

AXES = "xyz"


def rows(m: Measurement) -> list[tuple[str, float | tuple, str]]:
    """(label, SI value, unit) rows: unit "m", "m^2", "m^3", "deg", "point" (a position
    in m), "direction" (a unit vector) or "" (a word in the label)."""
    out: list[tuple[str, float | tuple, str]] = []
    one = len(m.entities) == 1
    for e in m.entities:
        head = "" if one else f"{e.name}: "
        if not one or e.entity != "vertex":
            out.append((f"{head}{KINDS[e.entity]} ({e.kind.lower()})", e.name, ""))
        if e.entity == "vertex":
            out.append((f"{head}Position", e.centre, "point"))
            continue
        for key, unit in (("length", "m"), ("area", "m^2"), ("volume", "m^3")):
            value = getattr(e, key)
            if value is not None:
                label = "Surface area" if key == "area" and e.entity == "body" else key.title()
                out.append((f"{head}{label}", value, unit))
        if e.radius is not None:
            out.append((f"{head}Radius", e.radius, "m"))
            out.append((f"{head}Diameter", 2 * e.radius, "m"))
        centre = "Centre" if e.kind in ("Circle",) else "Centroid"
        out.append((f"{head}{centre}", e.centre, "point"))
        if e.normal is not None:
            out.append((f"{head}Normal", e.normal, "direction"))
        if e.axis is not None:
            out.append((f"{head}Axis", e.axis, "direction"))
        if e.direction is not None:
            out.append((f"{head}Direction", e.direction, "direction"))
        if e.bounds is not None:
            lo, hi = e.bounds
            for i, axis in enumerate(AXES):
                out.append((f"{head}Size in {axis.upper()}", hi[i] - lo[i], "m"))
        if e.least_radius is not None:
            out.append((f"{head}Least radius of curvature", e.least_radius, "m"))
        if e.oriented is not None:
            for word, size in zip(("Length", "Width", "Thickness"), e.oriented, strict=True):
                out.append((f"{head}{word} (its own axes)", size, "m"))
        if e.topology is not None:
            t = e.topology
            out.append((f"{head}Topology: {t['solids']} solids, {t['faces']} faces, "
                        f"{t['edges']} edges, {t['free_edges']} free edges", "", ""))  # fmt: skip
        if e.sharp_edges is not None:
            out.append((f"{head}Sharp edges (not tangent)", float(e.sharp_edges), ""))
    p = m.pair
    if "minimum_distance" in p:
        out.append(("Minimum distance", p["minimum_distance"], "m"))
    if "centre_distance" in p:
        out.append(("Distance between centres", p["centre_distance"], "m"))
        for axis in AXES:
            out.append((f"  d{axis.upper()}", p[f"d{axis}"], "m"))
    if "angle" in p:
        out.append(("Angle", p["angle"], "deg"))
    for key, unit in (("length", "m"), ("area", "m^2"), ("volume", "m^3")):
        if key in m.totals:
            out.append((f"Total {key}", m.totals[key], unit))
    return out


def text(value, unit: str, units: dict | None = None) -> str:
    """A row's value in display units (ADR 0024): "12.5 mm", "(0, 10, 5) mm", "90°"."""
    from puzzle.model.units import pretty, shown

    if unit == "":
        return str(value)
    if unit == "deg":
        return f"{value:.4g}°"
    if unit == "direction":
        return "(" + ", ".join(f"{v:.4g}" for v in _clean(value)) + ")"
    if units is None:
        from puzzle.settings import current

        units = current().display_units()
    if unit == "point":
        values = [shown(v, "m", units) for v in _clean(value)]
        return "(" + ", ".join(f"{v:.6g}" for v, _ in values) + f") {pretty(values[0][1])}"
    v, u = shown(value, unit, units)
    return f"{v:.6g} {pretty(u)}"


def _clean(values) -> list[float]:
    """Round-off zeros (1e-17) shown as 0."""
    scale = max((abs(v) for v in values), default=0.0)
    return [0.0 if abs(v) <= 1e-12 * max(scale, 1e-300) else v for v in values]
