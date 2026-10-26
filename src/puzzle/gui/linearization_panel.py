"""A linearized stress (ADR 0042, 0046) as ASME VIII-2 Annex 5-A presents it.

Shown in the Properties dock while the tool is selected:
- **how it is read:** the equivalent (von Mises or Tresca), the axes (global, or the
  line's own: through the wall, meridional, hoop) and, in the line's axes, ASME's
  bending for the normal stresses across the line only. Each change is an edit of the
  tool (one undo);
- **the breakdown** (a "Linearization" tab in Messages, where its eleven columns have
  room): membrane, bending, membrane + bending, peak and total at the start (inside),
  the middle and the end (outside), as the six components, the principal stresses and
  both equivalents, in a stress unit picked here (MPa, psi, ksi, ...);
- **the checks** against an allowable S, when one is given: Pm (or PL) ≤ S (1.5 S),
  PL + Pb ≤ 1.5 S, PL + Pb + Q ≤ 3 S;
- **Copy table** puts the breakdown on the clipboard as tab-separated text, for a
  report or a spreadsheet.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role
from puzzle.post.linearize import EQUIVALENTS, GLOBAL_LABELS, LOCAL_LABELS, ORDER

AXES = [
    ("Global axes (X, Y, Z)", "global"),
    ("The line's axes (through, meridional, hoop)", "line"),
]
BENDING = [
    ("Every component", "all"),
    ("ASME: hoop and meridional normal stresses only", "normal"),
]
MEMBRANE = [("General primary (Pm ≤ S)", "general"), ("Local primary (PL ≤ 1.5 S)", "local")]
HOOPS = [("Automatic", None), ("Along X", (1.0, 0.0, 0.0)), ("Along Y", (0.0, 1.0, 0.0)),
         ("Along Z", (0.0, 0.0, 1.0))]  # fmt: skip
# The breakdown's rows: (label, end, part).
ROWS = [
    ("Membrane", "start", "membrane"),
    ("Bending, start", "start", "bending"),
    ("Bending, end", "end", "bending"),
    ("Membrane + bending, start", "start", "membrane_bending"),
    ("Membrane + bending, end", "end", "membrane_bending"),
    ("Peak, start", "start", "peak"),
    ("Peak, end", "end", "peak"),
    ("Total, start", "start", "total"),
    ("Total, middle", "middle", "total"),
    ("Total, end", "end", "total"),
]


def _combo(pairs, current) -> QComboBox:
    box = QComboBox()
    for text, value in pairs:
        box.addItem(text, value)
    i = box.findData(current)
    box.setCurrentIndex(i if i >= 0 else 0)
    return box


class LinearizationPanel(QWidget):
    def __init__(self, window, probe, found: dict) -> None:
        from puzzle.model.units import CHOICES, pretty
        from puzzle.settings import current

        super().__init__()
        self.window, self.probe, self.found = window, probe, found
        self._loading = True
        default = current().display_units()["pressure"]
        if default == "Pa":  # SI's pascals read badly for stresses: MPa (N/mm²)
            default = "MPa"
        self.unit = getattr(window, "linearization_unit", None) or default

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        layout = QVBoxLayout(inner)

        layout.addWidget(role(QLabel(probe.name), "title"))
        layout.addWidget(role(QLabel(self._about()), "muted"))

        form = QFormLayout()
        self.equivalent = _combo(list((v, k) for k, v in EQUIVALENTS.items()), probe.equivalent)
        self.axes = _combo(AXES, probe.components)
        hoop = tuple(probe.hoop_direction) if probe.hoop_direction else None
        self.hoop = _combo(HOOPS, hoop)
        self.bending = _combo(BENDING, probe.bending)
        self.units = _combo([(pretty(u), u) for u in CHOICES["pressure"]], self.unit)
        self.allowable = QLineEdit(self._shown(probe.allowable) if probe.allowable else "")
        self.allowable.setPlaceholderText("none: no checks")
        self.membrane = _combo(MEMBRANE, probe.membrane)
        rows = [
            ("Equivalent", self.equivalent), ("Components in", self.axes),
            ("Hoop direction", self.hoop), ("Bending", self.bending),
            ("Stresses in", self.units), ("Allowable S", self.allowable),
            ("Membrane is", self.membrane),
        ]  # fmt: skip
        for text, widget in rows:
            form.addRow(text, widget)
        layout.addLayout(form)
        line = probe.components == "line"
        form.setRowVisible(self.hoop, line)
        form.setRowVisible(self.bending, line)

        self.note = role(QLabel(""), "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        show = QHBoxLayout()
        table = QPushButton("Breakdown")
        table.setToolTip("Membrane, bending and peak as a table, in Messages")
        table.clicked.connect(self.show_report)
        plot = QPushButton("Plot along the line")
        plot.clicked.connect(lambda: window.plots.show_path(probe))
        show.addWidget(table)
        show.addWidget(plot)
        show.addStretch()
        layout.addLayout(show)
        layout.addStretch()

        # The breakdown is wide (eleven columns): it goes in Messages, beside Plots.
        self.report = QWidget()
        lower = QVBoxLayout(self.report)
        lower.setContentsMargins(4, 4, 4, 4)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.checks = QTableWidget()
        self.checks.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        row = self.fontMetrics().height() + 8  # compact rows: all ten in sight
        for t in (self.table, self.checks):
            t.verticalHeader().setDefaultSectionSize(row)
        copy = QPushButton("Copy table")
        copy.setToolTip("The breakdown as tab-separated text, for a report or spreadsheet")
        copy.clicked.connect(self.copy)
        top = QHBoxLayout()
        self.caption = QLabel("")
        top.addWidget(self.caption, 1)
        top.addWidget(copy)
        lower.addLayout(top)
        tables = QHBoxLayout()
        tables.addWidget(self.table, 3)
        tables.addWidget(self.checks, 2)
        lower.addLayout(tables, 1)

        for box in (self.equivalent, self.axes, self.hoop, self.bending, self.membrane):
            box.currentIndexChanged.connect(lambda _=0: self.edited())
        self.allowable.editingFinished.connect(self.edited)
        self.units.currentIndexChanged.connect(lambda _=0: self.set_unit())
        self.fill()
        self._loading = False

    # --- values ----------------------------------------------------------------------

    def _about(self) -> str:
        from puzzle.gui.window import _position

        f, p = self.found, self.probe
        built = self.window.session.project.objects.get(p.construction)
        line = (f"along {built.name}" if built is not None
                else f"from {_position(p.point)} to {_position(p.end)}")  # fmt: skip
        source = self.window.session.project.objects.get(p.source)
        from puzzle.gui.result_tools import _value

        return (f"{source.name if source else '?'}, {line}; thickness "
                f"{_value(f['thickness'], 'length')}, {p.samples} points.")  # fmt: skip

    def _convert(self, si) -> np.ndarray:
        from puzzle.model.units import registry

        values = np.asarray(si, float)
        if self.unit == "Pa":
            return values
        return np.asarray(registry().Quantity(values, "Pa").to(self.unit).magnitude)

    def _number(self, si: float, digits: int) -> str:
        if abs(si) <= getattr(self, "_tiny", 0.0):
            return "0"
        return f"{float(self._convert([si])[0]):.{digits}g}"

    def _shown(self, si: float) -> str:
        return f"{float(self._convert([si])[0]):.6g}"

    def _row_values(self, end: str, part: str) -> dict:
        return self.found["middle"] if end == "middle" else self.found["parts"][end][part]

    def columns(self) -> list[tuple[str, str]]:
        """(header, key): the components in the axes used, then S1-S3 and equivalents."""
        names = LOCAL_LABELS if self.found.get("components_in") == "line" else GLOBAL_LABELS
        comps = list(self.found["parts"]["start"]["membrane"]["components"])
        out = [(names.get(c, c.upper()), c) for c in comps]
        return out + [("S1", "s1"), ("S2", "s2"), ("S3", "s3"),
                      ("von Mises", "von_mises"), ("Tresca", "tresca")]  # fmt: skip

    def cell(self, values: dict, key: str) -> float:
        if key in values["components"]:
            return values["components"][key]
        if key in ("s1", "s2", "s3"):
            return values["principal"][["s1", "s2", "s3"].index(key)]
        return values[key]

    def fill(self) -> None:
        from puzzle.model.units import pretty

        cols = self.columns()
        unit = pretty(self.unit)
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(ROWS))
        self.table.setHorizontalHeaderLabels([f"{h} ({unit})" if i == 0 else h
                                              for i, (h, _) in enumerate(cols)])  # fmt: skip
        self.table.setVerticalHeaderLabels([r[0] for r in ROWS])
        chosen = self.found["equivalent"]
        # Round-off (1e-9 of the largest) reads as 0, never as "-0" or "3e-11".
        every = [abs(self.cell(self._row_values(e, p), k)) for _l, e, p in ROWS for _h, k in cols]
        self._tiny = 1e-9 * max(every + [1.0])
        bold = QFont()
        bold.setBold(True)
        for r, (_label, end, part) in enumerate(ROWS):
            values = self._row_values(end, part)
            for c, (_h, key) in enumerate(cols):
                item = QTableWidgetItem(self._number(self.cell(values, key), 4))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if key == chosen:
                    item.setFont(bold)
                self.table.setItem(r, c, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.caption.setText(f"<b>{self.probe.name}</b>: membrane, bending and peak ({unit})")
        self._fill_checks(unit)
        bending = (" Bending is for the hoop and meridional normal stresses only (ASME "
                   "VIII-2 5-A.4.1.2); the rest of the linear part is in the peak."
                   if self.found.get("bending") == "normal" else "")  # fmt: skip
        self.note.setText(
            "Start and end are the line's two ends (inside and outside the wall); bending "
            f"changes sign between them. The {EQUIVALENTS[chosen]} column (bold) is the one "
            "the value and the checks use."
            + bending
            + " Which load is primary and which secondary is yours "
            "to judge: the numbers are the linearized stresses."
        )

    def _fill_checks(self, unit: str) -> None:
        rows = self.found.get("checks") or []
        self.checks.setVisible(bool(rows))
        if not rows:
            return
        self.checks.setColumnCount(5)
        self.checks.setRowCount(len(rows))
        self.checks.setHorizontalHeaderLabels(["Check", f"Stress ({unit})", f"Limit ({unit})",
                                               "Ratio", "Passes"])  # fmt: skip
        self.checks.verticalHeader().hide()
        for r, row in enumerate(rows):
            cells = [row["name"], f"{float(self._convert([row['value']])[0]):.4g}",
                     f"{float(self._convert([row['limit']])[0]):.4g}", f"{row['ratio']:.3f}",
                     "✓ yes" if row["ok"] else "✗ no"]  # fmt: skip
            for c, text in enumerate(cells):
                self.checks.setItem(r, c, QTableWidgetItem(text))
        self.checks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def show_report(self) -> None:
        """The breakdown's tab in Messages, opened."""
        w = self.window
        tabs = w.output_tabs
        if tabs.indexOf(self.report) < 0:
            tabs.addTab(self.report, "Linearization")
        tabs.setCurrentWidget(self.report)
        w._messages_open_height = max(w._messages_open_height, 340)  # room for its ten rows
        w.fold_messages(False)
        if w.messages_dock.height() < 300:  # already open, but too low for the table
            w.resizeDocks([w.messages_dock], [340], Qt.Orientation.Vertical)

    def drop_report(self) -> None:
        tabs = self.window.output_tabs
        i = tabs.indexOf(self.report)
        if i >= 0:
            tabs.removeTab(i)
        self.report.hide()
        self.report.deleteLater()

    # --- changes ---------------------------------------------------------------------

    def set_unit(self) -> None:
        """Only how the numbers read: kept for the session, never in the project."""
        if self._loading:
            return
        self.unit = self.units.currentData()
        self.window.linearization_unit = self.unit
        self.fill()

    def edited(self) -> None:
        """The tool's settings changed: one edit, then the tool is read again."""
        if self._loading:
            return
        from puzzle.commands.model import Edit, EditOp

        text = self.allowable.text().strip()
        allowable = None
        if text:
            try:
                allowable = float(self._to_si(float(text)))
            except ValueError:
                self.note.setText("Type the allowable stress as a number")
                return
        values = {
            "equivalent": self.equivalent.currentData(),
            "components": self.axes.currentData(),
            "hoop_direction": list(self.hoop.currentData()) if self.hoop.currentData() else None,
            "bending": self.bending.currentData() if self.axes.currentData() == "line" else "all",
            "allowable": allowable,
            "membrane": self.membrane.currentData(),
        }
        ops = [EditOp(op="set", path=k, value=v) for k, v in values.items()
               if getattr(self.probe, k) != (tuple(v) if isinstance(v, list) else v)]  # fmt: skip
        if ops:
            self.window.submit(Edit(object=str(self.probe.id), ops=ops))

    def _to_si(self, value: float) -> float:
        from puzzle.model.units import registry

        if self.unit == "Pa":
            return value
        return float(registry().Quantity(value, self.unit).to("Pa").magnitude)

    def text(self) -> str:
        """The breakdown as tab-separated text (headers, then one line per row)."""
        from puzzle.model.units import pretty

        cols = self.columns()
        lines = ["\t".join([f"Stress ({pretty(self.unit)})"] + [h for h, _ in cols])]
        for label, end, part in ROWS:
            values = self._row_values(end, part)
            nums = [self._number(self.cell(values, k), 6) for _, k in cols]
            lines.append("\t".join([label, *nums]))
        for row in self.found.get("checks") or []:
            stress, limit = self._convert([row["value"], row["limit"]])
            cells = [row["name"], f"{stress:.6g}", f"{limit:.6g}", f"{row['ratio']:.4f}"]
            lines.append("\t".join([*cells, "yes" if row["ok"] else "no"]))
        return "\n".join(lines)

    def copy(self) -> None:
        QApplication.clipboard().setText(self.text())
        self.window.statusBar().showMessage("The breakdown is on the clipboard", 5000)


__all__ = ["LinearizationPanel", "ORDER"]
