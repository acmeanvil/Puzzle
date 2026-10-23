"""Compare a swept study's points (ADR 0018): side by side, against a baseline, in a table.

- Side by side: the chosen result for every point in a grid of views, one colour scale,
  cameras linked (turn one, all turn).
- Difference: one point minus a baseline point (or their ratio) on the baseline's mesh.
- Summary: each point's minimum and maximum, and where the maximum is, with a bar chart.

Values are SI underneath and shown in the display units (Settings > Units).
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.errors import PuzzleError
from puzzle.gui.viewport import Viewport, _display
from puzzle.post import compare
from puzzle.post.quantities import catalog, default


def _items(cases: list[compare.Case]):
    """The results every point has, in the usual order."""
    lists = []
    for c in cases:
        fields = {}
        for step in c.results.steps:
            for name, f in step.fields.items():
                fields[name] = (name, f.location, list(f.components))
        lists.append(catalog(list(fields.values())))
    common = set.intersection(*({i.key for i in items} for items in lists)) if lists else set()
    return [i for i in lists[0] if i.key in common] if lists else []


class Bars(QWidget):
    """A bar per point (its maximum), labelled; enough for a handful of points."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.bars: list[tuple[str, float]] = []
        self.unit = ""

    def set(self, bars: list[tuple[str, float]], unit: str) -> None:
        self.bars, self.unit = bars, unit
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if not self.bars:
            return
        p = QPainter(self)
        w, h = self.width(), self.height()
        top = max(abs(v) for _, v in self.bars) or 1.0
        slot = w / len(self.bars)
        base = h - 24
        for i, (text, v) in enumerate(self.bars):
            bar = (base - 20) * abs(v) / top
            x = int(i * slot + slot * 0.2)
            p.fillRect(x, int(base - bar), int(slot * 0.6), int(bar), QColor("#4f7cac"))
            p.drawText(x, int(base - bar) - 4, f"{v:.4g} {self.unit}".strip())
            p.drawText(x, h - 6, text)
        p.end()


class CompareWindow(QDialog):
    def __init__(self, title: str, cases: list[compare.Case], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Compare: {title}")
        self.resize(1100, 750)
        self.cases = cases
        self.items = _items(cases)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Result:"))
        self.result = QComboBox()
        for item in self.items:
            self.result.addItem(item.label, item.key)
        first = default(self.items)
        if first is not None:
            self.result.setCurrentIndex(self.result.findData(first.key))
        row.addWidget(self.result, 1)
        layout.addLayout(row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.message = QLabel()
        layout.addWidget(self.message)

        # Side by side.
        grid_page = QWidget()
        grid = QGridLayout(grid_page)
        cols = max(1, math.ceil(math.sqrt(len(cases))))
        self.views: list[Viewport] = []
        self.view_titles: list[QLabel] = []
        for i, case in enumerate(cases):
            cell = QVBoxLayout()
            name = QLabel(case.label)
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view = Viewport()
            cell.addWidget(name)
            cell.addWidget(view, 1)
            grid.addLayout(cell, i // cols, i % cols)
            self.views.append(view)
            self.view_titles.append(name)
            if view.plotter is not None:
                view.view.camera_moved.connect(lambda v=view: self._follow(v))
        self.tabs.addTab(grid_page, "Side by side")

        # Difference from a baseline.
        diff_page = QWidget()
        diff = QVBoxLayout(diff_page)
        pick = QHBoxLayout()
        self.other, self.baseline, self.mode = QComboBox(), QComboBox(), QComboBox()
        for i, case in enumerate(cases):
            self.other.addItem(case.label, i)
            self.baseline.addItem(case.label, i)
        self.other.setCurrentIndex(min(1, len(cases) - 1))
        self.mode.addItem("Change (point − baseline)", False)
        self.mode.addItem("Ratio (point ÷ baseline)", True)
        for text, box in (("Point:", self.other), ("Baseline:", self.baseline), ("", self.mode)):
            if text:
                pick.addWidget(QLabel(text))
            pick.addWidget(box, 1)
        diff.addLayout(pick)
        self.diff_view = Viewport()
        diff.addWidget(self.diff_view, 1)
        self.tabs.addTab(diff_page, "Difference")

        # Summary.
        sum_page = QWidget()
        summ = QVBoxLayout(sum_page)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Point", "Minimum", "Maximum", "Maximum at (x, y, z)"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        summ.addWidget(self.table, 1)
        self.bars = Bars()
        summ.addWidget(self.bars)
        self.tabs.addTab(sum_page, "Summary")

        self.result.currentIndexChanged.connect(self.redraw)
        for box in (self.other, self.baseline, self.mode):
            box.currentIndexChanged.connect(self.redraw_difference)
        self.redraw()

    def item(self):
        key = self.result.currentData()
        return next((i for i in self.items if i.key == key), None)

    def redraw(self, *_) -> None:
        item = self.item()
        if item is None:
            self.message.setText("These runs have no result in common to compare.")
            return
        clim = compare.common_range(self.cases, item)
        for view, case in zip(self.views, self.cases, strict=True):
            view.show_result(case.mesh, case.results, item.field, item=item, clim=clim)
        self._summary(item)
        self.redraw_difference()

    def redraw_difference(self, *_) -> None:
        item = self.item()
        if item is None:
            return
        base = self.cases[self.baseline.currentData()]
        other = self.cases[self.other.currentData()]
        relative = bool(self.mode.currentData())
        try:
            d = compare.difference(base, other, item, relative=relative)
        except PuzzleError as exc:
            self.message.setText(str(exc))
            return
        shown = compare.difference_item(item, base.label, relative)
        self.diff_view.show_result(base.mesh, d, "difference", item=shown, deform=False)
        self.message.setText(
            f"{other.label} against {base.label}: {self.diff_view.shown['min']:.4g} … "
            f"{self.diff_view.shown['max']:.4g} {self.diff_view.shown.get('unit') or ''}".strip()
        )

    def _summary(self, item) -> None:
        rows = compare.summary(self.cases, item)
        self.table.setRowCount(len(rows))
        unit = ""
        bars = []
        for r, row in enumerate(rows):
            (lo, hi), unit = _display(np.array([row["min"], row["max"]]), item.unit)
            where = ""
            if "max_at" in row:
                xyz, length = _display(np.array(row["max_at"]), "length")
                where = ", ".join(f"{c:.4g}" for c in xyz) + f" {length}"
            cells = [row["label"], f"{lo:.4g}", f"{hi:.4g}", where]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
            bars.append((row["label"], float(hi)))
        unit = unit or ""
        for c, head in ((1, "Minimum"), (2, "Maximum")):
            self.table.setHorizontalHeaderItem(
                c, QTableWidgetItem(f"{head} ({unit})" if unit else head)
            )
        self.bars.set(bars, unit)

    def _follow(self, moved: Viewport) -> None:
        """Every other view takes the moved one's camera."""
        cam = moved.plotter.camera
        for view in self.views:
            if view is moved or view.plotter is None:
                continue
            c = view.plotter.camera
            c.position, c.focal_point, c.up = cam.position, cam.focal_point, cam.up
            c.view_angle = cam.view_angle
            view.plotter.renderer.ResetCameraClippingRange()
            view.view.refresh()
