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

import json
from dataclasses import dataclass, field
from pathlib import Path

import gmsh
import numpy as np

from puzzle.errors import PuzzleError
from puzzle.geometry.meshfile import is_mesh_file
from puzzle.geometry.model import (
    ENTITY_DIM,
    import_step,
    load_facets,
    load_shapes,
    match,
    place,
)
from puzzle.geometry.session import gmsh_session
from puzzle.mesh.mesh import ELEMENT_NAMES, CellBlock, Group, Mesh
from puzzle.model.schema import (
    Component,
    Geometry,
    MeshRecipe,
    NamedSelection,
    Project,
)

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
    # Gmsh entity (dim, tag) -> (part, geometry entity name): what a pick in the view
    # names. History geometry gives its persistent names (Plate/+x), others face-N.
    names: dict[tuple[int, int], tuple[str, str]] = field(default_factory=dict)
    # Each body's bounding faces, (3, tag) -> [(2, tag), ...]: a body is shown, and
    # picked, through its surface.
    boundaries: dict[tuple[int, int], list[tuple[int, int]]] = field(default_factory=dict)
    # Each geometric vertex's position, (0, tag) -> (x, y, z): for picking points.
    vertices: dict[tuple[int, int], tuple[float, float, float]] = field(default_factory=dict)
    # (part, entity, name) -> [kind, measure, x, y, z]: anchors for new selections (G3).
    anchors: dict[tuple[str, str, str], list] = field(default_factory=dict)
    # Part -> its geometry's selections as resolved (`naming.SelectionReport.to_dict`):
    # kept, re-found, by rule, or detached with ranked candidates.
    selections: dict[str, list[dict]] = field(default_factory=dict)
    # ADR 0019: histograms of the volume elements' quality and the worst elements.
    quality: dict = field(default_factory=dict)
    # Each volume element's shape quality (ids, values), for colouring the mesh by it.
    element_quality: tuple = field(default_factory=tuple)


