"""The bolt generator (ADR 0050): bolts proposed for a component's holes, then made in
one command batch (one undo).

Holes are the parts' cylindrical faces as placed (`geometry.holes`). Anchors come
from a rule (`BoltRule`: diameters, parts, an axis direction, a coaxial tolerance,
lengths), from two selection sets (each face of one paired with the coaxial face of
the other), or from picked faces (paired among themselves). Each proposal is a pair of
holes of different parts on one axis; the generator gives it a bolt type (the rule's,
or the largest type that fits the hole), a name by the rule's pattern and its group,
and tags it with the rule, so running the rule again says which bolts it would add and
which no longer have holes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from puzzle.geometry.holes import HolePair


@dataclass
class Hole:
    part: str
    face: str
    point: np.ndarray  # on the axis
    axis: np.ndarray  # unit
    diameter: float  # m
    centre: np.ndarray  # the face's centre


@dataclass
class Proposal:
    pair: HolePair
    name: str
    type: str | None  # a bolt type's id
    type_name: str | None
    length: float  # m, between the holes' centres
    note: str = ""


@dataclass
class Plan:
    """What a run of the generator would do: the bolts to add, and (for a rule run
    again) the bolts it made whose holes are gone."""

    add: list[Proposal] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)  # names of bolts to remove
    kept: list[str] = field(default_factory=list)  # names of bolts already there


def holes(project, comp, root) -> tuple[list[Hole], float]:
    """Every cylindrical face of the component's parts, and the model's size (m)."""
    from puzzle.geometry.contacts import _placed_faces, model_size
    from puzzle.geometry.holes import _cylinders
    from puzzle.geometry.session import occ_lock

    with occ_lock:
        faces = _placed_faces(project, comp, root)
        size = model_size(faces) or 1.0
        found = _cylinders(faces)
    return [Hole(p, n, o, d / np.linalg.norm(d), 2 * r, c) for p, n, o, d, r, c in found], size


def pair(found: list[Hole], tolerance: float, limit=None, *, same_part: bool = False,
         ground: bool = False) -> list[HolePair]:  # fmt: skip
    """Holes on one axis line (within `tolerance`, diameters within 25 %), in order along
    it, become one bolt from the first to the last when they are of two parts or more:
    a stack's middle holes are the bolt's `through`. A line breaks where consecutive
    holes are more than 5 diameters apart (two joints on one axis), and where a hole
    repeats the part before it (unless `same_part`: a clamp's two holes). With `ground`,
    a hole on no line with another gets a bolt to ground. `limit(first, last)` may
    refuse a bolt."""
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
            gap = float((h.centre - chain[-1].centre) @ axis)
            repeat = h.part == chain[-1].part and not same_part
            if gap > 5 * max(h.diameter, chain[-1].diameter) or repeat:
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
            if len({h.part for h in c}) < 2 and not (same_part and len(c) >= 2):
                continue
            first, last = c[0], c[-1]
            if limit is not None and not limit(first, last):
                continue
            out.append(HolePair(first.part, first.face, last.part, last.face,
                                min(h.diameter for h in c),
                                float(np.linalg.norm(last.centre - first.centre)),
                                [(h.part, h.face) for h in c[1:-1]]))  # fmt: skip
    return sorted(out, key=lambda p: (p.source_part, p.source_face))


def by_rule(project, comp, root, rule) -> list[HolePair]:
    """The pairs a rule picks out."""
    found, size = holes(project, comp, root)
    lo = rule.min_diameter.to_si() if rule.min_diameter else 0.0
    hi = rule.max_diameter.to_si() if rule.max_diameter else math.inf
    tol = rule.coaxial_tolerance.to_si() if rule.coaxial_tolerance else 1e-3 * size
    axis = np.asarray(rule.axis, float) / np.linalg.norm(rule.axis) if rule.axis else None
    cos = math.cos(math.radians(rule.axis_tolerance))
    found = [
        h
        for h in found
        if lo - 1e-9 <= h.diameter <= hi + 1e-9
        and (not rule.parts or h.part in rule.parts)
        and (axis is None or abs(float(h.axis @ axis)) >= cos)
    ]
    short = rule.min_length.to_si() if rule.min_length else 0.0
    long = rule.max_length.to_si() if rule.max_length else math.inf

    def length_ok(a: Hole, b: Hole) -> bool:
        return short - 1e-9 <= float(np.linalg.norm(b.centre - a.centre)) <= long + 1e-9

    return pair(found, tol, length_ok, same_part=rule.same_part, ground=rule.ground_unpaired)


def by_sets(project, comp, root, first: str, second: str) -> list[HolePair]:
    """Each hole face of one selection paired with the coaxial hole of the other."""
    found, size = holes(project, comp, root)
    members = {name: _faces_of(project, comp, name) for name in (first, second)}

    def in_set(h: Hole, name: str) -> bool:
        return (h.part, h.face) in members[name]

    chosen = [h for h in found if in_set(h, first) or in_set(h, second)]
    return pair(chosen, 1e-3 * size,
                lambda a, b: (in_set(a, first) and in_set(b, second))
                or (in_set(a, second) and in_set(b, first)))  # fmt: skip


def by_picks(project, comp, root, picked: list[tuple[str, str]]) -> list[HolePair]:
    """Picked hole faces ((part, face) pairs), paired among themselves by axis."""
    found, size = holes(project, comp, root)
    return pair([h for h in found if (h.part, h.face) in set(picked)], 1e-3 * size)


def _faces_of(project, comp, selection: str) -> set[tuple[str, str]]:
    """(part, face name) of a "Part/Selection" face selection's members."""
    from puzzle.model.schema import Geometry

    part, _, name = selection.partition("/")
    instance = next((p for p in comp.parts if p.name == part), None)
    geometry = project.objects.get(instance.geometry) if instance is not None else None
    if not isinstance(geometry, Geometry):
        return set()
    sel = next((s for s in geometry.selections if s.name == name and s.entity == "face"), None)
    return {(part, m) for m in sel.members} if sel is not None else set()


def matching_type(project, diameter: float):
    """The largest bolt type that fits a hole of this diameter (a clearance hole is a
    little larger than its bolt), within 25 %: an M10 for a 10.5 mm hole."""
    from puzzle.model.schema import BoltType

    best = None
    for t in project.of_type(BoltType):
        d = t.diameter.to_si()
        if d <= diameter * (1 + 1e-6) and d >= 0.75 * diameter and (best is None or d > best[0]):
            best = (d, t)
    return best[1] if best else None


def plan(project, comp, pairs: list[HolePair], rule=None, bolt_type=None) -> Plan:
    """Names, types and lengths for the pairs; with a rule run again, the bolts it made
    that are still there (kept) and those whose holes are gone (stale)."""
    from puzzle.model.schema import BoltType

    out = Plan()
    made = {(c.source, c.target): c for c in comp.connectors if rule and c.rule == rule.name}
    existing = {frozenset((c.source, c.target)) for c in comp.connectors}
    pattern = rule.names if rule is not None else "Bolt {n}"
    taken = {c.name for c in comp.connectors}
    n = 1
    seen = set()
    for p in pairs:
        ends = frozenset(_ends_of(project, comp, p))
        if ends and ends in existing:
            name = next(c.name for c in comp.connectors if frozenset((c.source, c.target)) == ends)
            out.kept.append(name)
            seen.add(ends)
            continue
        chosen = bolt_type or (
            project.objects.get(rule.type) if rule is not None and rule.type else None
        )
        if not isinstance(chosen, BoltType):
            chosen = matching_type(project, p.diameter)
        while (name := pattern.format(n=n, rule=rule.name if rule else "")) in taken:
            n += 1
        taken.add(name)
        note = "" if chosen is not None else "no bolt type fits: its own diameter, the hole's"
        kind, kind_name = (str(chosen.id), chosen.name) if chosen else (None, None)
        out.add.append(Proposal(p, name, kind, kind_name, p.centre_distance, note))
    for ends, c in made.items():
        if frozenset(ends) not in seen:
            out.stale.append(c.name)
    return out


def _bearing_rings(project, comp, planned: Plan, commands_: list) -> list:
    """Bearing rings imprinted under the heads and nuts (ADR 0050): an `imprint` step on
    each hole's geometry (once per geometry and hole), a face selection "Bearing n" on
    its rings, and each bolt's `bearing` naming it, as its joint style needs."""
    from uuid import UUID

    from puzzle.bolts import JOINTS, type_values
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import BoltType

    made: dict = {}  # (geometry id, face) -> selection name
    steps: dict = {}  # geometry id -> [history steps], [selections]
    bolts = commands_[-1]
    for op, proposal in zip(bolts.ops, planned.add, strict=True):
        btype = project.objects.get(UUID(proposal.type)) if proposal.type else None
        if not isinstance(btype, BoltType):
            continue
        kinds = JOINTS.get(btype.joint, ("wall", "wall"))
        radius = type_values(project, btype)["diameter"] * 0.8
        if btype.bearing_diameter is not None:
            radius = btype.bearing_diameter.to_si() / 2
        bearing = []
        ends = ((proposal.pair.source_part, proposal.pair.source_face),
                (proposal.pair.target_part, proposal.pair.target_face))  # fmt: skip
        for (part, face), kind in zip(ends, kinds, strict=True):
            if part is None or kind not in ("head", "nut"):
                bearing.append(None)
                continue
            geometry = project.objects[next(i.geometry for i in comp.parts if i.name == part)]
            key = (geometry.id, face)
            if key not in made:
                history, selections = steps.setdefault(geometry.id, ([], []))
                taken = {s.name for s in geometry.history} | {s["name"] for s in history}
                n = 1
                while f"Ring {n}" in taken:
                    n += 1
                history.append({"kind": "imprint", "name": f"Ring {n}",
                                "args": {"hole": face, "r": radius}})  # fmt: skip
                names = {s.name for s in geometry.selections} | {s["name"] for s in selections}
                k = 1
                while f"Bearing {k}" in names:
                    k += 1
                selections.append({"name": f"Bearing {k}", "entity": "face",
                                   "members": [f"Ring {n}/annulus"]})  # fmt: skip
                made[key] = f"Bearing {k}"
            bearing.append(f"{part}/{made[key]}")
        if any(bearing):
            op.value = dict(op.value) | {"bearing": bearing}
    edits = [
        Edit(object=str(gid), ops=[
            *(EditOp(op="append", path="history", value=h) for h in history),
            *(EditOp(op="append", path="selections", value=s) for s in selections),
        ])
        for gid, (history, selections) in steps.items()
    ]  # fmt: skip
    return [*edits, *commands_]


def _stack_selections(project, comp, planned: Plan) -> dict[tuple[str, str], str]:
    """A face selection ("Part/Name") for each middle hole of the planned stacks: an
    existing one holding exactly that face, else a new "Stack hole n"."""
    from puzzle.model.schema import Geometry

    out: dict[tuple[str, str], str] = {}
    new_names: dict = {}
    for proposal in planned.add:
        for part, face in proposal.pair.through:
            if (part, face) in out:
                continue
            geometry = project.objects[next(i.geometry for i in comp.parts if i.name == part)]
            hit = next((s for s in geometry.selections
                        if s.entity == "face" and s.members == [face]), None)  # fmt: skip
            if isinstance(geometry, Geometry) and hit is not None:
                out[part, face] = f"{part}/{hit.name}"
                continue
            taken = {s.name for s in geometry.selections} | new_names.setdefault(geometry.id, set())
            n = 1
            while f"Stack hole {n}" in taken:
                n += 1
            new_names[geometry.id].add(f"Stack hole {n}")
            out[part, face] = f"{part}/Stack hole {n}"
    return out


def _ends_of(project, comp, p: HolePair) -> tuple[str, str] | tuple[()]:
    """The existing face selections holding exactly each hole, if both exist."""
    from puzzle.model.schema import Geometry

    names = []
    for part, face in ((p.source_part, p.source_face), (p.target_part, p.target_face)):
        if part is None:  # a bolt to ground's missing end
            names.append(None)
            continue
        instance = next(i for i in comp.parts if i.name == part)
        geometry = project.objects.get(instance.geometry)
        hit = (
            next(
                (s for s in geometry.selections if s.entity == "face" and s.members == [face]), None
            )
            if isinstance(geometry, Geometry)
            else None
        )
        if hit is None:
            return ()
        names.append(f"{part}/{hit.name}")
    return tuple(names)


def commands(project, comp, planned: Plan, *, material=None, ends=None, rule=None,
             remove_stale: bool = False) -> list:  # fmt: skip
    """The batch: the holes' face selections and the bolts (typed bolts take their
    type's values; others the hole's diameter and `material`), the rule kept on the
    component, and, if asked, the rule's bolts whose holes are gone removed."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.geometry.holes import fastener_commands

    pairs = [p.pair for p in planned.add]
    for proposal in planned.add:
        if proposal.type is None and material is None:
            raise ValueError(f"{proposal.name}: no bolt type fits its hole; choose a type or "
                             "a material")  # fmt: skip
    # The bolts are built whole first; typed ones then drop the placeholder material.
    placeholder = material or "00000000-0000-0000-0000-000000000000"
    out = (
        fastener_commands(project, comp, pairs, placeholder, ends or "distributing")
        if pairs
        else []
    )
    stack_selections = _stack_selections(project, comp, planned)
    if pairs:  # fastener_commands' last command appends the bolts: give them our values
        bolts = out[-1]
        for op, proposal in zip(bolts.ops, planned.add, strict=True):
            value = dict(op.value)
            value["name"] = proposal.name
            if proposal.pair.through:
                value["through"] = [stack_selections[h] for h in proposal.pair.through]
            if proposal.type is not None:
                value["type"] = proposal.type
                # Built untyped first, the bolt took the defaults: its type's win.
                for key in ("diameter", "material", "ends", "joint", "formulation"):
                    value.pop(key, None)
                if ends:
                    value["ends"] = ends
            if rule is not None:
                value["rule"] = rule.name
                value["group"] = rule.group or rule.name
            if proposal.pair.target_part is None:  # a bolt to ground
                value["ground"] = True
                if rule is not None:
                    value["ground_direction"] = list(rule.ground_direction)
                    if rule.ground_length is not None:
                        value["ground_length"] = rule.ground_length.model_dump(mode="json")
            op.value = value
    if rule is not None and rule.bearing_faces and pairs:
        out = _bearing_rings(project, comp, planned, out)
    if stack_selections:  # the middle holes' selections, before the bolts naming them
        made: dict = {}
        for (part, face), name in stack_selections.items():
            geometry = project.objects[next(i.geometry for i in comp.parts if i.name == part)]
            made.setdefault(geometry.id, {})[name.split("/", 1)[1]] = face
        selections = [
            Edit(object=str(gid), ops=[
                EditOp(op="append", path="selections",
                       value={"name": n, "entity": "face", "members": [f]})
                for n, f in faces.items()
            ])
            for gid, faces in made.items()
        ]  # fmt: skip
        out = [*selections, *out]
    if rule is not None:
        known = [r.name for r in comp.bolt_rules]
        stored = rule.model_dump(mode="json")
        op = (
            EditOp(op="set", path=f"bolt_rules.{known.index(rule.name)}", value=stored)
            if rule.name in known
            else EditOp(op="append", path="bolt_rules", value=stored)
        )
        out.append(Edit(object=str(comp.id), ops=[op]))
    if remove_stale and planned.stale:
        indices = sorted((i for i, c in enumerate(comp.connectors) if c.name in planned.stale),
                         reverse=True)  # fmt: skip
        removals = [EditOp(op="remove", path=f"connectors.{i}") for i in indices]
        out.append(Edit(object=str(comp.id), ops=removals))
    return out
