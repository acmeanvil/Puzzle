"""Fasteners from holes (ADR 0027): coaxial holes in different bodies, one fastener per
stack.

Every cylindrical face of each part, placed as the mesher places it, is a hole (or a
shaft) on an axis line with a radius, in one of the part's bodies. Holes on one line,
with radii within 25 % of each other, are a stack a fastener can join when they are in
two bodies or more (of different parts, or several bodies of one part, as a STEP
assembly imported as one part): its ends at the first and the last hole along the axis,
the holes between passed through (`through`), its diameter the smallest hole's. A line
breaks into separate stacks where one hole ends more than a hole diameter before the
next begins (two joints on one axis). `fastener_commands` turns accepted stacks into
one batch: a face selection per hole (saved, so it survives regeneration) and the
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


@dataclass
class Hole:
    part: str
    face: str
    point: np.ndarray  # on the axis
    axis: np.ndarray  # unit
    diameter: float  # m
    centre: np.ndarray  # the face's centre
    body: str | None = None  # the part's body it is a face of
    length: float | None = None  # m, the face's extent along its axis


def _cylinders(faces: dict[str, dict[str, list]], owners: dict | None = None) -> list[Hole]:
    """Every face that is one cylinder, as a hole (in its body, from `owners`: part ->
    face -> body, as `contacts._placed_faces` fills it)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    uv_bounds = _static(BRepTools, "UVBounds")
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
            _, _, vmin, vmax = uv_bounds(shapes[0])  # v runs along a cylinder's axis, in m
            axis = np.array([d.X(), d.Y(), d.Z()])
            out.append(
                Hole(
                    part,
                    name,
                    np.array([p.X(), p.Y(), p.Z()]),
                    axis / np.linalg.norm(axis),
                    2 * float(cyl.Radius()),
                    np.array([c.X(), c.Y(), c.Z()]),
                    (owners or {}).get(part, {}).get(name),
                    float(vmax - vmin),
                )
            )
    return out


def find_holes(project, comp, root) -> tuple[list[Hole], float]:
    """Every cylindrical face of the component's parts, and the model's size (m)."""
    from puzzle.geometry.contacts import _placed_faces, model_size

    owners: dict = {}
    with occ_lock:
        faces = _placed_faces(project, comp, root, owners)
        size = model_size(faces) or 1.0
        found = _cylinders(faces, owners)
    return found, size


def stacks(found: list[Hole], tolerance: float, limit=None, *, same_part: bool = False,
           ground: bool = False) -> list[HolePair]:  # fmt: skip
    """Holes on one axis line (within `tolerance`, diameters within 25 %), in order along
    it, become one bolt from the first to the last when they are in two bodies or more
    (of different parts, or of one part): a stack's middle holes are the bolt's
    `through`. A line breaks where one hole ends more than a hole diameter before the
    next begins (two joints on one axis; for holes of unknown length, where their
    centres are more than 5 diameters apart), and where a hole repeats the body before
    it (unless `same_part`: a clamp's two holes). With `ground`, a hole on no line with
    another gets a bolt to ground. `limit(first, last)` may refuse a bolt."""
    parent = list(range(len(found)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(found):
        for j in range(i + 1, len(found)):
            b = found[j]
            if abs(abs(float(a.axis @ b.axis)) - 1) > 1e-6:
                continue
            offset = b.point - a.point
            if np.linalg.norm(offset - (offset @ a.axis) * a.axis) > tolerance:
                continue
            if abs(a.diameter - b.diameter) > 0.25 * max(a.diameter, b.diameter):
                continue
            parent[root(j)] = root(i)
    lines: dict[int, list[Hole]] = {}
    for i, h in enumerate(found):
        lines.setdefault(root(i), []).append(h)
    out = []
    for holes_ in lines.values():
        axis = holes_[0].axis
        holes_.sort(key=lambda h: float(h.centre @ axis))
        chains, chain = [], [holes_[0]]
        for h in holes_[1:]:
            last = chain[-1]
            d = max(h.diameter, last.diameter)
            gap = float((h.centre - last.centre) @ axis)
            if h.length is not None and last.length is not None:
                apart = gap - (h.length + last.length) / 2 > d
            else:
                apart = gap > 5 * d
            repeat = (h.part, h.body) == (last.part, last.body) and not same_part
            if apart or repeat:
                chains.append(chain)
                chain = [h]
            else:
                chain.append(h)
        chains.append(chain)
        for c in chains:
            if len(c) == 1 and ground:  # alone on its axis: anchored to the ground
                h = c[0]
                out.append(HolePair(h.part, h.face, None, None, h.diameter, 0.0))
                continue
            if len({(h.part, h.body) for h in c}) < 2 and not (same_part and len(c) >= 2):
                continue
            first, last = c[0], c[-1]
            if limit is not None and not limit(first, last):
                continue
            out.append(HolePair(first.part, first.face, last.part, last.face,
                                min(h.diameter for h in c),
                                float(np.linalg.norm(last.centre - first.centre)),
                                [(h.part, h.face) for h in c[1:-1]]))  # fmt: skip
    return sorted(out, key=lambda p: (p.source_part, p.source_face))


def find_hole_pairs(project, comp, root, *, tolerance: float | None = None) -> list[HolePair]:
    """The component's stacks of coaxial holes in different bodies (axis lines within
    `tolerance`, default 0.1 % of the model's size; radii within 25 %), one per joint:
    the first hole, the last and those between (`stacks`)."""
    found, size = find_holes(project, comp, root)
    return stacks(found, tolerance if tolerance is not None else 1e-3 * size)


def fastener_commands(project, comp, pairs: list[HolePair], material, ends: str = "rigid") -> list:
    """The batch making a fastener per stack: each hole's face selection (an existing one
    holding exactly that face, or "Hole <n>"; a stack's middle holes "Stack hole <n>"),
    then the fasteners ("Bolt 1", ...), the middle holes as their `through`."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import BeamFastener, Geometry, NamedSelection, Quantity

    parts = {p.name: p for p in comp.parts}
    made: dict = {}
    fasteners = []
    taken = {c.name for c in comp.connectors}
    n = 1

    def selection(part: str, face: str, prefix: str) -> str:
        """The face's selection: the user's holding just it, else one made in this
        batch for the same role (parts sharing a geometry share it), else a new one."""
        geometry = project.get(parts[part].geometry, Geometry)
        new = made.setdefault(geometry.id, [])
        mine = [s for s in new if s.name.startswith(f"{prefix} ")]
        for s in [*geometry.selections, *mine]:
            if s.entity == "face" and s.members == [face]:
                return f"{part}/{s.name}"
        names = {s.name for s in [*geometry.selections, *new]}
        k = 1
        while f"{prefix} {k}" in names:
            k += 1
        new.append(NamedSelection(name=f"{prefix} {k}", entity="face", members=[face]))
        return f"{part}/{prefix} {k}"

    for pair in pairs:
        sides = []
        for part, face in (
            (pair.source_part, pair.source_face),
            (pair.target_part, pair.target_face),
        ):
            if part is None:  # a bolt to ground (ADR 0050): one hole only
                sides.append(None)
                continue
            sides.append(selection(part, face, "Hole"))
        through = [selection(part, face, "Stack hole") for part, face in pair.through]
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
                through=through,
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
        if sels
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
