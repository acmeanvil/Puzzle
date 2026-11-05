"""The Contact tool (ADR 0056): a docked panel with every contact, in tabs.

Setup: one row per contact of every component: name, the two sides, type, friction,
meshing, stiffness and search distance, and what the chosen solver is given for it,
in the solver's own words (`Adapter.contact_summary`, from the code that writes its
input). Cells edit as commands; an edit made with several rows selected applies to
each of them, as one batch (one undo).

Results: for a run and step, each contact's normal and frictional force, its area in
contact and sticking, its largest pressure, penetration, sliding and gap
(`post.contact`); buttons show the contact fields in the view.

Initial state: Check works each contact's state out from the mesh (meshing first if
there is none): its status, the share of the source face in touch and overhanging,
the largest gap and penetration, the nearest distance; drawn on the source faces in
the view (closed green, near yellow, open orange, far red, overhanging purple), with
the checks' warnings, each naming the contact and the fix.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role

ROLE = Qt.ItemDataRole.UserRole
STATUS_WORDS = {"closed": "Closed", "near": "Near", "open": "Open", "far": "Far",
                "overhanging": "Overhanging"}  # fmt: skip


@dataclass(frozen=True)
class Column:
    title: str
    field: str | None  # the contact's field it edits; None: shown only
    kind: str = "text"  # text, choice, number, length


COLUMNS = [
    Column("Name", "name"),
    Column("Component", None),
    Column("Source (slave)", "source", "choice"),
    Column("Target (master)", "target", "choice"),
    Column("Type", "type", "choice"),
    Column("Friction", "friction", "number"),
    Column("Meshing", "meshing", "choice"),
    Column("Stiffness", "stiffness", "choice"),
    Column("Search distance", "search_distance", "length"),
    Column("Interface", "interface", "choice"),
    Column("Offset", "offset", "length"),
    Column("Solver is given", None),
]


def contact_rows(project) -> list[tuple]:
    """(component, index, contact) for every contact, components by name."""
    from puzzle.model.schema import Component

    return [(comp, i, c) for comp in sorted(project.of_type(Component), key=lambda c: c.name)
            for i, c in enumerate(comp.contacts)]  # fmt: skip


def status_colours() -> dict[str, str]:
    from puzzle.gui.theme import current

    return dict(zip(STATUS_WORDS, current().contact, strict=True))


def _length(metres) -> str:
    from puzzle.contact_check import shown_length

    return "—" if metres is None else shown_length(abs(metres) if metres == 0 else metres)


class _Delegate(QStyledItemDelegate):
    def __init__(self, tab) -> None:
        super().__init__(tab.table)
        self.tab = tab

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt's name
        column = COLUMNS[index.column()]
        if column.kind != "choice":
            return super().createEditor(parent, option, index)
        box = QComboBox(parent)
        for text, value in self.tab.choices(index.row(), column):
            box.addItem(text, value)
        return box

    def setEditorData(self, editor, index):  # noqa: N802
        if isinstance(editor, QComboBox):
            found = editor.findData(self.tab.stored(index.row(), COLUMNS[index.column()]))
            editor.setCurrentIndex(max(found, 0))
            editor.showPopup()
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # noqa: N802
        value = editor.currentData() if isinstance(editor, QComboBox) else editor.text()
        self.tab.edit(index.row(), COLUMNS[index.column()], value)


class SetupTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.rows: list[tuple] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Solver"))
        self.solver = QComboBox()
        self.solver.currentIndexChanged.connect(lambda _i: self.refresh())
        bar.addWidget(self.solver)
        bar.addStretch(1)
        self.count = role(QLabel(""), "muted")
        bar.addWidget(self.count)
        layout.addLayout(bar)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c.title for c in COLUMNS])
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)  # fmt: skip
        self.table.setItemDelegate(_Delegate(self))
        self.table.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.table, 1)
        self.empty = role(QLabel("No contacts yet: a component's Find contacts…, or Model → "
                                 "Add contact…"), "muted")  # fmt: skip
        layout.addWidget(self.empty)

    def _solvers(self, project) -> None:
        from puzzle.contact_check import solvers_of
        from puzzle.gui.choices import solver_label

        used = [s for comp, _i, _c in self.rows for s in solvers_of(project, comp)]
        wanted = list(dict.fromkeys([*used, "calculix", "code_aster"]))
        have = [self.solver.itemData(i) for i in range(self.solver.count())]
        if have == wanted:
            return
        current = self.solver.currentData()
        self.solver.blockSignals(True)
        self.solver.clear()
        for name in wanted:
            self.solver.addItem(solver_label(name), name)
        self.solver.setCurrentIndex(max(self.solver.findData(current), 0))
        self.solver.blockSignals(False)

    def refresh(self) -> None:
        from puzzle.adapters import available_adapters

        session = self.window.session
        project = session.project if session is not None else None
        self.rows = contact_rows(project) if project else []
        if project:
            self._solvers(project)
        solver = self.solver.currentData()
        given: dict[str, dict] = {}
        adapters = available_adapters()
        meshes = getattr(self.window, "meshes", {})
        for comp in {id(c): c for c, _i, _x in self.rows}.values():
            try:
                adapter = adapters[solver]()
                given[str(comp.id)] = adapter.contact_summary(project, comp,
                                                              meshes.get(str(comp.id)))  # fmt: skip
            except Exception:  # noqa: BLE001 - nothing to say for this solver
                given[str(comp.id)] = {}
        self.table.setRowCount(len(self.rows))
        for r, (comp, i, contact) in enumerate(self.rows):
            for k, column in enumerate(COLUMNS):
                item = QTableWidgetItem(self._text(comp, contact, column, given))
                item.setData(ROLE, (str(comp.id), i))
                if column.field is None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column.title == "Solver is given":
                    item.setToolTip(item.text())
                self.table.setItem(r, k, item)
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()
        self.empty.setVisible(not self.rows)
        self.table.setVisible(bool(self.rows))
        self.count.setText(f"{len(self.rows)} contact{'s' * (len(self.rows) != 1)}")

    def _text(self, comp, contact, column: Column, given: dict) -> str:
        from puzzle.gui.choices import label

        if column.title == "Component":
            return comp.name
        if column.title == "Solver is given":
            return given.get(str(comp.id), {}).get(contact.name, "Not described")
        if column.field == "meshing":
            return label(contact.effective_meshing)
        value = getattr(contact, column.field)
        if column.field == "search_distance":
            return _length(value.to_si()) if value is not None else "Automatic (1 % of model)"
        if column.field == "offset":
            return _length(value.to_si()) if value is not None else "—"
        if column.field == "friction":
            return "—" if value is None else f"{value:g}"
        if column.kind == "choice" and column.field not in ("source", "target"):
            return label(value)
        return str(value)

    def row_at(self, r: int):
        item = self.table.item(r, 0)
        if item is None:
            return None
        cid, index = item.data(ROLE)
        return next(((c, i, x) for c, i, x in self.rows if str(c.id) == cid and i == index), None)

    def stored(self, r: int, column: Column):
        row = self.row_at(r)
        return getattr(row[2], column.field) if row and column.field else None

    def choices(self, r: int, column: Column) -> list[tuple[str, object]]:
        from puzzle.gui.choices import choices

        row = self.row_at(r)
        if row is None:
            return []
        return choices(self.window.session.project, row[0],
                       f"contacts.{row[1]}.{column.field}") or []  # fmt: skip

    def edit(self, r: int, column: Column, value) -> None:
        """Set the column on this row, or on every selected row when it is one of them."""
        from puzzle.commands.model import Edit

        rows = {i.row() for i in self.table.selectedIndexes()}
        targets = sorted(rows) if r in rows and len(rows) > 1 else [r]
        by_component: dict[str, list] = {}
        try:
            for t in targets:
                row = self.row_at(t)
                if row is not None:
                    by_component.setdefault(str(row[0].id), []).extend(
                        ops(row[2], row[1], column.field, column.kind, value)
                    )
        except ValueError as exc:
            self.window.statusBar().showMessage(str(exc), 8000)
            return
        commands = [Edit(object=cid, ops=o) for cid, o in by_component.items()]
        if len(commands) == 1:
            self.window.submit(commands[0])
        elif commands:
            self.window.submit_batch(commands)
        self.refresh()

    def _selected(self) -> None:
        from puzzle.gui.tree import Node

        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) == 1 and (row := self.row_at(next(iter(rows)))) is not None:
            comp, i, contact = row
            self.window.tree.select(Node("contact", str(comp.id), f"contacts.{i}",
                                         contact.name))  # fmt: skip


def ops(contact, index: int, field: str, kind: str, value) -> list:
    """The edits that set `field` on contact `index`, keeping it valid: a frictional
    contact gets a coefficient (0.2) and loses it when it changes type; only a bond can
    stay meshed conformally; an offset interface gets an offset (0) and loses it after;
    bonds keep the geometry as it is."""
    from puzzle.commands.model import EditOp
    from puzzle.model.units import known, plain
    from puzzle.settings import current

    path = f"contacts.{index}."
    text = str(value).strip() if value is not None else ""
    if kind == "number":
        try:
            value = float(text) if text else None
        except ValueError:
            raise ValueError(f"'{text}' is not a friction coefficient") from None
        if contact.type != "frictional" and value is not None:
            raise ValueError("Only a frictional contact takes a friction coefficient")
        if contact.type == "frictional" and value is None:
            raise ValueError("A frictional contact needs a friction coefficient")
    elif kind == "length":
        if field == "offset" and contact.interface != "offset":
            raise ValueError("Set the interface to Offset first")
        if not text or text.lower().startswith("auto") or text == "—":
            if field == "offset":
                raise ValueError("An offset contact needs its offset (− for an interference)")
            value = None
        else:
            number, _, unit = text.partition(" ")
            unit = plain(unit.strip() or current().display_units()["length"])
            try:
                value = {"value": float(number), "unit": unit}
            except ValueError:
                raise ValueError(f"'{text}' is not a length") from None
            if not known(unit):
                raise ValueError(f"'{text}': unknown unit")
    elif field == "name" and not text:
        raise ValueError("A contact needs a name")
    out = [EditOp(op="set", path=path + field, value=value)]
    if field == "interface":
        if value == "offset" and contact.offset is None:
            out.append(EditOp(op="set", path=path + "offset", value={"value": 0.0, "unit": "mm"}))
        if value != "offset" and contact.offset is not None:
            out.insert(0, EditOp(op="set", path=path + "offset", value=None))
    if field == "type":
        if value == "frictional" and contact.friction is None:
            out.append(EditOp(op="set", path=path + "friction", value=0.2))
        if value != "frictional" and contact.friction is not None:
            out.insert(0, EditOp(op="set", path=path + "friction", value=None))
        if value != "bonded" and contact.meshing == "conformal":
            out.insert(0, EditOp(op="set", path=path + "meshing", value=None))
        if value in ("bonded", "no_separation") and contact.interface != "as_is":
            out.insert(0, EditOp(op="set", path=path + "interface", value="as_is"))
            if contact.offset is not None:
                out.insert(0, EditOp(op="set", path=path + "offset", value=None))
    return out


INITIAL = ["Contact", "Component", "Status", "In touch", "Overhanging", "Largest gap",
           "Largest penetration", "Nearest", "Search distance"]  # fmt: skip


class InitialTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.found: dict[str, list] = {}  # component id -> its ContactStates
        self.warnings: dict[str, list] = {}  # component id -> the checks' Problems
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Work each contact's state out from the mesh (meshing "
                                     "first if needed)")  # fmt: skip
        self.check_button.clicked.connect(lambda: self.check())
        self.show_button = QPushButton("Show in view")
        self.show_button.clicked.connect(lambda: self.show_in_view())
        bar.addWidget(self.check_button)
        bar.addWidget(self.show_button)
        bar.addStretch(1)
        self.legend = QHBoxLayout()
        for status, word in STATUS_WORDS.items():
            swatch = QLabel("■ " + word)
            swatch.setObjectName(f"swatch-{status}")
            self.legend.addWidget(swatch)
        bar.addLayout(self.legend)
        layout.addLayout(bar)
        self.note = role(QLabel("Check works out where each contact starts: touching, "
                                "apart, or running past its target's edge."), "muted")  # fmt: skip
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(INITIAL))
        self.table.setHorizontalHeaderLabels(INITIAL)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        split.addWidget(self.table)
        self.messages = QListWidget()
        self.messages.setWordWrap(True)
        split.addWidget(self.messages)
        layout.addWidget(split, 1)
        self._colour_legend()

    def _colour_legend(self) -> None:
        for status, colour in status_colours().items():
            swatch = self.findChild(QLabel, f"swatch-{status}")
            if swatch is not None:
                swatch.setStyleSheet(f"color: {colour}")

    def components(self) -> list:
        from puzzle.model.schema import Component

        project = self.window.session.project
        return [c for c in sorted(project.of_type(Component), key=lambda c: c.name)
                if c.contacts]  # fmt: skip

    def check(self, comps=None) -> None:
        """Each component's contacts on its mesh; a component not meshed yet is meshed."""
        comps = list(comps if comps is not None else self.components())
        if not comps:
            self.note.setText("No contacts to check.")
            return
        comp, rest = comps[0], comps[1:]
        mesh = self.window.meshes.get(str(comp.id))
        if mesh is None:
            self.note.setText(f"Meshing {comp.name} first…")
            self.window.start_meshing(comp, then=lambda _m: self.check(comps))
            return
        import copy

        from puzzle import contact_check
        from puzzle.contact_check import solvers_of

        project = copy.deepcopy(self.window.session.project)
        solvers = solvers_of(project, comp) or [None]

        def work(_emit):
            found = contact_check.states(comp, mesh)
            warnings = []
            for s in solvers:
                for w in contact_check.checks(project, comp, mesh, s, found):
                    if w.to_dict() not in [x.to_dict() for x in warnings]:
                        warnings.append(w)
            return found, warnings

        def done(result) -> None:
            self.found[str(comp.id)], self.warnings[str(comp.id)] = result
            self.refresh()
            self.window.refresh_problems()
            if rest:
                self.check(rest)
            else:
                self.show_in_view()

        self.note.setText(f"Checking {comp.name}…")
        from puzzle.gui.feedback import busy

        busy(self.table, True, f"Checking {comp.name}…")
        self.window.background(
            f"Checking the contacts of {comp.name}…",
            lambda emit: work(emit),
            lambda result: (busy(self.table, False), done(result)),
            failed=lambda _exc: busy(self.table, False),
            lock=False,
        )

    def refresh(self) -> None:
        names = {str(c.id): c.name for c in self.components()} if self.window.session else {}
        rows = [(cid, s) for cid, found in self.found.items() if cid in names for s in found]
        self.table.setRowCount(len(rows))
        colours = status_colours()
        for r, (cid, s) in enumerate(rows):
            share = s.shares or {}
            cells = [
                s.name, names[cid], STATUS_WORDS.get(s.status, s.note or "Not checked"),
                f"{share.get('closed', 0):.0%}", f"{share.get('overhanging', 0):.0%}",
                _length(s.largest_gap), _length(s.largest_penetration), _length(s.nearest),
                _length(s.search_distance),
            ]  # fmt: skip
            for k, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(ROLE, (cid, s.name))
                if k == 2 and s.status in colours:
                    item.setForeground(QColor(colours[s.status]))
                if k == 2 and s.note:
                    item.setToolTip(s.note)
                self.table.setItem(r, k, item)
        self.table.resizeColumnsToContents()
        self.messages.clear()
        for cid, found in self.warnings.items():
            if cid in names:
                for w in found:
                    self.messages.addItem(w.message)
        if rows:
            n = sum(len(v) for k, v in self.warnings.items() if k in names)
            self.note.setText(
                f"{len(rows)} contact{'s' * (len(rows) != 1)} checked; "
                + (f"{n} warning{'s' * (n != 1)}." if n else "no warnings.")
            )

    def show_in_view(self) -> None:
        """The checked contacts' source faces coloured by status, over the mesh."""
        comps = [c for c in self.components() if str(c.id) in self.found]
        if not comps:
            return
        comp = comps[0]
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) == 1:
            cid, _name = self.table.item(next(iter(rows)), 0).data(ROLE)
            comp = next((c for c in comps if str(c.id) == cid), comp)
        mesh = self.window.meshes.get(str(comp.id))
        if mesh is not None:
            self.window.viewport.show_contact_state(mesh, self.found[str(comp.id)],
                                                    status_colours())  # fmt: skip


