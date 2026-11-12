"""Weak springs (ADR 0080): bodies only sliding contact holds get small springs to ground.

A body held only through frictionless or frictional contact is free until the contact
closes and is pressed, so its first solve is singular. `applied` (in `run_study`, after
meshing) gives each such body, as `contact_check.held_by_contact_only` finds them (and,
with the study's `weak_springs` = "always", bodies held by nothing in a held component),
three nodes with springs to ground in x, y and z:

- the node nearest the body's centre and the two farthest out along its first two
  principal axes, none on a contact, loaded or held face where the body has others;
- each spring factor × E × V / L² (the body's Young's modulus, its bounding box's volume
  and diagonal: E A / L for a bar, E L for a cube), so a seated body passes about
  `factor` of its load through them however slender it is.

They are written as the elastic supports' springs (a node group with an `elastic_support`
of that stiffness per node), so every adapter that takes elastic supports takes them.
After the solve `finish` reports the force they carry: a share above the study's
`weak_spring_limit` means the body never seated (warning `weak_springs_carry`).

Never for a model held by nothing at all (`unconstrained` stays refused), and not in
modal or buckling studies, whose free modes are wanted.
"""

from __future__ import annotations

import dataclasses

import numpy as np

STEPS = ("stationary", "transient")
GROUP = "__weak_springs"


@dataclasses.dataclass
class Springs:
    part: str
    nodes: np.ndarray
    stiffness: float  # N/m, each of x, y and z at each node
    supports: np.ndarray = dataclasses.field(default_factory=lambda: np.zeros(0, np.int64))
    """The model's held nodes, whose reactions carry the rest of the load."""


def bodies(project, comp, mode: str) -> list[str]:
    """The parts needing weak springs: held only by sliding contact (and, `always`, by
    nothing in a held component)."""
    from puzzle.contact_check import held_by_contact_only

    wanted = {"contact_only_hold"} | ({"free_body"} if mode == "always" else set())
    return [p.object for p in held_by_contact_only(project, comp) if p.code in wanted]


def _modulus(project, comp, part: str) -> float:
    from puzzle.model.schema import Material

    for a in comp.materials:
        if a.selection.split("/", 1)[0] == part:
            m = project.objects.get(a.material)
            if isinstance(m, Material) and "youngs_modulus" in m.properties:
                return float(m.properties["youngs_modulus"].to_si())
    return 200e9  # no material yet: Project.problems says so before a solve


def _avoid(comp, part: str) -> set[str]:
    """The part's contact, loaded and held faces."""
    out = {s for c in comp.contacts for s in (c.source, c.target)}
    for ph in comp.physics:
        out |= {f.selection for f in ph.features}
    return {s for s in out if s and s.split("/", 1)[0] == part}


def nodes_for(mesh, part: str, avoid: set[str]) -> np.ndarray:
    """Three nodes of the part spread out: nearest its centre, and farthest along its
    first two principal axes, off the faces in `avoid` where it has other nodes."""
    ids = mesh.groups[part].node_ids
    skip = set()
    for name in avoid:
        if name in mesh.groups:
            skip |= set(mesh.groups[name].node_ids.tolist())
    free = np.array([n for n in ids.tolist() if n not in skip]) if skip else ids
    if len(free) < 3:
        free = ids
    xyz = mesh.node_coords(free)
    centre = mesh.node_coords(ids).mean(axis=0)
    _, _, vt = np.linalg.svd(mesh.node_coords(ids) - centre, full_matrices=False)
    chosen = [int(free[np.argmin(np.linalg.norm(xyz - centre, axis=1))])]
    for axis in vt[:2]:
        for n in free[np.argsort(-((xyz - centre) @ axis))].tolist():
            if n not in chosen:
                chosen.append(int(n))
                break
    return np.array(chosen, dtype=np.int64)


def _scale(mesh, part: str) -> float:
    """The body's stiffness per unit modulus, V / L² (A / L for a bar, L for a cube; a 2D
    part's area per metre of depth), from its bounding box: E times it is how stiff the
    body is, so the springs stay `factor` of that however slender it is."""
    extents = np.ptp(mesh.node_coords(mesh.groups[part].node_ids), axis=0)
    size = float(np.linalg.norm(extents))
    volume = float(np.prod([e for e in extents if e > 1e-12 * size])) if size else 0.0
    return volume / size**2 if size else 1.0


