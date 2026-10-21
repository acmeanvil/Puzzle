"""The main window: model tree, properties, 3D view, output, and (next) the agent.

Every change goes through the project's `Session` as a command (ADR 0015), so edits
made here are undoable and logged like a script's or an agent's. The window watches
history.jsonl, so changes another client makes (an agent over MCP, the CLI) show up.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
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
        for label, widget in (("Field", self.field), ("Step", self.step)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        row.addWidget(self.deform)
        row.addStretch()
        self.field.currentTextChanged.connect(lambda _: on_change())
        self.step.valueChanged.connect(lambda _: on_change())
        self.deform.toggled.connect(lambda _: on_change())

    def load(self, results: Results) -> None:
        names = list(dict.fromkeys(n for s in results.steps for n in s.fields))
        chosen = self.field.currentText()  # kept across refreshes when the run has it
        for w in (self.field, self.step):
            w.blockSignals(True)
        self.field.clear()
        self.field.addItems(names)
        defaults = ("displacement", "temperature", "velocity")
        preferred = chosen if chosen in names else next((n for n in defaults if n in names), None)
        if preferred:
            self.field.setCurrentText(preferred)
        self.step.setRange(1, max(1, len(results.steps)))
        self.step.setValue(self.step.maximum())
        for w in (self.field, self.step):
            w.blockSignals(False)


class MainWindow(QMainWindow):
    def __init__(self, root: Path | None = None, *, provider_factory=None) -> None:
        super().__init__()
        self.setWindowTitle("Puzzle")
        self.resize(1400, 900)
        self.session: Session | None = None
        self.meshes: dict[str, Mesh] = {}  # component id -> its last generated mesh
        self.result: tuple[Mesh, Results] | None = None
        self.tasks: list[Task] = []  # keep running tasks alive

        self.tree = ModelTree()
        self.properties = PropertiesPanel()
        self.viewport = Viewport()
        self.results_bar = ResultsBar(self._redraw_result)
        self.results_bar.hide()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(20000)
        self.history = QListWidget()
        self.problems = QListWidget()

        centre = QWidget()
        column = QVBoxLayout(centre)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.results_bar)
        column.addWidget(self.viewport, 1)
        self.setCentralWidget(centre)
        self._dock("Model", self.tree, Qt.DockWidgetArea.LeftDockWidgetArea)
        self._dock("Properties", self.properties, Qt.DockWidgetArea.RightDockWidgetArea)
        tabs = QTabWidget()
        tabs.addTab(self.log, "Output")
        tabs.addTab(self.history, "History")
        tabs.addTab(self.problems, "Problems")
        self.output_tabs = tabs
        self._dock("Messages", tabs, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.agent = AgentPanel(self, provider_factory)
        self.agent_dock = self._dock("Agent", self.agent, Qt.DockWidgetArea.RightDockWidgetArea)
        self.messages_dock = self.findChild(QDockWidget, "Messages")
        self.properties_dock = self.findChild(QDockWidget, "Properties")
        # The view and the chat get the room; messages and properties stay compact.
        vertical, horizontal = Qt.Orientation.Vertical, Qt.Orientation.Horizontal
        self.resizeDocks([self.messages_dock], [170], vertical)
        self.resizeDocks([self.properties_dock, self.agent_dock], [260, 560], vertical)
        self.resizeDocks([self.agent_dock], [440], horizontal)

        self._actions()
        self.tree.currentItemChanged.connect(lambda *_: self._selected())
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

    def reload(self) -> None:
        """Re-read the project from disk: another client may have changed it."""
        if self.session is not None:
            self.session = Session(self.session.root)
            self.refresh()

    def refresh(self) -> None:
        s = self.session
        if s is None:
            return
        self.tree.populate(s.project, self._store().runs())
        self.history.clear()
        for entry in s.history(limit=200):  # newest first
            self.history.addItem(_history_line(entry))
        self.problems.clear()
        for p in s.project.problems():
            self.problems.addItem(f"[{p.code}] {p.object}: {p.message}")
        self.output_tabs.setTabText(2, f"Problems ({self.problems.count()})")
        self.agent.refresh()
        self._selected()

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
            ("open", "Open…", QKeySequence.StandardKey.Open, self._open_dialog),
            ("undo", "Undo", QKeySequence.StandardKey.Undo, self.undo),
            ("mesh", "Mesh", "Ctrl+M", self.mesh_selected),
            ("run", "Run", "Ctrl+R", self.run_selected),
            ("material", "Add material…", None, self._add_material_dialog),
            ("delete", "Delete", QKeySequence.StandardKey.Delete, self.delete_selected),
            ("reload", "Reload", QKeySequence.StandardKey.Refresh, self.reload),
        ):
            action = QAction(text, self)
            if key is not None:
                action.setShortcut(key)
            action.triggered.connect(slot)
            bar.addAction(action)
            menu.addAction(action)
            self.actions_by_name[name] = action

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
        labels = [f"{library_material(k).name}  ({k})" for k in keys]
        label, ok = QInputDialog.getItem(
            self, "Add material", "Library material:", labels, 0, False
        )
        if ok:
            self.add_material(keys[labels.index(label)])

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
        self.properties.show_object(str(obj.id), obj.model_dump(mode="json"), node.path, names)
        self.results_bar.hide()
        comp = self._component_of(node)
        mesh = self.meshes.get(str(comp.id)) if comp is not None else None
        if mesh is None:
            return
        highlight = None
        if node.kind == "selection" and isinstance(obj, Geometry):
            for part in comp.parts:
                if str(part.geometry) == str(obj.id) and f"{part.name}/{node.label}" in mesh.groups:
                    highlight = f"{part.name}/{node.label}"
                    break
        elif node.kind in ("feature", "assignment"):
            target = self._feature_selection(obj, node.path)
            highlight = target if target in mesh.groups else None
        self.viewport.show_mesh(mesh, highlight)

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
        field = self.results_bar.field.currentText()
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
        self.statusBar().showMessage(
            f"{shown['scalar']} ({shown['step']}): {shown['min']:.4g} … {shown['max']:.4g}"
        )

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
            for c in project.of_type(Component):
                if (
                    any(str(p.geometry) == str(obj.id) for p in c.parts)
                    and str(c.id) in self.meshes
                ):
                    return c
        return None

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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.viewport.close_plotter()
        super().closeEvent(event)


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(text)
    except ValueError:
        return text


def _history_line(entry: dict) -> str:
    who = entry.get("actor", {})
    actor = who.get("kind", "?") + (f" {who['name']}" if who.get("name") else "")
    if entry.get("approved_by"):
        actor += f" (accepted by {entry['approved_by'].get('kind', 'user')})"
    kind = entry.get("type", "?")
    cmd = entry.get("command", {})
    what = cmd.get("command", "") if isinstance(cmd, dict) else ""
    if what == "batch":
        what = "batch of " + ", ".join(c.get("command", "?") for c in cmd.get("commands", []))
    target = cmd.get("object") or cmd.get("study") or cmd.get("kind") or cmd.get("key") or ""
    return f"#{entry.get('seq', '?')}  {actor}  {kind} {what} {target}".rstrip()
