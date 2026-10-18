"""Write a solver-neutral mesh as MED (ADR 0005) or another Gmsh format, through Gmsh.

The mesh is rebuilt in Gmsh from its nodes, element blocks and groups, so no Gmsh
file is needed. Group names can be renamed on the way out, because solvers such as
Code_Aster restrict them (short, no slashes).
"""

from __future__ import annotations

from pathlib import Path

import gmsh

from puzzle.geometry.session import gmsh_session
from puzzle.mesh.mesh import ELEMENT_NAMES, Mesh

GMSH_TYPES = {name: code for code, name in ELEMENT_NAMES.items()}


def write_med(
    mesh: Mesh, path: Path, names: dict[str, str] | None = None, *, msh_version: float | None = None
) -> None:
    """Write `mesh` to `path` (format from the suffix: .med, .msh, ...); `names` maps
    Puzzle group names to the names to write.

    Only the groups in `names` are written (all groups when it is None). Elements
    outside every written group are dropped, as with Gmsh's own MED export.
    """
    names = names if names is not None else {g: g for g in mesh.groups}
    with gmsh_session("med-export"):
        entities = sorted({b.entity for b in mesh.blocks})
        for dim, tag in entities:
            gmsh.model.addDiscreteEntity(dim, tag)
        host = next((e for e in entities if e[0] == 3), entities[0])
        gmsh.model.mesh.addNodes(
            host[0], host[1], mesh.node_ids.tolist(), mesh.coords.ravel().tolist()
        )
        for b in mesh.blocks:
            if b.element not in GMSH_TYPES:
                raise ValueError(f"MED export of {b.element} elements is not supported")
            gmsh.model.mesh.addElementsByType(
                b.entity[1], GMSH_TYPES[b.element], b.ids.tolist(), b.connectivity.ravel().tolist()
            )
        for name, out_name in names.items():
            group = mesh.groups[name]
            tags = sorted({t for d, t in group.entities if d == group.dim})
            gmsh.model.addPhysicalGroup(group.dim, tags, name=out_name)
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        if msh_version is not None:  # e.g. 2.2 for readers that predate MSH 4
            gmsh.option.setNumber("Mesh.MshFileVersion", msh_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(path))


def safe_names(group_names: list[str], prefix: str = "G") -> dict[str, str]:
    """Short, solver-safe names (G1, G2, ...) for the given groups, in order."""
    return {name: f"{prefix}{i}" for i, name in enumerate(group_names, start=1)}