def generate_mesh(
    project: Project,
    component_id,
    root: Path,
    out_dir: Path,
    *,
    verbose: bool = False,
    preview: bool = False,
) -> MeshResult:
    """Mesh a component. Geometry sources resolve relative to the project `root`.

    `preview`: a quick, coarse first-order surface mesh only (no volume), for showing
    and picking the geometry before it is meshed for real.
    """
    comp = project.get(component_id, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    if comp.dimension != "3d":
        raise MeshError(f"{comp.dimension} meshing is not implemented yet")
    if recipe.mesher == "netgen" and recipe.element_shape == "hexahedra":
        raise MeshError(
            "Netgen meshes tetrahedra (and boundary-layer prisms); use Gmsh for hexahedra"
        )
    if not preview and recipe.element_shape == "hexahedra" and recipe.element_order != 1:
        raise MeshError("second-order hexahedra are not supported yet; use element_order 1")

    geometries = {p.name: project.get(p.geometry, Geometry) for p in comp.parts}
    warnings: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = {name: _source(project, g, root, out_dir, warnings) for name, g in geometries.items()}
    # History selections name faces persistently; the mesher works with face-N names.
    geometries = {name: g for name, (g, _, _, _) in sources.items()}
    paths = {name: path for name, (_, path, _, _) in sources.items()}
    persistent = {name: back for name, (_, _, back, _) in sources.items()}
    reports = {name: [r.to_dict() for r in rs] for name, (_, _, _, rs) in sources.items()}
    faceted = [p for p in comp.parts if is_mesh_file(paths[p.name])]
    if faceted and (len(comp.parts) > 1 or any(faceted[0].translation) or any(faceted[0].rotation)):
        raise MeshError(
            "a faceted (STL/OBJ/3MF) part must be its component's only part, unplaced, for now"
        )
    references = {
        name: import_step(path, geometries[name].source_unit, geometries[name].facet_angle)
        for name, path in paths.items()
    }

    with gmsh_session("mesh", verbose=verbose):
        found: dict[str, dict[str, tuple[int, int]]] = {}
        if faceted:  # triangles grouped into faces by Gmsh; named as import_step named them
            part, geo = faceted[0], geometries[faceted[0].name]
            volume, groups = load_facets(
                paths[part.name], out_dir, geo.source_unit, geo.facet_angle, reparametrize=True
            )
            # A user's face may be several patches Gmsh can re-mesh: names map to lists.
            found[part.name] = {"body-1": volume} | {
                f"face-{i}": [(2, t) for t in tags] for i, tags in groups.items()
            }
        volumes: dict[str, list[tuple[int, int]]] = {}
        for part in comp.parts if not faceted else []:
            before = set(gmsh.model.getEntities(3))
            load_shapes(paths[part.name])
            volumes[part.name] = sorted(set(gmsh.model.getEntities(3)) - before)
            place(volumes[part.name], part.translation, part.rotation)
        if not faceted:
            gmsh.model.occ.synchronize()
            _fragment_conformal(comp, volumes)

        all_volumes = gmsh.model.getEntities(3)
        for part in comp.parts if not faceted else []:
            ref = references[part.name]
            placement = {
                "translation": part.translation,
                "rotation": part.rotation,
                "tol": 1e-5 * ref.size,
            }
            bodies = match(ref.of_dim(3), all_volumes, **placement)
            missing = [e.name for e in ref.of_dim(3) if e.name not in bodies]
            if missing:
                raise MeshError(f"part '{part.name}': bodies {missing} were lost while placing it")
            # Faces, edges and vertices only among the part's own bodies' boundaries: two
            # parts can have identical faces in the same place (two halves touching).
            found[part.name] = match(ref.entities, _closure(list(bodies.values())), **placement)

        _physical_groups(comp, geometries, references, found, warnings)
        names = {
            entity: (part, persistent[part].get(name, name))
            for part, entities in found.items()
            for name, hit in entities.items()
            for entity in (hit if isinstance(hit, list) else [hit])
        }
        boundaries = {
            v: sorted(set(gmsh.model.getBoundary([v], combined=False, oriented=False)))
            for v in gmsh.model.getEntities(3)
        }
        vertices = {
            (0, t): tuple(gmsh.model.getValue(0, t, [])) for _, t in gmsh.model.getEntities(0)
        }
        anchors = _anchors(references, persistent)
        if preview:
            size = max(r.size for r in references.values()) / 40
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 8)
            gmsh.model.mesh.generate(2)
            mesh = _extract()
            return MeshResult(
                mesh=mesh,
                path=out_dir,
                min_quality=1.0,
                warnings=warnings,
                names=names,
                boundaries=boundaries,
                vertices=vertices,
                anchors=anchors,
                selections=reports,
            )
        if recipe.mesher == "netgen":  # Netgen meshes; Gmsh raises the order after
            from puzzle.mesh.netgen_mesher import mesh_with_netgen

            _options(recipe)
            mesh_with_netgen(recipe, out_dir, *_netgen_controls(recipe))
        else:
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
                    raise MeshError(
                        "some volumes could not be meshed with hexahedra (not block-like)"
                    )
        if recipe.element_order > 1:
            # Curved second-order elements can fold where midside nodes snap onto tight
            # curves (fillets, small holes); Gmsh's optimiser untangles them.
            gmsh.option.setNumber("Mesh.HighOrderOptimize", HIGH_ORDER[recipe.high_order_optimise])
        gmsh.model.mesh.setOrder(recipe.element_order)
        min_quality = _min_quality()
        quality, element_quality = mesh_quality()
        if min_quality <= 0:
            raise MeshError(
                "the mesh has inverted elements (the worst near "
                f"{_where(quality)}); refine it or simplify the geometry"
            )
        floor = recipe.quality_floor
        shape = quality.get("measures", {}).get("shape", {}).get("min", 1.0)
        if floor is not None and shape < floor:
            raise MeshError(
                f"the worst element's shape quality is {shape:.3g}, below the "
                f"recipe's floor {floor:g}, near {_where(quality)}; refine there",
                code="invalid",
            )

        path = out_dir / MESH_FILE
        (out_dir / "quality.json").write_text(json.dumps(quality))
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.write(str(path))
        mesh = _extract()

    return MeshResult(
        mesh=mesh,
        path=path,
        min_quality=min_quality,
        warnings=warnings,
        names=names,
        boundaries=boundaries,
        vertices=vertices,
        anchors=anchors,
        selections=reports,
        quality=quality,
        element_quality=element_quality,
    )


