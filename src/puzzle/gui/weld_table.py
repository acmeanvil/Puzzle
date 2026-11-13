"""The Welds panel (ADR 0078, Window → Welds): for a run with light welds, one row per weld
with its length, total force, largest line force, its AISC 360 J2 utilisation at the
governing station (LRFD and ASD), the instantaneous centre's in-plane utilisation and
the filler; failing rows marked, worst first. A selected weld's stations are listed
below with their line forces in the weld's frame. Exports to CSV. Evaluated from the
stored run on demand (`post.weld_fields.results`), as MCP `weld_results`."""

from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role

HEADERS = ["Weld", "Length", "Total force", "Largest line force", "J2 (LRFD)", "J2 (ASD)",
           "In-plane, IC (LRFD)", "Filler"]  # fmt: skip
STATIONS = ["Station", "Along", "Normal to throat", "Shear on throat", "Magnitude"]


def _table(headers) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return t


class WeldTable(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.found: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.run = QComboBox()
        self.run.currentIndexChanged.connect(lambda _i: self.show_results())
        self.directional = QCheckBox("Directional strength (J2-5)")
        self.directional.setToolTip("1 + 0.5 sin^1.5 θ, for linear welds loaded in plane")
        self.directional.toggled.connect(lambda _on: self.show_results())
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(lambda: self.export())
        bar.addWidget(QLabel("Run"))
        bar.addWidget(self.run, 1)
        bar.addWidget(self.directional)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)
        self.note = role(QLabel(""), "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = _table(HEADERS)
        self.table.itemSelectionChanged.connect(self._stations)
        self.stations = _table(STATIONS)
        split.addWidget(self.table)
        split.addWidget(self.stations)
        layout.addWidget(split, 1)

    def refresh(self) -> None:
        """Completed runs whose results have weld line forces, newest first."""
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
            if "weld_line_force" in names:
                when = r.recorded_at.strftime("%Y-%m-%d %H:%M")
                self.run.addItem(f"{r.study_name} ({r.solver}, {when})", r.run_id)
        found = self.run.findData(chosen)
        self.run.setCurrentIndex(found if found >= 0 else 0)
        self.run.blockSignals(False)
        self.show_results()

    def show_results(self) -> None:
        from puzzle.model.schema import Component, Study
        from puzzle.post.weld_fields import results

        run_id = self.run.currentData()
        self.found = []
        if run_id is None:
            self.note.setText("Run a study with light welds to see their forces here.")
        else:
            store, project = self.window._store(), self.window.session.project
            record = next(r for r in store.runs() if r.run_id == run_id)
            try:
                study = next(o for o in project.of_type(Study) if str(o.id) == record.study_id)
                comp = project.get(study.component, Component)
                out = results(project, comp, store.read_mesh(record.mesh_id),
                              store.read_results(run_id),
                              directional=self.directional.isChecked())  # fmt: skip
                self.found = sorted(out["welds"], key=lambda w: -self._worst(w))
                self.note.setText(
                    "AISC 360-16 J2 at each weld's governing station, and its in-plane load "
                    "against the instantaneous centre's strength; worst first.")  # fmt: skip
            except Exception as exc:  # the model changed since the run
                self.note.setText(f"These welds can't be read from this run: {exc}")
        self.table.setRowCount(len(self.found))
        for r, w in enumerate(self.found):
            s = w.get("strength") or {}
            ic = s.get("instantaneous_centre") or {}
            cells = [
                w["weld"], self._length(w["length_m"]),
                f"{sum(v * v for v in w['force_N']) ** 0.5 / 1e3:.4g} kN",
                f"{w['peak_line_force_N_per_m'] / 1e3:.4g} N/mm",
                f"{s['utilisation_lrfd']:.3f}" if s.get("checked") else s.get("why", "")[:60],
                f"{s['utilisation_asd']:.3f}" if s.get("checked") else "",
                f"{ic['utilisation_lrfd']:.3f}" if ic.get("checked") else ic.get("why", "")[:60],
                s.get("filler", ""),
            ]  # fmt: skip
            failing = self._worst(w) > 1.0
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if failing:
                    bold = QFont(item.font())
                    bold.setBold(True)
                    item.setFont(bold)
                    item.setToolTip("over its available strength")
                if c == 6 and ic.get("note"):
                    item.setToolTip(ic["note"])
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        if self.found:
            self.table.selectRow(0)
        self._stations()

    @staticmethod
    def _worst(w: dict) -> float:
        s = w.get("strength") or {}
        ic = s.get("instantaneous_centre") or {}
        return max(s.get("utilisation_lrfd", 0.0) if s.get("checked") else 0.0,
                   ic.get("utilisation_lrfd", 0.0) if ic.get("checked") else 0.0)  # fmt: skip

    @staticmethod
    def _length(metres: float) -> str:
        from puzzle.settings import length_unit

        unit, per_metre = length_unit()
        return f"{metres * per_metre:.4g} {unit}"

    def _stations(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1 or not self.found:
            self.stations.setRowCount(0)
            return
        w = self.found[next(iter(rows))]
        line = w.get("stations") or []
        self.stations.setRowCount(len(line))
        for r, st in enumerate(line):
            cells = [str(r + 1), *(f"{st.get(k, 0.0) / 1e3:.4g} N/mm"
                                   for k in ("along", "throat_normal", "throat_shear",
                                             "magnitude"))]  # fmt: skip
            for c, text in enumerate(cells):
                self.stations.setItem(r, c, QTableWidgetItem(text))
        self.stations.resizeColumnsToContents()

    def export(self, path: str | None = None) -> str | None:
        path = path or self.window.ask_save_path("Export weld results", "weld-results.csv",
                                                 "CSV files (*.csv)")  # fmt: skip
        if not path:
            return None
        with open(path, "w", newline="", encoding="utf-8") as f:
            out = csv.writer(f)
            out.writerow(["Weld", "Length (m)", "Total force (N)", "Largest line force (N/m)",
                          "J2 LRFD", "J2 ASD", "IC LRFD", "Filler"])  # fmt: skip
            for w in self.found:
                s = w.get("strength") or {}
                ic = s.get("instantaneous_centre") or {}
                out.writerow([w["weld"], w["length_m"],
                              sum(v * v for v in w["force_N"]) ** 0.5,
                              w["peak_line_force_N_per_m"], s.get("utilisation_lrfd"),
                              s.get("utilisation_asd"), ic.get("utilisation_lrfd"),
                              s.get("filler")])  # fmt: skip
        return path
