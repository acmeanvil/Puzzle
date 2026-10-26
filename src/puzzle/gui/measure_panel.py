"""Measure (ADR 0042): exact measurements of what is picked, in the Properties dock.

Opened from the Geometry ribbon's Inspect group or the pick bar's Measure button. It
turns picking on and lists, for the picks so far, what `geometry.measure` gives (length,
area, volume, radius, centroid, the distance and angle between two picks, totals) in
the display units, updated as the picks change. Copy puts the rows on the clipboard as
tab-separated text; Close returns the dock to Properties.
"""

from __future__ import annotations

import contextlib

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role

HINT = (
    "Double-click in the view to pick (Shift adds). One pick: its size and position. "
    "Two: the distance and angle between them. The filter in the pick bar says what a "
    "click picks: faces, edges, bodies or points."
)


class MeasurePanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.cache: dict = {}  # (geometry, content) -> named geometry
        layout = QVBoxLayout(self)
        title = QLabel("Measure")
        role(title, "title")
        self.hint = QLabel(HINT)
        self.hint.setWordWrap(True)
        role(self.hint, "muted")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Measure", "Value"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        buttons = QHBoxLayout()
        copy, close = QPushButton("Copy"), QPushButton("Close")
        copy.clicked.connect(self.copy)
        close.clicked.connect(self.close_panel)
        buttons.addWidget(copy)
        buttons.addStretch()
        buttons.addWidget(close)
        for w in (title, self.hint, self.message, self.table):
            layout.addWidget(w)
        layout.addLayout(buttons)
        window.viewport.picked_changed.connect(self.update)

    def rows(self) -> list[tuple[str, str]]:
        return [
            (self.table.item(r, 0).text(), self.table.item(r, 1).text())
            for r in range(self.table.rowCount())
        ]

    def update(self, *_args) -> None:
        from puzzle.geometry.measure import rows, text

        self.table.setRowCount(0)
        try:
            found = self.window.measure_picked(self.cache)
        except Exception as exc:  # a message for the user, never a crash
            self.message.setText(str(exc))
            return
        if found is None:
            self.message.setText("Nothing picked yet.")
            return
        self.message.setText("")
        shown = [(label, text(value, unit)) for label, value, unit in rows(found)]
        self.table.setRowCount(len(shown))
        for r, (label, value) in enumerate(shown):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            self.table.setItem(r, 1, QTableWidgetItem(value))

    def copy(self) -> None:
        QApplication.clipboard().setText("\n".join(f"{a}\t{b}" for a, b in self.rows()))

    def close_panel(self) -> None:
        with contextlib.suppress(RuntimeError, TypeError):  # already disconnected
            self.window.viewport.picked_changed.disconnect(self.update)
        self.window.measuring = None
        self.window.show_tool(None)
