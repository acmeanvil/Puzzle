"""The Geometry tab (ADR 0017): one part's history and every tool to change it.

The part is chosen at the top (or made: New part starts with a sketch; Import reads a
CAD file). Its history is listed below: a tick turns a step on or off, a drag
reorders, a double-click edits. The tools come in groups (Sketch, Create, Modify,
Prepare, Selections). A tool's options open in the Properties dock (`ToolPanel`): the
view previews the part with the pending step as values change, and edges or faces
are picked in the view; Apply makes the step one command, Cancel leaves the project
as it was. A sketch is drawn in the main view (`SketchWorkspace`), on a plane or a
picked flat face; Finish makes it a step.

The preview never writes the project: the window regenerates a copy with the
pending history (`MainWindow.pending`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.choices import label
from puzzle.model.schema import Geometry, ParameterSet

GROUPS = [
    ("Create", ["box", "cylinder", "cone", "sphere", "extrude", "revolve", "hole"]),
    ("Modify", ["fillet", "chamfer", "move", "rotate", "pattern", "split"]),
    ("Prepare", ["heal", "remove", "fragment", "enclosure"]),
]
NAMES = {"remove": "Remove features", "heal": "Heal", "fragment": "Share faces"}
PLANES = [("XY plane", "xy"), ("YZ plane", "yz"), ("XZ plane", "xz")]
ON_FACE = "face"


def tool_name(kind: str) -> str:
    return NAMES.get(kind, label(kind))


class ToolPanel(QWidget):
    """A step's options in the Properties dock, previewed as they change."""

    def __init__(self, workbench, geo: Geometry, kind: str, index: int | None, args, name):
        super().__init__()
        from puzzle.gui import menus
        from puzzle.gui.step_dialog import StepForm

        self.workbench, self.geo_id, self.kind, self.index = workbench, geo.id, kind, index
        before = index if index is not None else len(geo.history)
        self.form = StepForm(
            kind,
            args,
            name or _free_name(geo, tool_name(kind)),
            menus._tool_steps(geo, before),
            workbench.params(),
            menus.sketch_steps(geo, before),
            pick=self.pick,
        )
        title = QLabel(f"{'Edit' if index is not None else 'New'} {tool_name(kind).lower()}")
        title.setStyleSheet("font-weight: bold; font-size: 14px")
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c53030")
        apply, cancel = QPushButton("Apply"), QPushButton("Cancel")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        cancel.clicked.connect(self.cancel)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(apply)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.form)
        layout.addWidget(self.error)
        layout.addStretch()
        layout.addLayout(row)
        self.picking = None  # the RuleField being picked for
        self.timer = QTimer(self, singleShot=True, interval=350)
        self.timer.timeout.connect(self.preview)
        self.form.changed.connect(self.timer.start)
        self.preview()

    @property
    def window(self):
        return self.workbench.window

    def geometry(self) -> Geometry | None:
        return self.window.session.project.objects.get(self.geo_id)

    def history(self, with_step: bool = True) -> list[dict]:
        """The part's history with this step in it (or up to it, for picking)."""
        geo = self.geometry()
        steps = [s.model_dump(mode="json") for s in geo.history]
        at = self.index if self.index is not None else len(steps)
        if not with_step:
            return steps[:at]
        step = {"kind": self.kind, "name": self.form.name.text().strip(), "args": self.args()}
        if self.index is not None:
            steps[self.index] = {**steps[self.index], **step}
        else:
            steps.append(step)
        return steps

    def args(self) -> dict:
        return self.form.values()

    def preview(self) -> None:
        if self.picking is not None:
            return
        try:
            history = self.history()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.error.setText("")
        self.window.preview_history(self.geo_id, history)

    def pick(self, field) -> None:
        """First press: show the part before this step and pick on it. Second press:
        take the picks."""
        w = self.window
        if self.picking is None:
            self.picking = field
            w.preview_history(self.geo_id, self.history(with_step=False))
            w.viewport.set_pick_kind(field.entity)
            index = w.pick_bar.filter.findData(field.entity)
            w.pick_bar.filter.setCurrentIndex(index)
            w.toggle_picking(True)
            field.pick_button.setText(f"Use the picked {field.entity}s")
            w.statusBar().showMessage(
                f"Double-click the {field.entity}s, then press 'Use the picked {field.entity}s'",
                10000,
            )
            return
        names = [n for _, n in w.picked_names()]
        self.picking = None
        field.pick_button.setText("Pick in view")
        w.viewport.clear_picks()
        w.toggle_picking(False)
        field.set_picked(names)
        self.preview()

    def apply(self) -> None:
        from puzzle.gui import menus

        try:
            args, name = self.args(), self.form.name.text().strip()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        geo = self.geometry()
        out = menus.apply_step(self.window, geo, self.kind, name, args, index=self.index)
        if out is not None:
            self.workbench.close_tool()

    def cancel(self) -> None:
        self.workbench.close_tool()


