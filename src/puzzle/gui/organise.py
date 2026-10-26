"""Keeping the model tree tidy: renaming anything under a heading, and groups.

Everything here is a command (one undo each):
- **rename:** a project object, result tool or derived result by its `name`; a stored
  run or a swept study's group of runs by a name in `TreeLabels` (the runs themselves
  are a cache, so their names live in the project);
- **groups** (`TreeGroup`): New group… under a heading (with what is selected in it),
  Move to group, Out of group, Ungroup. A group can hold groups.
"""

from __future__ import annotations

from uuid import UUID

from puzzle.commands.model import Create, Delete, Edit, EditOp
from puzzle.gui.tree import RENAMABLE, member_key
from puzzle.model.schema import TreeGroup, TreeLabels


def _uuid(text):
    try:
        return UUID(str(text))
    except ValueError:
        return None


def labels(window) -> TreeLabels | None:
    return next(iter(window.session.project.of_type(TreeLabels)), None)


def rename(window, node, text: str):
    """Give `node`'s thing the name `text` (refused when another of its kind has it)."""
    text = text.strip()
    if not text or node.kind not in RENAMABLE:
        return None
    project = window.session.project
    if node.kind in ("run", "sweep"):
        field = "runs" if node.kind == "run" else "sweeps"
        found = labels(window)
        if found is None:
            data = {"name": "Tree labels", field: {node.object: text}}
            return window.submit(Create(kind="labels", data=data))
        names = dict(getattr(found, field)) | {node.object: text}
        return window.submit(Edit(object=str(found.id), ops=[EditOp(op="set", path=field,
                                                                     value=names)]))  # fmt: skip
    obj = project.objects.get(_uuid(node.object))
    if obj is None or obj.name == text:
        return None
    clash = next((o for o in project.objects.values()
                  if type(o) is type(obj) and o.id != obj.id and o.name == text), None)  # fmt: skip
    if clash is not None:
        what = getattr(type(obj), "kind", "object")
        window.statusBar().showMessage(f"Another {what} is already called '{text}'", 8000)
        return None
    return window.submit(Edit(object=str(obj.id), ops=[EditOp(op="set", path="name",
                                                              value=text)]))  # fmt: skip


def groups_in(window, section: str) -> list[TreeGroup]:
    return [g for g in window.session.project.of_type(TreeGroup) if g.section == section]


def move(window, keys: list[str], group: str | None, section: str):
    """Put `keys` into `group` (None: out of every group), in `section`; one undo."""
    keys = [k for k in keys if k and k != group]
    commands = []
    for g in groups_in(window, section):
        members = [m for m in g.members if m not in keys]
        if str(g.id) == group:
            members += keys
        if members != g.members:
            commands.append(Edit(object=str(g.id), ops=[EditOp(op="set", path="members",
                                                               value=members)]))  # fmt: skip
    if not commands:
        return None
    return window.submit_batch(commands) if len(commands) > 1 else window.submit(commands[0])


def new_group(window, section: str, keys: list[str] | None = None, name: str | None = None):
    """A group under `section`, holding `keys` (taken out of any other group)."""
    from puzzle.gui.quick_form import Ask

    if name is None:
        taken = {g.name for g in groups_in(window, section)}
        n = 1
        while f"Group {n}" in taken:
            n += 1
        got = window.ask_form(
            "New group",
            [Ask("name", "Name", kind="text", default=f"Group {n}")],
            intro=f"A folder under {section} to keep the tree tidy; drag items into it, or "
            "right-click them → Move to group.",
            check=lambda v: None if v.get("name", "").strip() else "Give it a name",
        )
        if got is None:
            return None
        name = got["name"].strip()
    keys = list(keys or [])
    group = TreeGroup(name=name, section=section, members=keys)
    commands = [Create(kind="group", data=group.model_dump(mode="json"))]
    for g in groups_in(window, section):  # each item sits in one group at a time
        members = [m for m in g.members if m not in keys]
        if members != g.members:
            commands.append(Edit(object=str(g.id), ops=[EditOp(op="set", path="members",
                                                               value=members)]))  # fmt: skip
    return window.submit_batch(commands) if len(commands) > 1 else window.submit(commands[0])


def ungroup(window, group_id: str):
    """Remove a group; what it held goes back to where the group was."""
    project = window.session.project
    group = project.objects.get(_uuid(group_id))
    if not isinstance(group, TreeGroup):
        return None
    commands = []
    for g in groups_in(window, group.section):
        if group_id in g.members:  # it sat inside another group: its members go there
            i = g.members.index(group_id)
            members = g.members[:i] + group.members + g.members[i + 1 :]
            commands.append(Edit(object=str(g.id), ops=[EditOp(op="set", path="members",
                                                               value=members)]))  # fmt: skip
    commands.append(Delete(object=str(group.id)))
    return window.submit_batch(commands) if len(commands) > 1 else window.submit(commands[0])


def selected_keys(window, section: str) -> list[str]:
    """The groupable items selected in the tree, in `section`."""
    tree = window.tree
    return [member_key(i.data(0, tree_role())) for i in tree.selectedItems()
            if tree.groupable(i) and tree.section_of(i) == section]  # fmt: skip


def tree_role():
    from puzzle.gui.tree import ROLE

    return ROLE


def add_to_menu(window, menu, node) -> None:
    """Rename, and the group actions, for a tree item's right-click menu."""
    tree = window.tree
    item = tree.item_for(node)
    if item is None:
        return
    section = tree.section_of(item)
    if node.kind == "section":
        if section is not None:
            menu.addSeparator()
            menu.addAction("New group…").triggered.connect(lambda: new_group(window, section))
        return
    if node.kind not in RENAMABLE:
        return
    menu.addSeparator()
    menu.addAction("Rename…\tF2").triggered.connect(lambda: tree.editItem(item))
    if node.kind == "group":
        menu.addAction("Ungroup").triggered.connect(lambda: ungroup(window, node.object))
    if not tree.groupable(item) or section is None:
        return
    keys = selected_keys(window, section) or [member_key(node)]
    sub = menu.addMenu("Move to group")
    for g in groups_in(window, section):
        if str(g.id) in keys:
            continue
        action = sub.addAction(g.name)
        action.triggered.connect(lambda _=False, gid=str(g.id): move(window, keys, gid, section))
    if groups_in(window, section):
        sub.addSeparator()
    sub.addAction("New group…").triggered.connect(lambda: new_group(window, section, keys))
    parent = item.parent().data(0, tree_role())
    if parent.kind == "group":
        menu.addAction("Out of group").triggered.connect(lambda: move(window, keys, None, section))
