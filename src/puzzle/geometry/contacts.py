"""Find where the parts of a component touch (ADR 0039), and the bodies of a part.

A side is a part, or a body of a part with several (an imported STEP assembly is often
one part holding every body). Every pair of faces on different sides is a candidate
when their bounding boxes, grown by the tolerance, overlap. A candidate is a contact when
- the faces come within the tolerance of each other (`BRepExtrema_DistShapeShape`),
- their outward normals at the closest points face each other, within an angle, and
- they overlap: the common area of the two faces, once the gap is closed, is above a
  minimum (faces that only meet along an edge don't count).

Faces are each part's named faces (history names, or face-N for imported files), placed
as the mesher places the part: rotated about the origin (x, then y, then z), then moved.
Nothing is made here: `proposals` groups the pairs by part pair for review, and
`contact_commands` turns accepted proposals into one batch of commands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from puzzle.geometry.history import _static
from puzzle.geometry.session import occ_lock


@dataclass
class FacePair:
    """Two faces of different parts that touch: by part and face name."""

    source_part: str
    source_face: str
    target_part: str
    target_face: str
    distance: float  # m, the gap (0 when they touch)
    area: float  # m², the area they overlap
    source_body: str | None = None  # the body on each side, when its part has several
    target_body: str | None = None
    cylinders: dict | None = None  # two coaxial cylinders of near-equal radius (`coaxial`)


@dataclass
class Proposal:
    """One contact to make between two parts: the faces on each side (several when the
    parts touch in several places), the largest gap and the total overlap."""

    source_part: str
    target_part: str
    source_faces: list[str]
    target_faces: list[str]
    distance: float
    area: float
    type: str = "bonded"
    friction: float | None = None
    pairs: list[FacePair] = field(default_factory=list)
    source_body: str | None = None  # bodies of one part: each side's body
    target_body: str | None = None
    labels: tuple[str, str] | None = None  # each side's readable name
    # ADR 0076: two coaxial cylinders of near-equal radius may be a thread (a tapped
    # hole, a screw in a nut): the suggestion (`thread_setup.suggest_size`), and
    # whether to make the contact a thread of it.
    thread: dict | None = None
    use_thread: bool = False

    @property
    def name(self) -> str:
        a, b = self.labels or (self.source_part, self.target_part)
        return f"{a} to {b}"

    @property
    def within_part(self) -> bool:
        """Two bodies of one part: joined by Share faces unless a contact parts them."""
        return self.source_part == self.target_part


def _placement(translation, rotation):
    """The part's placement as one OCC transform: rotations about x, y, z, then a move."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    total = gp_Trsf()
    if any(translation):
        total.SetTranslation(gp_Vec(*translation))
    for axis, angle in reversed(
        list(zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), rotation, strict=True))
    ):
        if angle:
            turn = gp_Trsf()
            turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), math.radians(angle))
            total.Multiply(turn)  # applied before what is already in `total`
    return total


def _placed_faces(project, comp, root, owners: dict | None = None) -> dict[str, dict[str, list]]:
    """part name -> face name -> placed TopoDS faces; `owners`, when given, gets part
    name -> face name -> its body's name (empty for a part with one body)."""
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.naming import named_geometry

    face = _static(TopoDS, "Face")  # OCP's name for the downcast varies by version
    from puzzle.model.schema import Geometry

    cache: dict = {}
    out: dict[str, dict[str, list]] = {}
    for part in comp.parts:
        geometry = project.get(part.geometry, Geometry)
        if part.geometry not in cache:
            cache[part.geometry] = named_geometry(project, geometry, root)
        named = cache[part.geometry]
        where = TopLoc_Location(_placement(part.translation, part.rotation))
        oriented = _as_in_solid(named)
        out[part.name] = {
            name: [face(oriented(f).Moved(where)) for f in faces]
            for name, faces in named.of.get("face", {}).items()
        }
        if owners is not None:
            owners[part.name] = _face_owners(named)
    return out


def _as_in_solid(named):
    """f -> the same face as the shape holds it, with the orientation it has there: a
    history's named faces may come back FORWARD whatever their solid says, and the
    facing test reads outward normals from the orientation (a pin in a history-built
    bore looked side by side, not face to face)."""
    from OCP.TopAbs import TopAbs_FACE

    from puzzle.geometry.history import _subshapes
    from puzzle.geometry.naming import _Index

    held = _Index()
    for f in _subshapes(named.shape, TopAbs_FACE):
        held.get(f).append(f)

    def oriented(f):
        found = held.get(f)
        return found[0] if found else f

    return oriented


