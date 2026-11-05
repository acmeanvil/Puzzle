"""Turn a component and its mesh recipe into a Gmsh mesh with named groups.

Steps:
1. Import each part's geometry and describe it on its own (the reference names).
2. Import all parts into one model and place them (rotation, then translation).
3. Fragment the touching bodies of each part (`Geometry.share_faces`: a STEP file
   gives every body its own faces, so they would be meshed apart), then the parts
   joined by conformal bonded contacts, so they share faces and nodes (ADR 0005).
   Nonconformal parts stay separate.
4. Find every named entity again by its signature (a face a fragment split, by its
   pieces) and build physical groups: one per part (its bodies) and one per named
   selection, "Part/Selection".
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
    describe,
    import_step,
    load_facets,
    load_shapes,
    match,
    place,
    rotation_matrix,
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
    # The edges' nodes before meshing (ADR 0019 edge seeds), (n, 3) in m.
    seeds: np.ndarray | None = None
    # Each body's possible method (ADR 0019): (3, tag) -> (method, why).
    methods: dict = field(default_factory=dict)


def generate_mesh(
    project: Project,
    component_id,
    root: Path,
    out_dir: Path,
    *,
    verbose: bool = False,
    preview: bool = False,
    seeds: bool = False,
) -> MeshResult:
    """Mesh a component. Geometry sources resolve relative to the project `root`.

    `preview`: a quick, coarse first-order surface mesh only (no volume), for showing
    and picking the geometry before it is meshed for real.
    """
    comp = project.get(component_id, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    planar = comp.dimension != "3d"  # ADR 0048: faces in the XY plane, meshed in 2D
    if planar and recipe.mesher == "netgen":
        raise MeshError("2D models are meshed with Gmsh (Netgen's 2D is not wired in)")
    if recipe.mesher == "netgen" and recipe.element_shape == "hexahedra":
        raise MeshError(
            "Netgen meshes tetrahedra (and boundary-layer prisms); use Gmsh for hexahedra"
        )
    hexes = recipe.element_shape == "hexahedra" and not planar  # 2D: quadrilaterals
    if not preview and hexes and recipe.element_order != 1:
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
            before = set(gmsh.model.getEntities())
            load_shapes(paths[part.name])
            new = sorted(set(gmsh.model.getEntities()) - before)
            top = max((d for d, _ in new), default=3)  # a solid, a sheet, or lines (beams)
            volumes[part.name] = [e for e in new if e[0] == top]
            if planar and top != 2:
                raise MeshError(
                    f"part '{part.name}': a {comp.dimension} component's parts are faces in "
                    "the XY plane (a sketch's face or a sheet), not "
                    + ("solids" if top == 3 else "lines")
                )
            joined = top == 3 or planar  # 2D regions share their edges, as bodies faces
            if joined and len(volumes[part.name]) > 1 and geometries[part.name].share_faces:
                volumes[part.name] = _share_faces(part.name, volumes[part.name], top)
            place(volumes[part.name], part.translation, part.rotation)
        if not faceted:
            gmsh.model.occ.synchronize()
            _fragment_conformal(comp, volumes)
        if planar:
            _check_plane(comp)

        for part in comp.parts if not faceted else []:
            ref = references[part.name]
            top = _top_dim(ref)
            all_volumes = gmsh.model.getEntities(top)
            placement = {
                "translation": part.translation,
                "rotation": part.rotation,
                "tol": 1e-5 * ref.size,
            }
            bodies = match(ref.of_dim(top), all_volumes, **placement)
            missing = [e.name for e in ref.of_dim(top) if e.name not in bodies]
            if missing:
                raise MeshError(f"part '{part.name}': bodies {missing} were lost while placing it")
            # Faces, edges and vertices only among the part's own bodies' boundaries: two
            # parts can have identical faces in the same place (two halves touching).
            closure = _closure(list(bodies.values()))
            found[part.name] = match(ref.entities, closure, **placement)
            _pieces(ref.entities, closure, found[part.name], **placement)

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
        methods = {v: _method(faces) for v, faces in boundaries.items()}
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
                methods=methods,
            )
        if seeds:  # the edges only, with the recipe's sizes: where their nodes will fall
            _sizes(recipe, comp, found, warnings, project, root)
            gmsh.model.mesh.generate(1)
            _ids, coords, _ = gmsh.model.mesh.getNodes(1, -1, includeBoundary=True)
            return MeshResult(
                mesh=Mesh(node_ids=np.zeros(0, dtype=np.int64), coords=np.zeros((0, 3))),
                path=out_dir,
                min_quality=1.0,
                warnings=warnings,
                names=names,
                seeds=np.unique(np.asarray(coords).reshape(-1, 3), axis=0),
            )
        if recipe.mesher == "netgen":  # Netgen meshes; Gmsh raises the order after
            from puzzle.mesh.netgen_mesher import mesh_with_netgen

            _options(recipe)
            mesh_with_netgen(
                recipe,
                out_dir,
                *_netgen_controls(recipe),
                size_file=_netgen_size_file(recipe, out_dir, project, root),
            )
        elif planar:  # ADR 0048: triangles, or quadrilaterals for "hexahedra"
            _sizes(recipe, comp, found, warnings, project, root)
            if recipe.element_shape == "hexahedra":
                gmsh.option.setNumber("Mesh.RecombineAll", 1)
                gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)  # 8-node quads
            gmsh.model.mesh.generate(2)
        else:
            _sizes(recipe, comp, found, warnings, project, root)
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
        dim = 2 if planar else 3
        min_quality = _min_quality(dim)
        quality, element_quality = mesh_quality(dim)
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
        if planar:
            _counter_clockwise()
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.write(str(path))
        mesh = _extract()
        mesh.planar = planar
        mesh.axisymmetric = comp.dimension == "2d_axisymmetric"
        if planar:  # exactly in the plane: CalculiX refuses 1e-18 m of round-off
            mesh.coords[:, 2] = 0.0

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


def _share_faces(part: str, volumes: list[tuple[int, int]], dim: int = 3) -> list[tuple[int, int]]:
    """Fragment one part's bodies (or a 2D part's regions) by each other, so those that
    touch share the faces (edges) between them, and so their nodes. Overlaps are
    refused: fragmenting would cut the overlap out as a body of its own."""
    out, _ = gmsh.model.occ.fragment(volumes, [])
    gmsh.model.occ.synchronize()
    joined = [e for e in out if e[0] == dim]
    if len(joined) != len(volumes):
        raise MeshError(
            f"part '{part}': its bodies overlap ({len(volumes)} bodies became "
            f"{len(joined)} when joined); remove the overlap, or turn off Share faces "
            "on its geometry"
        )
    return joined


def _pieces(reference, candidates, found: dict, *, translation, rotation, tol: float) -> None:
    """Re-find faces and edges a fragment split: an unmatched entity is the set of
    candidates of its kind lying in its placed bounding box whose measures add up to
    its own. Found sets go into `found` as lists; others stay unmatched (and warned of)."""
    missing = [e for e in reference if e.name not in found and e.dim in (1, 2)]
    if not missing:
        return
    r = rotation_matrix(rotation)
    shift = np.asarray(translation, dtype=float)
    pool = sorted((dt for dt in candidates if dt[0] in (1, 2)), key=lambda dt: (-dt[0], dt[1]))
    described = list(zip(describe(pool), pool, strict=True))
    for ref in missing:
        lo, hi = np.asarray(ref.bbox[:3]), np.asarray(ref.bbox[3:])
        corners = np.array([[(lo, hi)[i >> k & 1][k] for k in range(3)] for i in range(8)])
        placed = corners @ r.T + shift
        pad = tol + 1e-6  # OCC pads bounding boxes by ~1e-7 m
        box_lo, box_hi = placed.min(axis=0) - pad, placed.max(axis=0) + pad
        inside = [
            (e, dt)
            for e, dt in described
            if e.dim == ref.dim
            and e.kind == ref.kind
            and np.all(np.asarray(e.bbox[:3]) >= box_lo)
            and np.all(np.asarray(e.bbox[3:]) <= box_hi)
        ]
        total = sum(e.measure for e, _ in inside)
        if len(inside) > 1 and abs(total - ref.measure) <= 1e-5 * max(ref.measure, 1e-30):
            found[ref.name] = [dt for _, dt in inside]


def _counter_clockwise() -> None:
    """A 2D model's elements turned counter-clockwise seen from +z (ADR 0048): a face
    whose outline was drawn clockwise, mirrored or imported facing -z meshes the other
    way round, and CalculiX refuses those elements (nonpositive Jacobian)."""
    for dim, tag in gmsh.model.getEntities(2):
        types, _, nodes = gmsh.model.mesh.getElements(dim, tag)
        if not len(types):
            continue
        per = gmsh.model.mesh.getElementProperties(types[0])[3]
        corners = np.asarray(nodes[0], dtype=np.int64).reshape(-1, per)[:, :3]
        ids, xyz, _ = gmsh.model.mesh.getNodes(dim, tag, includeBoundary=True)
        at = dict(zip(ids.tolist(), np.asarray(xyz).reshape(-1, 3)[:, :2], strict=True))
        p = np.array([[at[int(n)] for n in tri] for tri in corners])
        a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        if (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() < 0:
            gmsh.model.mesh.reverse([(dim, tag)])


def _check_plane(comp: Component) -> None:
    """A 2D model lies in the XY plane at z = 0; an axisymmetric one at x >= 0 (Y is the
    axis, X the radius: ADR 0048)."""
    lo = hi = None
    for dim, tag in gmsh.model.getEntities(2):
        box = np.asarray(gmsh.model.getBoundingBox(dim, tag))
        lo = box[:3] if lo is None else np.minimum(lo, box[:3])
        hi = box[3:] if hi is None else np.maximum(hi, box[3:])
    if lo is None:
        raise MeshError("a 2D component needs faces to mesh")
    tol = 1e-6 * max(float(np.max(hi - lo)), 1e-9) + 1e-6  # OCC pads boxes by ~1e-7 m
    if lo[2] < -tol or hi[2] > tol:
        raise MeshError(
            "a 2D model lies in the XY plane at z = 0; these faces reach z = "
            f"{lo[2]:.4g} to {hi[2]:.4g} m",
            code="not_planar",
        )
    if comp.dimension == "2d_axisymmetric" and lo[0] < -tol:
        raise MeshError(
            f"an axisymmetric model lies at x >= 0 (Y is the axis, X the radius); it reaches "
            f"x = {lo[0]:.4g} m",
            code="negative_radius",
        )


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


def _top_dim(reference) -> int:
    """A part's own dimension: 3 for a solid, 2 for a sheet, 1 for lines (beams)."""
    return max((e.dim for e in reference.entities), default=3)


