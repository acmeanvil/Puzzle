"""New analysis (ADR 0090 UX-3): what a first analysis needs, made in one batch.

A first static analysis asked for four of Puzzle's own nouns made by hand, in order and
from three menus: a mesh recipe, a component, its physics and a study. `commands` plans
them from what a person means: the geometry (all of its parts, or several geometries
as one component's parts), the analysis (static structural, modal, buckling, steady
heat), the material (one in the project or from the library, put on every body) and
how fine a mesh (coarse, medium, fine: a global size from the parts' bounding box).
The solver is the first one, in `SOLVERS` order, whose capability matrix runs that
analysis, unless one is named.

Nothing new is made: the objects are ordinary, named after the geometry, and edited
as any other. One batch, so one history entry and one undo. The GUI's New analysis…
(the toolbar, Model → Studies, the Advisor), MCP `analysis_new` and `puzzle
new-analysis` all go through here.
"""

from __future__ import annotations

import math
from uuid import uuid4

from puzzle.errors import PuzzleError
from puzzle.model.schema import Component, Geometry, Material, Project, Study

# key -> (what a person calls it, physics, step type)
ANALYSES = {
    "static": ("Static structural", "solid_mechanics", "stationary"),
    "modal": ("Modal (natural frequencies)", "solid_mechanics", "modal"),
    "buckling": ("Buckling", "solid_mechanics", "buckling"),
    "steady_heat": ("Steady heat", "heat_transfer", "stationary"),
}
# Elements across the parts' bounding-box diagonal, by how fine.
FINENESS = {"coarse": 10, "medium": 20, "fine": 40}
FINENESS_LABELS = {"coarse": "Coarse", "medium": "Medium", "fine": "Fine"}
SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx")  # the first that fits is chosen
DEFAULT_SIZE = 0.005  # m, when the geometry can't be measured


def solver_for(analysis: str) -> str:
    """The first solver, in `SOLVERS` order, that runs `analysis`."""
    from puzzle.adapters import available_adapters

    _label, physics, step = ANALYSES[analysis]
    adapters = available_adapters()
    for name in SOLVERS:
        if name in adapters and step in adapters[name].capabilities.physics.get(physics, ()):
            return name
    raise PuzzleError(f"no installed solver runs {ANALYSES[analysis][0].lower()}",
                      code="unsupported")  # fmt: skip


