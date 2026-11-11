"""The model tree: every project object, its parts, and the stored runs.

Anything under a section's heading can be renamed (F2, or right-click → Rename): a
project object, a result tool, a stored run, a swept study's group of runs, a group.
Groups (`TreeGroup`) are folders under one heading; items are dragged into and out of
them, or moved with right-click → Move to group. Both are edits of the project, so they
undo; neither changes a result.

Components, physics and geometries show default folders (the maintainer, 2026-11-11):
a component's Geometry, Material assignments and Fasteners, each physics' Contacts and
Loads and boundary conditions, a geometry's History, Bodies and Selections. They are
only how items are shown: each item sorts into its folder by its kind, so whatever is
added lands there, and a group shows in the folder its members are in (or the one it
was made in, `TreeGroup.folder`)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
)

from puzzle.gui.choices import label, solver_label
from puzzle.model.schema import (
    Amplitude,
    BoltType,
    Component,
    ConstructionGeometry,
    CoordinateSystem,
    Geometry,
    ImportedField,
    Material,
    MeshRecipe,
    MeshStudy,
    Operator,
    Optimisation,
    ParameterSet,
    Project,
    ResultDefinition,
    ResultProbe,
    SettingsPreset,
    Study,
    SweepStep,
    Transfer,
    TreeGroup,
    TreeLabels,
)
from puzzle.results.store import RunRecord

ROLE = Qt.ItemDataRole.UserRole
STATE = Qt.ItemDataRole.UserRole + 1  # the item's state key (ADR 0033)


@dataclass(frozen=True)
class Node:
    """What a tree item stands for.

    kind: "section", "object", "selection", "part", "physics", "feature", "step" or
    "run". `object` is the project object's id (or the run id for runs); `path` is the
    dotted field path inside it, as Edit commands take it. `key` is how a group inside
    an object names an item of it (a selection, contact, bolt or feature: by name, not
    by its index, which shifts when another is removed).
    """

    kind: str
    object: str = ""
    path: str = ""
    label: str = ""
    key: str = ""


SECTIONS = [
    ("Parameters", ParameterSet),
    ("Geometry", Geometry),
    ("Materials", Material),
    ("Meshes", MeshRecipe),
    ("Components", Component),
    ("Bolt types", BoltType),  # before the studies that use them (the maintainer, 2026-11-11)
    ("Studies", Study),
    ("Transfers", Transfer),
    ("Imported fields", ImportedField),  # ADR 0044
    ("Amplitudes", Amplitude),
    ("Coordinate systems", CoordinateSystem),
    ("Construction geometry", ConstructionGeometry),
    ("Operators", Operator),  # ADR 0062
    ("Optimisations", Optimisation),  # ADR 0063
    ("Mesh studies", MeshStudy),  # ADR 0066
    ("Solver presets", SettingsPreset),
]


def _visibility_marks(tree, item, obj) -> None:
    """D2's marks: hidden geometry and bodies muted with the eye-off icon; a system or
    construction object drawn always with the eye."""
    from puzzle.gui.visibility import mark

    if isinstance(obj, Geometry):
        if obj.hidden:
            mark(item, True, "hidden")
        elif obj.hidden_bodies:
            mark(item, True, f"bodies hidden: {', '.join(obj.hidden_bodies)}")
    elif isinstance(obj, CoordinateSystem | ConstructionGeometry):
        v = tree.visibility
        if v is not None and str(obj.id) in v.shown:
            mark(item, False, "drawn always (right-click to hide)")


def _mark_hidden(item, geometry) -> None:
    """A hidden geometry in italics, a suppressed one struck through, each saying so."""
    font = item.font(0)
    font.setItalic(geometry.hidden)
    font.setStrikeOut(geometry.suppressed)
    item.setFont(0, font)
    words = []
    if geometry.suppressed:
        words.append("suppressed: left out of meshing and solving")
    if geometry.hidden:
        words.append("hidden: not drawn, still solved")
    item.setToolTip(0, f"{geometry.name}, " + "; ".join(words))


# What can be renamed in place, and dragged into a group.
RENAMABLE = ("object", "probe", "derived", "run", "sweep", "group")


def member_key(node: Node) -> str | None:
    """How a group names an item it holds; None for what can't be grouped."""
    if node.kind == "folder":
        return None
    if node.kind in ("object", "probe", "derived", "group"):
        return node.object
    if node.kind in ("run", "sweep"):
        return f"{node.kind}:{node.object}"
    return node.key or None  # an item inside an object (a selection, a contact, ...)


