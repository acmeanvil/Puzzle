"""Help → Element library (ADR 0082): every solver's elements, grouped by what they
model, filtered by solver, family and study; the same listing as `puzzle elements`."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)


class ElementLibrary(QDialog):
    def __init__(self, parent=None) -> None:
        from puzzle import elements

        super().__init__(parent)
        self.setWindowTitle("Element library")
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.solver = self._combo(bar, "Solver", [("Every solver", None)]
                                  + [(n, k) for k, n in elements.SOLVER_NAMES.items()])  # fmt: skip
        self.family = self._combo(bar, "Family", [("Every family", None)]
                                  + [(t, k) for k, t in elements.FAMILIES.items()])  # fmt: skip
        studies = [(t.capitalize(), k) for k, t in elements.STUDIES.items()]
        self.study = self._combo(bar, "Study", [("Every study", None), *studies])
        bar.addStretch(1)
        layout.addLayout(bar)
        self.text = QTextBrowser(self)
        self.text.setOpenExternalLinks(True)
        layout.addWidget(self.text, 1)
        self.show_listing()

    def _combo(self, bar, label: str, items) -> QComboBox:
        bar.addWidget(QLabel(label, self))
        box = QComboBox(self)
        for text, value in items:
            box.addItem(text, value)
        box.currentIndexChanged.connect(self.show_listing)
        bar.addWidget(box)
        return box

    def show_listing(self) -> None:
        from puzzle import elements

        self.text.setMarkdown(elements.listing(self.solver.currentData(),
                                               self.family.currentData(),
                                               self.study.currentData()))  # fmt: skip


def show_library(window) -> ElementLibrary:
    dialog = ElementLibrary(window)
    window._element_library = dialog  # kept, so Qt doesn't drop it
    dialog.show()
    return dialog
