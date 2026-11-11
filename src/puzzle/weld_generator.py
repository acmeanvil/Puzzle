"""Welds from a picked edge (ADR 0078 decision 7), as the bolt generator makes bolts.

`plan(project, comp, root, part, edges, leg, ...)`: the edges are on one part (by their
names, "B/-x|B/-z": the two faces they join). Of each edge's two faces, the one lying
on another part (the seat: a lapped plate's underside, a bracket's foot) tells which
part the weld joins and on which of its faces the other leg lies; the edge's other face
carries the first leg. Every edge must reach the same other part. `commands(...)` is
one batch (one undo): the selections (the path, each leg's faces) and the weld.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puzzle.errors import PuzzleError


@dataclass
class WeldPlan:
    part: str
    other: str
    edges: list[str]
    leg_faces: list[str]  # on `part`
    base_faces: list[str]  # on `other`
    notes: list[str] = field(default_factory=list)


def plan(project, comp, root, part: str, edges: list[str]) -> WeldPlan:
    """Where a weld along `edges` of `part` goes (module docstring)."""
    from puzzle.geometry.contacts import find_contacts

    if part not in {p.name for p in comp.parts}:
        raise PuzzleError(f"no part '{part}' in {comp.name}", code="not_found")
    if not edges:
        raise PuzzleError("pick the edges the weld runs along", code="missing_input")
    touching = find_contacts(project, comp, root)
    others, legs, bases = set(), [], []
    for edge in edges:
        faces = edge.split("|")
        if len(faces) != 2:
            raise PuzzleError(f"'{edge}' is not an edge between two faces (A|B)",
                              code="invalid")  # fmt: skip
        seated = []
        for pair in touching:
            for mine, my_face, theirs, their_face in (
                (pair.source_part, pair.source_face, pair.target_part, pair.target_face),
                (pair.target_part, pair.target_face, pair.source_part, pair.source_face),
            ):
                if mine == part and my_face in faces and theirs != part:
                    seated.append((my_face, theirs, their_face))
        if not seated:
            raise PuzzleError(
                f"edge '{edge}' of {part} doesn't sit on another part: neither of its faces "
                "touches one (a fillet's edge lies on the part it joins)", code="invalid",
            )  # fmt: skip
        seat = seated[0][0]
        if len({s[0] for s in seated}) > 1:
            raise PuzzleError(f"both faces of edge '{edge}' touch other parts: pick the "
                              "edge at the joint's corner", code="invalid")  # fmt: skip
        legs.append(next(f for f in faces if f != seat))
        for _f, theirs, their_face in seated:
            others.add(theirs)
            bases.append(their_face)
    if len(others) != 1:
        raise PuzzleError(f"the edges reach {sorted(others)}: one weld joins two parts",
                          code="invalid")  # fmt: skip
    return WeldPlan(part, others.pop(), list(edges), list(dict.fromkeys(legs)),
                    list(dict.fromkeys(bases)))  # fmt: skip


def _selection(geometry, entity: str, members: list[str], name: str):
    """An existing selection holding just `members`, else a new one -> (name, new?)."""
    for s in geometry.selections:
        if s.entity == entity and sorted(s.members) == sorted(members) and not s.rule:
            return s.name, False
    taken = {s.name for s in geometry.selections}
    out, k = name, 2
    while out in taken:
        out, k = f"{name} {k}", k + 1
    return out, True


def commands(project, comp, planned: WeldPlan, *, name: str, leg, leg2=None,
             type: str = "fillet", groove=None, pattern: str = "continuous",
             segment=None, pitch=None, filler=None) -> list:  # fmt: skip
    """The batch making the selections and the weld (a `Weld`'s fields as given)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Geometry, NamedSelection, Weld

    if any(w.name == name for w in comp.welds):
        raise PuzzleError(f"{comp.name} has a weld '{name}' already", code="exists")
    by_part = {p.name: project.get(p.geometry, Geometry) for p in comp.parts}
    out, chosen = [], {}
    wanted = [
        (planned.part, "edge", planned.edges, f"{name} path"),
        (planned.part, "face", planned.leg_faces, f"{name} leg"),
        (planned.other, "face", planned.base_faces, f"{name} base"),
    ]
    adds: dict = {}
    for part, entity, members, label in wanted:
        geo = by_part[part]
        made, new = _selection(geo, entity, members, label)
        chosen[(part, entity, tuple(members))] = f"{part}/{made}"
        if new:
            adds.setdefault(geo.id, []).append(
                NamedSelection(name=made, entity=entity, members=members)
            )
    for gid, selections in adds.items():
        out.append(Edit(object=str(gid), ops=[
            EditOp(op="append", path="selections", value=s.model_dump(mode="json"))
            for s in selections]))  # fmt: skip
    weld = Weld(
        name=name, type=type, groove=groove, leg=leg, leg2=leg2,
        path=chosen[(planned.part, "edge", tuple(planned.edges))],
        faces=[chosen[(planned.part, "face", tuple(planned.leg_faces))],
               chosen[(planned.other, "face", tuple(planned.base_faces))]],
        pattern=pattern, segment=segment, pitch=pitch, filler=filler,
    )  # fmt: skip
    out.append(Edit(object=str(comp.id), ops=[
        EditOp(op="append", path="welds", value=weld.model_dump(mode="json", exclude_none=True))
    ]))  # fmt: skip
    return out
