"""The model tree: every project object, its parts, and the stored runs."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from puzzle.model.schema import (
    Component,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    Study,
    SweepStep,
    Transfer,
)
from puzzle.results.store import RunRecord

ROLE = Qt.ItemDataRole.UserRole


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
]


class ModelTree(QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)

    def populate(self, project: Project, runs: list[RunRecord]) -> None:
        """Rebuild from the project, keeping what was selected and expanded."""
        keep = self.current()
        opened = {self._key(i) for i in self._items() if i.isExpanded()}
        self.clear()
        root = self._add(
            self.invisibleRootItem(), project.name, Node("project", label=project.name)
        )
        flagged = {p.object for p in project.problems()}
        for title, cls in SECTIONS:
            objects = project.of_type(cls)
            if not objects:
                continue
            section = self._add(root, title, Node("section", label=title))
            for obj in objects:
                mark = "  ⚠" if obj.name in flagged else ""
                item = self._add(
                    section, obj.name + mark, Node("object", str(obj.id), "", obj.name)
                )
                self._children(item, obj, project)
        if runs:
            section = self._add(root, "Results", Node("section", label="Results"))
            for r in reversed(runs):
                label = f"{r.study_name} · {r.solver} · {r.started_at:%Y-%m-%d %H:%M}"
                self._add(section, label, Node("run", r.run_id, "", label))
        for item in self._items():
            node = item.data(0, ROLE)
            if self._key(item) in opened or node.kind in ("project", "section"):
                item.setExpanded(True)
            if keep is not None and node == keep:
                self.setCurrentItem(item)

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
            for i, s in enumerate(obj.selections):
                text = f"{s.name}  ({s.entity} ×{len(s.members)})"
                self._add(item, text, Node("selection", oid, f"selections.{i}", s.name))
        elif isinstance(obj, Component):
            for i, p in enumerate(obj.parts):
                text = f"part {p.name}: {names.get(str(p.geometry), '?')}"
                self._add(item, text, Node("part", oid, f"parts.{i}", p.name))
            for i, a in enumerate(obj.materials):
                text = f"material {names.get(str(a.material), '?')} → {a.selection}"
                self._add(item, text, Node("assignment", oid, f"materials.{i}", a.selection))
            for i, k in enumerate(obj.contacts):
                self._add(item, f"contact {k.name}", Node("contact", oid, f"contacts.{i}", k.name))
            for i, ph in enumerate(obj.physics):
                p_item = self._add(item, ph.type, Node("physics", oid, f"physics.{i}", ph.type))
                for j, f in enumerate(ph.features):
                    text = f"{f.type} on {f.selection}"
                    path = f"physics.{i}.features.{j}"
                    self._add(p_item, text, Node("feature", oid, path, f.selection))
        elif isinstance(obj, Study):
            for i, step in enumerate(obj.steps):
                text = (
                    f"sweep ({len(step.points())} points)"
                    if isinstance(step, SweepStep)
                    else step.type
                )
                self._add(item, text, Node("step", oid, f"steps.{i}", text))

    def _add(self, parent, text: str, node: Node) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, ROLE, node)
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