def _physical_groups(comp, geometries, references, found, warnings) -> None:
    for part in comp.parts:
        top = _top_dim(references[part.name])
        bodies = []
        for e in references[part.name].of_dim(top):
            hit = found[part.name][e.name]
            bodies += hit if isinstance(hit, list) else [hit]
        gmsh.model.addPhysicalGroup(top, [t for _, t in bodies], name=part.name)
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


FINITE_VOLUME = {"code_saturne", "openfoam"}  # they take first-order cells


def preferred_order(project) -> int:
    """A new recipe's element order (ADR 0019, Settings → Meshing → Element types): with
    "each solver's preferred types", first order when every study's solver is a
    finite-volume CFD solver, else second (the FE solvers' quadratic tets); with
    "neutral", second."""
    from puzzle.model.schema import Study
    from puzzle.settings import current

    if current().get("meshing.element_types") != "solver":
        return 2
    solvers = {s.solver for s in project.of_type(Study)}
    return 1 if solvers and solvers <= FINITE_VOLUME else 2


def _method(faces) -> tuple[str, str]:
    """How a body can be meshed: structured hexahedra when it is block-like (six faces,
    each bounded by four edges, as Gmsh's automatic transfinite meshing needs), else
    tetrahedra, saying why."""
    sides = [len(gmsh.model.getBoundary([f], combined=False, oriented=False)) for f in faces]
    if len(faces) == 6 and all(n == 4 for n in sides):
        return "hexahedra", "block-like: six four-sided faces"
    if len(faces) != 6:
        return "tetrahedra", f"{len(faces)} faces; a block has six"
    odd = sum(n != 4 for n in sides)
    return "tetrahedra", f"{odd} of its faces {'is' if odd == 1 else 'are'} not four-sided"


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


