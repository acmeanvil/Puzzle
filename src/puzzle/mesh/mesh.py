"""A solver-neutral mesh: nodes, element blocks and named groups.

Node and element ids are Gmsh's. Element connectivity uses Gmsh node ordering;
each solver adapter reorders nodes for its own element conventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Gmsh element type -> Puzzle element name.
ELEMENT_NAMES = {
    15: "vertex",
    1: "line2",
    8: "line3",
    2: "tri3",
    9: "tri6",
    3: "quad4",
    16: "quad8",
    10: "quad9",
    4: "tet4",
    11: "tet10",
    5: "hex8",
    17: "hex20",
    12: "hex27",
    6: "wedge6",
    18: "wedge15",
    13: "wedge18",
    7: "pyramid5",
    19: "pyramid13",
    14: "pyramid14",
}


@dataclass
class CellBlock:
    """Elements of one type on one geometric entity."""

    element: str
    dim: int
    entity: tuple[int, int]  # Gmsh (dim, tag) the elements were meshed on
    ids: np.ndarray  # (n,) int64
    connectivity: np.ndarray  # (n, nodes_per_element) int64 node ids


@dataclass
class Group:
    """A named set of entities: a part's bodies or a named selection."""

    name: str
    dim: int
    entities: list[tuple[int, int]]
    node_ids: np.ndarray  # sorted, unique

    def blocks(self, mesh: Mesh) -> list[CellBlock]:
        keys = set(self.entities)
        return [b for b in mesh.blocks if b.entity in keys]


@dataclass
class Mesh:
    node_ids: np.ndarray  # (N,) int64
    coords: np.ndarray  # (N, 3) float64, metres
    blocks: list[CellBlock] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)

    def element_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in self.blocks:
            counts[b.element] = counts.get(b.element, 0) + len(b.ids)
        return counts

    def _part_blocks(self, dim: int) -> list[CellBlock]:
        """Elements of parts whose own dimension is `dim`. A solid's mesh also has line
        and surface blocks, on edges and faces named by selections; those are not
        elements of the model."""
        keys = {
            e
            for g in self.groups.values()
            if "/" not in g.name and g.dim == dim
            for e in g.entities
        }
        return [b for b in self.blocks if b.dim == dim and b.entity in keys]

    def beam_blocks(self) -> list[CellBlock]:
        """Line elements of line bodies (ADR 0027 beams)."""
        return self._part_blocks(1)

    def shell_blocks(self) -> list[CellBlock]:
        """Surface elements of sheet bodies (ADR 0027 shells)."""
        return self._part_blocks(2)

    def beam_nodes(self) -> set[int]:
        return {int(n) for b in self.beam_blocks() for n in b.connectivity.ravel()}

    def shell_nodes(self) -> set[int]:
        return {int(n) for b in self.shell_blocks() for n in b.connectivity.ravel()}

    def structural_blocks(self) -> list[CellBlock]:
        """Beams and shells: elements with rotations."""
        return self.beam_blocks() + self.shell_blocks()

    def node_coords(self, ids: np.ndarray) -> np.ndarray:
        """Coordinates of the given node ids."""
        order = np.argsort(self.node_ids)
        return self.coords[order[np.searchsorted(self.node_ids, ids, sorter=order)]]
