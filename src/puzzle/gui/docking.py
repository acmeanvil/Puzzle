"""The window's panels (ADR 0031, 0045): one module between the window and Qt's docks.

The window makes its panels, groups them, saves and restores the layout, and offers
named layouts and auto-hide only through `Docks`. A different docking library (the Qt
Advanced Docking System, say, once it is on conda-forge and stops pinning PySide6) can
replace Qt's `QDockWidget` by changing this module alone.

- **Tab groups:** `tab(a, b)` puts two panels in one place as tabs (Agent and Advisor).
- **Named layouts:** built-in ones the window registers (`builtin`), and the user's own,
  saved as files under `<user data dir>/layouts/` (Window → Layouts).
- **Auto-hide:** a side panel can hide into a slim bar on its window edge; clicking its
  name there shows it over its side, and it hides again once focus leaves it (a click in
  the 3D view). Which panels auto-hide is part of a saved layout.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QDockWidget, QToolBar, QToolButton

STATE, AUTO = b"\n--state--\n", b"\n--auto--\n"


def layouts_dir() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "layouts"


class SideButton(QToolButton):
    """A panel's name written up the window's edge, as auto-hidden panels show."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        size = super().sizeHint()
        return QSize(size.height(), size.width() + 8)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        if self.underMouse() or self.isDown():
            painter.fillRect(self.rect(), self.palette().button())
        painter.setPen(self.palette().buttonText().color())
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(0, 0, self.height(), self.width(), Qt.AlignmentFlag.AlignCenter,
                         self.text())  # fmt: skip
        painter.end()


