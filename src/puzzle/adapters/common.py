"""Mesh helpers shared by solver adapters."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from puzzle.mesh.mesh import CellBlock, Group, Mesh

# Volume element faces as corner positions (Gmsh corner order), numbered the way
# CalculiX numbers them (S1, S2, ...). Other solvers only use the parent element.
FACES = {
    "tet4": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "tet10": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "hex8": [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
}

STRUCTURAL = {"fixed", "displacement", "force", "pressure"}
THERMAL = {"temperature", "heat_flux", "convection"}
FLUID = {"velocity_inlet", "mass_flow_inlet", "pressure_outlet", "wall", "symmetry"}


class InputError(ValueError):
    """The study can't be written for this solver; the message says why."""


@dataclass
class FaceLookup:
    """Which volume element face each surface element sits on."""

    lookup: dict[frozenset[int], tuple[int, int]]

    @classmethod
    def build(cls, blocks: Iterable[CellBlock]) -> FaceLookup:
        lookup = {}
        for b in blocks:
            if b.element not in FACES:
                raise InputError(f"{b.element} elements are not supported yet")
            for face_no, corners in enumerate(FACES[b.element], start=1):
                keys = b.connectivity[:, corners]
                for eid, key in zip(b.ids, keys, strict=True):
                    lookup[frozenset(key.tolist())] = (int(eid), face_no)
        return cls(lookup)

    def of(self, surface: CellBlock) -> list[tuple[int, int]]:
        """(parent element id, face number) for each element of a surface block."""
        corners = 3 if surface.element.startswith("tri") else 4
        out = []
        for row in surface.connectivity[:, :corners]:
            hit = self.lookup.get(frozenset(row.tolist()))
            if hit is None:
                raise InputError("a surface element does not lie on any volume element face")
            out.append(hit)
        return out


def surface_blocks(group: Group, mesh: Mesh) -> list[CellBlock]:
    blocks = [b for b in group.blocks(mesh) if b.dim == 2]
    if not blocks:
        raise InputError(f"'{group.name}' must be a face selection for surface loads")
    return blocks


def surface_area(group: Group, mesh: Mesh) -> float:
    """Total area of a face selection, from its (flat-sided) triangles."""
    area = 0.0
    for b in surface_blocks(group, mesh):
        if b.element.startswith("quad"):
            # Two triangles per (planar) quad: corners 0-1-2 and 0-2-3.
            q = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3)
            for tri in (q[:, [0, 1, 2]], q[:, [0, 2, 3]]):
                area += float(
                    0.5
                    * np.linalg.norm(
                        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1
                    ).sum()
                )
            continue
        if not b.element.startswith("tri"):
            raise InputError(f"area of {b.element} faces is not supported yet")
        xyz = mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        area += float(
            0.5
            * np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1).sum()
        )
    return area


def match_points(
    points: np.ndarray, reference: np.ndarray, ids: np.ndarray, rel_tol: float = 1e-5
) -> np.ndarray:
    """For each point, the id of the reference point at the same place.

    Solvers write coordinates with less precision than Puzzle keeps (EnSight uses
    float32), so points match within `rel_tol` of the model size. A grid hash checks
    the neighbouring cells too, so values near a cell edge still match.
    """
    size = float(np.linalg.norm(reference.max(axis=0) - reference.min(axis=0))) or 1.0
    tol = rel_tol * size
    grid: dict[tuple[int, int, int], list[int]] = {}
    keys = np.floor(reference / tol).astype(np.int64)
    for i, k in enumerate(map(tuple, keys)):
        grid.setdefault(k, []).append(i)
    out = np.empty(len(points), dtype=np.int64)
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for j, p in enumerate(points):
        k = np.floor(p / tol).astype(np.int64)
        best, best_d = -1, tol
        for o in offsets:
            for i in grid.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ()):
                d = float(np.linalg.norm(reference[i] - p))
                if d <= best_d:
                    best, best_d = i, d
        if best < 0:
            raise InputError(f"result point {p} matches no mesh entity")
        out[j] = ids[best]
    return out