def applied(project, study, mesh, caps=None, record=None):
    """(mesh, project, springs): the mesh with each body's spring nodes as a group, the
    project with an elastic support there, and the springs, for `finish`."""
    from puzzle.mesh.mesh import Group, Mesh
    from puzzle.model.schema import Component, Feature, Quantity

    mode = getattr(study, "weak_springs", "auto")
    comp = project.objects.get(study.component)
    if mode == "never" or not isinstance(comp, Component):
        return mesh, project, []
    solid = [i for i, p in enumerate(comp.physics) if p.type == "solid_mechanics"]
    steps = [s for s in study.solve_steps() if s.type in STEPS
             and (not s.physics or "solid_mechanics" in s.physics)]  # fmt: skip
    if not solid or not steps:
        return mesh, project, []
    parts = [p for p in bodies(project, comp, mode) if p in mesh.groups]
    if not parts:
        return mesh, project, []
    if caps is not None and "elastic_support" not in caps.features:
        from puzzle.adapters.common import InputError

        raise InputError(f"{', '.join(parts)} need weak springs, which this solver doesn't "
                         "write; set the study's weak springs to never and hold them",
                         code="unsupported")  # fmt: skip
    factor = float(getattr(study, "weak_spring_factor", 1e-6))
    from puzzle.contact_check import HOLDING

    held = [f.selection for ph in comp.physics for f in ph.features
            if f.type in HOLDING and f.selection in mesh.groups]  # fmt: skip
    supports = (np.unique(np.concatenate([mesh.groups[g].node_ids for g in held]))
                if held else np.zeros(0, np.int64))  # fmt: skip
    groups = dict(mesh.groups)
    features = list(comp.physics[solid[0]].features)
    springs = []
    for part in parts:
        nodes = nodes_for(mesh, part, _avoid(comp, part))
        k = factor * _modulus(project, comp, part) * _scale(mesh, part)
        name = f"{part}/{GROUP}"
        groups[name] = Group(name, 0, [], np.sort(nodes))
        # Per node, not per area: a point group's nodes each count as 1 m² (support_nodes).
        stiffness = Quantity(value=k, unit="N/m^3")
        features.append(Feature(type="elastic_support", selection=name,
                                values={"kn": stiffness, "kt": stiffness}))  # fmt: skip
        springs.append(Springs(part, nodes, k, supports))
        if record is not None:
            from puzzle import progress

            record(progress.Message(level="info", code="weak_springs",
                                    text=f"weak springs on '{part}' ({k:.3g} N/m at three "
                                    "nodes) until its contact closes"))  # fmt: skip
    physics = list(comp.physics)
    physics[solid[0]] = physics[solid[0]].model_copy(update={"features": features})
    objects = dict(project.objects)
    objects[comp.id] = comp.model_copy(update={"physics": physics})
    written = Mesh(mesh.node_ids, mesh.coords, list(mesh.blocks), groups)
    for key in ("planar", "axisymmetric"):
        if hasattr(mesh, key):
            setattr(written, key, getattr(mesh, key))
    return written, dataclasses.replace(project, objects=objects), springs


def finish(results, springs, limit: float = 1e-3, record=None) -> list[str]:
    """Each step's spring forces (`weak_spring_force` on their nodes) and a warning for
    every body whose springs carry more than `limit` of the load path."""
    from puzzle.results.model import Field

    if not springs:
        return []
    warnings = []
    for step in results.steps:
        u = step.fields.get("displacement")
        if u is None:
            continue
        ids = np.concatenate([s.nodes for s in springs])
        forces = np.concatenate([s.stiffness * u.at(s.nodes)[:, :3] for s in springs])
        step.fields["weak_spring_force"] = Field("weak_spring_force", "node", ["x", "y", "z"],
                                                 ids, forces)  # fmt: skip
        reaction = step.fields.get("reaction_force")
        for s in springs:
            # At the supports only: a solver's reactions over every node (Code_Aster's
            # REAC_NODA) sum to about nothing, the applied loads among them.
            held = (float(np.linalg.norm(np.nansum(reaction.at(s.supports)[:, :3], axis=0)))
                    if reaction is not None and len(s.supports) else 0.0)  # fmt: skip
            carried = float(np.linalg.norm((s.stiffness * u.at(s.nodes)[:, :3]).sum(axis=0)))
            share = carried / (carried + held) if carried + held > 0 else 0.0
            if share > limit:
                text = (f"the weak springs on '{s.part}' carry {share:.2%} of the load "
                        f"({carried:.3g} N) in {step.label()}: it never seated on its "
                        "contact, so its result is not to be trusted")  # fmt: skip
                warnings.append(text)
                if record is not None:
                    from puzzle import progress

                    record(progress.Message(level="warning", code="weak_springs_carry",
                                            text=text))  # fmt: skip
    return warnings