class Docks:
    def __init__(self, window) -> None:
        self.window = window
        self.panels: dict[str, QDockWidget] = {}
        self.builtins: dict[str, tuple[str, Callable[[], None]]] = {}  # key -> (name, apply)
        self.auto: set[str] = set()  # panels that hide into the side bar
        self.peeking: str | None = None  # an auto-hidden panel shown for now
        self.bars: dict[Qt.DockWidgetArea, QToolBar] = {}
        QApplication.instance().focusChanged.connect(self._focus_moved)

    # --- panels -----------------------------------------------------------------------

    def add(self, key: str, title: str, widget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self.window)
        dock.setObjectName(title)  # saved layouts name docks by this
        dock.setWidget(widget)
        dock.setProperty("panel", key)
        self.window.addDockWidget(area, dock)
        self.panels[key] = dock
        return dock

    def split(self, first: str, second: str, orientation=Qt.Orientation.Vertical) -> None:
        self.window.splitDockWidget(self.panels[first], self.panels[second], orientation)

    def tab(self, first: str, second: str) -> None:
        """`second` in a tab beside `first`, sharing its place."""
        self.window.tabifyDockWidget(self.panels[first], self.panels[second])

    def show(self, key: str, front: bool = True) -> None:
        """Open a panel (an auto-hidden one shows over its side), its tab to the front."""
        dock = self.panels[key]
        if key in self.auto:
            self._peek(key)
            return
        dock.show()
        if front:
            dock.raise_()

    def hide(self, key: str) -> None:
        self.panels[key].hide()

    def area(self, key: str) -> Qt.DockWidgetArea:
        return self.window.dockWidgetArea(self.panels[key])

    # --- auto-hide --------------------------------------------------------------------

    def set_auto_hide(self, key: str, on: bool) -> None:
        """A side panel hidden into a slim bar on its edge (on), or back in place."""
        if on == (key in self.auto):
            return
        dock = self.panels[key]
        if on:
            self.auto.add(key)
            dock.hide()
        else:
            self.auto.discard(key)
            if self.peeking == key:
                self.peeking = None
            dock.show()
        self._rebuild_bars()

    def _bar(self, area: Qt.DockWidgetArea) -> QToolBar:
        bar = self.bars.get(area)
        if bar is None:
            bar = QToolBar(self.window)
            bar.setObjectName(f"auto-hide-{int(area.value)}")
            bar.setMovable(False)
            bar.setFloatable(False)
            bar.setOrientation(Qt.Orientation.Vertical)
            edge = (Qt.ToolBarArea.LeftToolBarArea if area == Qt.DockWidgetArea.LeftDockWidgetArea
                    else Qt.ToolBarArea.RightToolBarArea)  # fmt: skip
            self.window.addToolBar(edge, bar)
            self.bars[area] = bar
        return bar

    def _rebuild_bars(self) -> None:
        for bar in self.bars.values():
            bar.clear()
            bar.hide()
        for key in sorted(self.auto, key=list(self.panels).index):
            dock = self.panels[key]
            bar = self._bar(self.area(key))
            button = SideButton()
            button.setText(dock.windowTitle())
            button.setToolTip(f"Show the {dock.windowTitle()} panel")
            button.clicked.connect(lambda _=False, k=key: self._peek(k))
            bar.addWidget(button)
            bar.show()

    def _peek(self, key: str) -> None:
        """Show an auto-hidden panel until focus leaves it."""
        if self.peeking and self.peeking != key:
            self.panels[self.peeking].hide()
        self.peeking = key
        dock = self.panels[key]
        dock.show()
        dock.raise_()
        dock.widget().setFocus()

    def _focus_moved(self, _old, new) -> None:
        if self.peeking is None or new is None:
            return
        dock = self.panels[self.peeking]
        widget = new
        while widget is not None:  # still inside the panel (or a popup of it)
            if widget is dock:
                return
            widget = widget.parentWidget()
        if new.window() is not self.window:
            return  # a dialog or menu over the window: keep it open meanwhile
        dock.hide()
        self.peeking = None

    # --- layouts ----------------------------------------------------------------------

    def state(self) -> bytes:
        """The window's geometry, the docks' state and the auto-hidden panels."""
        w = self.window
        auto = ",".join(sorted(self.auto)).encode()
        return bytes(w.saveGeometry()) + STATE + bytes(w.saveState()) + AUTO + auto

    def restore(self, data: bytes, geometry: bool = True) -> bool:
        head, _, auto = data.partition(AUTO)
        shape, _, state = head.partition(STATE)
        if not state or not self.window.restoreState(QByteArray(state)):
            return False
        if geometry and shape:
            self.window.restoreGeometry(QByteArray(shape))
        self._on_screen()
        keys = {k for k in auto.decode(errors="ignore").split(",") if k in self.panels}
        for key in list(self.auto - keys):
            self.set_auto_hide(key, False)
        for key in keys - self.auto:
            self.set_auto_hide(key, True)
        return True

    def _on_screen(self) -> None:
        """A floating panel whose saved place is on no screen now (a monitor unplugged)
        goes back into the window."""
        from PySide6.QtGui import QGuiApplication

        screens = [sc.availableGeometry() for sc in QGuiApplication.screens()]
        for dock in self.panels.values():
            if dock.isFloating() and not any(g.intersects(dock.geometry()) for g in screens):
                dock.setFloating(False)

    def builtin(self, key: str, name: str, apply: Callable[[], None]) -> None:
        self.builtins[key] = (name, apply)

    def names(self) -> list[tuple[str, str]]:
        """(name, key) of every layout: the built-in ones, then the user's, by name."""
        out = [(name, key) for key, (name, _) in self.builtins.items()]
        folder = layouts_dir()
        if folder.is_dir():
            for path in sorted(folder.glob("*.layout")):
                out.append((_read_name(path), f"user:{path.stem}"))
        return out

    def apply(self, key: str) -> bool:
        if key in self.builtins:
            for panel in list(self.auto):
                self.set_auto_hide(panel, False)
            self.builtins[key][1]()
            return True
        path = layouts_dir() / f"{key.removeprefix('user:')}.layout"
        try:
            data = path.read_bytes()
        except OSError:
            return False
        _, _, body = data.partition(b"\n")
        return self.restore(body, geometry=False)

    def save(self, name: str) -> str:
        """The current layout under `name` (replacing one of that name); its key."""
        folder = layouts_dir()
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "layout"
        (folder / f"{slug}.layout").write_bytes(name.encode() + b"\n" + self.state())
        return f"user:{slug}"

    def delete(self, key: str) -> None:
        if key.startswith("user:"):
            (layouts_dir() / f"{key.removeprefix('user:')}.layout").unlink(missing_ok=True)


def _read_name(path: Path) -> str:
    try:
        with path.open("rb") as f:
            return f.readline().decode(errors="ignore").strip() or path.stem
    except OSError:
        return path.stem
