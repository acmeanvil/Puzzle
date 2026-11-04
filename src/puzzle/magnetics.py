"""Checks and helpers for magnetic studies (ADR 0047 items 3 and the air region).

- **Skin depth:** δ = √(2 / (ω μ σ)) in each conductor at the study's highest
  frequency. Its elements should be at most δ/2 across, or the current crowding to its
  surface is smeared and losses come out low. `warnings` compares the mesh recipe's
  size for each conductor (before meshing); `mesh_warnings` the elements meshed.
- **Air region:** the field of a coil reaches a few times its size; an insulated outer
  boundary closer than about five times the coils' size squeezes it. `mesh_warnings`
  says so at a solve's start.
- `skin_refinement`: a "refine near" control per conductor, δ/3 within δ of its
  surface, back to the conductor's size by 4δ.

Warnings never refuse a run (as contacts' checks, ADR 0056).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.adapters.eddy import skin_depth
from puzzle.model.schema import Component, Material, MeshRecipe, Project, Quantity, Study

ELEMENTS_PER_DEPTH = 2  # at least two elements across δ
AIR_RATIO = 5.0  # the outer boundary at least this many coil half-sizes from their centre


@dataclass
class Warning_:
    code: str
    object: str
    message: str


def _frequency(study: Study) -> float:
    """The highest frequency a magnetic study solves (Hz), or 0."""
    from puzzle.adapters.common import frequency_list

    top = 0.0
    for step in study.solve_steps():
        if step.type == "frequency_domain" and (not step.physics or
                                                "magnetic_fields" in step.physics):  # fmt: skip
            try:
                top = max(top, max(frequency_list(step)))
            except Exception:  # noqa: BLE001 - a bad frequency list is reported elsewhere
                continue
    return top


def conductors(project: Project, comp: Component) -> list[tuple[str, float, float]]:
    """(body, μr, σ) of each conducting body that can carry eddy currents (not a
    winding's)."""
    physics = next((p for p in comp.physics if p.type == "magnetic_fields"), None)
    if physics is None:
        return []
    windings = {f.selection for f in physics.features if f.type == "stranded_coil"}
    out = []
    for a in comp.materials:
        m = project.objects.get(a.material)
        if not isinstance(m, Material) or a.selection in windings:
            continue
        sigma, mu = m.properties.get("electrical_conductivity"), m.properties.get(
            "relative_permeability")  # fmt: skip
        if sigma is not None and sigma.to_si() > 0:
            out.append((a.selection, mu.to_si() if mu else 1.0, sigma.to_si()))
    return out


def recipe_size(recipe: MeshRecipe, selection: str) -> float | None:
    """The finest size the recipe asks for in a body (m), or None if it sets none."""
    sizes = []  # a recipe's local sizes load as size controls
    for c in recipe.controls:
        if getattr(c, "selection", None) == selection and c.kind in ("size", "refine_near"):
            sizes.append(c.size.to_si())
    if sizes:
        return min(sizes)
    return recipe.global_size.to_si() if recipe.global_size else None


def _shown(metres: float) -> str:
    from puzzle.contact_check import shown_length

    return shown_length(metres)


def _hz(hz: float) -> str:
    return f"{hz / 1e3:g} kHz" if hz >= 1e3 else f"{hz:g} Hz"


def warnings(project: Project) -> list[Warning_]:
    """Conductors whose recipe size is coarser than δ/2 at their study's top frequency."""
    out: list[Warning_] = []
    seen = set()
    for study in project.of_type(Study):
        hz = _frequency(study)
        comp = project.objects.get(study.component)
        if not hz or not isinstance(comp, Component):
            continue
        recipe = project.objects.get(comp.mesh)
        if not isinstance(recipe, MeshRecipe):
            continue
        for body, mu, sigma in conductors(project, comp):
            delta, size = skin_depth(hz, mu, sigma), recipe_size(recipe, body)
            if size is None or size <= delta / ELEMENTS_PER_DEPTH or (study.id, body) in seen:
                continue
            seen.add((study.id, body))
            out.append(Warning_("skin_depth", study.name,
                f"{body}: the skin depth is {_shown(delta)} at {_hz(hz)}, its elements "
                f"{_shown(size)}; aim for {_shown(delta / 3)} near its surface (Add a "
                "skin-depth refinement)"))  # fmt: skip
    return out


def skin_refinement(project: Project, study: Study) -> list:
    """Refine-near controls for each conductor of the study that needs one."""
    from puzzle.model.schema import RefineNearControl

    hz = _frequency(study)
    comp = project.get(study.component, Component)
    recipe = project.get(comp.mesh, MeshRecipe)
    out = []
    for body, mu, sigma in conductors(project, comp):
        delta = skin_depth(hz, mu, sigma)
        size = recipe_size(recipe, body)
        if not hz or (size is not None and size <= delta / ELEMENTS_PER_DEPTH):
            continue

        def q(x: float) -> Quantity:
            return Quantity(value=float(f"{x * 1e3:.3g}"), unit="mm")

        out.append(RefineNearControl(selection=body, size=q(delta / 3), near=q(delta),
                                     far=q(4 * delta)))  # fmt: skip
    return out


def _element_size(mesh, name: str) -> float:
    """The median edge of a body's elements (m), from their corners."""
    edges = []
    for b in mesh.groups[name].blocks(mesh):
        corners = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3)
        for i, j in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
            edges.append(np.linalg.norm(corners[:, i] - corners[:, j], axis=1))
    return float(np.median(np.concatenate(edges))) if edges else 0.0


def mesh_warnings(project: Project, study: Study, mesh) -> list[Warning_]:
    """At a solve's start: the elements meshed against δ, and the air around the coils."""
    comp = project.get(study.component, Component)
    out: list[Warning_] = []
    hz = _frequency(study)
    for body, mu, sigma in conductors(project, comp) if hz else []:
        if body not in mesh.groups:
            continue
        delta, size = skin_depth(hz, mu, sigma), _element_size(mesh, body)
        if size > delta / ELEMENTS_PER_DEPTH:
            out.append(Warning_("skin_depth", study.name,
                f"{body}: elements of {_shown(size)} against a skin depth of {_shown(delta)} "
                f"at {_hz(hz)}: losses and resistance will come out low"))  # fmt: skip
    physics = next((p for p in comp.physics if p.type == "magnetic_fields"), None)
    coils = [f.selection for f in physics.features
             if f.type in ("coil_current", "stranded_coil")] if physics else []  # fmt: skip
    insulated = [f.selection for f in physics.features
                 if f.type == "magnetic_insulation"] if physics else []  # fmt: skip
    if coils and insulated and all(c in mesh.groups for c in coils):
        ids = np.unique(np.concatenate([b.connectivity.ravel() for c in coils
                                        for b in mesh.groups[c].blocks(mesh)]))  # fmt: skip
        xyz = mesh.node_coords(ids)
        centre, extent = xyz.mean(axis=0), float(np.ptp(xyz, axis=0).max())
        outer = float(np.linalg.norm(mesh.coords - centre, axis=1).max())
        if extent and outer < AIR_RATIO * extent / 2:
            out.append(Warning_("air_region", study.name,
                f"the outer boundary is {_shown(outer)} from the coils' centre, under "
                f"{AIR_RATIO / 2:g}× their size ({_shown(extent)}): the insulated boundary "
                "squeezes the field; enlarge the air or use a symmetry face"))  # fmt: skip
    return out
