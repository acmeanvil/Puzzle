"""Find where the parts of a component touch (ADR 0039).

Every pair of faces on different parts is a candidate when their bounding boxes, grown
by the tolerance, overlap. A candidate is a contact when
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

    @property
    def name(self) -> str:
        return f"{self.source_part} to {self.target_part}"


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


def _placed_faces(project, comp, root) -> dict[str, dict[str, list]]:
    """part name -> face name -> placed TopoDS faces."""
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
        out[part.name] = {
            name: [face(f.Moved(where)) for f in faces]
            for name, faces in named.of.get("face", {}).items()
        }
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
    centre = BRepBuilderAPI_MakeVertex(props.CentreOfMass()).Vertex()
    normals = []
    for face in (a, moved):
        on = BRepExtrema_DistShapeShape(centre, face)
        if not on.NbSolution() or on.SupportTypeShape2(1) != BRepExtrema_IsInFace:
            return None
        normals.append(_normal(face, *on.ParOnFaceS2(1)))
    if normals[0] @ normals[1] > -cos_limit:
        return None  # side by side, not face to face
    return gap, area


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
    """Every pair of faces on different parts of `comp` that touch. `tolerance` (m)
    defaults to 0.5 % of the model's size, `min_area` (m²) to 1e-6 of its square."""
    with occ_lock:
        faces = _placed_faces(project, comp, root)
        size = model_size(faces) or 1.0
        tol = tolerance if tolerance is not None else 5e-3 * size
        least = min_area if min_area is not None else 1e-6 * size * size
        cos_limit = math.cos(math.radians(angle))
        parts = list(faces)
        boxes = {
            (p, n, i): _box(f, tol)
            for p in parts
            for n, fs in faces[p].items()
            for i, f in enumerate(fs)
        }
        out: list[FacePair] = []
        for i, pa in enumerate(parts):
            for pb in parts[i + 1 :]:
                for na, fas in faces[pa].items():
                    for nb, fbs in faces[pb].items():
                        gaps, area = [], 0.0
                        for ia, fa in enumerate(fas):
                            for ib, fb in enumerate(fbs):
                                if boxes[(pa, na, ia)].IsOut(boxes[(pb, nb, ib)]):
                                    continue
                                hit = _touch(fa, fb, tol, cos_limit, least)
                                if hit is not None:
                                    gaps.append(hit[0])
                                    area += hit[1]
                        if gaps:
                            out.append(FacePair(pa, na, pb, nb, max(gaps), area))
    return out


def proposals(pairs: list[FacePair]) -> list[Proposal]:
    """One proposal per pair of parts (a bolted flange is one decision), bonded."""
    grouped: dict[tuple[str, str], Proposal] = {}
    for p in pairs:
        key = (p.source_part, p.target_part)
        prop = grouped.setdefault(key, Proposal(p.source_part, p.target_part, [], [], 0.0, 0.0))
        if p.source_face not in prop.source_faces:
            prop.source_faces.append(p.source_face)
        if p.target_face not in prop.target_faces:
            prop.target_faces.append(p.target_face)
        prop.distance = max(prop.distance, p.distance)
        prop.area += p.area
        prop.pairs.append(p)
    return list(grouped.values())


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
        for part, faces, other in (
            (prop.source_part, prop.source_faces, prop.target_part),
            (prop.target_part, prop.target_faces, prop.source_part),
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