def container_key(node: Node | None) -> str | None:
    """The scope a tree item opens for groups of what it holds: a heading's title, a
    geometry's or component's id (its selections; its parts, contacts, bolts...), or
    "<component id>/<physics>" (its loads and supports). None: it holds no groups."""
    if node is None:
        return None
    if node.kind == "section":
        return node.label
    if node.kind == "folder":  # a default folder: its object's (or physics') scope
        return node.path
    if node.kind == "object" and node.path == "" and node.key == "":
        return node.object
    if node.kind == "physics":
        return f"{node.object}/{node.label}"
    return None


def scope_name(project: Project, scope: str) -> str:
    """A group's scope in words: a heading, or the object (and physics) it sits in."""
    from puzzle.gui.choices import label as words

    oid, _, physics = scope.partition("/")
    obj = project.objects.get(_uuid(oid))
    if obj is None:
        return scope
    return f"{obj.name}: {words(physics)}" if physics else obj.name


# Default folders: (key, title, the member-key kinds it holds).
COMPONENT_FOLDERS = (
    ("geometry", "Geometry", ("part", "shell", "beam")),
    ("materials", "Material assignments", ("assignment",)),
    ("fasteners", "Fasteners", ("connector", "weld")),
)
PHYSICS_FOLDERS = (
    ("contacts", "Contacts", ("contact",)),
    ("loads", "Loads and boundary conditions", ("feature",)),
)
GEOMETRY_FOLDERS = (("history", "History"), ("bodies", "Bodies"), ("selections", "Selections"))
FOLDER_TITLES = {k: t for k, t, *_ in (*COMPONENT_FOLDERS, *PHYSICS_FOLDERS, *GEOMETRY_FOLDERS)}


def key_folder(project: Project, scope: str, key: str) -> str | None:
    """The default folder an item (by its member key) sits in, inside `scope`."""
    kind, _, name = key.partition(":")
    owner = project.objects.get(_uuid(scope.partition("/")[0]))
    if isinstance(owner, Geometry):
        if kind != "selection":
            return None
        sel = next((x for x in owner.selections if x.name == name), None)
        return "bodies" if sel is not None and sel.entity == "body" else "selections"
    if isinstance(owner, Component):
        for folder, _, kinds in (*COMPONENT_FOLDERS, *PHYSICS_FOLDERS):
            if kind in kinds:
                return folder
    return None


def group_folders(project: Project, group: TreeGroup, seen=()) -> set[str]:
    """The default folders a group shows in: the one it was made in, else those its
    members (through groups inside it) are in; empty for a heading's groups."""
    if group.folder:
        return {group.folder}
    out: set[str] = set()
    for m in group.members:
        inner = project.objects.get(_uuid(m))
        if isinstance(inner, TreeGroup):
            if m not in seen:
                out |= group_folders(project, inner, (*seen, str(group.id)))
        elif (found := key_folder(project, group.section, m)) is not None:
            out.add(found)
    return out


def folder_of(item) -> str | None:
    """The default folder a tree item sits in (or is), past any groups."""
    while item is not None:
        node = item.data(0, ROLE)
        if node is not None and node.kind == "folder":
            return node.key
        if node is not None and container_key(node) is not None:
            return None
        item = item.parent()
    return None


class NameDelegate(QStyledItemDelegate):
    """Edits an item's name, not its text ("Path 1", not "Path 1 · path"); the new name
    goes to the window as a command (`renamed`), never straight into the item."""

    renamed = Signal(object, str)  # the Node, the new name

    def setEditorData(self, editor, index) -> None:  # noqa: N802 - Qt's name
        node = index.data(ROLE)
        editor.setText(node.label if node is not None else index.data())
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:  # noqa: N802 - Qt's name
        node = index.data(ROLE)
        text = editor.text().strip() if isinstance(editor, QLineEdit) else ""
        if node is not None and text and text != node.label:
            self.renamed.emit(node, text)


