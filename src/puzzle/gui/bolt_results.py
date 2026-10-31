"""The bolt table's Results tab (ADR 0050).

For a run and step chosen at the top: one row per bolt with its axial force, shear,
torsion, the bending moment at each end and the largest, its stress σ = |N|/As + M/Z
and that stress over its grade's proof and yield strengths. Rows are sorted worst
first and the worst is marked. A selected bolt's loads along its length are plotted
(forces, then moments), from its first end to its second: a beam bolt carries no load
between its ends, so N, V and T are constant and M linear (`puzzle.bolts.along`).
The table exports to CSV.
"""

from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.bolts import BoltResult, bolt_results  # noqa: F401 - kept here for callers
from puzzle.gui.theme import role

ROLE = Qt.ItemDataRole.UserRole
HEADERS = ["Bolt", "Axial", "Shear", "Torsion", "Bending, first end", "Bending, second end",
           "Largest bending", "Stress", "Of proof", "Of yield"]  # fmt: skip


CSV_HEADERS = ["Bolt", "Component", "Axial (N)", "Shear (N)", "Torsion (N·m)",
               "Bending first end (N·m)", "Bending second end (N·m)", "Largest bending (N·m)",
               "Stress (Pa)", "Of proof", "Of yield"]  # fmt: skip


class ResultsTab(QWidget):
    def __init__(self, window) -> None:
        from puzzle.gui.material_builder import Curve

        super().__init__()
        self.window = window
        self.found: list[BoltResult] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.run = QComboBox()
        self.run.currentIndexChanged.connect(lambda _i: self._steps())
        self.step = QComboBox()
        self.step.currentIndexChanged.connect(lambda _i: self.show_results())
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(lambda: self.export())
        bar.addWidget(QLabel("Run"))
        bar.addWidget(self.run, 1)
        bar.addWidget(QLabel("Step"))
        bar.addWidget(self.step)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)
        self.note = role(QLabel(""), "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        # Docked anywhere, even short: the plots shrink and scroll, the splitter folds.
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(True)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._plot)
        split.addWidget(self.table)
        plots = QWidget()
        column = QVBoxLayout(plots)
        column.setContentsMargins(0, 0, 0, 0)
        self.plot_title = QLabel("")
        self.forces = Curve("Along the bolt", "Force")
        self.moments = Curve("Along the bolt", "Moment")
        for curve in (self.forces, self.moments):
            curve.setMinimumSize(140, 90)
        column.addWidget(self.plot_title)
        column.addWidget(self.forces, 1)
        column.addWidget(self.moments, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(plots)
        split.addWidget(scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)

    # --- runs and steps ----------------------------------------------------------------

    def refresh(self) -> None:
        """The runs that have bolt forces, newest first, keeping the one chosen."""
        session = self.window.session
        if session is None:
            return
        chosen = self.run.currentData()
        self.run.blockSignals(True)
        self.run.clear()
        try:
            store = self.window._store()
            runs = [r for r in store.runs() if r.status == "completed"]
        except Exception:  # no results yet
            runs = []
        for r in sorted(runs, key=lambda r: r.recorded_at, reverse=True):
            try:
                names = {f for f, _loc, _c in store.field_catalog(r.run_id)}
            except Exception:
                continue
            if "fastener" in names:
                when = r.recorded_at.strftime("%Y-%m-%d %H:%M")
                self.run.addItem(f"{r.study_name} ({r.solver}, {when})", r.run_id)
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
            steps = [s for s in results.steps if "fastener" in s.fields]
            for i, s in enumerate(steps):
                self.step.addItem(f"Step {i + 1}: {s.label()}", i)
            self.step.setCurrentIndex(len(steps) - 1)
        self.step.blockSignals(False)
        self.show_results()

    # --- the table and the plot --------------------------------------------------------

    def show_results(self) -> None:
        from puzzle.model.units import convert, pretty
        from puzzle.settings import current

        run_id = self.run.currentData()
        self.found = []
        if run_id is None:
            self.note.setText("Run a study with bolts to see their loads here.")
        else:
            try:
                self.found = bolt_results(self.window.session.project, self.window._store(),
                                          run_id, self.step.currentData())  # fmt: skip
                self.note.setText(
                    "Stress σ = |N|/As + M/Z (Z = πd³/32 on the stress area's diameter), "
                    "over the grade's proof and yield strengths; worst first."
                )
            except Exception as exc:  # the model changed since the run
                self.note.setText(f"These results no longer match the model: {exc}")
        units = current().display_units()
        force, stress = units["force"], units["pressure"]
        self.table.setRowCount(len(self.found))
        for r, b in enumerate(self.found):
            cells = [
                b.name,
                f"{convert(b.loads.axial[0], 'N', force):.4g} {pretty(force)}",
                f"{convert(b.loads.shear[0], 'N', force):.4g} {pretty(force)}",
                f"{b.loads.torsion[0]:.4g} N·m",
                f"{b.loads.bending[0]:.4g} N·m",
                f"{b.loads.bending[-1]:.4g} N·m",
                f"{b.loads.largest_bending:.4g} N·m",
                f"{convert(b.stress, 'Pa', stress):.4g} {pretty(stress)}",
                f"{b.proof:.0%}" if b.proof is not None else "no proof strength",
                f"{b.yielding:.0%}" if b.yielding is not None else "no yield strength",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(ROLE, r)
                if r == 0 and len(self.found) > 1:  # the worst, marked
                    bold = QFont(item.font())
                    bold.setBold(True)
                    item.setFont(bold)
                if c in (8, 9) and ((b.proof or 0) >= 1 if c == 8 else (b.yielding or 0) >= 1):
                    item.setForeground(self.palette().color(self.palette().ColorRole.Link))
                    item.setToolTip("at or over the limit")
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        if self.found:
            self.table.selectRow(0)
        self._plot()

    def _plot(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1 or not self.found:
            self.forces.set_series([])
            self.moments.set_series([])
            self.plot_title.setText("")
            return
        b = self.found[next(iter(rows))]
        s_mm = [x * 1e3 for x in b.loads.s]
        self.plot_title.setText(f"{b.name}: along its length (mm), first end at 0")
        self.forces.set_series([
            ("Axial (N)", list(zip(s_mm, b.loads.axial, strict=True))),
            ("Shear (N)", list(zip(s_mm, b.loads.shear, strict=True))),
        ])  # fmt: skip
        self.moments.set_series([
            ("Bending (N·m)", list(zip(s_mm, b.loads.bending, strict=True))),
            ("Torsion (N·m)", list(zip(s_mm, b.loads.torsion, strict=True))),
        ])  # fmt: skip

    def export(self, path: str | None = None) -> str | None:
        path = path or self.window.ask_save_path("Export bolt results", "bolt-results.csv",
                                                 "CSV files (*.csv)")  # fmt: skip
        if not path:
            return None
        with open(path, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(CSV_HEADERS)
            for b in self.found:
                out.writerow([b.name, b.component, b.loads.axial[0], b.loads.shear[0],
                              b.loads.torsion[0], b.loads.bending[0], b.loads.bending[-1],
                              b.loads.largest_bending, b.stress, b.proof, b.yielding])  # fmt: skip
        return path
