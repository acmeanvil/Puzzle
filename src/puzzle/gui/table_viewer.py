"""The Table viewer (ADR 0064 item 6): the selected table operator's rows, in their shown
units, with the count and each column's unit. A tab beside Plots.

Clicking a header sorts the view; on a Sort operator it sets that operator's order
instead (one Edit), so the node and the view never disagree. Double-clicking a cell of a
Filter rows operator's input keeps the rows equal to it. Only the first rows are drawn
(`ROWS`); the count is the whole table's.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from puzzle.errors import PuzzleError

ROWS = 500  # rows drawn: enough to read, never the million a result table can hold


class TableViewer(QWidget):
    def __init__(self, window) -> None:
        from puzzle.gui import theme

        super().__init__()
        self.window = window
        self.op = None
        self.table = None
        column = QVBoxLayout(self)
        self.title = theme.role(QLabel("Select a table operator to see its rows"), "muted")
        self.title.setWordWrap(True)
        column.addWidget(self.title)
        self.grid = QTableWidget(0, 0)
        self.grid.verticalHeader().hide()
        self.grid.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.grid.horizontalHeader().sectionClicked.connect(self._header)
        column.addWidget(self.grid, 1)

    def show_table(self, op) -> bool:
        """Show an operator's table; False (and nothing changed) when it has none."""
        from puzzle.operators import TYPES
        from puzzle.operators import evaluate as ev

        outs = TYPES[op.type].outputs
        if not outs or outs[0].type != "table":
            return False
        self.op = op
        s = self.window.session
        try:
            got = ev.value(ev.Context(s.project, self.window._store(), root=s.root), op)
        except PuzzleError as exc:
            self.table = None
            self.title.setText(f"{op.name}: {exc.message}")
            self.grid.setRowCount(0)
            self.grid.setColumnCount(0)
            return True
        self.table = got
        self._draw()
        return True

    def _draw(self) -> None:
        from puzzle.operators.frames import shown_frame

        t = self.table
        frame = shown_frame(t)  # display (or converted) units, headers "name [unit]"
        h = t.frame.height
        more = f" (the first {ROWS} shown)" if h > ROWS else ""
        self.title.setText(f"{self.op.name}: {h} row{'s' if h != 1 else ''} × "
                           f"{t.frame.width}{more}")  # fmt: skip
        self.grid.setColumnCount(frame.width)
        self.grid.setHorizontalHeaderLabels(frame.columns)
        rows = frame.head(ROWS).rows()
        self.grid.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                text = "" if v is None else f"{v:.6g}" if isinstance(v, float) else str(v)
                self.grid.setItem(r, c, QTableWidgetItem(text))
        self.grid.resizeColumnsToContents()

    def _header(self, index: int) -> None:
        if self.table is None:
            return
        name = self.table.columns[index]
        if self.op is not None and self.op.type == "sort":  # the operator's own order
            descending = self.op.by == [name] and not self.op.descending
            self.window.submit_batch([{"command": "edit", "object": str(self.op.id), "ops": [
                {"op": "set", "path": "by", "value": [name]},
                {"op": "set", "path": "descending", "value": descending}]}])  # fmt: skip
            self.show_table(self.window.session.project.objects[self.op.id])
            return
        self.grid.sortItems(index)  # the view only
