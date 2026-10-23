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
    QStackedWidget,
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
from puzzle.model.schema import Component, Geometry, ResultDefinition, Study
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
    """Which result to show (every one the run's fields give: total deformation, its
    components, equivalent and principal stresses, ...), the step, and deformation."""

    def __init__(self, on_change) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.field = QComboBox()  # the result: data is a post.quantities key
        self.field.setMinimumWidth(240)
        self.items: dict = {}
        self.step = QSpinBox()
        self.deform = QCheckBox("Deformed")
        self.deform.setChecked(True)
        self.title = ElidedLabel("")
        self.title.setStyleSheet("color: gray")
        for label, widget in (("Result", self.field), ("Step", self.step)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        row.addWidget(self.deform)
        row.addWidget(self.title, 1)
        self.field.currentIndexChanged.connect(lambda _: on_change())
        self.step.valueChanged.connect(lambda _: on_change())
        self.deform.toggled.connect(lambda _: on_change())

    def load(self, results: Results, chosen: str | None = None, items=None) -> None:
        """The run's results, grouped by field; `chosen` (a key) or the last choice stays
        selected when the run has it, else the default (deformation, temperature, ...).
        `items` lists them when the fields alone don't say (a derived result's)."""
        from puzzle.post.quantities import catalog, default

        if items is None:
            fields: dict = {}
            for s in results.steps:
                for f in s.fields.values():
                    fields.setdefault(f.name, (f.name, f.location, f.components))
            items = catalog(list(fields.values()))
        chosen = chosen or self.field.currentData()
        for w in (self.field, self.step):
            w.blockSignals(True)
        self.field.clear()
        self.items = {i.key: i for i in items}
        group = None
        for item in items:
            if group is not None and item.group != group:
                self.field.insertSeparator(self.field.count())
            group = item.group
            self.field.addItem(item.label, item.key)
        pick = chosen if chosen in self.items else (default(items).key if items else None)
        if pick:
            self.field.setCurrentIndex(self.field.findData(pick))
        self.step.setRange(1, max(1, len(results.steps)))
        self.step.setValue(self.step.maximum())
        for w in (self.field, self.step):
            w.blockSignals(False)

    def item(self):
        return self.items.get(self.field.currentData())


def _measure(value: float, unit: str) -> str:
    """A measurement (SI) in the display units (ADR 0024): "10 mm", "0.3937 in"."""
    from puzzle.model.units import pretty, shown
    from puzzle.settings import current

    v, u = shown(value, unit, current().display_units())
    return f"{v:.4g} {pretty(u)}"


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
            text += f"  ·  area {_measure(size['area'], 'm^2')}"
        if size.get("length"):
            text += f"  ·  length {_measure(size['length'], 'm')}"
        if "distance" in size:
            apart = "centres" if self.window.viewport.pick_kind == "face" else "points"
            text += f"  ·  {apart} {_measure(size['distance'], 'm')} apart"
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
        # A tool's pending history (geometry id, steps as dicts): previewed, not saved.
        self.pending: tuple | None = None

        self.tree = ModelTree()
        self.properties = PropertiesPanel()
        self.viewport = Viewport()
        self.results_bar = ResultsBar(self._redraw_result)
        self.results_bar.hide()
        # Which part of the work is on screen (ADR 0029): model, geometry, sketch or
        # results. Panels that belong to one context show only in it.
        self.context = "model"
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
        # The centre is the 3D view, or the sketch canvas while sketching (ADR 0017).
        self.centre = QStackedWidget()
        self.centre.addWidget(centre)
        self.setCentralWidget(self.centre)
        left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        # Left: the model tree, and the Geometry tab beside it, with the properties of
        # what is selected (or the open tool's options) beneath. Right: the agent, full
        # height. Bottom: messages.
        from puzzle.gui.workbench import Workbench

        self.workbench = Workbench(self)
        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(self.tree, "Model")
        self.left_tabs.addTab(self.workbench, "Geometry")
        self.left_tabs.currentChanged.connect(lambda _: self._tab_changed())
        column.insertWidget(0, self.workbench.ribbon)  # the geometry tools, above the view
        self.workbench.ribbon.hide()
        self.model_dock = self._dock("Model", self.left_tabs, left)
        self.side = QStackedWidget()  # the properties, or a tool's options
        self.side.addWidget(self.properties)
        self.properties_dock = self._dock("Properties", self.side, left)
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
        self.set_context("model")
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
        from puzzle import settings

        settings.use_project(s.project)  # its own units, if switched on (ADR 0030)
        self._show_units()
        try:
            runs = self._store().runs()
        except PuzzleError as exc:  # an old results store: the model still opens
            runs = []
            if getattr(self, "_reported_store", None) != str(s.root):
                self._reported_store = str(s.root)
                if exc.code == "stale_store" and self._offer_new_store():
                    return self.refresh()
                self.report(exc)
        self.tree.populate(s.project, runs, self._field_catalogs(runs))
        self.history.clear()
        names = {str(o.id): o.name for o in s.project.objects.values()}
        for entry in s.history(limit=200):  # newest first
            self.history.addItem(_history_line(entry, names))
        self.refresh_problems()
        self.agent.refresh()
        self.workbench.refresh()
        self._selected()

    def _show_units(self) -> None:
        """The status bar's unit button: the system in effect, and whose it is."""
        from puzzle.model.units import pretty
        from puzzle.settings import current, project_system

        if not hasattr(self, "units_button"):
            from PySide6.QtWidgets import QPushButton

            self.units_button = QPushButton()
            self.units_button.setFlat(True)
            self.units_button.setToolTip("Units shown in this project: click to change")
            self.units_button.clicked.connect(self.edit_units)
            self.statusBar().addPermanentWidget(self.units_button)
        u = current().display_units()
        kinds = ("length", "force", "pressure", "temperature")
        text = " · ".join(pretty(u[k]) for k in kinds)
        whose = "project" if project_system() is not None else "yours"
        self.units_button.setText(f"{text}  ({whose})")

    def edit_units(self):
        """The project's unit switch: its own system for everyone, or each user's."""
        from puzzle.commands.model import Create, Edit, EditOp
        from puzzle.gui.settings_dialog import ProjectUnitsDialog
        from puzzle.model.schema import ProjectUnits

        if self.session is None:
            return None
        current = next(iter(self.session.project.of_type(ProjectUnits)), None)
        dialog = ProjectUnitsDialog(current, self)
        if not self.run_dialog(dialog):
            return None
        use, system = dialog.choice()
        if current is None:
            if not use:
                return None
            return self.submit(Create(kind="units", data={"use": True, "system": system}))
        ops = [
            EditOp(op="set", path=k, value=v)
            for k, v in (("use", use), ("system", system))
            if getattr(current, k) != v
        ]
        return self.submit(Edit(object=str(current.id), ops=ops)) if ops else None

    def _offer_new_store(self) -> bool:
        """An older Puzzle's results can't be read: offer to move them aside (kept, not
        deleted) and start a new store. Results are a cache of the studies, which can
        be run again."""
        old = self.session.root / RESULTS_DIR
        if not self.ask_yes(
            "Results from an older Puzzle",
            "This project's results were saved by an older version of Puzzle and can't be "
            "read.\n\nMove them aside (into a folder next to it, nothing is deleted) and "
            "start with no results? You can run the studies again to get them back.",
        ):
            return False
        n = 1
        while (aside := old.with_name(f"{old.name}-older-puzzle-{n}")).exists():
            n += 1
        old.rename(aside)
        self.log.appendPlainText(f"Old results moved to {aside.name}; run the studies again.")
        self.statusBar().showMessage("Started a new results store", 8000)
        self._reported_store = None
        return True

    def _field_catalogs(self, runs) -> dict:
        """Each run's stored fields (metadata only), for the tree's result listing; runs
        never change, so each is read once."""
        cache = self.__dict__.setdefault("_catalogs", {})
        try:
            store = self._store()
            for r in runs:
                if r.run_id not in cache:
                    cache[r.run_id] = store.field_catalog(r.run_id)
        except PuzzleError:
            return {}
        return cache

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
            ("builder", "Material builder…", None, lambda: self.open_material_builder()),
            ("delete", "Delete", QKeySequence.StandardKey.Delete, self.delete_selected),
            ("reload", "Reload", QKeySequence.StandardKey.Refresh, self.reload),
            ("pick", "Pick", "P", self.toggle_picking),
            ("settings", "Settings…", QKeySequence.StandardKey.Preferences, self.open_settings),
        ):
            action = QAction(text, self)
            if key is not None:
                action.setShortcut(key)
            action.triggered.connect(slot)
            bar.addAction(action)
            menu.addAction(action)
            self.actions_by_name[name] = action
        # On macOS, Settings… moves to the application menu, where Mac users look for it.
        self.actions_by_name["settings"].setMenuRole(QAction.MenuRole.PreferencesRole)
        pick = self.actions_by_name["pick"]
        pick.setCheckable(True)
        pick.setToolTip("Double-click faces in the view to pick them; drag still rotates")

    def open_material_builder(self, material=None) -> None:
        """The material builder (ADR 0020); with a project material, to save it as yours."""
        dialog = self.material_builder()
        if material is not None:
            dialog.key = dialog.parent_key = None
            dialog.editor.load(material)
        dialog.exec()

    def material_builder(self):
        """The builder to show (tests replace this)."""
        from puzzle.gui.material_builder import MaterialBuilder

        return MaterialBuilder(self, self)

    def open_settings(self) -> None:
        """The Settings dialog (ADR 0024); what it changes shows at once."""
        if self.settings_dialog().exec():
            self.settings_changed()

    def settings_dialog(self):
        """The dialog to show (tests replace this)."""
        from puzzle.gui.settings_dialog import SettingsDialog

        return SettingsDialog(parent=self)

    def settings_changed(self) -> None:
        from puzzle.settings import reload

        reload()
        self.agent.agent = None  # the next message builds the agent with the new settings
        if self.session is not None:
            self.refresh()
        self.statusBar().showMessage("Settings saved", 5000)

    def new_project(self, root: Path, name: str, units: str | None = None) -> None:
        """Create an empty project in `root` and open it; `units` (a unit system) makes
        the project show those units to everyone who opens it (ADR 0030)."""
        from puzzle.model.schema import Project, ProjectUnits
        from puzzle.project import save_project

        root.mkdir(parents=True, exist_ok=True)
        project = Project(name=name)
        if units is not None:
            project.add(ProjectUnits(system=units))
        save_project(project, root)
        self.open(root)

    def _new_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Folder for the new project")
        if not path:
            return
        name = self.ask_text("New project", "Project name:", Path(path).name)
        if not name:
            return
        from puzzle.model.units import SYSTEMS
        from puzzle.settings import current

        mine = current().get("units.system")
        systems = [(label, key) for key, (label, _) in SYSTEMS.items()]
        systems.sort(key=lambda p: p[1] != mine)  # the user's own first
        units = self.choose("New project", "Units for this project:", systems)
        if units is not None:
            self.new_project(Path(path), name, units)

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
        if node is None or node.kind not in ("object", "derived"):
            return
        self.submit(Delete(object=node.object))

    def add_material(self, key: str) -> None:
        self.submit(AddLibraryMaterial(key=key))

    def _add_material_dialog(self) -> None:
        """The library grouped by type, group and alloy, with a search (MaterialPicker)."""
        key = self.pick_material()
        if key:
            self.add_material(key)

    def pick_material(self) -> str | None:
        """A library key from the picker, or None (tests replace this)."""
        from puzzle.gui.material_builder import MaterialPicker

        picker = MaterialPicker(self)
        return picker.key if picker.exec() else None

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
        if self.left_tabs.currentWidget() is self.workbench:
            self.show_workbench_part()
            return
        node = self.tree.current()
        s = self.session
        if s is None or node is None:
            return
        names = {str(o.id): o.name for o in s.project.objects.values()}
        if node.kind in ("run", "result", "result_group"):
            self._select_run(node.object, node.path if node.kind == "result" else None)
            return
        if node.kind in ("sweep", "compare"):
            runs = [r for r in self._store().runs() if r.study_id == node.object and r.point]
            self.properties.show_text(
                {"Points run": len({r.point for r in runs}), "Compare": "double-click Compare…"}
            )
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
        if isinstance(obj, ResultDefinition):
            self.show_derived(obj)
            return
        self.set_context("model")
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

    # --- the Geometry tab (ADR 0017) --------------------------------------------------

    def set_context(self, context: str) -> None:
        """What is on screen: "model", "geometry", "sketch" or "results" (ADR 0029).
        The results bar belongs to results, the ribbon to geometry and sketches."""
        self.context = context
        self.results_bar.setVisible(context == "results" and self.result is not None)
        self.workbench.ribbon.setVisible(context in ("geometry", "sketch"))

    def _tab_changed(self) -> None:
        on = self.left_tabs.currentWidget() is self.workbench
        if on:
            self.set_context("geometry")
            self.workbench.refresh()
        else:
            self.set_context("model")
        self._selected()

    def show_workbench_part(self) -> None:
        """The Geometry tab's part on its own (with a tool's pending step, if any)."""
        geo = self.workbench.geometry()
        if geo is None:
            self.viewport.clear()
            return
        if self.workbench.sketching is not None:
            return
        steps = self.pending[1] if self.pending and self.pending[0] == geo.id else None
        kinds = [s["kind"] for s in steps if not s.get("suppressed")] if steps else None
        if kinds is None:
            kinds = [s.kind for s in geo.history if not s.suppressed]
        if geo.history and all(k == "sketch" for k in kinds):  # nothing solid to show yet
            self.viewport.clear()
            self.statusBar().showMessage(
                f"{geo.name} has only sketches so far: extrude or revolve one", 8000
            )
            return
        if self.workbench.tool is None:
            from puzzle.gui.choices import choices

            project = self.session.project
            names = {str(o.id): o.name for o in project.objects.values()}
            self.properties.show_object(
                str(geo.id),
                geo.model_dump(mode="json"),
                "",
                names,
                lambda path: choices(project, geo, path),
            )
        comp = self._standalone(geo)
        mesh = self._preview(comp)
        if mesh is None:
            return
        cid = str(comp.id)
        self.mesh_component = cid
        self.viewport.boundaries = self.mesh_boundaries.get(cid, {})
        self.viewport.vertices = self.mesh_vertices.get(cid, {})
        self.viewport.show_mesh(mesh, None, [])

    def preview_history(self, geo_id, history) -> None:
        """Preview the part with `history` instead of its own (None: its own again)."""
        self.pending = (geo_id, history) if geo_id is not None else None
        self._selected()

    def show_tool(self, panel) -> None:
        """A tool's options in the Properties dock (None: the properties again)."""
        if panel is None:
            self.side.setCurrentWidget(self.properties)
            self.properties_dock.setWindowTitle("Properties")
            return
        self.side.addWidget(panel)
        self.side.setCurrentWidget(panel)
        self.properties_dock.setWindowTitle("Tool")

    def show_sketch(self, workspace) -> None:
        """The sketch canvas in the centre (None: the 3D view again)."""
        if workspace is None:
            old = self.centre.currentWidget()
            self.centre.setCurrentIndex(0)
            if old is not self.centre.widget(0):
                self.centre.removeWidget(old)
                old.deleteLater()
            if self.context == "sketch":
                self.set_context("geometry")
            return
        self.left_tabs.setCurrentWidget(self.workbench)
        self.set_context("sketch")
        self.centre.addWidget(workspace)
        self.centre.setCurrentWidget(workspace)

    def _select_run(self, run_id: str, key: str | None = None) -> None:
        """A run's results in the view; `key` picks one of them (from the model tree)."""
        from puzzle.gui.choices import solver_label

        store = self._store()
        record = store.get_run(run_id)
        self.properties.show_text(
            {
                "Study": record.study_name,
                "Solver": solver_label(record.solver),
                "Ran": "natively" if record.runtime == "native" else f"in {record.runtime}",
                "Started": f"{record.started_at:%Y-%m-%d %H:%M:%S}",
                "Duration (s)": round(record.duration_s, 2),
                "Result steps": record.steps,
            }
        )
        if getattr(self, "_result_run", None) != run_id or self.result is None:
            self.result = (store.read_mesh(record.mesh_id), store.read_results(run_id))
            self._result_run = run_id
        self.results_bar.blockSignals(True)
        self.results_bar.load(self.result[1], key)
        self.results_bar.blockSignals(False)
        self.set_context("results")
        self._redraw_result()

    def show_derived(self, definition, key: str | None = None) -> None:
        """A derived result (ADR 0025), evaluated from its runs, in the view."""
        from puzzle.post.derived import evaluate

        try:
            d = evaluate(self.session.project, self._store(), definition)
        except PuzzleError as exc:
            self.result = None
            self.set_context("model")
            self.viewport.clear()
            self.report(exc)
            return
        for w in d.warnings:
            self.log.appendPlainText(f"{definition.name}: {w}")
        if d.warnings:
            self.statusBar().showMessage(f"{definition.name}: {d.warnings[0]}", 10000)
        self.result = (d.mesh, d.results)
        self._result_run = f"derived:{definition.id}"
        self.results_bar.blockSignals(True)
        self.results_bar.load(d.results, key, items=d.items)
        self.results_bar.blockSignals(False)
        self.set_context("results")
        self._redraw_result()

    def new_derived(self, method: str = "linear", study=None, definition=None):
        """The combination or equation dialog; OK makes (or changes) the derived result
        in one command."""
        from puzzle.commands.model import Create, Edit, EditOp
        from puzzle.gui.derived_dialog import CombinationDialog, EquationDialog, FatigueDialog

        project, store = self.session.project, self._store()
        if method == "fatigue":
            dialog = FatigueDialog(project, store, self, definition=definition, study=study)
        elif method == "equation":
            dialog = EquationDialog(project, store, self, definition=definition, source=study)
        else:
            dialog = CombinationDialog(project, store, self, definition=definition, study=study)
        if not self.run_dialog(dialog):
            return None
        data = dialog.data()
        if definition is None:
            outcome = self.submit(Create(kind="result", data=data))
        else:
            current = definition.model_dump(mode="json")
            ops = [
                EditOp(op="set", path=k, value=v)
                for k, v in {
                    **{
                        k: None
                        for k in (
                            "quantity",
                            "source",
                            "expression",
                            "unit",
                            "source_point",
                            "fatigue",
                        )
                    },
                    **data,
                }.items()
                if current.get(k) != v
            ]
            outcome = self.submit(Edit(object=str(definition.id), ops=ops)) if ops else None
        if outcome is not None:
            name = data["name"]
            made = next(
                (o for o in self.session.project.of_type(ResultDefinition) if o.name == name), None
            )
            if made is not None:
                self.tree.select(Node("derived", str(made.id)))
        return outcome

    def _redraw_result(self) -> None:
        if self.result is None:
            return
        mesh, results = self.result
        item = self.results_bar.item()
        field = item.field if item is not None else None
        steps = [s for s in results.steps if field in s.fields]
        if not field or not steps:
            return
        index = min(self.results_bar.step.value(), len(steps)) - 1
        try:
            self.viewport.show_result(
                mesh,
                results,
                field,
                step_index=index,
                deform=self.results_bar.deform.isChecked(),
                item=item,
            )
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return
        shown = self.viewport.shown
        self.results_bar.title.setText(shown.get("title", ""))
        unit = f" {shown['unit']}" if shown.get("unit") else ""
        self.statusBar().showMessage(
            f"{shown['scalar']} ({shown['step']}): {shown['min']:.4g} … {shown['max']:.4g}{unit}"
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
        from puzzle.model.schema import ParameterSet

        params = {
            k: q.to_si()
            for ps in self.session.project.of_type(ParameterSet)
            for k, q in ps.values.items()
        }
        dialog = StepDialog(kind, args, name, features, self, params=params)
        if dialog.exec() and dialog.result_args is not None:
            return dialog.name.text().strip() or name, dialog.result_args
        return None

    def choose_reattachment(self, geometry: str, reports: list[dict], rules: dict):
        """The reattach dialog: choices for `naming.reattach_ops`, or None."""
        from puzzle.gui.reattach import ReattachDialog

        dialog = ReattachDialog(geometry, reports, rules, self)
        return dialog.choices() if dialog.exec() else None

    def compare_cases(self, study_id: str) -> list:
        """A swept study's points as Compare cases: the newest run of each point."""
        from puzzle.post.compare import Case
        from puzzle.sweep import title

        store = self._store()
        seen, cases = set(), []
        for r in reversed(store.runs()):  # newest first
            labels = r.point_labels()
            if r.study_id != study_id or not labels or title(labels) in seen:
                continue
            seen.add(title(labels))
            cases.append(
                Case(
                    title(labels),
                    store.read_mesh(r.mesh_id),
                    store.read_results(r.run_id),
                    r.run_id,
                )
            )
        # In the study's own order (its sweep's points), not by run time: points run
        # in the same second may come back in either order.
        from puzzle.sweep import labels

        study = self.session.project.objects.get(_uuid(study_id))
        order = []
        if isinstance(study, Study):
            order = [title(labels(self.session.project, p)) for p in study.sweep_points()]
        rank = {t: i for i, t in enumerate(order)}
        return sorted(cases[::-1], key=lambda c: rank.get(c.label, len(rank)))

    def open_compare(self, study_id: str):
        """The Compare window for a swept study (side by side, difference, summary)."""
        from puzzle.gui.compare import CompareWindow

        cases = self.compare_cases(study_id)
        if len(cases) < 2:
            self.statusBar().showMessage("Compare needs at least two runs of the study", 8000)
            return None
        study = self.session.project.objects.get(_uuid(study_id))
        window = CompareWindow(study.name if study else cases[0].label, cases, self)
        window.show()
        self._compare = window  # kept open beside the main window
        return window

    def run_dialog(self, dialog) -> bool:
        """Show a dialog and say whether it was accepted (tests replace this to fill it)."""
        return bool(dialog.exec())

    def ask_yes(self, title: str, text: str) -> bool:
        """A yes or no question (tests replace this)."""
        reply = QMessageBox.question(self, title, text)
        return reply == QMessageBox.StandardButton.Yes

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
        elif node is not None and node.kind == "compare":
            self.open_compare(node.object)

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
        if (
            node is None
            or not node.object
            or node.kind
            in (
                "run",
                "result",
                "result_group",
                "sweep",
                "compare",
            )
        ):
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
        if self.pending is not None:  # a tool's step, previewed before it is applied
            from puzzle.model.schema import HistoryStep

            geo_id, steps = self.pending
            geo = project.objects.get(geo_id)
            if geo is not None:
                history = [HistoryStep(**step) for step in steps]
                project.objects[geo_id] = geo.model_copy(update={"history": history})
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
            if self._previewing == (cid, key):  # not a preview already superseded
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
        pending = None
        if self.pending is not None and any(p.geometry == self.pending[0] for p in comp.parts):
            pending = [str(self.pending[0]), self.pending[1]]
        text = json.dumps([parts, shapes, params, pending], sort_keys=True, default=str)
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
