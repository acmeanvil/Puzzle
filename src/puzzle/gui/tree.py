"""The model tree: every project object, its parts, and the stored runs.

Anything under a section's heading can be renamed (F2, or right-click → Rename): a
project object, a result tool, a stored run, a swept study's group of runs, a group.
Groups (`TreeGroup`) are folders under one heading; items are dragged into and out of
them, or moved with right-click → Move to group. Both are edits of the project, so they
undo; neither changes a result."""

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
    Component,
    ConstructionGeometry,
    CoordinateSystem,
    Geometry,
    Material,
    MeshRecipe,
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
    dotted field path inside it, as Edit commands take it.
    """

    kind: str
    object: str = ""
    path: str = ""
    label: str = ""


SECTIONS = [
    ("Parameters", ParameterSet),
    ("Geometry", Geometry),
    ("Materials", Material),
    ("Meshes", MeshRecipe),
    ("Components", Component),
    ("Studies", Study),
    ("Transfers", Transfer),
    ("Amplitudes", Amplitude),
    ("Coordinate systems", CoordinateSystem),
    ("Construction geometry", ConstructionGeometry),
    ("Solver presets", SettingsPreset),
]


# What can be renamed in place, and dragged into a group.
RENAMABLE = ("object", "probe", "derived", "run", "sweep", "group")


def member_key(node: Node) -> str | None:
    """How a group names an item it holds; None for what can't be grouped."""
    if node.kind in ("object", "probe", "derived", "group"):
        return node.object
    if node.kind in ("run", "sweep"):
        return f"{node.kind}:{node.object}"
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
        from puzzle.project.status import run_state

        states = states if states is not None else {}
        keep = self.current()
        opened = {self._key(i) for i in self._items() if i.isExpanded()}
        self.clear()
        root = self._add(
            self.invisibleRootItem(), project.name, Node("project", label=project.name)
        )
        for title, cls in SECTIONS:
            objects = project.of_type(cls)
            if not objects:
                continue
            section = self._add(root, title, Node("section", label=title))
            for obj in objects:
                item = self._add(section, obj.name, Node("object", str(obj.id), "", obj.name))
                self._state(item, states.get(str(obj.id)))
                self._children(item, obj, project)
        defined = project.of_type(ResultDefinition)
        probes = project.of_type(ResultProbe)
        if runs or defined or probes:
            from puzzle.post.derived import METHODS
            from puzzle.post.quantities import catalog as results_of
            from puzzle.sweep import title

            section = self._add(root, "Results", Node("section", label="Results"))
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
        for item in self._items():
            node = item.data(0, ROLE)
            if self._key(item) in opened or node.kind in ("project", "section"):
                item.setExpanded(True)
            if keep is not None and node == keep:
                self.setCurrentItem(item)

    def _group(self, root: QTreeWidgetItem, project: Project) -> None:
        """Each section's groups as folders at its top, holding their members."""
        groups = project.of_type(TreeGroup)
        if not groups:
            return
        for i in range(root.childCount()):
            section = root.child(i)
            title = section.data(0, ROLE).label
            mine = [g for g in groups if g.section == title]
            if not mine:
                continue
            ids = {str(g.id) for g in mine}
            nested = {m for g in mine for m in g.members if m in ids}
            folders = {str(g.id): QTreeWidgetItem([g.name]) for g in mine}
            for g in mine:
                folders[str(g.id)].setData(0, ROLE, Node("group", str(g.id), "", g.name))
            owner = {m: str(g.id) for g in mine for m in g.members}
            items = [section.child(j) for j in range(section.childCount())]
            for item in items:  # members into their folders
                key = member_key(item.data(0, ROLE))
                if key in owner:
                    section.removeChild(item)
                    folders[owner[key]].addChild(item)
            for n, g in enumerate(g for g in mine if str(g.id) not in nested):
                section.insertChild(n, folders[str(g.id)])
            for g in mine:  # groups inside groups, first in their folder
                for m in reversed(g.members):
                    if m in folders and m != str(g.id):
                        folders[str(g.id)].insertChild(0, folders[m])
            for folder in folders.values():
                self._flags(folder)

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
            if (found.kind, found.object, found.path) == (node.kind, node.object, node.path):
                return item
        return None

    def groupable(self, item: QTreeWidgetItem) -> bool:
        """Directly under a section or a group: a top-level thing that can be moved."""
        parent = item.parent()
        up = parent.data(0, ROLE) if parent is not None else None
        node = item.data(0, ROLE)
        return (up is not None and up.kind in ("section", "group")
                and member_key(node) is not None)  # fmt: skip

    def _flags(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, ROLE)
        flags = item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled & ~Qt.ItemFlag.ItemIsDropEnabled
        if node.kind in RENAMABLE:
            flags |= Qt.ItemFlag.ItemIsEditable
        if node.kind in ("section", "group") or member_key(node) is not None:
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
        section = self.section_of(target)
        dragged = [i for i in self.selectedItems() if self.groupable(i)
                   and self.section_of(i) == section]  # fmt: skip
        if not dragged or section is None:
            return
        node = target.data(0, ROLE)
        if node.kind == "group":
            group = node.object
        else:
            parent = target.parent()
            up = parent.data(0, ROLE) if parent is not None else None
            group = up.object if up is not None and up.kind == "group" else None
        keys = [member_key(i.data(0, ROLE)) for i in dragged]
        if group is not None and group in keys:
            return  # a group into itself
        self.moved.emit(keys, group, section)

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
        if node is not None and node.kind in ("project", "section", "sweep", "group"):
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
            if (found.kind, found.object, found.path) == (node.kind, node.object, node.path):
                self.setCurrentItem(item)
                return

    # --- building ----------------------------------------------------------------

    def _children(self, item: QTreeWidgetItem, obj, project: Project) -> None:
        oid = str(obj.id)
        names = {str(o.id): o.name for o in project.objects.values()}
        if isinstance(obj, Geometry):
            for i, step in enumerate(obj.history):
                off = "  (off)" if step.suppressed else ""
                text = f"{i + 1}. {label(step.kind)} {step.name}{off}"
                self._add(item, text, Node("operation", oid, f"history.{i}", step.name))
            for i, s in enumerate(obj.selections):
                text = f"{s.name}  ({_count(len(s.members), s.entity)})"
                self._add(item, text, Node("selection", oid, f"selections.{i}", s.name))
        elif isinstance(obj, Component):
            for i, p in enumerate(obj.parts):
                text = f"Part {p.name}: {names.get(str(p.geometry), '?')}"
                self._add(item, text, Node("part", oid, f"parts.{i}", p.name))
            for i, a in enumerate(obj.materials):
                text = f"{names.get(str(a.material), '?')} → {a.selection}"
                self._add(item, text, Node("assignment", oid, f"materials.{i}", a.selection))
            for i, k in enumerate(obj.contacts):
                text = f"Contact {k.name} ({label(k.type).lower()})"
                self._add(item, text, Node("contact", oid, f"contacts.{i}", k.name))
            for i, c in enumerate(obj.connectors):  # ADR 0027
                d = c.diameter
                text = f"Fastener {c.name} (Ø {d.value:g} {d.unit}): {c.source} → {c.target}"
                self._add(item, text, Node("connector", oid, f"connectors.{i}", c.name))
            for i, s in enumerate(obj.shell_sections):  # ADR 0027 shells
                text = f"Shell on {s.selection}: {s.describe()}"
                self._add(
                    item, text, Node("shell_section", oid, f"shell_sections.{i}", s.selection)
                )
            for i, b in enumerate(obj.beam_sections):  # ADR 0027 beams
                text = f"Beam section on {b.selection}: {b.describe()}"
                self._add(item, text, Node("beam_section", oid, f"beam_sections.{i}", b.selection))
            for i, ph in enumerate(obj.physics):
                p_item = self._add(
                    item, label(ph.type), Node("physics", oid, f"physics.{i}", ph.type)
                )
                for j, f in enumerate(ph.features):
                    text = f"{_feature_label(ph.type, f.type)} on {f.selection}"
                    path = f"physics.{i}.features.{j}"
                    self._add(p_item, text, Node("feature", oid, path, f.selection))
        elif isinstance(obj, Study):
            for i, step in enumerate(obj.steps):
                if isinstance(step, SweepStep):
                    from puzzle.sweep import describe

                    text = describe(step)
                else:
                    what = ", ".join(label(p).lower() for p in step.physics)
                    text = label(step.type) + (f" · {what}" if what else "")
                self._add(item, text, Node("step", oid, f"steps.{i}", text))

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


def _feature_label(physics: str, kind: str) -> str:
    """A feature as the add menu names it ("Fixed support"), else its type in words."""
    from puzzle.model.catalog import CATALOG

    spec = next((s for s in CATALOG.get(physics, []) if s.type == kind), None)
    return spec.label if spec else label(kind)


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(str(text))
    except ValueError:
        return None