def _face_owners(named) -> dict[str, str]:
    """face name -> the body holding it, when the shape has several bodies (none else)."""
    from OCP.TopAbs import TopAbs_FACE

    from puzzle.geometry.history import _subshapes
    from puzzle.geometry.naming import _Index

    bodies = named.of.get("body", {})
    if sum(len(v) for v in bodies.values()) < 2:
        return {}
    held = _Index()  # keyed by the face itself (a TShape handle's hash is not stable)
    for body, solids in bodies.items():
        for solid in solids:
            for f in _subshapes(solid, TopAbs_FACE):
                held.get(f).append(body)
    out = {}
    for name, faces in named.of.get("face", {}).items():
        hit = next((b for f in faces for b in held.get(f)), None)
        if hit is not None:
            out[name] = hit
    return out


def body_labels(project, comp) -> dict[tuple[str, str], str]:
    """(part, body) -> its readable name: a body selection holding just that body (an
    imported STEP's "Pipe and Flange" for body-2), else none (the body's own name)."""
    from puzzle.model.schema import Geometry

    out: dict[tuple[str, str], str] = {}
    for part in comp.parts:
        geometry = project.get(part.geometry, Geometry)
        for s in geometry.selections:
            if s.entity == "body" and len(s.members) == 1:
                out.setdefault((part.name, s.members[0]), s.name)
    return out


def _box(face, grow: float):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    _static(BRepBndLib, "Add")(face, box)
    box.Enlarge(grow)
    return box


def _normal(face, u: float, v: float) -> np.ndarray:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    props = BRepLProp_SLProps(BRepAdaptor_Surface(face), u, v, 1, 1e-9)
    if not props.IsNormalDefined():
        return np.zeros(3)
    n = props.Normal()
    out = np.array([n.X(), n.Y(), n.Z()])
    return -out if face.Orientation() == TopAbs_REVERSED else out


def _touch(a, b, tolerance: float, cos_limit: float, min_area: float):
    """(gap, overlap area) of two faces, or None when they don't face each other.

    The gap is closed by moving b onto a along the shortest line between them; the
    overlap is their common area then, and the normals are compared at its centre."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape, BRepExtrema_IsInFace
    from OCP.BRepGProp import BRepGProp
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.GProp import GProp_GProps
    from OCP.TopLoc import TopLoc_Location

    dist = BRepExtrema_DistShapeShape(a, b)
    if not dist.IsDone() or dist.NbSolution() == 0 or dist.Value() > tolerance:
        return None
    gap = float(dist.Value())
    shift = gp_Trsf()
    if gap > 0:
        shift.SetTranslation(gp_Vec(dist.PointOnShape2(1), dist.PointOnShape1(1)))
    from OCP.TopoDS import TopoDS

    moved = _static(TopoDS, "Face")(b.Moved(TopLoc_Location(shift)))
    common = BRepAlgoAPI_Common(a, moved)
    if not common.IsDone():
        return None
    props = GProp_GProps()
    _static(BRepGProp, "SurfaceProperties")(common.Shape(), props)
    area = float(props.Mass())
    if area < min_area:
        return None  # they meet along an edge, or only by a corner
    # The normals at the overlap's centre; a curved overlap's centre may be off it (a
    # pin in its bore: on the axis, nearest the seam edge), so then at a point on it.
    for point in (props.CentreOfMass(), _point_on(common.Shape())):
        if point is None:
            continue
        centre = BRepBuilderAPI_MakeVertex(point).Vertex()
        normals = []
        for face in (a, moved):
            on = BRepExtrema_DistShapeShape(centre, face)
            if not on.NbSolution() or on.SupportTypeShape2(1) != BRepExtrema_IsInFace:
                break
            normals.append(_normal(face, *on.ParOnFaceS2(1)))
        if len(normals) == 2:
            if normals[0] @ normals[1] > -cos_limit:
                return None  # side by side, not face to face
            return gap, area
    return None


def _point_on(shape):
    """A point on `shape`'s first face, inside it: the centre of a triangle of a coarse
    triangulation (a face's middle may be a hole: an annulus, a flange's bolt circle)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location

    from puzzle.geometry.history import _subshapes

    faces = _subshapes(shape, TopAbs_FACE)
    if not faces:
        return None
    face = faces[0]
    BRepMesh_IncrementalMesh(face, 1e-3, False, 0.5)
    where = TopLoc_Location()
    mesh = _static(BRep_Tool, "Triangulation")(face, where)
    if mesh is None or mesh.NbTriangles() == 0:
        return None
    a, b, c = mesh.Triangle(1).Get()
    pts = [mesh.Node(k).Transformed(where.Transformation()) for k in (a, b, c)]
    from OCP.gp import gp_Pnt

    return gp_Pnt(*(sum(getattr(q, axis)() for q in pts) / 3 for axis in ("X", "Y", "Z")))