def _sizes(recipe: MeshRecipe, comp: Component, found, warnings, project=None, root=None) -> None:
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
        elif control.kind == "box" and control.axes is not None:  # turned (ADR 0049)
            fields.append(_turned_box(control, project, root))
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
        elif control.kind == "size_field":  # ADR 0066 phase 3: sizes from data
            from puzzle.mesh import size_field

            smallest = gmsh.option.getNumber("Mesh.MeshSizeMin")
            sizes = size_field.resolve(control, project, root)
            fields.append(size_field.gmsh_field(sizes.clamp(smallest, coarse)))
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


def _turned_box(control, project, root) -> int:
    """A box along a Cartesian system's axes, its corners in them: a MathEval field (Gmsh's
    Box follows the global axes), the size inside where every local coordinate is
    within the box's half-widths."""
    from puzzle.axes import absolute
    from puzzle.model.schema import CoordinateSystem

    cs = absolute(project, project.get(control.axes, CoordinateSystem), root)
    if cs.type != "cartesian":
        raise MeshError(f"a mesh box '{control.name}' lies along a Cartesian system's axes",
                        code="invalid")  # fmt: skip
    r, o = cs.orientation(), cs.centre()
    lo, hi = np.asarray(control.low, float), np.asarray(control.high, float)
    middle, half = (lo + hi) / 2, np.abs(hi - lo) / 2
    terms = []
    for i in range(3):  # local u_i = r[:, i] . (X - o), measured from the box's middle
        shift = float(r[:, i] @ o) + middle[i]
        local = "+".join(f"({r[j, i]:.15g})*{v}" for j, v in enumerate("xyz"))
        terms.append(f"Fabs({local}-({shift:.15g}))-({half[i]:.15g})")
    outside = f"Max(Max({terms[0]},{terms[1]}),{terms[2]})"
    inside, far = control.size.to_si(), 1e22
    f = gmsh.model.mesh.field.add("MathEval")
    gmsh.model.mesh.field.setString(f, "F", f"{inside:.15g}+{far - inside:.15g}*Step({outside})")
    return f