class Workbench(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.geo_id = None
        self.tool: ToolPanel | None = None
        self.sketching: dict | None = None  # geometry, step index, plane, face of a sketch

        self.part = QComboBox()
        self.part.currentIndexChanged.connect(lambda _: self._part_chosen())
        new, imp = QPushButton("New part…"), QPushButton("Import…")
        new.clicked.connect(self.new_part)
        imp.clicked.connect(self._import)
        top = QHBoxLayout()
        top.addWidget(self.part, 1)
        top.addWidget(new)
        top.addWidget(imp)

        self.steps = QListWidget()
        self.steps.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.steps.model().rowsMoved.connect(self._moved)
        self.steps.itemChanged.connect(self._ticked)
        self.steps.itemDoubleClicked.connect(lambda item: self.edit(self.steps.row(item)))
        self.steps.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.steps.customContextMenuRequested.connect(self._step_menu)

        # The tools as a ribbon above the view (shown while this tab is open): groups
        # side by side, two rows of buttons each.
        self.ribbon = QWidget()
        bar = QHBoxLayout(self.ribbon)
        bar.setContentsMargins(4, 2, 4, 2)
        self.ribbon.setStyleSheet(
            "QGroupBox { font-weight: bold; margin-top: 2px; padding-top: 14px; }"
            "QToolButton { padding: 2px 8px; }"
        )
        self.tool_buttons: dict[str, QToolButton] = {}

        def button(text: str, tip: str = "") -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            return b

        def group(title: str, buttons: list[QToolButton]) -> None:
            box = QGroupBox(title)
            g = QGridLayout(box)
            g.setContentsMargins(4, 2, 4, 2)
            g.setSpacing(2)
            per_row = max(1, (len(buttons) + 1) // 2)
            for n, b in enumerate(buttons):
                g.addWidget(b, n // per_row, n % per_row)
            bar.addWidget(box)

        self.new_sketch_button = button(
            "New sketch…", "Draw a profile on a plane or on a picked flat face"
        )
        self.new_sketch_button.clicked.connect(lambda: self.new_sketch())
        group("Sketch", [self.new_sketch_button])
        for title, kinds in GROUPS:
            buttons = []
            for kind in kinds:
                b = button(tool_name(kind))
                b.clicked.connect(lambda _=False, k=kind: self.start_tool(k))
                self.tool_buttons[kind] = b
                buttons.append(b)
            if title == "Prepare":
                check = button("Check", "Gaps, slivers, short edges, overlapping bodies")
                check.clicked.connect(self._check)
                buttons.append(check)
            group(title, buttons)
        from_picks = button(
            "New from picks…", "Turn on Pick, double-click in the view, then make a selection"
        )
        reattach = button("Reattach…", "Review selections an edit or a new file detached")
        from_picks.clicked.connect(self._from_picks)
        reattach.clicked.connect(self._reattach)
        group("Selections", [from_picks, reattach])
        bar.addStretch()

        normal = QWidget()
        column = QVBoxLayout(normal)
        column.setContentsMargins(4, 4, 4, 4)
        column.addLayout(top)
        tip = QLabel("The tools are in the ribbon above the view.")
        tip.setStyleSheet("color: gray")
        column.addWidget(tip)
        history = QLabel("History: tick turns a step on or off, drag reorders, double-click edits")
        history.setWordWrap(True)
        history.setStyleSheet("color: gray")
        column.addWidget(history)
        column.addWidget(self.steps, 2)

        sketch_page = QWidget()
        sk = QVBoxLayout(sketch_page)
        self.sketch_title = QLabel("")
        self.sketch_title.setStyleSheet("font-weight: bold; font-size: 14px")
        self.sketch_title.setWordWrap(True)
        hint = QLabel(
            "Draw in the main view: pick a tool above the canvas, click to place points, "
            "then constrain and dimension. Edge names become face names on the part."
        )
        hint.setWordWrap(True)
        finish, cancel = QPushButton("Finish sketch"), QPushButton("Cancel")
        finish.setDefault(True)
        finish.clicked.connect(self.finish_sketch)
        cancel.clicked.connect(self.cancel_sketch)
        sk.addWidget(self.sketch_title)
        sk.addWidget(hint)
        sk.addWidget(finish)
        sk.addWidget(cancel)
        sk.addStretch()

        self.pages = QStackedWidget()
        self.pages.addWidget(normal)
        self.pages.addWidget(sketch_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)

    # --- state ------------------------------------------------------------------------

    def params(self) -> dict[str, float]:
        project = self.window.session.project
        return {k: q.to_si() for ps in project.of_type(ParameterSet) for k, q in ps.values.items()}

    def geometry(self) -> Geometry | None:
        s = self.window.session
        if s is None or self.geo_id is None:
            return None
        obj = s.project.objects.get(self.geo_id)
        return obj if isinstance(obj, Geometry) else None

    def refresh(self) -> None:
        """The parts and the chosen part's history, from the project."""
        s = self.window.session
        if s is None:
            return
        geos = sorted(s.project.of_type(Geometry), key=lambda g: g.name)
        self.part.blockSignals(True)
        self.part.clear()
        for g in geos:
            self.part.addItem(g.name, str(g.id))  # Qt matches text ids, not UUID objects
        if self.geo_id is None and geos:
            self.geo_id = geos[0].id
        index = self.part.findData(str(self.geo_id))
        self.part.setCurrentIndex(index)
        if index < 0:
            self.geo_id = None
        self.part.blockSignals(False)
        self._list_steps()
        enabled = self.geometry() is not None and self.sketching is None
        for b in self.tool_buttons.values():
            b.setEnabled(enabled)

    def _list_steps(self) -> None:
        self.steps.blockSignals(True)
        self.steps.clear()
        geo = self.geometry()
        for i, step in enumerate(geo.history if geo else []):
            item = QListWidgetItem(f"{i + 1}. {step_text(step)}")
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled
            )
            on = Qt.CheckState.Unchecked if step.suppressed else Qt.CheckState.Checked
            item.setCheckState(on)
            self.steps.addItem(item)
        self.steps.blockSignals(False)

    def show_part(self, geo_id) -> None:
        self.geo_id = geo_id
        self.refresh()
        self.window.show_workbench_part()

    def _part_chosen(self) -> None:
        from uuid import UUID

        data = self.part.currentData()
        self.show_part(UUID(data) if data else None)

    # --- history ----------------------------------------------------------------------

    def _ticked(self, item) -> None:
        from puzzle.gui import menus

        geo, i = self.geometry(), self.steps.row(item)
        if (
            geo is not None
            and (item.checkState() == Qt.CheckState.Unchecked) != geo.history[i].suppressed
        ):
            menus.toggle_step(self.window, geo, i)

    def _moved(self, _parent, start, _end, _dest, row) -> None:
        from puzzle.gui import menus

        geo = self.geometry()
        if geo is None:
            return
        target = row - 1 if row > start else row
        if target != start:
            menus.move_step(self.window, geo, start, target - start)

    def _step_menu(self, pos) -> None:
        from puzzle.gui import menus

        geo, i = self.geometry(), self.steps.currentRow()
        if geo is None or i < 0:
            return
        menu = QMenu(self)
        menu.addAction("Edit…").triggered.connect(lambda: self.edit(i))
        step = geo.history[i]
        toggle = "Turn on" if step.suppressed else "Turn off"
        menu.addAction(toggle).triggered.connect(lambda: menus.toggle_step(self.window, geo, i))
        if i > 0:
            menu.addAction("Move up").triggered.connect(
                lambda: menus.move_step(self.window, geo, i, -1)
            )
        if i < len(geo.history) - 1:
            menu.addAction("Move down").triggered.connect(
                lambda: menus.move_step(self.window, geo, i, +1)
            )
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(
            lambda: menus.remove(self.window, geo, f"history.{i}")
        )
        menu.exec(self.steps.viewport().mapToGlobal(pos))

    def edit(self, index: int) -> None:
        geo = self.geometry()
        if geo is None or index < 0:
            return
        step = geo.history[index]
        if step.kind == "sketch":
            self.edit_sketch(geo, index)
        else:
            self.start_tool(step.kind, index)

    # --- tools ------------------------------------------------------------------------

    def start_tool(self, kind: str, index: int | None = None) -> ToolPanel | None:
        geo = self.geometry()
        if geo is None:
            self.window.statusBar().showMessage("Choose or make a part first", 6000)
            return None
        self.close_tool(refresh=False)
        args, name = ({}, "")
        if index is not None:
            args, name = dict(geo.history[index].args), geo.history[index].name
        self.tool = ToolPanel(self, geo, kind, index, args, name)
        self.window.show_tool(self.tool)
        return self.tool

    def close_tool(self, refresh: bool = True) -> None:
        if self.tool is not None:
            self.tool.deleteLater()
            self.tool = None
            self.window.show_tool(None)
            self.window.preview_history(None, None)
        if refresh:
            self.refresh()

    def _check(self) -> None:
        from puzzle.gui import menus

        if self.geometry() is not None:
            menus.check_geometry_of(self.window, self.geometry())

    def _reattach(self) -> None:
        from puzzle.gui import menus

        if self.geometry() is not None:
            menus.reattach_selections(self.window, self.geometry())

    def _from_picks(self) -> None:
        w = self.window
        if not w.picked_names():
            w.toggle_picking(True)
            w.statusBar().showMessage(
                "Double-click in the view to pick, then press New from picks… again", 10000
            )
            return
        w.pick_bar._new()

    # --- parts ------------------------------------------------------------------------

    def new_part(self) -> None:
        """A new part starts with a sketch; finishing it offers the extrusion."""
        w = self.window
        n = len(w.session.project.of_type(Geometry)) + 1
        name = w.ask_text("New part", "Name:", f"Part {n}")
        if not name:
            return
        plane = w.choose("New part", "Sketch its first profile on:", PLANES)
        if plane is None:
            return
        self._start_sketch({"new_part": name, "plane": plane, "face": None, "index": None})

    def _import(self) -> None:
        from puzzle.gui import menus

        before = {g.id for g in self.window.session.project.of_type(Geometry)}
        if menus.import_step_file(self.window) is not None:
            new = [g for g in self.window.session.project.of_type(Geometry) if g.id not in before]
            if new:
                self.show_part(new[0].id)

    # --- sketches ---------------------------------------------------------------------

    def new_sketch(self, plane: str | None = None, face: str | None = None) -> None:
        """A sketch on a plane, or on the flat face picked in the view."""
        w = self.window
        geo = self.geometry()
        if geo is None:
            self.new_part()
            return
        if plane is None and face is None:
            picked = [n for e, (_, n) in self._picked_faces()]
            options = list(PLANES)
            if len(picked) == 1:
                options.insert(0, (f"The picked face ({picked[0]})", ON_FACE))
            choice = w.choose("New sketch", "Sketch on:", options)
            if choice is None:
                return
            if choice == ON_FACE:
                face = picked[0]
            else:
                plane = choice
        self._start_sketch({"geo": geo.id, "plane": plane or "xy", "face": face, "index": None})

    def _picked_faces(self):
        w = self.window
        names = w.mesh_names.get(w.mesh_component or "", {})
        return [(e, names[e]) for e in w.viewport.picked if e[0] == 2 and e in names]

    def edit_sketch(self, geo: Geometry, index: int) -> None:
        self.geo_id = geo.id
        a = geo.history[index].args
        self._start_sketch(
            {
                "geo": geo.id,
                "plane": a.get("plane", "xy"),
                "face": a.get("face"),
                "index": index,
                "offset": a.get("offset", 0.0),
                "spec": a.get("sketch"),
                "name": geo.history[index].name,
            }
        )

    def _start_sketch(self, state: dict) -> None:
        from puzzle.gui.sketcher import SketchWorkspace

        self.close_tool(refresh=False)
        self.sketching = state
        where = (
            f"on {state['face']}" if state.get("face") else f"on the {state['plane'].upper()} plane"
        )
        what = state.get("name") or "a new sketch"
        self.sketch_title.setText(f"Sketching {what} {where}")
        self.workspace = SketchWorkspace(state.get("spec"), self.params())
        self.window.show_sketch(self.workspace)
        self.pages.setCurrentIndex(1)
        self.refresh()

    def finish_sketch(self) -> None:
        """The sketch as a step (one command); a new part's first sketch makes the part,
        and a part with no solid yet goes on to the extrusion."""
        from puzzle.commands.model import Create
        from puzzle.gui import menus

        state, w = self.sketching, self.window
        if state is None or self.workspace.problem():
            return
        args = {"sketch": self.workspace.sketch(), "plane": state["plane"]}
        if state.get("face"):
            args = {"sketch": args["sketch"], "face": state["face"]}
        if state.get("offset"):
            args["offset"] = state["offset"]
        if "new_part" in state:
            data = {
                "name": state["new_part"],
                "history": [{"kind": "sketch", "name": "Sketch 1", "args": args}],
                "selections": [{"name": "Body", "entity": "body", "members": ["Body"]}],
            }
            out = w.submit(Create(kind="geometry", data=data))
            if out is None:
                return
            geo = next(g for g in w.session.project.of_type(Geometry) if g.name == data["name"])
        else:
            geo = w.session.project.objects.get(state["geo"])
            index = state.get("index")
            name = state.get("name") or _free_name(geo, "Sketch")
            if menus.apply_step(w, geo, "sketch", name, args, index=index) is None:
                return
            geo = w.session.project.objects.get(geo.id)
        self._end_sketch()
        self.geo_id = geo.id
        self.refresh()
        w.show_workbench_part()
        has_solid = any(s.kind not in ("sketch",) and not s.suppressed for s in geo.history)
        if not has_solid:
            self.start_tool("extrude")

    def cancel_sketch(self) -> None:
        self._end_sketch()
        self.refresh()
        self.window.show_workbench_part()

    def _end_sketch(self) -> None:
        self.sketching = None
        self.pages.setCurrentIndex(0)
        self.window.show_sketch(None)


def step_text(step) -> str:
    """A step as the history lists it: its name, with its kind when the name doesn't
    say it ("Sketch 1", "Extrude 1", but "Hole Bore")."""
    kind = tool_name(step.kind) if step.kind in NAMES else label(step.kind)
    return step.name if step.name.lower().startswith(kind.lower()) else f"{kind} {step.name}"


def _free_name(geo: Geometry, base: str) -> str:
    taken = {s.name for s in geo.history}
    i = 1
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"
