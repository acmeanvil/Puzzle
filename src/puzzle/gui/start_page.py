"""The start page: what the window shows before a project is open. The picture, New
and Open, and the projects opened lately (kept in `<user data dir>/recent.json`)."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ART = Path(__file__).parent / "art"
KEEP = 10  # recent projects remembered


def _recent_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "recent.json"


def recent() -> list[Path]:
    """Projects opened lately, newest first, those still on disk only."""
    try:
        paths = [Path(p) for p in json.loads(_recent_path().read_text())]
    except (OSError, ValueError, TypeError):
        return []
    return [p for p in paths if p.is_dir()]


def remember(root: Path) -> None:
    """Put a project at the top of the recent list."""
    root = Path(root).resolve()
    paths = [root, *(p for p in recent() if p.resolve() != root)][:KEEP]
    path = _recent_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([str(p) for p in paths], indent=1))
    except OSError:  # a read-only home: the list is a convenience only
        pass


def app_icon():
    from PySide6.QtGui import QIcon

    return QIcon(str(ART / "icon.png"))


class Picture(QWidget):
    """The start picture, drawn from its SVG at whatever size the page gives it."""

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtSvg import QSvgRenderer

        self.renderer = QSvgRenderer(str(ART / "start.svg"), self)
        self.setMinimumSize(240, 240)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(460, 460)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        from PySide6.QtGui import QPainter

        side = min(self.width(), self.height())
        painter = QPainter(self)
        painter.drawPixmap((self.width() - side) // 2, (self.height() - side) // 2,
                           self._drawn(side))  # fmt: skip
        painter.end()

    def _drawn(self, side: int):
        """The picture at this size: a few thousand triangles take ~0.25 s to paint, so
        it is drawn once per size, not on every repaint."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter, QPixmap

        ratio = self.devicePixelRatioF()
        key = (side, ratio)
        if getattr(self, "_cache", (None,))[0] != key:
            pixmap = QPixmap(round(side * ratio), round(side * ratio))
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.renderer.render(painter)
            painter.end()
            pixmap.setDevicePixelRatio(ratio)
            self._cache = (key, pixmap)
        return self._cache[1]


class StartPage(QWidget):
    """New, Open and recent projects beside the picture."""

    def __init__(self, window) -> None:
        super().__init__()
        from puzzle.gui import theme

        self.window = window
        row = QHBoxLayout(self)
        row.setContentsMargins(48, 32, 48, 32)
        row.setSpacing(40)
        self.picture = Picture()
        row.addWidget(self.picture, 3)

        column = QVBoxLayout()
        column.addStretch(1)
        name = QLabel("Puzzle")
        font = name.font()
        font.setPointSizeF(font.pointSizeF() * 3)
        font.setWeight(font.Weight.DemiBold)
        name.setFont(font)
        column.addWidget(name)
        tagline = theme.role(QLabel(
            "One model, every solver: CalculiX, Code_Aster, Elmer, FEniCSx, "
            "Code_Saturne and OpenFOAM."
        ), "muted")  # fmt: skip
        tagline.setWordWrap(True)
        column.addWidget(tagline)
        column.addSpacing(18)

        buttons = QHBoxLayout()
        self.new = QPushButton("New project…")
        self.new.setDefault(True)
        self.new.clicked.connect(window._new_dialog)
        self.open_button = QPushButton("Open project…")
        self.open_button.clicked.connect(window._open_dialog)
        buttons.addWidget(self.new)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        column.addSpacing(18)

        self.recent_title = theme.role(QLabel("Recent projects"), "title")
        column.addWidget(self.recent_title)
        self.recent = QListWidget()
        self.recent.setMaximumHeight(240)
        self.recent.setSpacing(3)
        self.recent.setFrameShape(QListWidget.Shape.NoFrame)
        self.recent.itemClicked.connect(self._open_recent)
        column.addWidget(self.recent)
        self.none = theme.role(QLabel("Projects you open show up here."), "muted")
        column.addWidget(self.none)
        column.addStretch(2)
        holder = QWidget()
        holder.setLayout(column)
        holder.setMaximumWidth(460)
        row.addWidget(holder, 2, Qt.AlignmentFlag.AlignVCenter)
        self.fill()

    def fill(self) -> None:
        """List the recent projects: the folder's name, its path beneath it."""
        self.recent.clear()
        paths = recent()
        for p in paths:
            item = QListWidgetItem(f"{p.name}\n{p.parent}")
            item.setToolTip(str(p))
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.recent.addItem(item)
        self.recent.setVisible(bool(paths))
        self.none.setVisible(not paths)

    def _open_recent(self, item: QListWidgetItem) -> None:
        self.window.open(Path(item.data(Qt.ItemDataRole.UserRole)))
