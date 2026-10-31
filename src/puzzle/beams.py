"""Orienting beams (ADR 0055): each member's first axis from how its section says.

A beam section's first axis (`BeamSection.orientation`, the section's local y) may be
given as a vector, a coordinate system's axis at the member (Cartesian, or radial,
hoop and axial for a cylindrical system), the direction toward a point, a face's
normal or an edge's direction; it is made square to the member and turned by `roll`.
`with_orientations` resolves these before writing: a section over members that need
different directions (a ring of columns facing a centre) is split into one section per
member, each on a mesh line group of its own (one per meshed edge), so both solvers
take plain vectors.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np


def _direction(xyz: np.ndarray) -> np.ndarray:
    """The line through points (their main direction)."""
    centred = xyz - xyz.mean(axis=0)
    return np.linalg.svd(centred, full_matrices=False)[2][0]


def _rotate(v: np.ndarray, about: np.ndarray, degrees: float) -> np.ndarray:
    if not degrees:
        return v
    k, t = about / np.linalg.norm(about), math.radians(degrees)
    return v * math.cos(t) + np.cross(k, v) * math.sin(t) + k * (k @ v) * (1 - math.cos(t))


def first_axis(project, mesh, section, member_xyz: np.ndarray, what: str) -> np.ndarray:
    """The section's first axis for one straight member (its nodes' coordinates)."""
    from puzzle.adapters.common import InputError, support_nodes
    from puzzle.model.schema import CoordinateSystem

    along = _direction(member_xyz)
    at = member_xyz.mean(axis=0)
    if section.orient == "vector":
        v = np.asarray(section.orientation, float)
    elif section.orient == "axes":
        cs = project.objects.get(section.axes)
        if not isinstance(cs, CoordinateSystem):
            raise InputError(f"{what}: orient by a coordinate system, but none is chosen")
        v = cs.frames(at[None])[0][:, "xyz".index(section.axes_axis)]
    elif section.orient == "toward":
        if section.toward:
            group = mesh.groups.get(section.toward)
            if group is None:
                raise InputError(f"{what}: no point selection '{section.toward}' in the mesh")
            point = mesh.node_coords(group.node_ids).mean(axis=0)
        elif section.toward_point is not None:
            point = np.asarray(section.toward_point, float)
        else:
            raise InputError(f"{what}: orient toward a point, but none is given")
        v = point - at
    else:  # normal or edge: of a reference selection
        group = mesh.groups.get(section.reference or "")
        if group is None:
            raise InputError(f"{what}: no selection '{section.reference}' to orient by")
        if section.orient == "normal":
            if group.dim != 2:
                raise InputError(f"{what}: '{section.reference}' is not a face selection")
            _ids, areas, normals = support_nodes(group, mesh)
            v = (areas[:, None] * normals).sum(axis=0)
        else:
            if group.dim != 1:
                raise InputError(f"{what}: '{section.reference}' is not an edge selection")
            v = _direction(mesh.node_coords(group.node_ids))
    v = np.asarray(v, float)
    v = v - (v @ along) * along
    if np.linalg.norm(v) < 1e-9 * max(1.0, float(np.linalg.norm(at))):
        raise InputError(
            f"{what}: its orientation runs along the member, so it gives no direction "
            "across it; orient it another way",
            code="invalid",
        )
    v = _rotate(v / np.linalg.norm(v), along, section.roll)
    return v / np.linalg.norm(v)


def with_orientations(project, component_id, mesh):
    """(the mesh with a line group per member where a section needs one, the project
    whose beam sections are plain vectors). Sections given as a vector with no roll
    are left alone."""
    from puzzle.mesh.mesh import Group
    from puzzle.model.schema import Component

    comp = project.objects.get(component_id)
    if not isinstance(comp, Component) or not comp.beam_sections:
        return mesh, project
    if all(b.orient == "vector" and not b.roll for b in comp.beam_sections):
        return mesh, project
    sections = []
    for b in comp.beam_sections:
        if b.orient == "vector" and not b.roll:
            sections.append(b)
            continue
        group = mesh.groups.get(b.selection)
        if group is None:
            sections.append(b)  # the adapter says it isn't meshed
            continue
        members = [blk for blk in group.blocks(mesh) if blk.dim == 1]
        axes = []
        for blk in members:
            xyz = mesh.node_coords(np.unique(blk.connectivity))
            axes.append(first_axis(project, mesh, b, xyz, f"beam section on '{b.selection}'"))
        plain = {"orient": "vector", "roll": 0.0}
        if len(members) <= 1 or all(np.allclose(a, axes[0], atol=1e-9) for a in axes):
            vector = tuple(float(c) for c in axes[0]) if axes else b.orientation
            sections.append(b.model_copy(update=plain | {"orientation": vector}))
            continue
        for k, (blk, a) in enumerate(zip(members, axes, strict=True), start=1):
            # Keep the part first: adapters find a part's sections by "Part/...".
            name = (
                f"{b.selection} (member {k})" if "/" in b.selection else f"{b.selection}/member {k}"
            )
            if name not in mesh.groups:
                nodes = np.unique(blk.connectivity)
                mesh = dataclasses.replace(
                    mesh, groups={**mesh.groups, name: Group(name, 1, [blk.entity], nodes)}
                )
            sections.append(b.model_copy(update=plain | {
                "selection": name, "orientation": tuple(float(c) for c in a)}))  # fmt: skip
    objects = dict(project.objects)
    objects[comp.id] = comp.model_copy(update={"beam_sections": sections})
    return mesh, dataclasses.replace(project, objects=objects)
