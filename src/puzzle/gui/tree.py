"""The model tree: every project object, its parts, and the stored runs."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from puzzle.gui.choices import label, solver_label
from puzzle.model.schema import (
    Component,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    ResultDefinition,
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

    def populate(
        self, project: Project, runs: list[RunRecord], catalogs: dict | None = None
    ) -> None:
        """Rebuild from the project, keeping what was selected and expanded; `catalogs`
        (run id -> its stored fields) lists each run's results under it."""
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
        defined = project.of_type(ResultDefinition)
        if runs or defined:
            from puzzle.post.derived import METHODS
            from puzzle.post.quantities import catalog as results_of
            from puzzle.sweep import title

            section = self._add(root, "Results", Node("section", label="Results"))
            for d in defined:  # derived results (ADR 0025), evaluated when shown
                mark = "  ⚠" if d.name in flagged else ""
                text = f"{d.name} · {METHODS[d.method].split(' (')[0].lower()}{mark}"
                self._add(section, text, Node("derived", str(d.id), "", d.name))
            sweeps: dict[str, tuple[QTreeWidgetItem, set]] = {}  # study id -> heading, points
            for r in reversed(runs):  # newest first
                when = f"{solver_label(r.solver)} · {r.started_at:%Y-%m-%d %H:%M}"
                labels = r.point_labels()
                if labels:  # one point of a swept study: under the study's heading
                    if r.study_id not in sweeps:
                        head = self._add(
                            section, f"{r.study_name}: compare", Node("sweep", r.study_id)
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
                    text = f"{r.study_name} · {when}"
                    run = self._add(section, text, Node("run", r.run_id, "", text))
                # Every result the run can show, by field (double-click or select one).
                groups: dict = {}
                for item in results_of((catalogs or {}).get(r.run_id, [])):
                    if item.group not in groups:
                        node = Node("result_group", r.run_id, item.group, item.group)
                        groups[item.group] = self._add(run, item.group, node)
                    leaf = Node("result", r.run_id, item.key, item.label)
                    self._add(groups[item.group], item.label, leaf)
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
