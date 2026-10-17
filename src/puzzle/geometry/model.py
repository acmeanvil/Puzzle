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


def import_step(path: Path) -> GeometryModel:
    """Read a STEP (or IGES/BREP) file and describe its entities."""
    with gmsh_session("import"):
        load_shapes(path)
        return GeometryModel(source=path, entities=describe(gmsh.model.getEntities()))


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
        center, measure = (
            tuple(gmsh.model.occ.getCenterOfMass(dim, tag)),
            gmsh.model.occ.getMass(dim, tag),
        )
    return Entity(name, dim, gmsh.model.getType(dim, tag), measure, center, bbox)


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
