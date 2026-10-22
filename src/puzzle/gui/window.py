"""The main window: model tree, properties, 3D view, output, and (next) the agent.

Every change goes through the project's `Session` as a command (ADR 0015), so edits
made here are undoable and logged like a script's or an agent's. The window watches
history.jsonl, so changes another client makes (an agent over MCP, the CLI) show up.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.commands import Session
from puzzle.commands.model import AddLibraryMaterial, Delete, Edit, EditOp, RunStudy
from puzzle.errors import PuzzleError
from puzzle.gui.agent_panel import AgentPanel
from puzzle.gui.properties import PropertiesPanel
from puzzle.gui.tasks import Task, start
from puzzle.gui.tree import ModelTree, Node
from puzzle.gui.viewport import Viewport
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Geometry, Study
from puzzle.project.layout import HISTORY, RESULTS_DIR, WORK_DIR
from puzzle.results.model import Results
from puzzle.results.store import ResultStore


class ElidedLabel(QLabel):
    """A one-line label that never asks for width: long text (many picked names) is
    cut with an ellipsis, in full in its tooltip. A plain QLabel's minimum width is its
    text's, which made the view push the docks aside."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt's name)
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().sizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        rect = self.contentsRect()
        shown = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, rect.width()
        )
        painter = QPainter(self)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)


