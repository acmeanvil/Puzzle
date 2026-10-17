"""Write Elmer's native mesh files (mesh.header, mesh.nodes, mesh.elements, mesh.boundary).

Nodes are renumbered 1..N in Puzzle's node order; `node_ids` maps them back. Each
volume element gets the body of the material assignment that covers it; each face
selection a study uses becomes one boundary, with parent elements so Elmer can find
normals and fluxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from puzzle.adapters.common import FaceLookup, InputError, surface_blocks
from puzzle.mesh.mesh import Mesh

# Puzzle element -> (Elmer type code, node reorder from Gmsh order or None).
# Elmer's 510 tetra ends with edges (1-3, 2-3), like VTK and CalculiX.
ELEMENTS = {
    "tet4": (504, None),
    "tet10": (510, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "hex8": (808, None),
    "tri3": (303, None),
    "tri6": (306, None),
    "quad4": (404, None),
}


@dataclass
class ElmerMesh:
    node_ids: np.ndarray  # Puzzle node id of Elmer node i + 1
    bodies: dict[str, int]  # volume group name -> Elmer body index
    boundaries: dict[str, int]  # face group name -> Elmer boundary index


def write_mesh(mesh: Mesh, directory: Path, bodies: list[str], boundaries: list[str]) -> ElmerMesh:
    """Write the mesh; `bodies` are volume group names (an element takes the first that
    contains it), `boundaries` the face group names to write."""
    directory.mkdir(parents=True, exist_ok=True)
    elmer_id = {int(n): i + 1 for i, n in enumerate(mesh.node_ids)}

    def renumber(conn: np.ndarray) -> np.ndarray:
        return np.vectorize(elmer_id.__getitem__, otypes=[np.int64])(conn)

    volume = [b for b in mesh.blocks if b.dim == 3]
    body_of: dict[int, int] = {}
    for index, name in enumerate(bodies, start=1):
        group = mesh.groups.get(name)
        if group is None or group.dim != 3:
            raise InputError(f"'{name}' is not a body selection")
        for b in group.blocks(mesh):
            for eid in b.ids:
                body_of.setdefault(int(eid), index)

    counts: dict[int, int] = {}
    element_lines = []
    for b in volume:
        if b.element not in ELEMENTS:
            raise InputError(f"Elmer adapter does not support {b.element} elements yet")
        code, order = ELEMENTS[b.element]
        conn = renumber(b.connectivity[:, order] if order else b.connectivity)
        for eid, nodes in zip(b.ids, conn, strict=True):
            body = body_of.get(int(eid))
            if body is None:
                raise InputError(f"element {eid} has no material; assign one to every part")
            element_lines.append(f"{eid} {body} {code} " + " ".join(map(str, nodes)))
        counts[code] = counts.get(code, 0) + len(b.ids)

    faces = FaceLookup.build(volume)
    boundary_lines = []
    next_id = max((int(b.ids.max()) for b in mesh.blocks), default=0) + 1
    for index, name in enumerate(boundaries, start=1):
        group = mesh.groups.get(name)
        if group is None:
            raise InputError(f"no selection '{name}' in the mesh")
        for b in surface_blocks(group, mesh):
            code, order = ELEMENTS[b.element]
            parents = faces.of(b)
            conn = renumber(b.connectivity[:, order] if order else b.connectivity)
            for (parent, _), nodes in zip(parents, conn, strict=True):
                boundary_lines.append(
                    f"{next_id} {index} {parent} 0 {code} " + " ".join(map(str, nodes))
                )
                next_id += 1
            counts[code] = counts.get(code, 0) + len(b.ids)

    (directory / "mesh.header").write_text(
        f"{len(mesh.node_ids)} {len(element_lines)} {len(boundary_lines)}\n{len(counts)}\n"
        + "".join(f"{code} {n}\n" for code, n in sorted(counts.items()))
    )
    (directory / "mesh.nodes").write_text(
        "".join(
            f"{i + 1} -1 {x:.15g} {y:.15g} {z:.15g}\n" for i, (x, y, z) in enumerate(mesh.coords)
        )
    )
    (directory / "mesh.elements").write_text("\n".join(element_lines) + "\n")
    (directory / "mesh.boundary").write_text(
        "\n".join(boundary_lines) + ("\n" if boundary_lines else "")
    )
    return ElmerMesh(
        node_ids=mesh.node_ids.copy(),
        bodies={n: i for i, n in enumerate(bodies, start=1)},
        boundaries={n: i for i, n in enumerate(boundaries, start=1)},
    )
