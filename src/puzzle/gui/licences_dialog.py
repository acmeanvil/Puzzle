"""Help → Licences (ADR 0068 decision 9; ADR 0090 package 16, ADR-57): every package
this Puzzle carries, by environment, with its licence; the selected one's licence texts
below; the GPL's offer of the source on top. Read from `puzzle.licences.listing`."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from puzzle.gui.theme import role

HEADS = ["Environment", "Package", "Version", "Licence"]


class LicencesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        from PySide6.QtCore import Qt

        from puzzle import licences

        super().__init__(parent)
        self.setWindowTitle("Licences")
        self.setProperty("help", "page:guides/interface")
        self.rows = licences.listing()
        layout = QVBoxLayout(self)
        where = licences.shipped()
        said = (f"{len(self.rows)} packages, from this installation's licences folder."
                if where is not None else f"{len(self.rows)} packages, read from the "
                "environments Puzzle runs in (a developer's checkout: the installer's "
                "own list is written by scripts/collect_licences.py).")  # fmt: skip
        intro = QLabel("Puzzle is free software under the GNU General Public License, "
                       "version 3 or later. It runs these programs and libraries by "
                       "others, each under its own licence. " + said)  # fmt: skip
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find a package or a licence")
        self.filter.textChanged.connect(self._filter)
        layout.addWidget(self.filter)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(len(self.rows), len(HEADS))
        self.table.setHorizontalHeaderLabels(HEADS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for r, row in enumerate(self.rows):
            cells = [row.environment, row.name, row.version, row.licence or "(not recorded)"]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.table.itemSelectionChanged.connect(self._show)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        split.addWidget(self.table)
        split.addWidget(self.text)
        layout.addWidget(split, 1)
        offer = role(QLabel("Where the source is: the offer below."), "muted")
        layout.addWidget(offer)
        self.offer = QPlainTextEdit(licences.SOURCE_OFFER)
        self.offer.setReadOnly(True)
        self.offer.setMaximumHeight(140)
        layout.addWidget(self.offer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(900, 700)
        if self.rows:
            self.table.selectRow(0)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for r, row in enumerate(self.rows):
            hay = f"{row.environment} {row.name} {row.licence}".lower()
            self.table.setRowHidden(r, not all(w in hay for w in words))

    def _show(self) -> None:
        from puzzle import licences

        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) == 1:
            self.text.setPlainText(licences.text_of(self.rows[next(iter(rows))]))


def show_licences(window) -> LicencesDialog:
    dialog = LicencesDialog(window)
    window.run_dialog(dialog)
    return dialog
