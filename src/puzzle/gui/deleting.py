"""Deleting what the tree has selected, several things at once, as one command.

Objects (a study, a material, a result tool...) are Deletes, ordered so what uses
another goes first; items inside an object (a load, a contact, a bolt, a selection, a
step) are `remove` ops, highest index first so the others keep theirs, one Edit per
object; an item whose object (or physics) goes too is left to it. A group goes and what
it held stays, moved up to where the group was. All of that is one batch, so one undo.
Stored runs are a cache, not the project: they are deleted from the results store
after the batch, and that does not undo (the confirmation says so).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puzzle.commands.model import Delete, Edit, EditOp

# Items inside an object that a remove op takes away.
INNER = ("feature", "assignment", "contact", "part", "connector", "beam_section",
         "shell_section", "physics", "selection", "operation", "step")  # fmt: skip
OBJECTS = ("object", "derived", "probe")


@dataclass
class Plan:
    commands: list = field(default_factory=list)
    runs: list[str] = field(default_factory=list)  # run ids, deleted from the store after
    names: list[str] = field(default_factory=list)  # what goes, in words

    def __bool__(self) -> bool:
        return bool(self.commands or self.runs)


def _uuid(text):
    from uuid import UUID

    try:
        return UUID(str(text))
    except ValueError:
        return None


def _path_key(path: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in path.split("."))


def plan(project, nodes, run_name=lambda run_id: "results") -> Plan:
    """The commands deleting `nodes` (tree Nodes), and the runs to delete after."""
    from puzzle.model.schema import TreeGroup

    out = Plan()
    objects = []
    for n in nodes:
        obj = project.objects.get(_uuid(n.object)) if n.object else None
        if n.kind in OBJECTS and obj is not None and obj.id not in {o.id for o in objects}:
            objects.append(obj)
    gone = {o.id for o in objects}
    groups = [o for o in objects if isinstance(o, TreeGroup)]
    ops: dict = {}  # object id -> remove paths
    for n in nodes:
        if n.kind == "run":
            if n.object not in out.runs:
                out.runs.append(n.object)
                out.names.append(run_name(n.object))
        elif n.kind == "group":
            g = project.objects.get(_uuid(n.object))
            if isinstance(g, TreeGroup) and g.id not in gone:
                groups.append(g)
                gone.add(g.id)
                objects.append(g)
        elif n.kind in INNER and n.path:
            obj = project.objects.get(_uuid(n.object))
            if obj is not None and obj.id not in gone:
                ops.setdefault(obj.id, set()).add(n.path)
                out.names.append(n.label or n.kind)
    out.names = [o.name for o in objects] + out.names
    out.commands += _group_edits(project, groups, gone)
    for oid, paths in ops.items():
        keep = [p for p in paths if not any(p.startswith(q + ".") for q in paths if q != p)]
        keep.sort(key=_path_key, reverse=True)  # later items first: indices stay right
        out.commands.append(Edit(object=str(oid), ops=[EditOp(op="remove", path=p)
                                                      for p in keep]))  # fmt: skip
    out.commands += [Delete(object=str(o.id)) for o in _users_first(objects)]
    return out


def _group_edits(project, groups, gone) -> list:
    """What deleted groups held moves up into the groups that held them (kept ones)."""
    from puzzle.model.schema import TreeGroup

    if not groups:
        return []
    deleted = {str(g.id): g for g in groups}

    def flat(members, seen=()) -> list[str]:
        out = []
        for m in members:
            if m in deleted and m not in seen:
                out += flat(deleted[m].members, (*seen, m))
            elif m not in deleted:
                out.append(m)
        return out

    edits = []
    for g in project.of_type(TreeGroup):
        if g.id in gone or not any(m in deleted for m in g.members):
            continue
        members = list(dict.fromkeys(flat(g.members)))
        edits.append(Edit(object=str(g.id), ops=[EditOp(op="set", path="members",
                                                         value=members)]))  # fmt: skip
    return edits


def _users_first(objects: list) -> list:
    """Deletes in an order the session accepts: an object before those it uses."""
    left = list(objects)
    out = []
    while left:
        ids = {o.id for o in left}
        free = [o for o in left if not any(o.id in u.dependencies() for u in left
                                           if u.id != o.id and u.id in ids)]  # fmt: skip
        if not free:  # a cycle: the session says what is in the way
            out += left
            break
        out += free
        left = [o for o in left if o not in free]
    return out


def question(p: Plan) -> str:
    """The one confirmation: how many, and what."""
    n = len(p.names)
    shown = ", ".join(p.names[:6]) + (f" and {n - 6} more" if n > 6 else "")
    text = f"Delete these {n} items: {shown}?"
    if p.runs:
        text += (
            " Deleted results don't come back with Undo; running their studies again "
            "makes them anew. The rest undoes in one step."
        )
    else:
        text += " Undo brings them all back in one step."
    return text
