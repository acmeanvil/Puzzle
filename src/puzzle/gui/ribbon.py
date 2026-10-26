"""One ribbon for the Geometry, Mesh and Results tools (ADR 0045, step 2).

Groups side by side, each a grid of buttons in at most two rows under a small title.
Every button has the same height and fills its grid cell, so columns line up and the
edges are straight. A one-button group sits on the top row, like the others' first row.
Buttons never shrink below their words: when the view is too narrow for every group,
groups fold from the right into one drop-down each, named after the group, as Office
and Ansys ribbons do. The look is one style sheet here, not a copy per ribbon.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

HEIGHT = 24  # every ribbon button, in pixels
STYLE = (  # palette colours, so it follows the theme (ADR 0045)
    "QLabel#ribbon_title { color: palette(placeholder-text); font-size: 11px;"
    " font-weight: 600; }"
    "QFrame#ribbon_group { border: none; border-right: 1px solid palette(mid); }"
    "QToolButton { padding: 0px 8px; background: palette(button);"
    " border: 1px solid palette(mid); border-radius: 3px; }"
    "QToolButton:hover { border-color: palette(highlight); }"
    "QToolButton:pressed, QToolButton:checked { background: palette(highlight);"
    " color: palette(highlighted-text); border-color: palette(highlight); }"
    "QToolButton:disabled { color: palette(placeholder-text); }"
)


class Ribbon(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ribbon")
        self.setStyleSheet(STYLE)
        self.bar = QHBoxLayout(self)
        self.bar.setContentsMargins(4, 2, 4, 2)
        self.bar.setSpacing(6)
        # The ribbon's width comes from the window; fold() makes the groups fit it.
        self.bar.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.bar.addStretch()
        self.groups: list[tuple[QFrame, QWidget, list[QToolButton]]] = []

    @staticmethod
    def button(text: str, tip: str = "", checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setCheckable(checkable)
        b.setFixedHeight(HEIGHT)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        b.setMinimumWidth(b.sizeHint().width())  # never "C…r": fold the group instead
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return b

    def group(self, title: str, buttons: list[QToolButton]) -> QFrame:
        """A titled group: up to two rows, filled left to right."""
        box = QFrame()
        box.setObjectName("ribbon_group")
        column = QVBoxLayout(box)
        column.setContentsMargins(2, 0, 8, 0)
        column.setSpacing(1)
        name = QLabel(title)
        name.setObjectName("ribbon_title")
        column.addWidget(name)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
        per_row = max(1, (len(buttons) + 1) // 2)
        for n, b in enumerate(buttons):
            grid.addWidget(b, n // per_row, n % per_row)
        for row in range(2):  # two rows' room always, so every group is as tall
            grid.setRowMinimumHeight(row, HEIGHT)
        column.addLayout(grid)
        column.addStretch()
        self.bar.insertWidget(self.bar.count() - 1, box)
        folded = self.button(f"{title} ▾", f"The {title} tools")
        folded.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        folded.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        folded.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        menu = QMenu(folded)
        menu.aboutToShow.connect(lambda m=menu, bs=buttons: self._fill(m, bs))
        folded.setMenu(menu)
        # Folded, it sits where the groups' top row is: under a title's height.
        holder = QWidget()
        down = QVBoxLayout(holder)
        name.ensurePolished()
        down.setContentsMargins(0, name.sizeHint().height() + column.spacing(), 0, 0)
        down.addWidget(folded)
        down.addStretch()
        holder.hide()
        self.bar.insertWidget(self.bar.count() - 1, holder)
        self.groups.append((box, holder, buttons))
        self.fold()
        return box

    @staticmethod
    def _fill(menu: QMenu, buttons: list[QToolButton]) -> None:
        """A folded group's menu: its buttons as items, in order, ticks kept."""
        menu.clear()
        for b in buttons:
            if b.isHidden():
                continue  # a button its panel hides for now
            item = QAction(b.text(), menu)
            item.setToolTip(b.toolTip())
            item.setEnabled(b.isEnabled())
            if b.isCheckable():
                item.setCheckable(True)
                item.setChecked(b.isChecked())
            item.triggered.connect(lambda _=False, b=b: b.click())
            menu.addAction(item)

    def fold(self) -> None:
        """Fold groups, from the right, until the rest fit the width there is."""
        room = self.width() - 12
        margins = self.bar.spacing()
        wide = [box.sizeHint().width() + margins for box, _, _ in self.groups]
        narrow = [f.sizeHint().width() + margins for _, f, _ in self.groups]
        cut = len(self.groups)
        while cut > 0 and sum(wide[:cut]) + sum(narrow[cut:]) > room:
            cut -= 1
        for n, (box, folded, _) in enumerate(self.groups):
            box.setVisible(n < cut)
            folded.setVisible(n >= cut)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.fold()

    def minimumSizeHint(self):  # noqa: N802 - Qt's name
        """As narrow as every group folded: the ribbon never holds the window open."""
        hint = super().minimumSizeHint()
        hint.setWidth(sum(f.sizeHint().width() + self.bar.spacing() for _, f, _ in self.groups))
        return hint
