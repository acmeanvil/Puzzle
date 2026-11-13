"""A stacked widget that is as large as the page it shows, not its largest page.

Qt's QStackedWidget takes the largest size hint of every page, hidden ones included, so
a page not on screen held its area: the start page kept the 3D view's area from going
under 752 px with a project open, and a closed Legend panel kept the Properties dock at
its height (the tree above could no longer be dragged larger).
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QStackedWidget


class ShownStack(QStackedWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    # A page's set minimum counts as well as its hint, as Qt's layouts take them.
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        page = self.currentWidget()
        if page is None:
            return super().sizeHint()
        return page.sizeHint().expandedTo(page.minimumSize())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint()
        return page.minimumSizeHint().expandedTo(page.minimumSize())
