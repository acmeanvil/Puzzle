"""Fasteners from holes (ADR 0027): coaxial holes in different parts, paired.

Every cylindrical face of each part, placed as the mesher places it, is a hole (or a
shaft) on an axis line with a radius. Two faces of different parts on the same line,
with radii within 25 % of each other, are a pair a fastener can join: its ends at the
two faces, its diameter the smaller hole's. `fastener_commands` turns accepted pairs
into one batch: a face selection per hole (saved, so it survives regeneration) and the
fasteners.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.geometry.session import occ_lock


@dataclass
class HolePair:
    source_part: str
    source_face: str
    target_part: str
    target_face: str
    diameter: float  # m, the smaller hole's
    centre_distance: float  # m, between the two faces' centres
    through: list = field(default_factory=list)  # (part, face) of a stack's middle holes


def _cylinders(
    faces: dict[str, dict[str, list]],
) -> list[tuple[str, str, np.ndarray, np.ndarray, float, np.ndarray]]:
    """(part, face name, a point on the axis, axis direction, radius, face centre)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    out = []
    for part, by_name in faces.items():
        for name, shapes in by_name.items():
            if len(shapes) != 1:
                continue
            surface = BRepAdaptor_Surface(shapes[0])
            if surface.GetType() != GeomAbs_Cylinder:
                continue
            cyl = surface.Cylinder()
            ax = cyl.Axis()
            p, d = ax.Location(), ax.Direction()
            props = GProp_GProps()
            _static(BRepGProp, "SurfaceProperties")(shapes[0], props)
            c = props.CentreOfMass()
            out.append(
                (
                    part,
                    name,
                    np.array([p.X(), p.Y(), p.Z()]),
                    np.array([d.X(), d.Y(), d.Z()]),
                    float(cyl.Radius()),
                    np.array([c.X(), c.Y(), c.Z()]),
                )
            )
    return out


def find_hole_pairs(project, comp, root, *, tolerance: float | None = None) -> list[HolePair]:
    """Coaxial cylindrical faces of different parts (axis lines within `tolerance`,
    default 0.1 % of the model's size; radii within 25 %)."""
    from puzzle.geometry.contacts import _placed_faces, model_size

    with occ_lock:
        faces = _placed_faces(project, comp, root)
        size = model_size(faces) or 1.0
        tol = tolerance if tolerance is not None else 1e-3 * size
        cylinders = _cylinders(faces)
    pairs = []
    for i, (pa, na, oa, da, ra, ca) in enumerate(cylinders):
        for pb, nb, ob, db, rb, cb in cylinders[i + 1 :]:
            if pa == pb or abs(abs(da @ db) - 1) > 1e-6:
                continue
            offset = ob - oa
            if np.linalg.norm(offset - (offset @ da) * da) > tol:
                continue  # parallel, not on one line
            if abs(ra - rb) > 0.25 * max(ra, rb):
                continue
            pairs.append(HolePair(pa, na, pb, nb, 2 * min(ra, rb), float(np.linalg.norm(cb - ca))))
    return pairs


def fastener_commands(project, comp, pairs: list[HolePair], material, ends: str = "rigid") -> list:
    """The batch making a fastener per pair: each hole's face selection (an existing one
    holding exactly that face, or "Hole <n>"), then the fasteners ("Bolt 1", ...)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.geometry.contacts import _selection_for
    from puzzle.model.schema import BeamFastener, Geometry, Quantity

    parts = {p.name: p for p in comp.parts}
    made: dict = {}
    fasteners = []
    taken = {c.name for c in comp.connectors}
    n = 1
    for pair in pairs:
        sides = []
        for part, face in (
            (pair.source_part, pair.source_face),
            (pair.target_part, pair.target_face),
        ):
            if part is None:  # a bolt to ground (ADR 0050): one hole only
                sides.append(None)
                continue
            geometry = project.get(parts[part].geometry, Geometry)
            known = [*geometry.selections, *made.get(geometry.id, [])]
            hole = len([s for s in known if s.name.startswith("Hole")]) + 1
            sides.append(f"{part}/{_selection_for(geometry, [face], f'Hole {hole}', made)}")
        while f"Bolt {n}" in taken:
            n += 1
        taken.add(f"Bolt {n}")
        fasteners.append(
            BeamFastener(
                name=f"Bolt {n}",
                source=sides[0],
                target=sides[1],
                ground=sides[1] is None,
                diameter=Quantity(value=round(pair.diameter * 1e3, 6), unit="mm"),
                material=material,
                ends=ends,
            )
        )
    commands = [
        Edit(
            object=str(gid),
            ops=[
                EditOp(op="append", path="selections", value=s.model_dump(mode="json"))
                for s in sels
            ],
        )
        for gid, sels in made.items()
    ]
    if fasteners:
        commands.append(
            Edit(
                object=str(comp.id),
                ops=[
                    EditOp(op="append", path="connectors", value=f.model_dump(mode="json"))
                    for f in fasteners
                ],
            )
        )
    return commands
