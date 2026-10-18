"""Turn a component and its mesh recipe into a Gmsh mesh with named groups.

Steps:
1. Import each part's geometry and describe it on its own (the reference names).
2. Import all parts into one model and place them (rotation, then translation).
3. Fragment the parts joined by conformal bonded contacts, so they share faces
   and nodes (ADR 0005). Nonconformal parts stay separate.
4. Find every named entity again by its signature and build physical groups:
   one per part (its bodies) and one per named selection, "Part/Selection".
5. Mesh with the recipe's sizes and element order; save `mesh.msh`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import gmsh
import numpy as np

from puzzle.errors import PuzzleError
from puzzle.geometry.model import ENTITY_DIM, import_step, load_shapes, match, place
from puzzle.geometry.session import gmsh_session
from puzzle.mesh.mesh import ELEMENT_NAMES, CellBlock, Group, Mesh
from puzzle.model.schema import Component, Geometry, MeshRecipe, NamedSelection, Project

MESH_FILE = "mesh.msh"


class MeshError(PuzzleError, RuntimeError):
    code = "mesh_failed"


@dataclass
class MeshResult:
    mesh: Mesh
    path: Path
    min_quality: (
        float  # smallest scaled Jacobian over the volume elements (1 = ideal, <= 0 = inverted)
    )
    warnings: list[str] = field(default_factory=list)


def generate_mesh(
    project: Project, component_id, root: Path, out_dir: Path, *, verbose: bool = False
) -> MeshResult:
    """Mesh a component. Geometry sources resolve relative to the project `root`."""
    comp = project.get(component_id, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    if comp.dimension != "3d":
        raise MeshError(f"{comp.dimension} meshing is not implemented yet")
    if recipe.mesher != "gmsh":
        raise MeshError(f"the {recipe.mesher} mesher is not implemented yet")
    if recipe.element_shape == "hexahedra" and recipe.element_order != 1:
        raise MeshError("second-order hexahedra are not supported yet; use element_order 1")

    geometries = {p.name: project.get(p.geometry, Geometry) for p in comp.parts}
    references = {name: import_step(root / g.source) for name, g in geometries.items()}
    warnings: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    with gmsh_session("mesh", verbose=verbose):
        volumes: dict[str, list[tuple[int, int]]] = {}
        for part in comp.parts:
            before = set(gmsh.model.getEntities(3))
            load_shapes(root / geometries[part.name].source)
            volumes[part.name] = sorted(set(gmsh.model.getEntities(3)) - before)
            place(volumes[part.name], part.translation, part.rotation)
        gmsh.model.occ.synchronize()

        _fragment_conformal(comp, volumes)

        everything = gmsh.model.getEntities()
        found: dict[str, dict[str, tuple[int, int]]] = {}
        for part in comp.parts:
            ref = references[part.name]
            found[part.name] = match(
                ref.entities,
                everything,
                translation=part.translation,
                rotation=part.rotation,
                tol=1e-5 * ref.size,
            )
            missing = [e.name for e in ref.of_dim(3) if e.name not in found[part.name]]
            if missing:
                raise MeshError(f"part '{part.name}': bodies {missing} were lost while placing it")

        _physical_groups(comp, geometries, references, found, warnings)
        _sizes(recipe, comp, found, warnings)

        if recipe.element_shape == "hexahedra":
            # Structured hexes: every block-like volume gets a transfinite mesh, recombined.
            gmsh.model.mesh.setTransfiniteAutomatic()
            gmsh.option.setNumber("Mesh.RecombineAll", 1)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.model.mesh.generate(3)
        if recipe.element_shape == "hexahedra":
            types, _, _ = gmsh.model.mesh.getElements(3)
            if any(t != 5 for t in types):
                raise MeshError("some volumes could not be meshed with hexahedra (not block-like)")
        gmsh.model.mesh.setOrder(recipe.element_order)
        min_quality = _min_quality()
        if min_quality <= 0:
            raise MeshError("the mesh has inverted elements; refine it or simplify the geometry")

        path = out_dir / MESH_FILE
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.write(str(path))
        mesh = _extract()

    return MeshResult(mesh=mesh, path=path, min_quality=min_quality, warnings=warnings)


def _fragment_conformal(comp: Component, volumes: dict[str, list[tuple[int, int]]]) -> None:
    joined = {
        sel.split("/", 1)[0]
        for c in comp.contacts
        if c.effective_meshing == "conformal"
        for sel in (c.source, c.target)
    }
    tools = [dt for name in sorted(joined) for dt in volumes.get(name, [])]
    if len(joined) > 1 and tools:
        gmsh.model.occ.fragment(tools, [])
        gmsh.model.occ.synchronize()


def _resolve(
    members: list[str], part: str | None, found: dict[str, dict[str, tuple[int, int]]]
) -> tuple[list[tuple[int, int]], list[str]]:
    """Gmsh dim/tags for selection members; unresolved members are returned separately."""
    tags, lost = [], []
    for member in members:
        owner, name = (part, member) if part else member.split("/", 1)
        dimtag = found.get(owner, {}).get(name)
        (tags if dimtag else lost).append(dimtag or f"{owner}/{name}")
    return sorted(set(tags)), lost


def _physical_groups(comp, geometries, references, found, warnings) -> None:
    for part in comp.parts:
        bodies = [found[part.name][e.name] for e in references[part.name].of_dim(3)]
        gmsh.model.addPhysicalGroup(3, [t for _, t in bodies], name=part.name)
    selections: list[tuple[str, str | None, NamedSelection]] = [
        (f"{p.name}/{s.name}", p.name, s) for p in comp.parts for s in geometries[p.name].selections
    ] + [(s.name, None, s) for s in comp.selections]
    for group_name, part, sel in selections:
        dimtags, lost = _resolve(sel.members, part, found)
        if lost:
            warnings.append(
                f"selection '{group_name}': {', '.join(lost)} not found in the meshed geometry"
            )
        if dimtags:
            dim = ENTITY_DIM[sel.entity]
            gmsh.model.addPhysicalGroup(dim, [t for d, t in dimtags if d == dim], name=group_name)


LISTS = {1: "CurvesList", 2: "SurfacesList", 3: "VolumesList"}  # Constant field lists


def _sizes(recipe: MeshRecipe, comp: Component, found, warnings) -> None:
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
    if recipe.global_size:
        gmsh.option.setNumber("Mesh.MeshSizeMax", recipe.global_size.to_si())
    groups = {
        name: dim
        for dim, tag in gmsh.model.getPhysicalGroups()
        for name in [gmsh.model.getPhysicalName(dim, tag)]
    }
    fields = []
    for name, size in recipe.local_sizes.items():
        if name not in groups:
            warnings.append(f"local size on unknown selection '{name}' ignored")
            continue
        dim = groups[name]
        tag = next(
            t
            for d, t in gmsh.model.getPhysicalGroups(dim)
            if gmsh.model.getPhysicalName(d, t) == name
        )
        entities = [int(e) for e in gmsh.model.getEntitiesForPhysicalGroup(dim, tag)]
        if dim == 0:
            gmsh.model.mesh.setSize([(0, e) for e in entities], size.to_si())
            continue
        # A size field covers the whole selection (and its boundary), not just its corners.
        field = gmsh.model.mesh.field.add("Constant")
        gmsh.model.mesh.field.setNumbers(field, LISTS[dim], entities)
        gmsh.model.mesh.field.setNumber(field, "VIn", size.to_si())
        fields.append(field)
    if fields:
        smallest = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(smallest, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(smallest)


def _min_quality() -> float:
    types, tags, _ = gmsh.model.mesh.getElements(3)
    ids = np.concatenate([np.asarray(t) for t in tags]) if tags else np.array([], dtype=np.int64)
    if not len(ids):
        raise MeshError("meshing produced no volume elements")
    return float(np.min(gmsh.model.mesh.getElementQualities(ids, "minSJ")))


def _extract() -> Mesh:
    node_ids, coords, _ = gmsh.model.mesh.getNodes()
    mesh = Mesh(
        node_ids=np.asarray(node_ids, dtype=np.int64), coords=np.asarray(coords).reshape(-1, 3)
    )
    for dim, tag in gmsh.model.getEntities():
        if dim == 0:
            continue
        types, elem_tags, elem_nodes = gmsh.model.mesh.getElements(dim, tag)
        for etype, ids, nodes in zip(types, elem_tags, elem_nodes, strict=True):
            n = gmsh.model.mesh.getElementProperties(etype)[3]
            mesh.blocks.append(
                CellBlock(
                    element=ELEMENT_NAMES.get(etype, f"gmsh{etype}"),
                    dim=dim,
                    entity=(dim, tag),
                    ids=np.asarray(ids, dtype=np.int64),
                    connectivity=np.asarray(nodes, dtype=np.int64).reshape(-1, n),
                )
            )
    for dim, ptag in gmsh.model.getPhysicalGroups():
        name = gmsh.model.getPhysicalName(dim, ptag)
        nodes, _ = gmsh.model.mesh.getNodesForPhysicalGroup(dim, ptag)
        mesh.groups[name] = Group(
            name=name,
            dim=dim,
            entities=[(dim, int(e)) for e in gmsh.model.getEntitiesForPhysicalGroup(dim, ptag)],
            node_ids=np.unique(np.asarray(nodes, dtype=np.int64)),
        )
    return mesh