class ModelTree(QTreeWidget):
    moved = Signal(list, object, str)  # member keys, the target group id (or None), section

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.visibility = None  # the window's (gui.visibility): what to mark shown or hidden
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.names = NameDelegate(self)
        self.setItemDelegate(self.names)
        self.renamed = self.names.renamed
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def populate(
        self,
        project: Project,
        runs: list[RunRecord],
        catalogs: dict | None = None,
        states: dict | None = None,
    ) -> None:
        """Rebuild from the project, keeping what was selected and expanded; `catalogs`
        (run id -> its stored fields) lists each run's results under it; `states`
        (object id -> `project.status.State`) gives each item its state icon (ADR 0033)."""

        states = states if states is not None else {}
        keep = self.current()
        opened = {self._key(i) for i in self._items() if i.isExpanded()}
        # Rebuilt quietly: the window shows the selection once after a refresh. Clearing
        # and re-selecting used to show "nothing", then the item, then the item again
        # (3.5 s on an operator), and the handlers could rebuild mid-loop.
        quiet = self.blockSignals(True)
        try:
            self._populate(project, runs, catalogs, states, keep, opened)
        finally:
            self.blockSignals(quiet)

    def _populate(self, project, runs, catalogs, states, keep, opened) -> None:
        from puzzle.project.status import run_state

        self.clear()
        root = self._add(
            self.invisibleRootItem(), project.name, Node("project", label=project.name)
        )
        for title, cls in SECTIONS:
            objects = project.of_type(cls)
            if not objects:
                continue
            section = self._add(root, title, Node("section", label=title))
            _section_icon(section, title)
            for obj in objects:
                item = self._add(section, obj.name, Node("object", str(obj.id), "", obj.name))
                self._state(item, states.get(str(obj.id)))
                if isinstance(obj, Geometry) and (obj.hidden or obj.suppressed):
                    _mark_hidden(item, obj)
                self._mark_visibility(item, obj)
                self._children(item, obj, project)
        defined = project.of_type(ResultDefinition)
        probes = project.of_type(ResultProbe)
        if runs or defined or probes:
            from puzzle.post.derived import METHODS
            from puzzle.post.quantities import catalog as results_of
            from puzzle.sweep import title

            section = self._add(root, "Results", Node("section", label="Results"))
            _section_icon(section, "Results")
            for d in defined:  # derived results (ADR 0025), evaluated when shown
                text = f"{d.name} · {METHODS[d.method].split(' (')[0].lower()}"
                item = self._add(section, text, Node("derived", str(d.id), "", d.name))
                self._state(item, states.get(str(d.id)))
            for p in probes:  # result tools (ADR 0032, 0042), read again for each run
                from puzzle.gui.result_tools import title

                text = title(p)
                item = self._add(section, text, Node("probe", str(p.id), "", p.name))
                self._state(item, states.get(str(p.source)))
            sweeps: dict[str, tuple[QTreeWidgetItem, set]] = {}  # study id -> heading, points
            named = next(iter(project.of_type(TreeLabels)), None)  # names given in the tree
            for r in reversed(runs):  # newest first
                when = f"{solver_label(r.solver)} · {r.started_at:%Y-%m-%d %H:%M}"
                study = project.objects.get(_uuid(r.study_id))
                study_name = study.name if study is not None else r.study_name  # renamed
                labels = r.point_labels()
                if labels:  # one point of a swept study: under the study's heading
                    if r.study_id not in sweeps:
                        head_text = (named.sweeps.get(r.study_id) if named else None) or (
                            f"{study_name}: compare"
                        )
                        head = self._add(
                            section, head_text, Node("sweep", r.study_id, "", head_text)
                        )
                        sweeps[r.study_id] = (head, set())
                        self._add(head, "Compare…", Node("compare", r.study_id, "", "Compare"))
                    head, seen = sweeps[r.study_id]
                    point = title(labels)
                    if point in seen:  # an older run of the same point
                        continue
                    seen.add(point)
                    text = f"{point} · {when}"
                    run = self._add(head, text, Node("run", r.run_id, "", text))
                else:
                    text = (named.runs.get(r.run_id) if named else None) or (
                        f"{study_name} · {when}"
                    )
                    run = self._add(section, text, Node("run", r.run_id, "", text))
                self._state(run, run_state(r, project))
                # Every result the run can show, by field (double-click or select one).
                groups: dict = {}
                for item in results_of((catalogs or {}).get(r.run_id, [])):
                    if item.group not in groups:
                        node = Node("result_group", r.run_id, item.group, item.group)
                        groups[item.group] = self._add(run, item.group, node)
                    leaf = Node("result", r.run_id, item.key, item.label)
                    self._add(groups[item.group], item.label, leaf)
        self._group(root, project)
        self._roll_up(self.invisibleRootItem())
        from puzzle.gui.theme import colour_levels

        colour_levels(self)  # headings by depth (ADR 0045)
        chosen = None
        for item in self._items():
            node = item.data(0, ROLE)
            if self._key(item) in opened or node.kind in ("project", "section"):
                item.setExpanded(True)
            if keep is not None and node == keep:
                chosen = item
        # Selected after the loop: selecting fires the window's handlers, which can
        # rebuild the tree and delete the items still to come ("already deleted").
        if chosen is not None:
            self.setCurrentItem(chosen)

    def _group(self, root: QTreeWidgetItem, project: Project) -> None:
        """Each scope's groups as folders holding their members: a section's at its top,
        an object's or a physics' before the first item they can hold."""
        groups = project.of_type(TreeGroup)
        if not groups:
            return
        scopes = {g.section for g in groups}
        containers = [i for i in self._items() if container_key(i.data(0, ROLE)) in scopes]
        for container in containers:
            node = container.data(0, ROLE)
            scope = container_key(node)
            here = node.key if node.kind == "folder" else None

            def placed(g, here=here) -> bool:  # in this folder, or in none (a heading's)
                spans = group_folders(project, g)
                return here in spans if here is not None else not spans

            mine = [g for g in groups if g.section == scope and placed(g)]
            if not mine:
                continue
            ids = {str(g.id) for g in mine}
            nested = {m for g in mine for m in g.members if m in ids}
            folders = {str(g.id): QTreeWidgetItem([g.name]) for g in mine}
            for g in mine:
                folders[str(g.id)].setData(0, ROLE, Node("group", str(g.id), "", g.name))
            owner = {m: str(g.id) for g in mine for m in g.members}
            items = [container.child(j) for j in range(container.childCount())]
            first = next((j for j, item in enumerate(items)
                          if member_key(item.data(0, ROLE)) is not None), len(items))  # fmt: skip
            for item in items:  # members into their folders
                key = member_key(item.data(0, ROLE))
                if key in owner:
                    container.removeChild(item)
                    folders[owner[key]].addChild(item)
            for n, g in enumerate(g for g in mine if str(g.id) not in nested):
                container.insertChild(first + n, folders[str(g.id)])
            for g in mine:  # groups inside groups, first in their folder
                for m in reversed(g.members):
                    if m in folders and m != str(g.id):
                        folders[str(g.id)].insertChild(0, folders[m])
            for folder in folders.values():
                self._flags(folder)

    def scope_of(self, item: QTreeWidgetItem) -> str | None:
        """The scope an item is grouped in: the heading it sits under, or the object (or
        physics) holding it (`container_key`), past any groups."""
        parent = item.parent() if item is not None else None
        while parent is not None:
            key = container_key(parent.data(0, ROLE))
            if key is not None:
                return key
            parent = parent.parent()
        return None

    def section_of(self, item: QTreeWidgetItem) -> str | None:
        """The heading an item sits under ("Studies", "Results", ...)."""
        while item is not None:
            node = item.data(0, ROLE)
            if node is not None and node.kind == "section":
                return node.label
            item = item.parent()
        return None

    def item_for(self, node: Node) -> QTreeWidgetItem | None:
        for item in self._items():
            found = item.data(0, ROLE)
            if _same(found, node):
                return item
        return None

    def groupable(self, item: QTreeWidgetItem) -> bool:
        """Directly under a scope (a section, an object, a physics) or a group: a thing
        that can be moved into a group there."""
        parent = item.parent()
        up = parent.data(0, ROLE) if parent is not None else None
        node = item.data(0, ROLE)
        return (up is not None and (up.kind == "group" or container_key(up) is not None)
                and member_key(node) is not None)  # fmt: skip

    def _flags(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, ROLE)
        flags = item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled & ~Qt.ItemFlag.ItemIsDropEnabled
        if node.kind in RENAMABLE:
            flags |= Qt.ItemFlag.ItemIsEditable
        if (node.kind == "group" or container_key(node) is not None
                or member_key(node) is not None):  # fmt: skip
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        if member_key(node) is not None:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        item.setFlags(flags)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """A drop moves the dragged items into a group (onto it, or onto one of its
        members) or out of every group (onto the heading or an ungrouped item), within
        one section. The window makes it a command; Qt never moves the items itself."""
        target = self.itemAt(event.position().toPoint())
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.ignore()
        if target is None:
            return
        node = target.data(0, ROLE)
        own = container_key(node)  # dropped on the scope itself: out of every group
        candidates = [i for i in self.selectedItems() if self.groupable(i)]
        section = next((self.scope_of(i) for i in candidates if self.scope_of(i) == own), None)
        section = section or self.scope_of(target)
        dragged = [i for i in candidates if self.scope_of(i) == section]
        if not dragged or section is None:
            return
        if node.kind == "group":
            group = node.object
        elif own is not None and own == section:
            group = None
        else:
            parent = target.parent()
            up = parent.data(0, ROLE) if parent is not None else None
            group = up.object if up is not None and up.kind == "group" else None
        here = folder_of(target)  # a part stays in Geometry, a load in its loads
        if here is not None and any(folder_of(i) != here for i in dragged):
            return
        keys = [member_key(i.data(0, ROLE)) for i in dragged]
        if group is not None and group in keys:
            return  # a group into itself
        self.moved.emit(keys, group, section)

    def _mark_visibility(self, item: QTreeWidgetItem, obj) -> None:
        _visibility_marks(self, item, obj)

    def _state(self, item: QTreeWidgetItem, state) -> None:
        """An item's state icon and, in its tooltip, what it means."""
        from puzzle.gui.icons import state_icon

        if state is None:
            return
        item.setIcon(0, state_icon(state.state))
        item.setToolTip(0, state.words)
        item.setData(0, STATE, state.state)

    def _roll_up(self, item: QTreeWidgetItem) -> str | None:
        """Headings (sections, the project, a swept study's runs) show their worst child."""
        from puzzle.project.status import State, worst

        found = [self._roll_up(item.child(i)) for i in range(item.childCount())]
        own = item.data(0, STATE) if item is not self.invisibleRootItem() else None
        node = item.data(0, ROLE) if item is not self.invisibleRootItem() else None
        if node is not None and node.kind in ("project", "section", "sweep", "group", "folder"):
            below = [s for s in found if s]
            if below:
                state = worst(below)
                urgent = state not in ("complete", "up_to_date", "unused", "not_run")
                n = sum(1 for s in below if s == state)
                reason = f"{n} item{'s' if n != 1 else ''} below" if urgent else ""
                self._state(item, State(state, reason))
                return state
        return own

    def current(self) -> Node | None:
        item = self.currentItem()
        return item.data(0, ROLE) if item is not None else None

    def select(self, node: Node) -> None:
        """Select the item for the same thing (kind, object and path; labels may differ)."""
        for item in self._items():
            found = item.data(0, ROLE)
            if _same(found, node):
                self.setCurrentItem(item)
                return

    # --- building ----------------------------------------------------------------

    def _children(self, item: QTreeWidgetItem, obj, project: Project) -> None:
        oid = str(obj.id)
        names = {str(o.id): o.name for o in project.objects.values()}
        if isinstance(obj, Geometry):
            folders = {k: self._folder(item, oid, oid, k) for k, _ in GEOMETRY_FOLDERS
                       if k != "history" or obj.history}  # fmt: skip
            for i, step in enumerate(obj.history):
                off = "  (off)" if step.suppressed else ""
                text = f"{i + 1}. {label(step.kind)} {step.name}{off}"
                self._add(folders["history"], text,
                          Node("operation", oid, f"history.{i}", step.name))  # fmt: skip
            for i, s in enumerate(obj.selections):
                text = f"{s.name}  ({_count(len(s.members), s.entity)})"
                where = folders["bodies" if s.entity == "body" else "selections"]
                s_item = self._add(where, text, Node("selection", oid, f"selections.{i}", s.name,
                                                     f"selection:{s.name}"))  # fmt: skip
                hidden = set(obj.hidden_bodies)
                if s.entity == "body" and s.members and set(s.members) <= hidden:
                    from puzzle.gui.visibility import mark

                    mark(s_item, True, "its bodies are hidden in the view")
        elif isinstance(obj, Component):
            folders = {k: self._folder(item, oid, oid, k) for k, *_ in COMPONENT_FOLDERS}
            for i, p in enumerate(obj.parts):
                text = f"Part {p.name}: {names.get(str(p.geometry), '?')}"
                part_item = self._add(folders["geometry"], text,
                                      Node("part", oid, f"parts.{i}", p.name,
                                           f"part:{p.name}"))  # fmt: skip
                geo = project.objects.get(p.geometry)
                if isinstance(geo, Geometry) and geo.hidden:
                    from puzzle.gui.visibility import mark

                    mark(part_item, True, "its geometry is hidden")
            for i, s in enumerate(obj.shell_sections):  # ADR 0027 shells
                text = f"Shell on {s.selection}: {s.describe()}"
                self._add(folders["geometry"], text, Node("shell_section", oid,
                          f"shell_sections.{i}", s.selection, f"shell:{s.selection}"))  # fmt: skip
            for i, b in enumerate(obj.beam_sections):  # ADR 0027 beams
                text = f"Beam section on {b.selection}: {b.describe()}"
                self._add(folders["geometry"], text, Node("beam_section", oid,
                          f"beam_sections.{i}", b.selection, f"beam:{b.selection}"))  # fmt: skip
            for i, a in enumerate(obj.materials):
                text = f"{names.get(str(a.material), '?')} → {a.selection}"
                self._add(folders["materials"], text, Node("assignment", oid, f"materials.{i}",
                          a.selection, f"assignment:{a.selection}"))  # fmt: skip
            v = self.visibility
            for i, c in enumerate(obj.connectors):  # ADR 0027; a bolt type's (ADR 0050)
                kind = names.get(str(c.type)) if c.type is not None else None
                d = c.diameter
                size = kind or (f"Ø {d.value:g} {d.unit}" if d is not None else "?")
                text = f"Fastener {c.name} ({size}): {c.source} → {c.target or 'ground'}"
                c_item = self._add(folders["fasteners"], text, Node("connector", oid,
                                   f"connectors.{i}", c.name, f"connector:{c.name}"))  # fmt: skip
                if v is not None and v.hidden_bolts:
                    from puzzle.gui.visibility import bolt_key, mark

                    if bolt_key(obj, c) in v.hidden_bolts:
                        mark(c_item, True, "hidden in the view")
            for i, w in enumerate(obj.welds):  # ADR 0078
                text = f"Weld {w.name} ({label(w.type).lower()}): {w.path}"
                self._add(folders["fasteners"], text,
                          Node("weld", oid, f"welds.{i}", w.name, f"weld:{w.name}"))  # fmt: skip

            def contacts(parent) -> None:  # a component's contacts, in each physics
                for i, k in enumerate(obj.contacts):
                    text = f"Contact {k.name} ({label(k.type).lower()})"
                    self._add(parent, text, Node("contact", oid, f"contacts.{i}", k.name,
                                                 f"contact:{k.name}"))  # fmt: skip

            if not obj.physics:
                contacts(self._folder(item, oid, oid, "contacts"))
            for i, ph in enumerate(obj.physics):
                p_item = self._add(
                    item, label(ph.type), Node("physics", oid, f"physics.{i}", ph.type)
                )
                contacts(self._folder(p_item, oid, oid, "contacts"))
                loads = self._folder(p_item, oid, f"{oid}/{ph.type}", "loads")
                for j, f in enumerate(ph.features):
                    text = f"{_feature_label(ph.type, f.type)} on {f.selection}"
                    sources = f.fields.values() if _connections_shown() else ()
                    for use in sources:  # ADR 0044: where its values come from
                        source = project.objects.get(use.field)
                        scale = f" ×{use.scale:g}" if use.scale != 1 else ""
                        text += f" ← {getattr(source, 'name', 'a missing field')}{scale}"
                    path = f"physics.{i}.features.{j}"
                    key = f"feature:{f.type}:{f.selection}"
                    f_item = self._add(loads, text, Node("feature", oid, path, f.selection, key))
                    _feature_icon(f_item, f.type)
                    v = self.visibility
                    if v is not None and v.hidden_features:
                        from puzzle.gui.visibility import feature_key, mark

                        if feature_key(obj, ph, f) in v.hidden_features:
                            mark(f_item, True, "its symbol is hidden in the view")
        elif isinstance(obj, Study):
            for i, step in enumerate(obj.steps):
                if isinstance(step, SweepStep):
                    from puzzle.sweep import describe

                    text = describe(step)
                else:
                    what = ", ".join(label(p).lower() for p in step.physics)
                    text = label(step.type) + (f" · {what}" if what else "")
                self._add(item, text, Node("step", oid, f"steps.{i}", text))

    def _folder(self, parent, oid: str, scope: str, key: str) -> QTreeWidgetItem:
        """A default folder: `path` is the scope its items are grouped in, `key` which."""
        title = FOLDER_TITLES[key]
        item = self._add(parent, title, Node("folder", oid, scope, title, key))
        from puzzle.gui.icons import icon

        item.setIcon(0, icon("project"))  # Tabler's folder
        return item

    def _add(self, parent, text: str, node: Node) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, ROLE, node)
        self._flags(item)
        return item

    def _items(self) -> list[QTreeWidgetItem]:
        out, stack = [], [self.invisibleRootItem()]
        while stack:
            parent = stack.pop()
            for i in range(parent.childCount()):
                child = parent.child(i)
                out.append(child)
                stack.append(child)
        return out

    @staticmethod
    def _key(item: QTreeWidgetItem) -> tuple:
        node = item.data(0, ROLE)
        return (node.kind, node.object, node.path, node.label)