RESULTS = ["Contact", "Normal force", "Frictional force", "In contact", "Sticking",
           "Largest pressure", "Largest penetration", "Largest sliding", "Largest gap"]  # fmt: skip
SHOWN = [("Pressure", "contact_pressure"), ("Status", "contact_status"),
         ("Sliding", "contact_sliding"), ("Gap", "contact_gap"),
         ("Frictional stress", "contact_shear")]  # fmt: skip


class ResultsTab(QWidget):
    """For a run and step: each contact's forces, area in contact and largest values;
    the contact fields shown in the view by a button each."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.found: list = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.run = QComboBox()
        self.run.currentIndexChanged.connect(lambda _i: self._steps())
        self.step = QComboBox()
        self.step.currentIndexChanged.connect(lambda _i: self.show_results())
        bar.addWidget(QLabel("Run"))
        bar.addWidget(self.run, 1)
        bar.addWidget(QLabel("Step"))
        bar.addWidget(self.step)
        layout.addLayout(bar)
        shown = QHBoxLayout()
        shown.addWidget(QLabel("Show"))
        self.buttons = {}
        for text, field in SHOWN:
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, f=field: self.show_field(f))
            shown.addWidget(button)
            self.buttons[field] = button
        shown.addStretch(1)
        layout.addLayout(shown)
        self.note = role(QLabel(""), "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.table = QTableWidget(0, len(RESULTS))
        self.table.setHorizontalHeaderLabels(RESULTS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        """The runs that have contact results, newest first, keeping the one chosen."""
        if self.window.session is None:
            return
        chosen = self.run.currentData()
        self.run.blockSignals(True)
        self.run.clear()
        try:
            store = self.window._store()
            runs = [r for r in store.runs() if r.status == "completed"]
        except Exception:  # noqa: BLE001 - no results yet
            runs = []
        from puzzle.gui.choices import solver_label

        for r in sorted(runs, key=lambda r: r.recorded_at, reverse=True):
            try:
                names = {f for f, _loc, _c in store.field_catalog(r.run_id)}
            except Exception:  # noqa: BLE001 - an unreadable run: not listed
                continue
            if "contact_pressure" in names:
                when = r.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M")
                self.run.addItem(f"{r.study_name} ({solver_label(r.solver)}, {when})", r.run_id)
        found = self.run.findData(chosen)
        self.run.setCurrentIndex(found if found >= 0 else 0)
        self.run.blockSignals(False)
        self._steps()

    def _steps(self) -> None:
        run_id = self.run.currentData()
        self.step.blockSignals(True)
        self.step.clear()
        if run_id is not None:
            results = self.window._store().read_results(run_id)
            steps = [s for s in results.steps if "contact_pressure" in s.fields]
            for i, s in enumerate(steps, start=1):
                self.step.addItem(f"Step {i}: {s.label()}", i)
            self.step.setCurrentIndex(len(steps) - 1)
        self.step.blockSignals(False)
        self.show_results()

    def show_results(self) -> None:
        from puzzle.model.units import convert, pretty
        from puzzle.post.contact import run_contact_results
        from puzzle.settings import current

        run_id = self.run.currentData()
        self.found = []
        for button in self.buttons.values():
            button.setEnabled(run_id is not None)
        if run_id is None:
            self.note.setText("Run a study with frictionless or frictional contact (CalculiX "
                              "or Code_Aster) to see its contact results here.")  # fmt: skip
        else:
            try:
                _step, self.found, _all = run_contact_results(
                    self.window.session.project, self.window._store(), run_id,
                    self.step.currentData())  # fmt: skip
                self.note.setText(
                    "Forces are the contact pressure and frictional stress "
                    "summed over each source face; areas where it is closed."
                )
            except Exception as exc:  # noqa: BLE001 - the model changed since the run
                self.note.setText(f"These results no longer match the model: {exc}")
        units = current().display_units()

        def q(value, si, kind):
            unit = units[kind]
            return f"{convert(value, si, unit):.4g} {pretty(unit)}"

        self.table.setRowCount(len(self.found))
        for r, c in enumerate(self.found):
            cells = [
                c.name, q(c.normal_force, "N", "force"), q(c.tangential_force, "N", "force"),
                f"{c.area_share:.0%}", f"{c.sticking_share:.0%}",
                q(c.max_pressure, "Pa", "pressure"), _length(c.max_penetration),
                _length(c.max_sliding), _length(c.max_gap),
            ]  # fmt: skip
            for k, text in enumerate(cells):
                self.table.setItem(r, k, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def show_field(self, field: str) -> None:
        """The run's contact field in the view, on the source faces."""
        run_id = self.run.currentData()
        if run_id is not None:
            self.window._select_run(run_id, f"{field}:value")


class ContactPanel(QWidget):
    """Window → Contacts: the Setup, Initial state and Results tabs."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.setup = SetupTab(window)
        self.initial = InitialTab(window)
        self.tabs.addTab(self.setup, "Setup")
        self.tabs.addTab(self.initial, "Initial state")
        self.results = ResultsTab(window)
        self.tabs.addTab(self.results, "Results")
        self.tabs.currentChanged.connect(lambda _i: self.refresh())
        layout.addWidget(self.tabs)

    def refresh(self) -> None:
        dock = getattr(self.window, "contacts_dock", None)
        if dock is not None and dock.isHidden():  # filled when shown
            return
        self.setup.refresh()
        self.initial.refresh()
        self.results.refresh()

    def warnings(self) -> list:
        """The last check's warnings, for the Problems list."""
        return [w for found in self.initial.warnings.values() for w in found]