QUALITY = {  # Gmsh's measure, and the bin edges of its histogram
    # Shape: the signed inverse condition number, 1 for a regular tet, <= 0 inverted.
    "shape": ("minSICN", np.linspace(0.0, 1.0, 11)),
    # Scaled Jacobian: 1 for any straight-sided element; it sees curved ones fold.
    "scaled_jacobian": ("minSJ", np.linspace(0.0, 1.0, 11)),
    "gamma": ("gamma", np.linspace(0.0, 1.0, 11)),
}
WORST = 10


def mesh_quality(dim: int = 3) -> tuple[dict, tuple]:
    """The volume elements' quality (ADR 0019; a 2D model's faces, ADR 0048): for each
    measure its min, mean and histogram, and the worst-shaped elements with their
    centres; and each element's shape (ids, values). The edge aspect ratio is the
    longest edge over the shortest (1 is ideal)."""
    _types, tags, _ = gmsh.model.mesh.getElements(dim)
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
        if control.kind == "size_field":  # its size file: `_netgen_size_file`
            continue
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


def _netgen_size_file(recipe: MeshRecipe, out_dir: Path, project, root) -> str | None:
    """Sizes from data as Netgen's size file (ADR 0066): every size field's points,
    each at most the recipe's size (Netgen only restricts its local size)."""
    from puzzle.mesh import size_field

    found = [size_field.resolve(c, project, root) for c in recipe.controls
             if c.kind == "size_field"]  # fmt: skip
    if not found:
        return None
    largest = recipe.global_size.to_si() if recipe.global_size else _model_size() / 10
    pts = np.vstack([s.points for s in found])
    h = np.concatenate([np.minimum(s.sizes, largest) for s in found])
    work = out_dir / "netgen"
    work.mkdir(parents=True, exist_ok=True)
    size_field.netgen_file(size_field.Sizes(pts, h, np.zeros((0, 4), np.int64)),
                           work / "sizes.msz")  # fmt: skip
    return "sizes.msz"


def _min_quality(dim: int = 3) -> float:
    types, tags, _ = gmsh.model.mesh.getElements(dim)
    ids = np.concatenate([np.asarray(t) for t in tags]) if tags else np.array([], dtype=np.int64)
    if not len(ids):
        if len(gmsh.model.mesh.getElements(1)[1]) or len(gmsh.model.mesh.getElements(2)[1]):
            return 1.0  # beams or shells only: no volume to judge
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
