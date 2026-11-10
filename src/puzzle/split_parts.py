"""One part per body (ADR 0039): an imported STEP assembly is one part holding every
body; this makes each body a part of its own, so contacts, bolts and results work part
by part as in an assembly built from parts.

Each body becomes a geometry made from the same file: an `import` step, then a `keep`
step holding that body (its name kept, so body selections still read). Face, edge and
point names are the import's, so every selection moves to the geometry of the body
holding its members, under its own name. References across the project ("part/
selection", or the part itself) follow: a material assignment, load, support or
section on a selection spanning several bodies becomes one per new part; another
spanning reference (a contact's side, say) is left on the first and reported.

The bodies a part's Share faces joined are joined by bonded contacts when asked (they
are no longer one part): the touching pairs the contact search finds, less the pairs a
contact already joins. Everything is one batch: one undo takes it all back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

from puzzle.errors import PuzzleError

IMPORT = "F"  # the import step's name: faces are "F/face-N"
SPLIT = ("materials", "physics", "beam_sections", "shell_sections")  # items duplicated


@dataclass
class Plan:
    """What splitting a part makes, for review before it is submitted."""

    part: str
    parts: list[tuple[str, str]]  # (new part name, body)
    commands: list = field(default_factory=list)
    bonds: list[str] = field(default_factory=list)  # bonded contacts added, by name
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (f"'{self.part}' becomes {len(self.parts)} parts: "
                + ", ".join(name for name, _ in self.parts))  # fmt: skip
        if self.bonds:
            text += f"; {len(self.bonds)} bonded contacts where the bodies touch"
        return text


def _renamed(member: str, plain: bool) -> str:
    """A plain import's member name in the history geometry (faces gain F/)."""
    if not plain:
        return member
    return "|".join(f"{IMPORT}/{t}" if t.startswith(("face-", "edge-")) else t
                    for t in member.split("|"))  # fmt: skip


