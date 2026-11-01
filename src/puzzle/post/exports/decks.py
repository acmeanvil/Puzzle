"""What the solver-deck exports (Nastran, Abaqus/CalculiX) share: the run's component,
each part's own elements with the material assigned to them, and node orders.

Puzzle's connectivity is Gmsh's. Quadratic elements differ from deck to deck only in
the order of their edge nodes, so each target order is given as the corner pairs its
edge nodes sit on and the permutation is worked out from Gmsh's own list.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import numpy as np

from puzzle.errors import PuzzleError

# Gmsh's edge nodes, after the corners: the corner pairs each lies between.
GMSH_EDGES = {
    "tet10": [(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)],
    "hex20": [(0, 1), (0, 3), (0, 4), (1, 2), (1, 5), (2, 3), (2, 6), (3, 7), (4, 5), (4, 7),
              (5, 6), (6, 7)],
    "wedge15": [(0, 1), (0, 2), (0, 3), (1, 2), (1, 4), (2, 5), (3, 4), (3, 5), (4, 5)],
    "tri6": [(0, 1), (1, 2), (2, 0)],
    "quad8": [(0, 1), (1, 2), (2, 3), (3, 0)],
}  # fmt: skip
CORNERS = {"tet10": 4, "hex20": 8, "wedge15": 6, "tri6": 3, "quad8": 4}


def edge_order(element: str, edges: list[tuple[int, int]]) -> list[int] | None:
    """Gmsh indices in a deck's order, its edge nodes on `edges` (None: no change)."""
    corners = CORNERS[element]
    gmsh = [frozenset(e) for e in GMSH_EDGES[element]]
    order = list(range(corners)) + [corners + gmsh.index(frozenset(e)) for e in edges]
    return None if order == list(range(len(order))) else order


@dataclass
class Piece:
    """A part's own elements of one type, with the material assigned to them."""

    part: str
    element: str
    dim: int
    ids: np.ndarray
    connectivity: np.ndarray  # Gmsh order
    material: object | None  # puzzle Material, None if none is assigned
    assignment: int  # index in comp.materials (-1 without one)


def component(job):
    """The run's study and component (the project's; a sweep point's materials are the
    project's own, not the point's)."""
    from puzzle.adapters.common import as_planar
    from puzzle.model.schema import Component, Study

    try:
        study = job.project.get(UUID(str(job.record.study_id)), Study)
        comp = job.project.get(study.component, Component)
    except KeyError:
        raise PuzzleError(
            f"the run's study '{job.record.study_name}' is no longer in the project",
            code="not_found",
        ) from None
    as_planar(job.mesh, comp)
    return study, comp


def pieces(project, comp, mesh) -> list[Piece]:
    """Every part's own elements (a solid's bodies, a sheet's faces, a line body's
    edges; not the faces and edges selections name), split by the material assigned."""
    from puzzle.model.schema import Material

    by_entity: dict = {}
    for i, a in enumerate(comp.materials):
        group = mesh.groups.get(a.selection)
        if group is None:
            continue
        for e in group.entities:
            by_entity.setdefault(e, (i, project.get(a.material, Material)))
    out = []
    for part in comp.parts:
        own = mesh.groups.get(part.name)
        if own is None:
            continue
        for b in own.blocks(mesh):
            if b.dim != own.dim:
                continue
            index, material = by_entity.get(b.entity, (-1, None))
            out.append(Piece(part.name, b.element, b.dim, b.ids, b.connectivity, material, index))
    if not out:
        raise PuzzleError("the mesh has no elements to export", code="not_found")
    return out


def si(material, key: str) -> float | None:
    q = material.properties.get(key) if material is not None else None
    return None if q is None else float(q.to_si())


def expansion_reference(material) -> float:
    """Where the material's thermal strain is zero (K): its expansion table's reference,
    else 20 °C, as the CalculiX adapter writes it."""
    table = material.tables.get("thermal_expansion")
    if table is not None and table.reference_temperature:
        return float(table.reference_temperature)
    return 293.15
