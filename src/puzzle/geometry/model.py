"""Imported geometry: named topological entities with geometric signatures.

Entities are named "body-N", "face-N", "edge-N" and "vertex-N", numbered in the
order the STEP file yields them, which is stable for a given file. Each entity
also carries a signature (kind, size, centre) so it can be found again after
the geometry is moved, rotated or fragmented. Matching names across edits of
the source file is the persistent-naming work of ADR 0004 and builds on these
signatures.

All lengths are in metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import gmsh
import numpy as np

from puzzle.geometry.session import gmsh_session

DIM_PREFIX = {3: "body", 2: "face", 1: "edge", 0: "vertex"}
PREFIX_DIM = {v: k for k, v in DIM_PREFIX.items()}
ENTITY_DIM = {"body": 3, "face": 2, "edge": 1, "vertex": 0}


@dataclass(frozen=True)
class Entity:
    name: str
    dim: int
    kind: str  # Gmsh/OCC type: "Plane", "Cylinder", "Line", "Circle", "Volume", ...
    measure: float  # volume, area or length; 0 for vertices
    center: tuple[float, float, float]
    bbox: tuple[float, float, float, float, float, float]
    label: str = ""  # the part's name in a STEP assembly, when the file gives one


@dataclass
class GeometryModel:
    source: Path
    entities: list[Entity] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_name = {e.name: e for e in self.entities}

    def __getitem__(self, name: str) -> Entity:
        return self._by_name[name]

    def of_dim(self, dim: int) -> list[Entity]:
        return [e for e in self.entities if e.dim == dim]

    @property
    def size(self) -> float:
        """The diagonal of the bounding box of all bodies; sets matching tolerances."""
        boxes = np.array([e.bbox for e in self.of_dim(3)] or [e.bbox for e in self.entities])
        lo, hi = boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)
        return float(np.linalg.norm(hi - lo))

    def labelled_bodies(self) -> dict[str, list[str]]:
        """Bodies grouped by their STEP part name ({"Bracket": ["body-1"], ...})."""
        out: dict[str, list[str]] = {}
        for e in self.of_dim(3):
            if e.label:
                out.setdefault(e.label, []).append(e.name)
        return out

    # Selection helpers: the rules a GUI pick or a script uses to build named selections.

    def faces_on_plane(self, axis: str, value: float, tol: float | None = None) -> list[str]:
        """Faces lying in the plane `axis` = `value` (e.g. "x", 0.0)."""
        i = "xyz".index(axis)
        # OpenCASCADE pads bounding boxes by its tolerance (about 1e-7 m), so never go below 1 µm.
        tol = tol if tol is not None else max(1e-6 * self.size, 1e-6)
        return [
            e.name
            for e in self.of_dim(2)
            if abs(e.bbox[i] - value) <= tol and abs(e.bbox[i + 3] - value) <= tol
        ]

    def faces_of_kind(self, kind: str) -> list[str]:
        return [e.name for e in self.of_dim(2) if e.kind == kind]


def import_step(path: Path, unit: str | None = None, angle: float = 40.0) -> GeometryModel:
    """Read a STEP, IGES or BREP file, or a faceted STL/OBJ/3MF one, and describe its
    entities. `unit` and `angle` (patch feature angle, degrees) apply to faceted files."""
    from puzzle.geometry.meshfile import is_mesh_file

    with gmsh_session("import"):
        if is_mesh_file(path):
            import tempfile

            load_facets(path, Path(tempfile.mkdtemp(prefix="puzzle-facets-")), unit, angle)
        else:
            load_shapes(path)
        return GeometryModel(source=path, entities=describe(gmsh.model.getEntities()))


def load_facets(
    path: Path,
    work: Path,
    unit: str | None = None,
    angle: float = 40.0,
    reparametrize: bool = False,
):
    """A faceted file as Gmsh geometry: faces where the surface turns by more than
    `angle` degrees, closed into one volume.

    Without `reparametrize` the faces are the ones users see and select (face-N in tag
    order). Meshing needs `reparametrize`, which may cut a face into several patches
    Gmsh can re-mesh; then the return value also maps each user face's number to its
    patches' tags. Returns ([(3, volume)], {face number: [surface tags]}).
    """
    from puzzle.geometry.meshfile import triangles, write_stl

    stl = write_stl(triangles(path, unit), Path(work) / f"{Path(path).stem}-m.stl")
    groups: dict[int, list[int]] = {}
    if reparametrize:
        # The user's faces first, in a model of their own, as triangle centroids.
        current = gmsh.model.getCurrent()
        gmsh.model.add("puzzle-facet-names")
        _classify(stl, angle, False)
        named = {
            i: _triangles(t).mean(axis=1)
            for i, (_, t) in enumerate(sorted(gmsh.model.getEntities(2)), start=1)
        }
        gmsh.model.remove()
        gmsh.model.setCurrent(current)
    _classify(stl, angle, reparametrize)
    surfaces = [t for _, t in gmsh.model.getEntities(2)]
    if reparametrize:
        for t in surfaces:
            probe = _triangles(t).mean(axis=1)[0]
            owner = min(named, key=lambda i: np.min(np.linalg.norm(named[i] - probe, axis=1)))
            groups.setdefault(owner, []).append(t)
    loop = gmsh.model.geo.addSurfaceLoop(surfaces)
    volume = gmsh.model.geo.addVolume([loop])
    gmsh.model.geo.synchronize()
    return [(3, volume)], groups


def _classify(stl: Path, angle: float, reparametrize: bool) -> None:
    gmsh.merge(str(stl))
    gmsh.model.mesh.removeDuplicateNodes()
    gmsh.model.mesh.classifySurfaces(math.radians(angle), True, reparametrize, math.pi)
    gmsh.model.mesh.createGeometry()


def named_entities(dimtags) -> dict[str, tuple[int, int]]:
    """The names `describe` gives these entities (face-1, ...), with their dim/tags."""
    counters = dict.fromkeys(DIM_PREFIX, 0)
    out = {}
    for dim, tag in sorted(dimtags, key=lambda dt: (-dt[0], dt[1])):
        counters[dim] += 1
        out[f"{DIM_PREFIX[dim]}-{counters[dim]}"] = (dim, tag)
    return out


def load_shapes(path: Path) -> list[tuple[int, int]]:
    if not path.exists():
        raise FileNotFoundError(path)
    dimtags = gmsh.model.occ.importShapes(str(path), highestDimOnly=False)
    gmsh.model.occ.synchronize()
    return dimtags


def describe(dimtags: list[tuple[int, int]]) -> list[Entity]:
    """Entities for the given Gmsh dim/tags, named by dimension and position in the list."""
    counters = dict.fromkeys(DIM_PREFIX, 0)
    out = []
    for dim, tag in sorted(dimtags, key=lambda dt: (-dt[0], dt[1])):
        counters[dim] += 1
        out.append(_entity(f"{DIM_PREFIX[dim]}-{counters[dim]}", dim, tag))
    return out


def _entity(name: str, dim: int, tag: int) -> Entity:
    bbox = tuple(gmsh.model.getBoundingBox(dim, tag))
    if dim == 0:
        center, measure = tuple(gmsh.model.getValue(0, tag, [])), 0.0
    else:
        try:
            center, measure = (
                tuple(gmsh.model.occ.getCenterOfMass(dim, tag)),
                gmsh.model.occ.getMass(dim, tag),
            )
        except Exception:  # noqa: BLE001 - a faceted (discrete) entity, not OCC
            center, measure = _discrete_props(dim, tag)
    # STEP part names come through OCCT's XDE reader as "Shapes/.../Name"; keep the last.
    label = (gmsh.model.getEntityName(dim, tag) or "").rstrip("/").split("/")[-1]
    return Entity(name, dim, gmsh.model.getType(dim, tag), measure, center, bbox, label)


def _discrete_props(dim: int, tag: int):
    """Centre and measure of a faceted entity, from its triangles (or line segments)."""
    if dim == 3:  # the divergence theorem over the boundary's triangles
        total, moment = 0.0, np.zeros(3)
        for _, st in gmsh.model.getBoundary([(3, tag)], oriented=True):
            for tri in _triangles(abs(st)):
                a, b, c = tri if st > 0 else tri[::-1]
                v = np.dot(a, np.cross(b, c)) / 6
                total += v
                moment += v * (a + b + c) / 4
        total = abs(total)
        return tuple(moment / total if total else np.zeros(3)), total
    if dim == 2:
        tris = _triangles(tag)
        areas = 0.5 * np.linalg.norm(
            np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1
        )
        centre = (areas[:, None] * tris.mean(axis=1)).sum(axis=0) / max(areas.sum(), 1e-300)
        return tuple(centre), float(areas.sum())
    types, _, nodes = gmsh.model.mesh.getElements(1, tag)
    tags, coords, _ = gmsh.model.mesh.getNodes(1, tag, includeBoundary=True)
    where = {int(t): i for i, t in enumerate(tags)}
    xyz = np.asarray(coords).reshape(-1, 3)
    length, moment = 0.0, np.zeros(3)
    for conn in nodes:
        for a, b in np.asarray(conn, int).reshape(-1, 2):
            pa, pb = xyz[where[a]], xyz[where[b]]
            seg = np.linalg.norm(pb - pa)
            length += seg
            moment += seg * (pa + pb) / 2
    return tuple(moment / length if length else xyz.mean(axis=0)), length


def _triangles(tag: int) -> np.ndarray:
    """A surface's mesh triangles as an (n, 3, 3) array of coordinates."""
    types, _, nodes = gmsh.model.mesh.getElements(2, tag)
    tags, coords, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
    where = {int(t): i for i, t in enumerate(tags)}
    xyz = np.asarray(coords).reshape(-1, 3)
    out = []
    for etype, conn in zip(types, nodes, strict=True):
        if etype != 2:  # first-order triangles only (what classification leaves)
            continue
        for a, b, c in np.asarray(conn, int).reshape(-1, 3):
            out.append([xyz[where[a]], xyz[where[b]], xyz[where[c]]])
    return np.asarray(out, float).reshape(-1, 3, 3)