def plan(project, comp, part_name: str, root, *, bond: bool = True) -> Plan:
    """The commands that split `comp`'s part `part_name` into one part per body."""
    from puzzle.commands.model import Create, Delete, Edit, EditOp
    from puzzle.geometry.contacts import _face_owners, body_labels
    from puzzle.geometry.naming import named_geometry
    from puzzle.geometry.session import occ_lock
    from puzzle.model.schema import Component, Geometry

    part = next((p for p in comp.parts if p.name == part_name), None)
    if part is None:
        raise PuzzleError(f"component '{comp.name}' has no part '{part_name}'", code="not_found")
    geometry = project.get(part.geometry, Geometry)
    with occ_lock:
        named = named_geometry(project, geometry, root)
        owners = _face_owners(named)
    bodies = list(named.of.get("body", {}))
    if len(bodies) < 2:
        raise PuzzleError(f"part '{part_name}' has one body: nothing to split", code="invalid")
    plain = not geometry.history
    labels = body_labels(project, comp)
    taken = {p.name for p in comp.parts if p.name != part_name}
    names: dict[str, str] = {}
    for body in bodies:
        name = labels.get((part_name, body)) or f"{part_name} {body}"
        unique, n = name, 2
        while unique in taken:
            unique, n = f"{name} {n}", n + 1
        taken.add(unique)
        names[body] = unique
    out = Plan(part_name, [(names[b], b) for b in bodies])

    def owner(member: str, entity: str) -> str | None:
        if entity == "body":
            return member if member in names else None
        return owners.get(member.split("|")[0])

    # Each selection's members, by body; a selection's bodies say where it goes.
    where: dict[str, list[str]] = {}
    per_body: dict[str, list] = {b: [] for b in bodies}
    for sel in geometry.selections:
        held: dict[str, list[str]] = {}
        for m in sel.members:
            b = owner(m, sel.entity)
            if b is not None:
                held.setdefault(b, []).append(m)
        if sel.rule and not held:  # a rule finds its members anew in every body
            held = {b: [] for b in bodies}
        where[sel.name] = [names[b] for b in bodies if b in held]
        for b, members in held.items():
            data = sel.model_dump(mode="json")
            data["members"] = [_renamed(m, plain) for m in members]
            data["anchors"] = {_renamed(k, plain): v for k, v in (sel.anchors or {}).items()
                               if k in members}  # fmt: skip
            per_body[b].append(data)
    where[""] = [names[b] for b in bodies]  # the part itself

    # The new geometries.
    created: dict[str, str] = {}
    for body in bodies:
        data = geometry.model_dump(mode="json")
        gid = str(uuid4())
        colours = {k: v for k, v in geometry.body_colours.items() if k == body}
        data.update(id=gid, name=names[body], selections=per_body[body], body_colours=colours,
                    hidden_bodies=[b for b in geometry.hidden_bodies if b == body])  # fmt: skip
        if plain:
            index = bodies.index(body) + 1
            keep = {"solids": [index], "body": body}
            steps = [{"kind": "import", "name": IMPORT, "args": {"file": geometry.source}},
                     {"kind": "keep", "name": "Keep", "args": keep}]  # fmt: skip
            brep = (geometry.source or "").lower().endswith(".brep")
            if brep and geometry.source_unit:
                steps[0]["args"]["unit"] = geometry.source_unit
            data.update(history=steps, source=None)
        else:
            keep = {"kind": "keep", "name": "Keep", "args": {"bodies": [body], "body": body}}
            data["history"] = [*data["history"], keep]
        created[body] = gid
        out.commands.append(Create(kind="geometry", data=data))

    # References follow their body.
    def rewrite(value, path=""):
        """`value` with part references moved; a list item on a spanning selection is
        repeated per part (in SPLIT lists), others are left on the first and noted."""
        if isinstance(value, str):
            if path.split(".")[-1] == "name":
                return value  # an object's own name, never a reference
            hit = _target(value, bare=path.split(".")[-1] == "selection")
            if hit is None:
                return value
            sel, parts = hit
            if not parts:
                out.notes.append(f"'{value}' ({path}) is on no body: left as it was")
                return value
            if len(parts) > 1:
                out.notes.append(f"'{value}' ({path}) spans {', '.join(parts)}: now on "
                                 f"'{parts[0]}' only")  # fmt: skip
            return f"{parts[0]}/{sel}" if sel else parts[0]
        if isinstance(value, dict):
            new = {}
            for k, v in value.items():
                hit = _target(k) if isinstance(k, str) else None
                if hit and len(hit[1]) > 1:  # a key per part (a mesh size by selection)
                    for p in hit[1]:
                        new[f"{p}/{hit[0]}" if hit[0] else p] = rewrite(v, f"{path}.{k}")
                    continue
                new[rewrite(k, path) if hit else k] = rewrite(v, f"{path}.{k}")
            return new
        if isinstance(value, list):
            items = []
            for i, v in enumerate(value):
                spread = _spread(v)
                if spread and (path.split(".")[-1] in SPLIT or path.endswith("features")
                               or all(isinstance(x, str) for x in value)):  # fmt: skip
                    items += [rewrite(x, f"{path}.{i}") for x in spread]
                else:
                    items.append(rewrite(v, f"{path}.{i}"))
            return items
        return value

    def _target(text: str, bare: bool = False):
        """(selection, new part names) for a reference into the split part, or None;
        the part's bare name counts only where a selection is meant (`bare`): a
        component may share its part's name."""
        if bare and text == part_name:
            return "", where[""]
        if text.startswith(part_name + "/"):
            sel = text[len(part_name) + 1 :]
            if sel in where:
                return sel, where[sel]
        return None

    def _spread(item):
        """A list item on a spanning selection, once per part; None otherwise."""
        if isinstance(item, str):
            hit = _target(item)
            if not hit or len(hit[1]) < 2:
                return None
            return [f"{p}/{hit[0]}" if hit[0] else p for p in hit[1]]
        if isinstance(item, dict) and isinstance(item.get("selection"), str):
            hit = _target(item["selection"], bare=True)
            if hit and len(hit[1]) > 1:
                return [{**item, "selection": f"{p}/{hit[0]}" if hit[0] else p} for p in hit[1]]
        return None

    for obj in project.objects.values():
        if isinstance(obj, Geometry):
            continue
        data = obj.model_dump(mode="json")
        ops = []
        for key, value in data.items():
            if key in ("id", "parts") and isinstance(obj, Component) and obj.id == comp.id:
                continue
            new = rewrite(value, key)
            if json.dumps(new, sort_keys=True) != json.dumps(value, sort_keys=True):
                ops.append(EditOp(op="set", path=key, value=new))
        if obj.id == comp.id:
            parts = []
            for p in comp.parts:
                if p.name != part_name:
                    parts.append(p.model_dump(mode="json"))
                    continue
                for body in bodies:
                    parts.append({**p.model_dump(mode="json"), "name": names[body],
                                  "geometry": created[body]})  # fmt: skip
            ops.append(EditOp(op="set", path="parts", value=parts))
            if bond:
                moved = next((o.value for o in ops if o.path == "contacts"), data["contacts"])
                ops += _bonds(project, comp, part_name, root, names, created, out, plain, moved)
        if ops:
            out.commands.append(Edit(object=str(obj.id), ops=ops))
    users = [c for c in project.of_type(Component) if c.id != comp.id
             and any(p.geometry == geometry.id for p in c.parts)]  # fmt: skip
    if not users and not any(p.geometry == geometry.id for p in comp.parts if p.name != part_name):
        out.commands.append(Delete(object=str(geometry.id)))
    out.notes = list(dict.fromkeys(out.notes))
    return out