def model_size(faces: dict[str, dict[str, list]]) -> float:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    for by_name in faces.values():
        for shapes in by_name.values():
            for f in shapes:
                _static(BRepBndLib, "Add")(f, box)
    if box.IsVoid():
        return 0.0
    lo, hi = box.CornerMin(), box.CornerMax()  # OCP 8 cannot return Get()'s struct
    return math.dist((lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z()))


def find_contacts(
    project,
    comp,
    root,
    *,
    tolerance: float | None = None,
    angle: float = 10.0,
    min_area: float | None = None,
) -> list[FacePair]:
    """Every pair of faces on different sides of `comp` that touch: different parts, or
    different bodies of one part. `tolerance` (m) defaults to 0.5 % of the model's
    size, `min_area` (m²) to 1e-6 of its square."""
    with occ_lock:
        owners: dict[str, dict[str, str]] = {}
        faces = _placed_faces(project, comp, root, owners)
        size = model_size(faces) or 1.0
        tol = tolerance if tolerance is not None else 5e-3 * size
        least = min_area if min_area is not None else 1e-6 * size * size
        cos_limit = math.cos(math.radians(angle))
        sides: dict[tuple[str, str | None], dict[str, list]] = {}  # (part, body) -> faces
        for p, by_name in faces.items():
            for n, fs in by_name.items():
                sides.setdefault((p, owners[p].get(n)), {})[n] = fs
        keys = list(sides)
        boxes = {
            (k, n, i): _box(f, tol)
            for k in keys
            for n, fs in sides[k].items()
            for i, f in enumerate(fs)
        }
        out: list[FacePair] = []
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                for na, fas in sides[a].items():
                    for nb, fbs in sides[b].items():
                        gaps, area = [], 0.0
                        for ia, fa in enumerate(fas):
                            for ib, fb in enumerate(fbs):
                                if boxes[(a, na, ia)].IsOut(boxes[(b, nb, ib)]):
                                    continue
                                hit = _touch(fa, fb, tol, cos_limit, least)
                                if hit is not None:
                                    gaps.append(hit[0])
                                    area += hit[1]
                        if gaps:
                            pair = FacePair(a[0], na, b[0], nb, max(gaps), area, a[1], b[1])
                            pair.cylinders = coaxial(fas, fbs)
                            out.append(pair)
    return out


# --- coaxial cylinders: a thread's two smooth faces (ADR 0076) -------------------------