NOUNS = {
    "face": ("face", "faces"),
    "edge": ("edge", "edges"),
    "body": ("body", "bodies"),
    "vertex": ("point", "points"),
}


def _count(n: int, entity: str) -> str:
    one, many = NOUNS.get(entity, (entity, entity + "s"))
    return f"{n} {one if n == 1 else many}"


def _connections_shown() -> bool:
    """Settings > Connections > In the tree and Properties (ADR 0044)."""
    from puzzle.settings import current

    return bool(current().get("connections.tree"))


def _section_icon(item, title: str) -> None:
    """A section heading's icon (ADR 0045), redrawn in the theme's text colour."""
    from puzzle.gui.icons import SECTIONS, icon

    if title in SECTIONS:
        item.setIcon(0, icon(SECTIONS[title]))


def _feature_icon(item, kind: str) -> None:
    """A support's or load's icon, as the view draws it (ADR 0045)."""
    from puzzle.gui.icons import FEATURES, icon

    if kind in FEATURES:
        item.setIcon(0, icon(FEATURES[kind]))


def _feature_label(physics: str, kind: str) -> str:
    """A feature as the add menu names it ("Fixed support"), else its type in words."""
    from puzzle.model.catalog import CATALOG

    spec = next((s for s in CATALOG.get(physics, []) if s.type == kind), None)
    return spec.label if spec else label(kind)


def _same(a: Node, b: Node) -> bool:
    """The same thing (labels may differ); default folders also by which folder."""
    return (a.kind, a.object, a.path) == (b.kind, b.object, b.path) and (
        a.kind != "folder" or a.key == b.key
    )


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(str(text))
    except ValueError:
        return None