def _anchors(references, persistent) -> dict[tuple[str, str, str], list]:
    """(part, entity, name) -> where that entity is in its geometry's own frame ([kind, measure,
    x, y, z], as `naming.anchor` gives it): what a new selection stores for its picks.
    A name on several file entities (a split face) gets their sum and weighted centre."""
    from puzzle.geometry.model import DIM_PREFIX
    from puzzle.geometry.naming import coarse_kind

    # Names are unique per entity type only: a seam edge is named by its one face.
    out: dict[tuple[str, str, str], list] = {}
    for part, ref in references.items():
        back = persistent.get(part, {})
        for e in ref.entities:
            key = (part, DIM_PREFIX[e.dim], back.get(e.name, e.name))
            kind = coarse_kind(e.dim, e.kind)
            weight = e.measure if e.measure > 0 else 1.0
            have = out.get(key)
            if have is None:
                out[key] = [kind, e.measure, *(weight * c for c in e.center), weight]
            else:
                have[0] = have[0] if have[0] == kind else "Mixed"
                have[1] += e.measure
                for i in range(3):
                    have[2 + i] += weight * e.center[i]
                have[5] += weight
    return {k: [v[0], v[1], *(c / v[5] for c in v[2:5])] for k, v in out.items()}


def _source(project: Project, geo: Geometry, root: Path, out_dir: Path, warnings: list[str]):
    """The geometry's file, the geometry with selections in the file's names (face-N),
    the file names' persistent names, and how each selection resolved (G3, see
    `naming.resolve_geometry`). Faceted files have no exact shape and keep their names."""
    if not geo.history and is_mesh_file(geo.source):
        return geo, root / geo.source, {}, []
    from puzzle.geometry.naming import resolve_geometry

    done = resolve_geometry(project, geo, root, out_dir)
    selections = []
    for sel, report in zip(geo.selections, done.reports, strict=True):
        for m in report.detached:
            near = f"; nearest: {m.candidates[0][0]}" if m.candidates else ""
            warnings.append(f"selection '{sel.name}': '{m.name}' is detached{near}")
        members = [f for n in report.resolved for f in done.files[sel.entity].get(n, [])]
        selections.append(sel.model_copy(update={"members": members}))
    geo = geo.model_copy(update={"selections": selections})
    return geo, done.path, done.back, done.reports