# --- Placement and matching -------------------------------------------------


def rotation_matrix(euler_xyz_deg: tuple[float, float, float]) -> np.ndarray:
    """Rotation about x, then y, then z (fixed axes), angles in degrees."""
    ax, ay, az = (math.radians(a) for a in euler_xyz_deg)
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return rz @ ry @ rx


def place(dimtags, translation, rotation) -> None:
    """Rotate (about the origin, x then y then z) and translate shapes in the OCC kernel."""
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), rotation, strict=True):
        if angle:
            gmsh.model.occ.rotate(dimtags, 0, 0, 0, *axis, math.radians(angle))
    if any(translation):
        gmsh.model.occ.translate(dimtags, *translation)


def match(
    reference: list[Entity],
    candidates: list[tuple[int, int]],
    *,
    translation=(0.0, 0.0, 0.0),
    rotation=(0.0, 0.0, 0.0),
    tol: float,
) -> dict[str, tuple[int, int]]:
    """Find each reference entity among `candidates` (dim, tag) after a placement.

    A match has the same dimension and kind, the same measure (relative 1e-6),
    and its centre within `tol` of the placed reference centre. Entities with no
    match (for example faces merged by fragmenting) are left out.
    """
    r = rotation_matrix(rotation)
    shift = np.asarray(translation, dtype=float)
    pool = describe(candidates)
    tags = dict(
        zip((e.name for e in pool), sorted(candidates, key=lambda dt: (-dt[0], dt[1])), strict=True)
    )
    found: dict[str, tuple[int, int]] = {}
    for ref in reference:
        target = r @ np.asarray(ref.center) + shift
        best, best_d = None, tol
        for cand in pool:
            if cand.dim != ref.dim or cand.kind != ref.kind:
                continue
            if abs(cand.measure - ref.measure) > 1e-6 * max(abs(ref.measure), 1e-30):
                continue
            d = float(np.linalg.norm(np.asarray(cand.center) - target))
            if d <= best_d:
                best, best_d = cand, d
        if best is not None:
            found[ref.name] = tags[best.name]
    return found
