"""Toolbar icons (ADR 0031), behind one lookup: `icon(name)`.

These are an interim set, simple 24-pixel line drawings made for Puzzle (GPL-3.0 with
the rest of it). The icon set proper is chosen with the Qt polish ADR (plan Phase F);
swapping it means replacing the drawings here, nothing else. Strokes use the palette's
text colour, so they read on light and dark themes.
"""

from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
)
DRAWINGS = {
    # Project
    "new": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7h6l2 2h10v10H3z"/>',
    "undo": '<path d="M9 7L4 12l5 5"/><path d="M4 12h11a5 5 0 0 1 0 10h-3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3'
    'M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    # Model
    "mesh": '<path d="M4 20L12 4l8 16z"/><path d="M8 12h8M12 4v16M8 12l4 8 4-8"/>',
    "run": '<path d="M7 4l13 8-13 8z"/>',
    # Materials
    "material": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9'
    'M12 12L4 7.5"/>',
    "builder": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M9 12h6M12 9v6"/>',
    # Selection
    "pick": '<path d="M5 3l14 7-6 2-2 6z"/><path d="M13 12l6 6"/>',
    # Docks
    "agent": '<rect x="4" y="6" width="16" height="12" rx="3"/><circle cx="9" cy="12" r="1.2"/>'
    '<circle cx="15" cy="12" r="1.2"/><path d="M12 3v3"/>',
    "messages": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    # View
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'
    '<rect x="8" y="8" width="8" height="8"/>',
    "iso": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    "edges": '<rect x="4" y="4" width="16" height="16"/><path d="M4 12h16M12 4v16"/>',
    "wireframe": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l16 9M20 7.5l-16 9'
    'M12 3v18"/>',
    "projection": '<path d="M3 8l9-4 9 4-9 4z"/><path d="M3 8v8l9 4 9-4V8"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "extremes": '<path d="M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"/>',
    "symbols": '<path d="M12 3v12M8 11l4 4 4-4"/><path d="M5 20h14"/>'
    '<path d="M7 20l2-3M11 20l2-3M15 20l2-3"/>',
    "section": '<path d="M4 7l8-4 8 4v10l-8 4-8-4z"/><path d="M3 12h18" stroke-dasharray="2 2"/>',
    "clear": '<path d="M6 6l12 12M18 6L6 18"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="1"/><path d="M4 16V4h12"/>',
}


def _colour() -> str:
    app = QApplication.instance()
    if app is None:
        return "#c8ccd2"
    return app.palette().color(QPalette.ColorRole.WindowText).name()


@cache
def icon(name: str) -> QIcon:
    """The icon for an action; an empty icon for an unknown name (the label still shows)."""
    from PySide6.QtSvg import QSvgRenderer

    body = DRAWINGS.get(name)
    if body is None:
        return QIcon()
    svg = (_HEAD.format(c=_colour()) + body + "</svg>").encode()
    renderer = QSvgRenderer(QByteArray(svg))
    out = QIcon()
    for size in (16, 24, 32, 48):
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
