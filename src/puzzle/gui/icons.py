"""Toolbar icons (ADR 0031, 0045), behind one lookup: `icon(name)`.

The set is Tabler's outline icons (MIT, `gui/tabler/LICENSE`), vendored as SVG files
under `gui/tabler/` and named in `TABLER`. Engineering icons no set has (a mesh, a
wireframe) are Puzzle's own drawings in `DRAWINGS`, in the same 24-pixel grid, stroke
and caps, so the two read as one set. Strokes take the theme's text colour, so they
read on light, dark and high-contrast themes alike.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
)
STROKE = "1.75"  # Tabler draws at 2; a little lighter reads better at 20-24 px
TABLER_DIR = Path(__file__).with_name("tabler")
# Action -> Tabler icon file (gui/tabler/<name>.svg).
TABLER = {
    "new": "file-plus",
    "open": "folder-open",
    "undo": "arrow-back-up",
    "settings": "settings",
    "run": "player-play",
    "material": "cube",
    "builder": "cube-plus",
    "pick": "pointer",
    "agent": "robot",
    "messages": "message-2",
    "fit": "maximize",
    "iso": "box",
    "edges": "border-all",
    "projection": "perspective",
    "camera": "camera",
    "extremes": "arrows-vertical",
    "symbols": "arrow-down-bar",
    "section": "slice",
    "clear": "x",
    "copy": "copy",
}
# Puzzle's own engineering icons, in Tabler's grid and stroke.
DRAWINGS = {
    "mesh": '<path d="M4 20L12 4l8 16z"/><path d="M8 12h8M12 4v16M8 12l4 8 4-8"/>',
    "wireframe": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l16 9M20 7.5l-16 9'
    'M12 3v18"/>',
}


def _colour() -> str:
    app = QApplication.instance()
    if app is None:
        return "#c8ccd2"
    return app.palette().color(QPalette.ColorRole.WindowText).name()


def icon(name: str) -> QIcon:
    """The icon for an action, drawn in the theme's text colour; an empty icon for an
    unknown name (the label still shows)."""
    return _icon(name, _colour())


def _svg(name: str, colour: str) -> bytes | None:
    """The icon's SVG in `colour`: Tabler's file, else Puzzle's own drawing."""
    if name in TABLER:
        text = (TABLER_DIR / f"{TABLER[name]}.svg").read_text()
        text = text.replace("currentColor", colour).replace(
            'stroke-width="2"', f'stroke-width="{STROKE}"'
        )
        return text.encode()
    body = DRAWINGS.get(name)
    if body is None:
        return None
    return (_HEAD.format(c=colour) + body + "</svg>").encode()


@cache
def _icon(name: str, colour: str) -> QIcon:
    from PySide6.QtSvg import QSvgRenderer

    svg = _svg(name, colour)
    if svg is None:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg))
    out = QIcon()
    for size in (16, 20, 24, 32, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        out.addPixmap(pixmap)
    return out


# Tree states (ADR 0033): a shape and a colour each, so they read without colour too.
STATE_DRAWINGS = {
    "complete": ("#4caf50", '<circle cx="12" cy="12" r="4" fill="{c}" stroke="none"/>'),
    "up_to_date": ("#4caf50", '<path d="M5 12l5 5 9-10"/>'),
    "missing": ("#e5534b", '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>'),
    "failed": ("#e5534b", '<circle cx="12" cy="12" r="9"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>'),
    "out_of_date": ("#d4a72c", '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>'),
    "running": ("#4f8fe6", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "not_run": ("#8a94a3", '<circle cx="12" cy="12" r="7"/>'),
    "unused": ("#8a94a3", '<path d="M6 12h12"/>'),
}


@cache
def state_icon(state: str) -> QIcon:
    """The small icon a tree item shows for its state."""
    from PySide6.QtSvg import QSvgRenderer

    colour, body = STATE_DRAWINGS.get(state, STATE_DRAWINGS["complete"])
    head = _HEAD.format(c=colour).replace('stroke-width="1.8"', 'stroke-width="2.4"')
    svg = (head + body.format(c=colour) + "</svg>").encode()
    renderer = QSvgRenderer(QByteArray(svg))
    out = QIcon()
    for size in (12, 16, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        out.addPixmap(pixmap)
    return out