def mesh_size(project: Project, geometries: list[Geometry], root, fineness: str) -> float:
    """A global size (m): the parts' bounding-box diagonal over FINENESS elements,
    rounded down to 1, 2 or 5 of a power of ten; DEFAULT_SIZE when it can't be read."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        from puzzle.geometry.naming import named_geometry

        box = Bnd_Box()
        for geo in geometries:
            BRepBndLib.Add_s(named_geometry(project, geo, root).shape, box, True)
        lo, hi = box.CornerMin(), box.CornerMax()  # OCP can't return Bnd_Box.Get's tuple
        diagonal = math.dist((lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z()))
    except Exception:  # noqa: BLE001 - faceted or unreadable: a size that works for parts
        return DEFAULT_SIZE
    if not math.isfinite(diagonal) or diagonal <= 0:
        return DEFAULT_SIZE
    raw = diagonal / FINENESS[fineness]
    power = 10 ** math.floor(math.log10(raw))
    step = max(s for s in (1, 2, 5) if s * power <= raw * (1 + 1e-9))
    return step * power


def _material(project: Project, material: str):
    """(the material's id, a Create for it or None): a project material by name, else
    a library key or name copied in."""
    from puzzle.materials import library_keys, library_material

    mine = next((m for m in project.of_type(Material) if material in (m.name, str(m.id))), None)
    if mine is not None:
        return mine.id, None
    keys = library_keys()
    key = material if material in keys else None
    if key is None:  # a library material by its name
        for k in keys:
            try:
                if library_material(k).name == material:
                    key = k
                    break
            except Exception:  # noqa: BLE001 - an entry that doesn't load: not this one
                continue
    if key is None:
        raise PuzzleError(f"no material '{material}' in the project or the library",
                          code="not_found")  # fmt: skip
    made = library_material(key)
    made.name = project.unique_name(made.name) if any(
        m.name == made.name for m in project.of_type(Material)) else made.name  # fmt: skip
    return made.id, {"command": "create", "kind": "material",
                     "data": made.model_dump(mode="json")}  # fmt: skip


def commands(
    project: Project,
    geometries: list[str],
    analysis: str = "static",
    material: str = "structural_steel",
    fineness: str = "medium",
    *,
    solver: str = "",
    name: str = "",
    root=None,
) -> list[dict]:
    """The batch that makes the analysis: (material,) mesh recipe, component, study.
    `geometries` by name or id; `name` the component's (default: the first
    geometry's), the study is "<name> <analysis>"."""
    from puzzle.commands import resolve
    from puzzle.mesh.generate import preferred_order
    from puzzle.model.catalog import STEP_DEFAULTS

    if analysis not in ANALYSES:
        raise PuzzleError(f"analysis is one of {', '.join(ANALYSES)}, not {analysis!r}",
                          code="invalid")  # fmt: skip
    if fineness not in FINENESS:
        raise PuzzleError(f"mesh is one of {', '.join(FINENESS)}, not {fineness!r}",
                          code="invalid")  # fmt: skip
    if not geometries:
        raise PuzzleError("name the geometry to analyse", code="invalid")
    geos = [resolve(project, g, Geometry) for g in geometries]
    title, physics, step = ANALYSES[analysis]
    solver = solver or solver_for(analysis)
    base = name or geos[0].name
    comp_name = project.unique_name(base) if any(
        c.name == base for c in project.of_type(Component)) else base  # fmt: skip
    material_id, make_material = _material(project, material)
    out = [make_material] if make_material else []
    size = mesh_size(project, geos, root, fineness)
    recipe_id = uuid4()
    out.append({"command": "create", "kind": "mesh", "data": {
        "id": str(recipe_id), "name": project.unique_name(f"{comp_name} mesh"),
        "global_size": {"value": round(size * 1000, 6), "unit": "mm"},
        "element_order": preferred_order(project)}})  # fmt: skip
    parts, assignments, taken = [], [], set()
    for geo in geos:
        part = geo.name if geo.name not in taken else f"{geo.name} {len(taken) + 1}"
        taken.add(part)
        parts.append({"name": part, "geometry": str(geo.id)})
        bodies = [s.name for s in geo.selections if s.entity == "body"]
        if "Body" in bodies:  # an import's: every body (its parts' own overlap it)
            bodies = ["Body"]
        if not bodies:
            raise PuzzleError(f"{geo.name} has no body selection to put the material on",
                              code="invalid")  # fmt: skip
        assignments += [{"material": str(material_id), "selection": f"{part}/{b}"}
                        for b in bodies]  # fmt: skip
    comp_id = uuid4()
    out.append({"command": "create", "kind": "component", "data": {
        "id": str(comp_id), "name": comp_name, "parts": parts, "mesh": str(recipe_id),
        "materials": assignments, "physics": [{"type": physics, "features": []}]}})  # fmt: skip
    study = f"{comp_name} {title.split(' (')[0].lower()}"
    if any(s.name == study for s in project.of_type(Study)):
        study = project.unique_name(study)
    out.append({"command": "create", "kind": "study", "data": {
        "name": study, "component": str(comp_id), "solver": solver,
        "steps": [{"type": step, "physics": [],
                   "settings": dict(STEP_DEFAULTS.get(step, {}))}]}})  # fmt: skip
    return out


def unanalysed(project: Project) -> list[Geometry]:
    """Geometries no component uses yet: what New analysis offers first."""
    used = {p.geometry for c in project.of_type(Component) for p in c.parts}
    return [g for g in project.of_type(Geometry) if g.id not in used]