def cylinder_of(face) -> tuple[np.ndarray, np.ndarray, float] | None:
    """(a point on the axis, its direction, the radius) of a cylindrical face, placed."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surface = BRepAdaptor_Surface(face)
    if surface.GetType() != GeomAbs_Cylinder:
        return None
    cyl = surface.Cylinder()
    p, d = cyl.Axis().Location(), cyl.Axis().Direction()
    return np.array([p.X(), p.Y(), p.Z()]), np.array([d.X(), d.Y(), d.Z()]), float(cyl.Radius())


def coaxial(a_faces, b_faces, rel: float = 0.02, angle: float = 1.0) -> dict | None:
    """Two sides' faces as coaxial cylinders whose radii are within `rel` of the larger
    diameter (the threads' `thread_radii` rule), or None: the axis, each side's radius
    (m) and which side is the inner one (the screw)."""
    cyls = [[cylinder_of(f) for f in faces] for faces in (a_faces, b_faces)]
    if not all(cs and all(c is not None for c in cs) for cs in cyls):
        return None
    origin, axis, r0 = cyls[0][0]
    cos_limit = math.cos(math.radians(angle))
    radii = []
    for side in cyls:
        rs = []
        for p, d, r in side:
            off = (p - origin) - ((p - origin) @ axis) * axis
            if abs(d @ axis) < cos_limit or np.linalg.norm(off) > 0.01 * r0 + 1e-7:
                return None
            rs.append(r)
        if max(rs) - min(rs) > rel * 2 * max(rs):
            return None
        radii.append(sum(rs) / len(rs))
    if abs(radii[0] - radii[1]) > rel * 2 * max(radii):
        return None
    return {"axis": [float(x) for x in axis], "origin": [float(x) for x in origin],
            "source_radius": radii[0], "target_radius": radii[1],
            "inner": "source" if radii[0] <= radii[1] else "target"}  # fmt: skip


def contact_cylinders(project, comp, contact, root) -> dict | None:
    """`coaxial` for a contact's two face selections (part selections only), or None."""
    from puzzle.model.schema import Geometry

    with occ_lock:
        placed = _placed_faces(project, comp, root)
        sides = []
        for ref in (contact.source, contact.target):
            part_name, _, sel_name = ref.partition("/")
            part = next((p for p in comp.parts if p.name == part_name), None)
            if part is None or not sel_name:
                return None
            geometry = project.get(part.geometry, Geometry)
            sel = next((s for s in geometry.selections
                        if s.name == sel_name and s.entity == "face"), None)  # fmt: skip
            if sel is None:
                return None
            faces = [f for m in sel.members for f in placed.get(part_name, {}).get(m, [])]
            if not faces:
                return None
            sides.append(faces)
        return coaxial(*sides)


def proposals(pairs: list[FacePair], labels: dict | None = None) -> list[Proposal]:
    """One proposal per pair of sides (a bolted flange is one decision), bonded.
    `labels` ((part, body) -> name, `body_labels`) names the bodies readably."""
    labels = labels or {}

    def label(part, body):
        return labels.get((part, body), body) if body else part

    grouped: dict[tuple, Proposal] = {}
    for p in pairs:
        key = (p.source_part, p.source_body, p.target_part, p.target_body)
        if key not in grouped:
            named = None
            if p.source_body or p.target_body:
                named = (label(p.source_part, p.source_body), label(p.target_part, p.target_body))
            grouped[key] = Proposal(
                p.source_part, p.target_part, [], [], 0.0, 0.0,
                source_body=p.source_body, target_body=p.target_body, labels=named,
            )  # fmt: skip
        prop = grouped[key]
        if p.source_face not in prop.source_faces:
            prop.source_faces.append(p.source_face)
        if p.target_face not in prop.target_faces:
            prop.target_faces.append(p.target_face)
        prop.distance = max(prop.distance, p.distance)
        prop.area += p.area
        prop.pairs.append(p)
    for prop in grouped.values():
        prop.thread = _thread_suggestion(prop.pairs)
    return list(grouped.values())


def _thread_suggestion(pairs: list[FacePair]) -> dict | None:
    """ADR 0076: a proposal whose pairs are all coaxial cylinders of near-equal radius
    may be a thread: its likely size from the inner (screw's) diameter."""
    if not pairs or any(p.cylinders is None for p in pairs):
        return None
    from puzzle.thread_setup import suggest_size

    c = pairs[0].cylinders
    radius = min(c["source_radius"], c["target_radius"])
    found = suggest_size(2 * radius)
    return {"radius": radius, "inner": c["inner"], "axis": c["axis"],
            **(found or {"form": "iso", "size": None})}  # fmt: skip


def _selection_for(geometry, faces: list[str], name: str, made: dict) -> str:
    """A face selection on the geometry holding exactly `faces`: an existing one, or a
    new one called `name` (made unique), noted in `made` (geometry id -> selections)."""
    for s in [*geometry.selections, *made.get(geometry.id, [])]:
        if s.entity == "face" and sorted(s.members) == sorted(faces):
            return s.name
    taken = {s.name for s in [*geometry.selections, *made.get(geometry.id, [])]}
    unique, n = name, 2
    while unique in taken:
        unique, n = f"{name} {n}", n + 1
    from puzzle.model.schema import NamedSelection

    made.setdefault(geometry.id, []).append(
        NamedSelection(name=unique, entity="face", members=list(faces))
    )
    return unique


def _thread_of(prop: Proposal):
    """The thread an accepted proposal asks for (ADR 0076), or None."""
    if not (prop.use_thread and prop.thread and prop.thread.get("size")):
        return None
    if prop.type not in ("bonded", "no_separation", "frictionless"):
        return None
    from puzzle.model.schema import Thread

    return Thread(form=prop.thread.get("form", "iso"), size=prop.thread["size"])


def contact_commands(project, comp, accepted: list[Proposal]) -> list:
    """The batch that makes the accepted contacts: the face selections they need on the
    parts' geometries (saved, so they survive regeneration), then the contacts."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Contact, Geometry

    parts = {p.name: p for p in comp.parts}
    made: dict = {}
    contacts = []
    taken = {c.name for c in comp.contacts}
    for prop in accepted:
        sides = []
        a, b = prop.labels or (prop.source_part, prop.target_part)
        for part, faces, other in (
            (prop.source_part, prop.source_faces, b),
            (prop.target_part, prop.target_faces, a),
        ):
            geometry = project.get(parts[part].geometry, Geometry)
            sides.append(f"{part}/{_selection_for(geometry, faces, f'Touches {other}', made)}")
        name, n = prop.name, 2
        while name in taken:
            name, n = f"{prop.name} {n}", n + 1
        taken.add(name)
        contacts.append(
            Contact(
                name=name,
                type=prop.type,
                source=sides[0],
                target=sides[1],
                friction=prop.friction if prop.type == "frictional" else None,
                thread=_thread_of(prop),
            )
        )
    commands = [
        Edit(
            object=str(gid),
            ops=[
                EditOp(op="append", path="selections", value=s.model_dump(mode="json"))
                for s in selections
            ],
        )
        for gid, selections in made.items()
    ]
    if contacts:
        commands.append(
            Edit(
                object=str(comp.id),
                ops=[
                    EditOp(op="append", path="contacts", value=c.model_dump(mode="json"))
                    for c in contacts
                ],
            )
        )
    return commands