def _bonds(project, comp, part_name, root, names, created, out, plain, contacts) -> list:
    """Bonded contacts for the touching bodies no contact joins yet, with their face
    selections added to the new geometries' Create data."""
    from puzzle.commands.model import EditOp
    from puzzle.geometry.contacts import find_contacts, proposals
    from puzzle.model.schema import Contact

    pairs = [p for p in proposals(find_contacts(project, comp, root))
             if p.source_part == p.target_part == part_name]  # fmt: skip
    if not pairs:
        return []
    creates = {c.data["id"]: c for c in out.commands if getattr(c, "command", "") == "create"}
    taken = {c["name"] for c in contacts}
    joined = [{c["source"].split("/")[0], (c.get("target") or "").split("/")[0]} for c in contacts]
    ops = []
    for prop in pairs:
        a, b = names[prop.source_body], names[prop.target_body]
        sides = []
        for body, faces, other in ((prop.source_body, prop.source_faces, b),
                                   (prop.target_body, prop.target_faces, a)):  # fmt: skip
            data = creates[created[body]].data
            members = sorted(_renamed(f, plain) for f in faces)
            same = [s["name"] for s in data["selections"]
                    if s["entity"] == "face" and sorted(s["members"]) == members]  # fmt: skip
            existing = same[0] if same else None
            if existing is None:
                existing, n = f"Touches {other}", 2
                while any(s["name"] == existing for s in data["selections"]):
                    existing, n = f"Touches {other} {n}", n + 1
                data["selections"].append({"name": existing, "entity": "face", "members": members})
            sides.append(f"{names[body]}/{existing}")
        if {a, b} in joined:  # a contact joins them already
            continue
        name, n = f"{a} to {b}", 2
        while name in taken:
            name, n = f"{a} to {b} {n}", n + 1
        taken.add(name)
        contact = Contact(name=name, type="bonded", source=sides[0], target=sides[1])
        ops.append(EditOp(op="append", path="contacts", value=contact.model_dump(mode="json")))
        out.bonds.append(name)
    return ops