class ResultsBar(QWidget):
    """Field, step and deformation controls for the result on screen."""

    def __init__(self, on_change) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.field = QComboBox()
        self.step = QSpinBox()
        self.deform = QCheckBox("Deformed")
        self.deform.setChecked(True)
        self.title = ElidedLabel("")
        self.title.setStyleSheet("color: gray")
        for label, widget in (("Field", self.field), ("Step", self.step)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        row.addWidget(self.deform)
        row.addWidget(self.title, 1)
        self.field.currentTextChanged.connect(lambda _: on_change())
        self.step.valueChanged.connect(lambda _: on_change())
        self.deform.toggled.connect(lambda _: on_change())

    def load(self, results: Results) -> None:
        names = list(dict.fromkeys(n for s in results.steps for n in s.fields))
        chosen = self.field.currentData()  # kept across refreshes when the run has it
        for w in (self.field, self.step):
            w.blockSignals(True)
        self.field.clear()
        from puzzle.gui.choices import label

        for n in names:
            self.field.addItem(label(n), n)
        defaults = ("displacement", "temperature", "velocity")
        preferred = chosen if chosen in names else next((n for n in defaults if n in names), None)
        if preferred:
            self.field.setCurrentIndex(self.field.findData(preferred))
        self.step.setRange(1, max(1, len(results.steps)))
        self.step.setValue(self.step.maximum())
        for w in (self.field, self.step):
            w.blockSignals(False)


# The selection filter: entity kind -> what the pick bar calls it.
FILTERS = {"face": "Faces", "edge": "Edges", "body": "Bodies", "vertex": "Points"}
ONE = {"face": "face", "edge": "edge", "body": "body", "vertex": "point"}


def _count(n: int, kind: str) -> str:
    return f"{n} {ONE[kind] if n == 1 else FILTERS[kind].lower()}"


class PickBar(QWidget):
    """What is picked in the view, and what to do with it."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.label = ElidedLabel("")
        self.filter = QComboBox()  # what a double-click picks
        for kind, label in FILTERS.items():
            self.filter.addItem(label, kind)
        self.filter.currentIndexChanged.connect(
            lambda: window.viewport.set_pick_kind(self.filter.currentData())
        )
        row.addWidget(self.filter)
        new, add, clear = (
            QPushButton("New selection…"),
            QPushButton("Add to selection"),
            QPushButton("Clear"),
        )
        new.clicked.connect(self._new)
        add.clicked.connect(lambda: window.add_picks_to_selection())
        clear.clicked.connect(lambda: window.viewport.clear_picks())
        row.addWidget(self.label, 1)
        for b in (new, add, clear):
            row.addWidget(b)
        window.viewport.picked_changed.connect(self.update_picks)

    def update_picks(self, picked) -> None:
        names = self.window.picked_names()
        shown = ", ".join(f"{p}/{n}" for p, n in names[:6]) + (" …" if len(names) > 6 else "")
        kind = self.window.viewport.pick_kind
        text = (
            f"{_count(len(names), kind)} picked: {shown}"
            if names
            else f"Double-click {FILTERS[kind].lower()} to pick"
        )
        size = self.window.viewport.measure(self.window.viewport.picked) if names else {}
        if size.get("area"):
            text += f"  ·  area {size['area'] * 1e6:.4g} mm²"
        if size.get("length"):
            text += f"  ·  length {size['length'] * 1e3:.4g} mm"
        if "distance" in size:
            apart = "centres" if self.window.viewport.pick_kind == "face" else "points"
            text += f"  ·  {apart} {size['distance'] * 1e3:.4g} mm apart"
        self.label.setText(text)
        self.setVisible(self.window.viewport.picking or bool(names))

    def _new(self) -> None:
        kinds = FILTERS[self.window.viewport.pick_kind].lower()
        name = self.window.ask_text("New selection", f"Name for the picked {kinds}:", "Selection")
        if name:
            self.window.selection_from_picks(name)


class MainWindow(QMainWindow):
    def __init__(self, root: Path | None = None, *, provider_factory=None) -> None:
        super().__init__()
        self.setWindowTitle("Puzzle")
        self.resize(1400, 900)
        self.session: Session | None = None
        self.meshes: dict[str, Mesh] = {}  # component id -> its last generated mesh
        self.mesh_names: dict[str, dict] = {}  # component id -> (dim, tag) -> (part, name)
        self.mesh_boundaries: dict[str, dict] = {}  # component id -> body -> its faces
        self.mesh_vertices: dict[str, dict] = {}  # component id -> vertex -> position
        self.mesh_anchors: dict[str, dict] = {}  # id -> (part, entity, name) -> anchor
        self.mesh_selections: dict[str, dict] = {}  # component id -> part -> reports
        self._selections_checked: dict[str, str] = {}  # component id -> what was checked
        self._selections_checking: dict[str, str] = {}
        self.mesh_component: str | None = None  # whose mesh the view shows
        self.previews: dict[str, tuple[str, object]] = {}  # component id -> (key, MeshResult)
        self._previewing: tuple[str, str] | None = None
        self.geometry_findings: dict[str, list[dict]] = {}  # geometry name -> its check
        self._standalones: dict[str, tuple] = {}  # geometry id -> (component, recipe)
        self.result: tuple[Mesh, Results] | None = None
        self.tasks: list[Task] = []  # keep running tasks alive

        self.tree = ModelTree()
        self.properties = PropertiesPanel()
        self.viewport = Viewport()
        self.results_bar = ResultsBar(self._redraw_result)
        self.results_bar.hide()
        self.pick_bar = PickBar(self)
        self.pick_bar.hide()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(20000)
        self.history = QListWidget()
        self.problems = QListWidget()

        centre = QWidget()
        column = QVBoxLayout(centre)
        # A few pixels clear of the dock separators: on macOS the separator handles
        # overlap the view's native OpenGL surface and smear marks along its edge.
        column.setContentsMargins(0, 0, 0, 6)
        column.addWidget(self.results_bar)
        column.addWidget(self.pick_bar)
        column.addWidget(self.viewport, 1)
        self.setCentralWidget(centre)
        left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        # Left: the model tree with the properties of what is selected beneath it.
        # Right: the agent, full height. Bottom: messages.
        self.model_dock = self._dock("Model", self.tree, left)
        self.properties_dock = self._dock("Properties", self.properties, left)
        self.splitDockWidget(self.model_dock, self.properties_dock, Qt.Orientation.Vertical)
        tabs = QTabWidget()
        tabs.addTab(self.log, "Output")
        tabs.addTab(self.history, "History")
        tabs.addTab(self.problems, "Problems")
        self.output_tabs = tabs
        self.messages_dock = self._dock("Messages", tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.agent = AgentPanel(self, provider_factory)
        self.agent_dock = self._dock("Agent", self.agent, right)
        self._sized = False  # dock sizes are applied once the window is on screen
        # Wide, visible separators: Qt's default ones on macOS are a hairline that is
        # hard to find and grab.
        self.setStyleSheet(
            "QMainWindow::separator { background: #3c3f44; width: 6px; height: 6px; }"
            "QMainWindow::separator:hover { background: #4f8fe6; }"
        )

        self._actions()
        self.tree.currentItemChanged.connect(lambda *_: self._selected())
        self.tree.itemDoubleClicked.connect(lambda *_: self._open_item())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.properties.edited.connect(self._edit)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self._reload_soon())
        self._reload_timer = QTimer(self, singleShot=True, interval=300)
        self._reload_timer.timeout.connect(self.reload)
        if root is not None:
            self.open(root)

    # --- project -----------------------------------------------------------------

    def open(self, root: Path) -> None:
        try:
            self.session = Session(Path(root))
        except PuzzleError as exc:
            QMessageBox.warning(self, "Open project", exc.message)
            return
        self.meshes.clear()
        self.result = None
        self.viewport.clear()
        self.results_bar.hide()
        history = self.session.root / HISTORY
        history.parent.mkdir(parents=True, exist_ok=True)
        history.touch()
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        self.watcher.addPath(str(history))
        self.setWindowTitle(f"Puzzle — {self.session.project.name}")
        self.agent.project_opened()
        self.refresh()
        # Open on the model, not an empty view: select the first component.
        first = next(iter(self.session.project.of_type(Component)), None)
        if first is not None:
            self.tree.select(Node("object", str(first.id)))

    def reload(self) -> None:
        """Re-read the project from disk: another client may have changed it."""
        if self.session is not None:
            self.session = Session(self.session.root)
            self.refresh()

    def refresh(self) -> None:
        s = self.session
        if s is None:
            return
        try:
            runs = self._store().runs()
        except PuzzleError as exc:  # an old results store: the model still opens
            runs = []
            if getattr(self, "_reported_store", None) != str(s.root):
                self._reported_store = str(s.root)
                self.report(exc)
        self.tree.populate(s.project, runs)
        self.history.clear()
        names = {str(o.id): o.name for o in s.project.objects.values()}
        for entry in s.history(limit=200):  # newest first
            self.history.addItem(_history_line(entry, names))
        self.refresh_problems()
        self.agent.refresh()
        self._selected()

    def refresh_problems(self) -> None:
        s = self.session
        self.problems.clear()
        for p in s.project.problems():
            self.problems.addItem(f"{p.object}: {p.message}")
        for geo, findings in self.geometry_findings.items():  # from "Check geometry"
            for f in findings:
                self.problems.addItem(f"{geo} (geometry {f['severity']}): {f['message']}")
        for line in self.detached_lines():
            self.problems.addItem(line)
        self.output_tabs.setTabText(2, f"Problems ({self.problems.count()})")

    def detached_lines(self) -> list[str]:
        """Detached selection members of the components meshed or previewed so far."""
        lines = []
        seen = set()
        for cid, parts in self.mesh_selections.items():
            comp = self.session.project.objects.get(_uuid(cid)) or next(
                (c for c, _ in self._standalones.values() if str(c.id) == cid), None
            )
            if comp is None:
                continue
            for part in comp.parts:
                geo = self.session.project.objects.get(part.geometry)
                for report in parts.get(part.name, []):
                    for m in report["members"]:
                        key = (geo.name if geo else part.name, report["selection"], m["name"])
                        if m["status"] != "detached" or key in seen:
                            continue
                        seen.add(key)
                        near = m["candidates"][0]["name"] if m["candidates"] else None
                        lines.append(
                            f"{key[0]}, selection {key[1]}: '{m['name']}' was not found"
                            + (f" (nearest: {near})" if near else "")
                            + ". Right-click the geometry: Reattach selections…"
                        )
        return lines

    def submit(self, command, **kwargs):
        """Submit a command; errors go to the status bar and the log. Returns the Outcome."""
        try:
            outcome = self.session.submit(command, **kwargs)
        except PuzzleError as exc:
            self.report(exc)
            self.refresh()
            return None
        self.refresh()
        return outcome

    # --- actions -----------------------------------------------------------------

    def _actions(self) -> None:
        bar = self.addToolBar("Main")
        bar.setMovable(False)
        menu = self.menuBar().addMenu("&Project")
        self.actions_by_name: dict[str, QAction] = {}
        for name, text, key, slot in (
            ("new", "New…", QKeySequence.StandardKey.New, self._new_dialog),
            ("open", "Open…", QKeySequence.StandardKey.Open, self._open_dialog),
            ("undo", "Undo", QKeySequence.StandardKey.Undo, self.undo),
            ("mesh", "Mesh", "Ctrl+M", self.mesh_selected),
            ("run", "Run", "Ctrl+R", self.run_selected),
            ("material", "Add material…", None, self._add_material_dialog),
            ("delete", "Delete", QKeySequence.StandardKey.Delete, self.delete_selected),
            ("reload", "Reload", QKeySequence.StandardKey.Refresh, self.reload),
            ("pick", "Pick", "P", self.toggle_picking),
        ):
            action = QAction(text, self)
            if key is not None:
                action.setShortcut(key)
            action.triggered.connect(slot)
            bar.addAction(action)
            menu.addAction(action)
            self.actions_by_name[name] = action
        pick = self.actions_by_name["pick"]
        pick.setCheckable(True)
        pick.setToolTip("Double-click faces in the view to pick them; drag still rotates")

    def new_project(self, root: Path, name: str) -> None:
        """Create an empty project in `root` and open it."""
        from puzzle.model.schema import Project
        from puzzle.project import save_project

        root.mkdir(parents=True, exist_ok=True)
        save_project(Project(name=name), root)
        self.open(root)

    def _new_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Folder for the new project")
        if not path:
            return
        name = self.ask_text("New project", "Project name:", Path(path).name)
        if name:
            self.new_project(Path(path), name)

    def _open_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open a Puzzle project")
        if path:
            self.open(Path(path))

    def undo(self) -> None:
        try:
            self.session.undo()
        except PuzzleError as exc:
            self.report(exc)
        self.refresh()

    def delete_selected(self) -> None:
        node = self.tree.current()
        if node is None or node.kind != "object":
            return
        self.submit(Delete(object=node.object))

    def add_material(self, key: str) -> None:
        self.submit(AddLibraryMaterial(key=key))

    def _add_material_dialog(self) -> None:
        from puzzle.materials import library_material, search_library

        keys = search_library("")
        named = [library_material(k).name for k in keys]
        # The library key only where two materials share a name.
        options = [
            (n if named.count(n) == 1 else f"{n} ({k})", k)
            for n, k in zip(named, keys, strict=True)
        ]
        key = self.choose("Add material", "Library material:", options)
        if key:
            self.add_material(key)

    def mesh_selected(self) -> Task | None:
        comp = self._component_of(self.tree.current())
        if comp is None:
            self.statusBar().showMessage("Select a component (or a study) to mesh", 5000)
            return None
        from puzzle.mesh import generate_mesh

        s = self.session
        out = s.root / WORK_DIR / "gui-mesh" / str(comp.id)
        project = copy.deepcopy(s.project)  # the GUI may edit while this meshes

        def work(_emit):
            return generate_mesh(project, comp.id, s.root, out)

        def done(meshed) -> None:
            self.meshes[str(comp.id)] = meshed.mesh
            self.mesh_names[str(comp.id)] = meshed.names
            self.mesh_boundaries[str(comp.id)] = meshed.boundaries
            self.mesh_vertices[str(comp.id)] = meshed.vertices
            self.mesh_anchors[str(comp.id)] = meshed.anchors
            self.mesh_selections[str(comp.id)] = meshed.selections
            self.refresh_problems()
            for w in meshed.warnings:
                self.log.appendPlainText(f"mesh: {w}")
            cells = sum(len(b.ids) for b in meshed.mesh.blocks if b.dim == 3)
            self.statusBar().showMessage(f"Meshed {comp.name}: {cells:,} elements", 8000)
            self._selected()

        return self.background(f"Meshing {comp.name}…", work, done)

    def run_selected(self) -> Task | None:
        node = self.tree.current()
        study = self._object(node)
        if not isinstance(study, Study):
            self.statusBar().showMessage("Select a study to run", 5000)
            return None
        s = self.session
        self.output_tabs.setCurrentWidget(self.log)
        self.log.appendPlainText(f"--- {study.name}: {study.solver}")

        def work(emit):
            return s.submit(RunStudy(study=str(study.id)), on_output=emit)

        def done(outcome) -> None:
            self.refresh()
            run = outcome.run
            cached = " (reused an identical run)" if run is not None and run.cached else ""
            self.statusBar().showMessage(f"{study.name} finished{cached}", 8000)
            if run is not None and run.run_id:
                self.tree.select(Node("run", run.run_id, "", ""))
                self._select_run(run.run_id)

        return self.background(f"Running {study.name}…", work, done)

    # --- selection ---------------------------------------------------------------

    def _selected(self) -> None:
        node = self.tree.current()
        s = self.session
        if s is None or node is None:
            return
        names = {str(o.id): o.name for o in s.project.objects.values()}
        if node.kind == "run":
            self._select_run(node.object)
            return
        obj = s.project.objects.get(_uuid(node.object)) if node.object else None
        if obj is None:
            self.properties.show_text({"project": s.project.name} if node.kind == "project" else {})
            return
        from puzzle.gui.choices import choices

        project = s.project
        self.properties.show_object(
            str(obj.id),
            obj.model_dump(mode="json"),
            node.path,
            names,
            lambda path: choices(project, obj, path),
        )
        self.results_bar.hide()
        comp = self._component_of(node)
        if comp is None:
            return
        mesh = self.meshes.get(str(comp.id))
        if mesh is None:
            mesh = self._preview(comp)
        if mesh is None:
            return
        highlight = None
        if node.kind == "selection" and isinstance(obj, Geometry):
            for part in comp.parts:
                if str(part.geometry) == str(obj.id):
                    highlight = f"{part.name}/{node.label}"
                    break
        elif node.kind in ("feature", "assignment"):
            highlight = self._feature_selection(obj, node.path)
        entities = self._entities_of(comp, highlight) if highlight else []
        if node.kind == "operation":  # the faces this step made (its pattern copies too)
            names = self.mesh_names.get(str(comp.id), {})
            mine = (f"{node.label}/", f"{node.label}#")
            entities = [e for e, (_, n) in names.items() if e[0] == 2 and n.startswith(mine)]
        self.mesh_component = str(comp.id)
        self.viewport.boundaries = self.mesh_boundaries.get(str(comp.id), {})
        self.viewport.vertices = self.mesh_vertices.get(str(comp.id), {})
        self.viewport.show_mesh(mesh, highlight if highlight in mesh.groups else None, entities)

    def _select_run(self, run_id: str) -> None:
        store = self._store()
        record = store.get_run(run_id)
        self.properties.show_text(
            {
                "study": record.study_name,
                "solver": record.solver,
                "runtime": record.runtime,
                "started": f"{record.started_at:%Y-%m-%d %H:%M:%S}",
                "duration (s)": round(record.duration_s, 2),
                "result steps": record.steps,
            }
        )
        self.result = (store.read_mesh(record.mesh_id), store.read_results(run_id))
        self.results_bar.load(self.result[1])
        self.results_bar.show()
        self._redraw_result()

    def _redraw_result(self) -> None:
        if self.result is None:
            return
        mesh, results = self.result
        field = self.results_bar.field.currentData()
        steps = [s for s in results.steps if field in s.fields]
        if not field or not steps:
            return
        index = min(self.results_bar.step.value(), len(steps)) - 1
        try:
            self.viewport.show_result(
                mesh, results, field, step_index=index, deform=self.results_bar.deform.isChecked()
            )
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return
        shown = self.viewport.shown
        self.results_bar.title.setText(shown.get("title", ""))
        self.statusBar().showMessage(
            f"{shown['scalar']} ({shown['step']}): {shown['min']:.4g} … {shown['max']:.4g}"
        )

    # --- picking -------------------------------------------------------------------

    def toggle_picking(self, on: bool | None = None) -> None:
        on = self.actions_by_name["pick"].isChecked() if on is None else on
        self.actions_by_name["pick"].setChecked(on)
        if on and self.viewport.mesh is None:
            self.statusBar().showMessage("Mesh a component and select it to pick faces", 8000)
        self.viewport.set_picking(on)
        self.pick_bar.update_picks(self.viewport.picked)

    def picked_names(self) -> list[tuple[str, str]]:
        """The picked entities as (part, geometry name)."""
        names = self.mesh_names.get(self.mesh_component or "", {})
        return [names[e] for e in self.viewport.picked if e in names]

    def selection_from_picks(self, name: str):
        """A new selection of the filter's kind on the picked entities' geometry (one
        geometry at a time)."""
        geo, members = self._picked_geometry()
        if geo is None:
            return None
        if any(s.name == name for s in geo.selections):
            self.statusBar().showMessage(f"{geo.name} already has a selection '{name}'", 8000)
            return None
        value = {
            "name": name,
            "entity": self.viewport.pick_kind,
            "members": members,
            "anchors": self._anchors_of(members),
        }
        out = self.submit(
            Edit(object=str(geo.id), ops=[EditOp(op="append", path="selections", value=value)])
        )
        if out is not None:
            self.viewport.clear_picks()
        return out

    def add_picks_to_selection(self, node: Node | None = None):
        """Add the picked entities to the selection chosen in the tree (of the same kind)."""
        node = node or self.tree.current()
        geo, members = self._picked_geometry()
        if geo is None or node is None or node.kind != "selection" or node.object != str(geo.id):
            self.statusBar().showMessage(
                "Select a selection of the picked entities' geometry", 8000
            )
            return None
        index = int(node.path.split(".")[1])
        kind = geo.selections[index].entity
        if kind != self.viewport.pick_kind:
            self.statusBar().showMessage(
                f"'{node.label}' holds {kind}s; set the filter to {FILTERS[kind]} to add to it",
                8000,
            )
            return None
        current = geo.selections[index].members
        merged = current + [m for m in members if m not in current]
        anchors = {**self._anchors_of(members), **geo.selections[index].anchors}
        ops = [
            EditOp(op="set", path=f"selections.{index}.members", value=merged),
            EditOp(op="set", path=f"selections.{index}.anchors", value=anchors),
        ]
        out = self.submit(Edit(object=str(geo.id), ops=ops))
        if out is not None:
            self.viewport.clear_picks()
        return out

    def _anchors_of(self, members: list[str]) -> dict[str, list]:
        """Where the picked members are (from the shown mesh's geometry): stored with
        the selection so it can be re-found if its names stop fitting (G3)."""
        anchors = self.mesh_anchors.get(self.mesh_component or "", {})
        parts, kind = {n: p for p, n in self.picked_names()}, self.viewport.pick_kind
        return {
            m: anchors[(parts[m], kind, m)] for m in members if (parts.get(m), kind, m) in anchors
        }

    def _picked_geometry(self):
        comp = self.session.project.objects.get(_uuid(self.mesh_component or "")) or next(
            (c for c, _ in self._standalones.values() if str(c.id) == self.mesh_component), None
        )  # a part shown on its own is picked on too
        picks = self.picked_names()
        if comp is None or not picks:
            self.statusBar().showMessage("Pick first (Pick, then double-click)", 8000)
            return None, []
        parts = {p.name: p for p in comp.parts}
        geometries = {str(parts[part].geometry) for part, _ in picks}
        if len(geometries) > 1:
            self.statusBar().showMessage(
                "The picks span several geometries; pick on one part", 8000
            )
            return None, []
        geo = self.session.project.objects.get(_uuid(geometries.pop()))
        return geo, list(dict.fromkeys(name for _, name in picks))

    def _entities_of(self, comp, selection: str) -> list[tuple[int, int]]:
        """A selection's face entities in the shown mesh, through the names it was made with."""
        part_name, _, sel_name = selection.partition("/")
        part = next((p for p in comp.parts if p.name == part_name), None)
        geo = self.session.project.objects.get(part.geometry) if part else None
        sel = next((s for s in geo.selections if s.name == sel_name), None) if geo else None
        if sel is None:
            return []
        names = self.mesh_names.get(str(comp.id), {})
        hits = [e for e, (p, n) in names.items() if p == part_name and n in sel.members]
        # A body shows through the faces that bound it.
        boundaries = self.mesh_boundaries.get(str(comp.id), {})
        faces = [f for e in hits if e[0] == 3 for f in boundaries.get(e, [])]
        return [e for e in hits if e[0] < 3] + faces

    # --- questions (tests replace these) -----------------------------------------

    def choose(self, title: str, label: str, items: list):
        """One of `items`: plain strings, or (shown name, value) pairs so that people
        read names while the value (a key, an id) is what comes back."""
        if not items:
            return None
        pairs = [i if isinstance(i, tuple) else (str(i), i) for i in items]
        shown = [p[0] for p in pairs]
        item, ok = QInputDialog.getItem(self, title, label, shown, 0, False)
        return pairs[shown.index(item)][1] if ok else None

    def edit_step(self, kind: str, args: dict, name: str, features: list[str]):
        """The step dialog: (name, args) when accepted, else None."""
        from puzzle.gui.step_dialog import StepDialog

        dialog = StepDialog(kind, args, name, features, self)
        if dialog.exec() and dialog.result_args is not None:
            return dialog.name.text().strip() or name, dialog.result_args
        return None

    def choose_reattachment(self, geometry: str, reports: list[dict], rules: dict):
        """The reattach dialog: choices for `naming.reattach_ops`, or None."""
        from puzzle.gui.reattach import ReattachDialog

        dialog = ReattachDialog(geometry, reports, rules, self)
        return dialog.choices() if dialog.exec() else None

    def ask_text(self, title: str, label: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

    def _open_item(self) -> None:
        """A double-click on a history step edits it in its dialog."""
        from puzzle.gui import menus

        node = self.tree.current()
        obj = self._object(node)
        if node is not None and node.kind == "operation" and isinstance(obj, Geometry):
            menus.edit_step(self, obj, int(node.path.split(".")[1]))

    def _context_menu(self, pos) -> None:
        from puzzle.gui.menus import build_menu

        item = self.tree.itemAt(pos)
        if item is None or self.session is None:
            return
        self.tree.setCurrentItem(item)
        menu = build_menu(self, self.tree.current())
        if menu is not None:
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- helpers -----------------------------------------------------------------

    def _edit(self, object_id: str, path: str, value) -> None:
        self.submit(Edit(object=object_id, ops=[EditOp(op="set", path=path, value=value)]))

    def background(self, message: str, work, done, *, failed=None, output=None, lock=True) -> Task:
        """Run `work(emit)` on the thread pool; `done(result)` back on the GUI thread.

        `lock` greys out Mesh and Run meanwhile (one solve at a time); `output` takes
        the lines `emit` sends (default: the Output log).
        """
        self.statusBar().showMessage(message)
        task = Task(work)
        task.lock = lock
        if lock:
            for name in ("mesh", "run"):
                self.actions_by_name[name].setEnabled(False)
        task.signals.output.connect(output or self.log.appendPlainText)

        def finish(result) -> None:
            self._finished(task)
            self.statusBar().clearMessage()
            done(result)

        def fail(exc) -> None:
            self._finished(task)
            self.report(exc)
            if failed is not None:
                failed(exc)

        task.signals.done.connect(finish)
        task.signals.failed.connect(fail)
        self.tasks.append(task)
        return start(task)

    def _finished(self, task: Task) -> None:
        self.tasks.remove(task)
        if not any(t.lock for t in self.tasks):
            for name in ("mesh", "run"):
                self.actions_by_name[name].setEnabled(True)

    def report(self, exc: Exception) -> None:
        text = exc.message if isinstance(exc, PuzzleError) else f"{type(exc).__name__}: {exc}"
        code = f"[{exc.code}] " if isinstance(exc, PuzzleError) else ""
        self.log.appendPlainText(f"error: {code}{text}")
        self.statusBar().showMessage(f"{code}{text.splitlines()[0] if text else ''}", 10000)

    def _store(self) -> ResultStore:
        return ResultStore(self.session.root / RESULTS_DIR)

    def _object(self, node: Node | None):
        if node is None or not node.object or node.kind == "run":
            return None
        return self.session.project.objects.get(_uuid(node.object))

    def _component_of(self, node: Node | None) -> Component | None:
        """The component a tree item belongs to: itself, its study's, or one using it."""
        obj = self._object(node)
        project = self.session.project
        if isinstance(obj, Component):
            return obj
        if isinstance(obj, Study):
            return project.objects.get(obj.component)
        if isinstance(obj, Geometry):
            users = [
                c for c in project.of_type(Component) if any(p.geometry == obj.id for p in c.parts)
            ]
            meshed = [c for c in users if str(c.id) in self.meshes]
            return (meshed or users or [self._standalone(obj)])[0]
        return None

    def _standalone(self, geo: Geometry) -> Component:
        """A stand-in component for a geometry no component uses yet, so it can be
        shown and picked; it lives only in the preview's copy of the project."""
        from puzzle.model.schema import MeshRecipe, PartInstance

        held = self._standalones.get(str(geo.id))
        if held is None:
            recipe = MeshRecipe(name=f"{geo.name} preview")
            comp = Component(
                name=geo.name, parts=[PartInstance(name=geo.name, geometry=geo.id)], mesh=recipe.id
            )
            held = self._standalones[str(geo.id)] = (comp, recipe)
        return held[0]

    def _preview(self, comp: Component):
        """The component's geometry as a quick surface mesh, made once per shape.

        Returns it when ready; otherwise starts making it and shows it when done.
        """
        cid = str(comp.id)
        key = self._preview_key(comp)
        cached = self.previews.get(cid)
        if cached is not None and cached[0] == key:
            if cid not in self.meshes:  # the real mesh's maps win once it exists
                self.mesh_names[cid] = cached[1].names
                self.mesh_boundaries[cid] = cached[1].boundaries
                self.mesh_vertices[cid] = cached[1].vertices
                self.mesh_anchors[cid] = cached[1].anchors
            self._check_selections(comp)
            return cached[1].mesh
        if self._previewing == (cid, key):
            return None  # already being made
        from puzzle.mesh import generate_mesh

        s = self.session
        project = copy.deepcopy(s.project)
        if comp.id not in project.objects:  # a geometry shown on its own
            _, recipe = self._standalones[str(comp.parts[0].geometry)]
            project.add(recipe)
            project.add(comp)
        out = s.root / WORK_DIR / "gui-preview" / cid
        self._previewing = (cid, key)

        def done(result) -> None:
            self._previewing = None
            self.previews[cid] = (key, result)
            self._selected()

        def failed(_exc) -> None:  # reported by background(): the step that failed
            self._previewing = None
            self.viewport.clear()

        self.background(
            f"Showing {comp.name}…",
            lambda _emit: generate_mesh(project, comp.id, s.root, out, preview=True),
            done,
            failed=failed,
            lock=False,
        )
        return None

    def _preview_key(self, comp: Component) -> str:
        """What the preview depends on: the parts and their geometries' shape (not their
        selections), and the parameters a history geometry may use."""
        import hashlib
        import json

        from puzzle.model.schema import ParameterSet

        project = self.session.project
        parts = [p.model_dump(mode="json") for p in comp.parts]
        shapes = []
        for p in comp.parts:
            geo = project.objects.get(p.geometry)
            if isinstance(geo, Geometry):
                shapes.append(geo.model_dump(mode="json", exclude={"selections", "name"}))
                shapes.append(self._files_stamp(geo))  # a file replaced outside Puzzle
        params = [ps.model_dump(mode="json") for ps in project.of_type(ParameterSet)]
        text = json.dumps([parts, shapes, params], sort_keys=True, default=str)
        return hashlib.sha256(text.encode()).hexdigest()

    def _files_stamp(self, geo: Geometry) -> list:
        """Size and time of the files a geometry reads (its source, its import steps)."""
        files = [geo.source] if geo.source else []
        files += [s.args.get("file") for s in geo.history if s.kind == "import"]
        out = []
        for f in files:
            path = self.session.root / str(f)
            try:
                st = path.stat()
                out.append([str(f), st.st_size, st.st_mtime_ns])
            except OSError:
                out.append([str(f), None])
        return out

    def _check_selections(self, comp: Component) -> None:
        """Resolve the shown parts' selections in the background whenever they (or the
        shape) change, so detached members reach Problems without a remesh (G3)."""
        import copy
        import json

        from puzzle.geometry.meshfile import is_mesh_file
        from puzzle.geometry.naming import check_selections

        cid, project, root = str(comp.id), self.session.project, self.session.root
        geos = {p.name: project.objects.get(p.geometry) for p in comp.parts}
        key = self._preview_key(comp) + json.dumps(
            {n: [s.model_dump(mode="json") for s in g.selections] for n, g in geos.items() if g},
            sort_keys=True,
        )
        if key in (self._selections_checked.get(cid), self._selections_checking.get(cid)):
            return
        self._selections_checking[cid] = key
        snapshot = copy.deepcopy(project)

        def work(_emit):
            out = {}
            for name, geo in geos.items():
                geo = snapshot.objects.get(geo.id) if geo is not None else None
                if (
                    geo is None
                    or not geo.selections
                    or (not geo.history and is_mesh_file(geo.source))
                ):
                    out[name] = []
                    continue
                out[name] = [r.to_dict() for r in check_selections(snapshot, geo, root).reports]
            return out

        def done(result) -> None:
            self._selections_checking.pop(cid, None)
            self._selections_checked[cid] = key
            self.mesh_selections[cid] = result
            self.refresh_problems()

        def failed(_exc) -> None:
            self._selections_checking.pop(cid, None)

        self.background("Checking selections…", work, done, lock=False, failed=failed)

    @staticmethod
    def _feature_selection(obj, path: str) -> str | None:
        node = obj
        for part in path.split("."):
            node = node[int(part)] if isinstance(node, list) else getattr(node, part)
        return getattr(node, "selection", None)

    def _dock(self, title: str, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _reload_soon(self) -> None:
        self._reload_timer.start()
        # Some editors replace the file; keep watching the new one.
        if self.session is not None and not self.watcher.files():
            self.watcher.addPath(str(self.session.root / HISTORY))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        if not self._sized:  # Qt ignores dock sizes set before the first show
            self._sized = True
            QTimer.singleShot(0, self._size_docks)

    def _size_docks(self) -> None:
        vertical, horizontal = Qt.Orientation.Vertical, Qt.Orientation.Horizontal
        self.resizeDocks([self.messages_dock], [170], vertical)
        self.resizeDocks([self.model_dock, self.properties_dock], [380, 320], vertical)
        self.resizeDocks([self.model_dock], [360], horizontal)  # one area per call
        self.resizeDocks([self.agent_dock], [420], horizontal)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.viewport.close_plotter()
        super().closeEvent(event)


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(text)
    except ValueError:
        return text


def _history_line(entry: dict, names: dict | None = None) -> str:
    """A history entry in words: who did what to which object, by name."""
    names = names or {}
    who = entry.get("actor", {})
    actor = (
        "You"
        if who.get("kind") == "user"
        else (f"Agent {who['name']}" if who.get("name") else "Agent")
    )
    if entry.get("approved_by"):
        actor += ", accepted by you"
    kind = entry.get("type", "")
    seq = entry.get("seq", "?")
    if kind == "decision":
        verb = "accepted" if entry.get("accepted") else "rejected"
        return f"#{seq}  {actor}: {verb} a proposal"
    if kind == "proposal":
        what = "; ".join(_describe(c, names) for c in entry.get("commands", []))
        return f"#{seq}  {actor}: proposed {what}"
    if kind == "confirmation_request":
        return f"#{seq}  {actor}: asked for a confirmation"
    cmd = entry.get("command", {})
    what = _describe(cmd, names) if isinstance(cmd, dict) else ""
    return f"#{seq}  {actor}: {what}"


def _describe(cmd: dict, names: dict) -> str:
    from puzzle.gui.choices import label

    command = cmd.get("command", "")

    def name(ref) -> str:
        return names.get(str(ref), str(ref)) if ref else "?"

    if command == "batch":
        return "; ".join(_describe(c, names) for c in cmd.get("commands", []))
    if command == "create":
        data = cmd.get("data", {})
        return f"added {label(cmd.get('kind', 'object')).lower()} {data.get('name', '')}".rstrip()
    if command == "edit":
        fields = dict.fromkeys(
            label(op.get("path", "").split(".")[0]).lower() for op in cmd.get("ops", [])
        )
        return f"changed {name(cmd.get('object'))} ({', '.join(fields)})"
    if command == "delete":
        return f"deleted {name(cmd.get('object'))}"
    if command == "add_library_material":
        return f"added material {cmd.get('name') or label(cmd.get('key', ''))}"
    if command == "copy_component":
        return f"copied {name(cmd.get('component'))} as {cmd.get('name', '')}"
    if command == "undo":
        return f"undid #{cmd.get('seq', '?')}"
    if command == "run_study":
        return f"ran {name(cmd.get('study'))}"
    if command == "set_autonomy":
        level = cmd.get("level")
        return f"set agents to {label(level).lower()}" if level else "changed agent settings"
    return label(command).lower()
