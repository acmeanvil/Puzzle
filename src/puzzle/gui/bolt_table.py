"""The bolt table (ADR 0050): a docked panel listing every bolt, in two tabs.

Properties: one row per bolt of every component: name, component, type, its two
ends, diameter, length, preload, formulation, ends and group. A value the bolt takes
from its type is shown faded, with the type named in its tooltip; typing a value sets
it on the bolt, and clearing it gives the type's back. An edit made with several rows
selected applies to each of them, as one command batch (one undo). Rows filter by text,
sort by any column, export to CSV, and selecting one selects the bolt in the tree.

Results: each bolt's forces for a chosen run and step, its use of its grade's proof
and yield loads, and the moments along a selected bolt (`results_tab`).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role

ROLE = Qt.ItemDataRole.UserRole
FROM_TYPE = "From its bolt type"


@dataclass(frozen=True)
class Column:
    title: str
    field: str | None  # the bolt's field it edits; None: shown only
    kind: str = "text"  # text, choice, length, force


COLUMNS = [
    Column("Name", "name"),
    Column("Component", None),
    Column("Type", "type", "choice"),
    Column("One end", "source", "choice"),
    Column("Other end", "target", "choice"),
    Column("Diameter", "diameter", "length"),
    Column("Length", None),
    Column("Preload", "pretension", "force"),
    Column("Formulation", "formulation", "choice"),
    Column("Ends", "ends", "choice"),
    Column("Joint", "joint", "choice"),
    Column("Group", "group"),
]


@dataclass
class Row:
    component: object
    index: int
    bolt: object  # as stored
    solved: object  # with its type's values (puzzle.bolts.resolve)
    length: float | None  # m, between the ends' centres on the last mesh


def bolt_rows(project, meshes: dict | None = None) -> list[Row]:
    """Every bolt of every component, resolved; lengths from the components' meshes."""
    from puzzle.adapters.common import fastener_ends
    from puzzle.bolts import resolve
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component

    out = []
    for comp in sorted(project.of_type(Component), key=lambda c: c.name):
        mesh = (meshes or {}).get(str(comp.id))
        for i, bolt in enumerate(comp.connectors):
            try:
                solved = resolve(project, bolt)
            except PuzzleError:
                solved = bolt
            length = None
            if mesh is not None:
                try:
                    a, b = (e.centre for e in fastener_ends(solved, mesh))
                    length = float(np.linalg.norm(np.asarray(b) - np.asarray(a)))
                except Exception:  # an end not in this mesh: no length to show
                    length = None
            out.append(Row(comp, i, bolt, solved, length))
    return out


def _shown_quantity(q, kind: str) -> str:
    from puzzle.model.units import convert, pretty
    from puzzle.settings import current

    if q is None:
        return ""
    unit = current().display_units()["length" if kind == "length" else "force"]
    return f"{convert(q.to_si(), 'm' if kind == 'length' else 'N', unit):.4g} {pretty(unit)}"


class _Delegate(QStyledItemDelegate):
    """A drop-down for the choice columns, plain text for the rest."""

    def __init__(self, panel) -> None:
        super().__init__(panel.table)
        self.panel = panel

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt's name
        column = COLUMNS[index.column()]
        if column.kind != "choice":
            return super().createEditor(parent, option, index)
        box = QComboBox(parent)
        for text, value in self.panel.choices(index.row(), column):
            box.addItem(text, value)
        return box

    def setEditorData(self, editor, index):  # noqa: N802
        if isinstance(editor, QComboBox):
            current = self.panel.stored(index.row(), COLUMNS[index.column()])
            found = editor.findData(current)
            editor.setCurrentIndex(max(found, 0))
            editor.showPopup()
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # noqa: N802
        column = COLUMNS[index.column()]
        value = editor.currentData() if isinstance(editor, QComboBox) else editor.text()
        self.panel.edit(index.row(), column, value)


class PropertiesTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.rows: list[Row] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter bolts")
        self.filter.textChanged.connect(self._filtered)
        self.count = role(QLabel(""), "muted")
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(lambda: self.export())
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.count)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c.title for c in COLUMNS])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setItemDelegate(_Delegate(self))
        self.table.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.table, 1)
        self.empty = role(QLabel("No bolts yet: Model → Add fastener…, or Fasteners from "
                                 "holes…"), "muted")  # fmt: skip
        layout.addWidget(self.empty)

    # --- filling ---------------------------------------------------------------

    def refresh(self) -> None:
        session = self.window.session
        project = session.project if session is not None else None
        self.rows = bolt_rows(project, getattr(self.window, "meshes", {})) if project else []
        names = {o.id: o.name for o in project.objects.values()} if project else {}
        faded = QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            for c, column in enumerate(COLUMNS):
                text, inherited = self._text(row, column, names)
                item = QTableWidgetItem(text)
                item.setData(ROLE, (str(row.component.id), row.index))
                if column.field is None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if inherited:
                    item.setForeground(faded)
                    item.setToolTip(f"from the bolt type {names.get(row.bolt.type, '')}")
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.empty.setVisible(not self.rows)
        self.table.setVisible(bool(self.rows))
        self._filtered()

    def _text(self, row: Row, column: Column, names: dict) -> tuple[str, bool]:
        """The cell's words, and whether the value is the type's (not the bolt's own)."""
        from puzzle.gui.choices import label

        bolt, solved = row.bolt, row.solved
        own = getattr(bolt, column.field) if column.field else None
        typed = bolt.type is not None and column.field is not None and own is None
        if column.title == "Component":
            return row.component.name, False
        if column.title == "Length":
            return (_shown_quantity(_metres(row.length), "length") if row.length else "—"), False
        if column.field == "type":
            return (names.get(bolt.type, "?") if bolt.type else "None"), False
        if column.kind in ("length", "force"):
            q = getattr(solved, column.field)
            return (_shown_quantity(q, column.kind) or "none"), typed
        value = getattr(solved, column.field)
        if column.field in ("formulation", "ends", "joint"):
            return label(value), typed
        return ("" if value is None else str(value)), typed

    def _filtered(self) -> None:
        words = self.filter.text().strip().lower()
        shown = 0
        for r in range(self.table.rowCount()):
            texts = [self.table.item(r, c).text().lower() for c in range(self.table.columnCount())]
            hide = bool(words) and not any(words in t for t in texts)
            self.table.setRowHidden(r, hide)
            shown += not hide
        total = len(self.rows)
        self.count.setText(f"{shown} of {total} bolts" if words else f"{total} bolts")

    # --- what a cell holds ---------------------------------------------------------

    def row_at(self, r: int) -> Row | None:
        item = self.table.item(r, 0)
        if item is None:
            return None
        cid, index = item.data(ROLE)
        return next((x for x in self.rows if str(x.component.id) == cid and x.index == index), None)

    def stored(self, r: int, column: Column):
        row = self.row_at(r)
        value = getattr(row.bolt, column.field) if row and column.field else None
        return str(value) if column.field == "type" and value is not None else value

    def choices(self, r: int, column: Column) -> list[tuple[str, object]]:
        from puzzle.gui.choices import choices

        row = self.row_at(r)
        if row is None:
            return []
        return choices(self.window.session.project, row.component,
                       f"connectors.{row.index}.{column.field}") or []  # fmt: skip

    # --- editing -----------------------------------------------------------------

    def edit(self, r: int, column: Column, value) -> None:
        """Set the column on this row, or on every selected row when it is one of them."""
        from puzzle.commands.model import Edit, EditOp

        rows = {i.row() for i in self.table.selectedIndexes()}
        targets = sorted(rows) if r in rows and len(rows) > 1 else [r]
        try:
            stored = self._value(column, value, [self.row_at(t) for t in targets])
        except ValueError as exc:
            self.window.statusBar().showMessage(str(exc), 8000)
            return
        by_component: dict[str, list] = {}
        for t in targets:
            row = self.row_at(t)
            if row is None:
                continue
            by_component.setdefault(str(row.component.id), []).append(
                EditOp(op="set", path=f"connectors.{row.index}.{column.field}", value=stored)
            )
        commands = [Edit(object=cid, ops=ops) for cid, ops in by_component.items()]
        if not commands:
            return
        if len(commands) == 1:
            self.window.submit(commands[0])
        else:
            self.window.submit_batch(commands)
        self.refresh()

    def _value(self, column: Column, value, rows):
        from puzzle.model.units import known, plain
        from puzzle.settings import current

        if column.kind == "choice":
            return value
        text = str(value).strip()
        if column.kind in ("length", "force"):
            if not text or text.lower() in ("none", "from type"):
                if column.field == "diameter" and any(r.bolt.type is None for r in rows):
                    raise ValueError("A bolt without a bolt type needs its own diameter")
                return None
            # "12" is in the unit shown; "12 mm" says its own (as in Properties).
            number, _, unit = text.partition(" ")
            if not unit.strip():
                unit = current().display_units()["length" if column.kind == "length" else "force"]
            unit = plain(unit)
            try:
                return {"value": float(number), "unit": unit} if known(unit) else _bad(text)
            except ValueError:
                raise ValueError(
                    f"'{text}' is not a {column.title.lower()}: type a number, "
                    "and a unit if not the one shown"
                ) from None
        if column.field == "name" and not text:
            raise ValueError("A bolt needs a name")
        return text or None

    # --- selection and export --------------------------------------------------------

    def _selected(self) -> None:
        from puzzle.gui.tree import Node

        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            return
        row = self.row_at(next(iter(rows)))
        if row is not None:
            self.window.tree.select(Node("connector", str(row.component.id),
                                         f"connectors.{row.index}", row.bolt.name))  # fmt: skip

    def export(self, path: str | None = None) -> str | None:
        path = path or self.window.ask_save_path("Export the bolt table", "bolts.csv",
                                                 "CSV files (*.csv)")  # fmt: skip
        if not path:
            return None
        with open(path, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow([c.title for c in COLUMNS])
            for r in range(self.table.rowCount()):
                if not self.table.isRowHidden(r):
                    out.writerow([self.table.item(r, c).text() for c in range(len(COLUMNS))])
        return path


def _bad(text: str):
    raise ValueError(f"'{text}': unknown unit")


def _metres(value: float | None):
    from puzzle.model.schema import Quantity

    return Quantity(value=value, unit="m") if value is not None else None


class BoltPanel(QWidget):
    """Window → Bolts: the Properties and Results tabs. Its minimum stays small (the
    Results plots shrink and scroll), so docked beside or under another panel it never
    spills over its neighbours."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.properties = PropertiesTab(window)
        self.tabs.addTab(self.properties, "Properties")
        from puzzle.gui.bolt_results import ResultsTab

        self.results = ResultsTab(window)
        self.tabs.addTab(self.results, "Results")
        self.tabs.currentChanged.connect(lambda _i: self.refresh())
        layout.addWidget(self.tabs)

    def refresh(self) -> None:
        dock = getattr(self.window, "bolts_dock", None)
        if dock is not None and dock.isHidden():  # filled when shown
            return
        self.properties.refresh()
        self.results.refresh()