def _closure(volumes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The volumes with every face, edge and vertex on their boundaries."""
    out, level = list(volumes), list(volumes)
    while level and level[0][0] > 0:
        level = sorted(set(gmsh.model.getBoundary(level, combined=False, oriented=False)))
        out += level
    return sorted(set(out))


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
        hit = found.get(owner, {}).get(name)
        if not hit:
            lost.append(f"{owner}/{name}")
        else:  # one entity, or several (a faceted face cut into patches)
            tags += hit if isinstance(hit, list) else [hit]
    return sorted(set(tags)), lost


def _physical_groups(comp, geometries, references, found, warnings) -> None:
    for part in comp.parts:
        bodies = []
        for e in references[part.name].of_dim(3):
            hit = found[part.name][e.name]
            bodies += hit if isinstance(hit, list) else [hit]
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
DISTANCE_LISTS = {0: "PointsList", 1: "CurvesList", 2: "SurfacesList"}
ALGORITHM_2D = {
    "automatic": 2,
    "meshadapt": 1,
    "delaunay": 5,
    "frontal_delaunay": 6,
    "frontal_quads": 8,
    "packing": 9,
}
ALGORITHM_3D = {"delaunay": 1, "frontal": 4, "mmg": 7, "hxt": 10}
HIGH_ORDER = {"off": 0, "optimise": 1, "elastic_and_optimise": 2, "elastic": 3}


def _options(recipe: MeshRecipe) -> None:
    """The recipe's typed settings, then its pass-through Gmsh options by name; a name
    Gmsh doesn't know is refused, never ignored (ADR 0019)."""
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", recipe.curvature_points)
    gmsh.option.setNumber("Mesh.Algorithm", ALGORITHM_2D[recipe.algorithm_2d])
    gmsh.option.setNumber("Mesh.Algorithm3D", ALGORITHM_3D[recipe.algorithm_3d])
    gmsh.option.setNumber("Mesh.Optimize", 0 if recipe.optimiser == "off" else 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if recipe.optimiser == "netgen" else 0)
    gmsh.option.setNumber("Mesh.Smoothing", recipe.smoothing)
    for name, value in recipe.gmsh_options.items():
        set_gmsh_option(name, value)


def set_gmsh_option(name: str, value) -> None:
    """Set a Gmsh option by its own name, refusing a name Gmsh doesn't have."""
    try:
        if isinstance(value, str):
            gmsh.option.getString(name)
            gmsh.option.setString(name, value)
        else:
            gmsh.option.getNumber(name)
            gmsh.option.setNumber(name, float(value))
    except Exception as exc:  # Gmsh raises a bare Exception for an unknown option
        raise MeshError(
            f"Gmsh has no {'text' if isinstance(value, str) else 'number'} option '{name}'",
            code="invalid",
        ) from exc


def _group_entities(name: str) -> tuple[int, list[int]] | None:
    """A physical group's dimension and entity tags, by its name."""
    for dim, tag in gmsh.model.getPhysicalGroups():
        if gmsh.model.getPhysicalName(dim, tag) == name:
            return dim, [int(e) for e in gmsh.model.getEntitiesForPhysicalGroup(dim, tag)]
    return None


def _model_size() -> float:
    x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(-1, -1)
    return float(np.linalg.norm([x1 - x0, y1 - y0, z1 - z0])) or 1.0


def _sizes(recipe: MeshRecipe, comp: Component, found, warnings) -> None:
    """The global size, then each control: size fields (combined by their minimum) and
    transfinite edges. Two size controls giving the same entity different sizes stop
    the mesh with both named (ADR 0019 review: never changed silently)."""
    _options(recipe)
    if recipe.global_size:
        gmsh.option.setNumber("Mesh.MeshSizeMax", recipe.global_size.to_si())
    coarse = recipe.global_size.to_si() if recipe.global_size else _model_size() / 10
    fields: list[int] = []
    sized: dict[tuple[int, int], tuple[str, float]] = {}

    def entities_of(control) -> tuple[int, list[int]]:
        hit = _group_entities(control.selection)
        if hit is None:
            raise MeshError(
                f"a {control.kind.replace('_', ' ')} control names '{control.selection}', "
                "which is not a selection of this component",
                code="invalid",
            )
        return hit

    for control in recipe.controls:
        if control.kind == "size":
            dim, entities = entities_of(control)
            size = control.size.to_si()
            for e in entities:
                before = sized.get((dim, e))
                if before is not None and not np.isclose(before[1], size, rtol=1e-9):
                    raise MeshError(
                        f"'{before[0]}' and '{control.selection}' give the same "
                        f"{['point', 'edge', 'face', 'body'][dim]} different sizes "
                        f"({before[1]:g} m and {size:g} m); keep one",
                        code="invalid",
                    )
                sized[(dim, e)] = (control.selection, size)
            if dim == 0:
                gmsh.model.mesh.setSize([(0, e) for e in entities], size)
                continue
            # A size field covers the whole selection (and its boundary), not its corners.
            f = gmsh.model.mesh.field.add("Constant")
            gmsh.model.mesh.field.setNumbers(f, LISTS[dim], entities)
            gmsh.model.mesh.field.setNumber(f, "VIn", size)
            fields.append(f)
        elif control.kind == "refine_near":
            dim, entities = entities_of(control)
            if dim == 3:  # near a body: near its boundary
                faces = gmsh.model.getBoundary([(3, e) for e in entities], oriented=False)
                dim, entities = 2, sorted({abs(t) for _, t in faces})
            distance = gmsh.model.mesh.field.add("Distance")
            gmsh.model.mesh.field.setNumbers(distance, DISTANCE_LISTS[dim], entities)
            threshold = gmsh.model.mesh.field.add("Threshold")
            gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
            gmsh.model.mesh.field.setNumber(threshold, "SizeMin", control.size.to_si())
            gmsh.model.mesh.field.setNumber(threshold, "SizeMax", coarse)
            gmsh.model.mesh.field.setNumber(threshold, "DistMin", control.near.to_si())
            gmsh.model.mesh.field.setNumber(threshold, "DistMax", control.far.to_si())
            fields.append(threshold)
        elif control.kind == "transfinite":
            dim, entities = entities_of(control)
            if dim != 1:
                raise MeshError(
                    f"a transfinite count goes on edges; '{control.selection}' is not an "
                    "edge selection",
                    code="invalid",
                )
            for e in entities:
                gmsh.model.mesh.setTransfiniteCurve(
                    e, control.count + 1, "Progression", control.progression
                )
        elif control.kind in ("box", "ball"):
            f = gmsh.model.mesh.field.add("Box" if control.kind == "box" else "Ball")
            gmsh.model.mesh.field.setNumber(f, "VIn", control.size.to_si())
            gmsh.model.mesh.field.setNumber(f, "VOut", 1e22)
            if control.kind == "box":
                for axis, lo, hi in zip("XYZ", control.low, control.high, strict=True):
                    gmsh.model.mesh.field.setNumber(f, f"{axis}Min", lo)
                    gmsh.model.mesh.field.setNumber(f, f"{axis}Max", hi)
            else:
                gmsh.model.mesh.field.setNumber(f, "Radius", control.radius.to_si())
                for axis, c in zip("XYZ", control.centre, strict=True):
                    gmsh.model.mesh.field.setNumber(f, f"{axis}Center", c)
            fields.append(f)
        elif control.kind == "boundary_layer":
            raise MeshError(
                "boundary layers are meshed by Netgen: set the recipe's mesher to Netgen "
                "(Gmsh's 3D boundary layers don't work on OpenCASCADE geometry)",
                code="unsupported",
            )
    if fields:
        smallest = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(smallest, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(smallest)


QUALITY = {  # Gmsh's measure, and the bin edges of its histogram
    # Shape: the signed inverse condition number, 1 for a regular tet, <= 0 inverted.
    "shape": ("minSICN", np.linspace(0.0, 1.0, 11)),
    # Scaled Jacobian: 1 for any straight-sided element; it sees curved ones fold.
    "scaled_jacobian": ("minSJ", np.linspace(0.0, 1.0, 11)),
    "gamma": ("gamma", np.linspace(0.0, 1.0, 11)),
}
WORST = 10


def mesh_quality() -> tuple[dict, tuple]:
    """The volume elements' quality (ADR 0019): for each measure its min, mean and
    histogram, and the worst-shaped elements with their centres; and each element's
    shape (ids, values). The edge aspect ratio is the longest edge over the shortest
    (1 is ideal)."""
    _types, tags, _ = gmsh.model.mesh.getElements(3)
    ids = np.concatenate([np.asarray(t) for t in tags]) if tags else np.array([], dtype=np.int64)
    if not len(ids):
        return {}, ()
    out: dict = {"elements": int(len(ids)), "measures": {}}
    values = {}
    for name, (gmsh_name, _bins) in QUALITY.items():
        values[name] = np.asarray(gmsh.model.mesh.getElementQualities(ids, gmsh_name))
    longest = np.asarray(gmsh.model.mesh.getElementQualities(ids, "maxEdge"))
    shortest = np.asarray(gmsh.model.mesh.getElementQualities(ids, "minEdge"))
    values["aspect_ratio"] = longest / np.maximum(shortest, 1e-300)
    top = max(2.0, float(np.ceil(values["aspect_ratio"].max())))
    edges = {name: e for name, (_, e) in QUALITY.items()}
    edges["aspect_ratio"] = np.linspace(1.0, top, 11)
    for name, v in values.items():
        counts, bins = np.histogram(np.clip(v, edges[name][0], edges[name][-1]), edges[name])
        out["measures"][name] = {
            "min": float(v.min()),
            "mean": float(v.mean()),
            "max": float(v.max()),
            "edges": [float(b) for b in bins],
            "counts": [int(c) for c in counts],
        }
    order = np.argsort(values["shape"])[:WORST]
    worst = []
    for i in order:
        _etype, nodes, _dim, _tag = gmsh.model.mesh.getElement(int(ids[i]))
        xyz = np.array([gmsh.model.mesh.getNode(int(n))[0] for n in nodes[:4]])
        worst.append(
            {
                "element": int(ids[i]),
                "shape": float(values["shape"][i]),
                "scaled_jacobian": float(values["scaled_jacobian"][i]),
                "gamma": float(values["gamma"][i]),
                "centre": [float(c) for c in xyz.mean(axis=0)],
            }
        )
    out["worst"] = worst
    return out, (ids.astype(np.int64), values["shape"])


def _where(quality: dict) -> str:
    worst = (quality or {}).get("worst") or []
    if not worst:
        return "an unknown place"
    x, y, z = worst[0]["centre"]
    return f"({x:.4g}, {y:.4g}, {z:.4g}) m"


def _netgen_controls(recipe: MeshRecipe) -> tuple[list[dict], list[dict]]:
    """Size controls as Netgen's local maxh per entity (by centre and measure), and
    boundary layers by their faces; the controls Netgen has no counterpart for are
    refused, naming Gmsh."""
    sizes, layers = [], []
    for control in recipe.controls:
        if control.kind not in ("size", "boundary_layer"):
            raise MeshError(
                f"Netgen takes size controls and boundary layers; a "
                f"{control.kind.replace('_', ' ')} control needs Gmsh",
                code="unsupported",
            )
        hit = _group_entities(control.selection)
        if hit is None:
            raise MeshError(
                f"a {control.kind.replace('_', ' ')} control names '{control.selection}', "
                "which is not a selection of this component",
                code="invalid",
            )
        dim, entities = hit
        if control.kind == "size":
            if dim == 0:
                raise MeshError("Netgen takes sizes on edges, faces and bodies, not points")
            sizes += [
                {
                    "dim": dim,
                    "centre": list(gmsh.model.occ.getCenterOfMass(dim, e)),
                    "measure": gmsh.model.occ.getMass(dim, e),
                    "maxh": control.size.to_si(),
                }
                for e in entities
            ]
        else:
            if dim != 2:
                raise MeshError("a boundary layer grows off faces: pick a face selection")
            first = control.first_height.to_si()
            layers.append(
                {
                    "faces": [
                        [*gmsh.model.occ.getCenterOfMass(2, e), gmsh.model.occ.getMass(2, e)]
                        for e in entities
                    ],
                    "thickness": [first * control.growth**i for i in range(control.layers)],
                }
            )
    return sizes, layers


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
