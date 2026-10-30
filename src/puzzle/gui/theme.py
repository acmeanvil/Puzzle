"""Puzzle's look (ADR 0045): design tokens, the Qt palette and style sheet made from them,
and the 3D view's background.

Every colour the GUI uses is named here once, in a light, a dark and a high-contrast
set. `apply` puts Qt's Fusion style on every platform (so the style sheet styles every
control alike) and colours it from the tokens; Settings > Interface > Theme picks the
set (following the system by default) and > 3D background a soft gradient (default,
as Ansys draws it) or a plain colour.

Widgets take a role instead of an inline colour: `role(widget, "muted")` (or "title",
"error", "needed", "done"), styled by the one sheet below.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt

THEMES = [
    ("Follow the system", "system"),
    ("Light", "light"),
    ("Dark", "dark"),
    ("High contrast", "high_contrast"),
]
BACKGROUNDS = [("Soft gradient", "gradient"), ("Plain", "plain")]


@dataclass(frozen=True)
class Tokens:
    name: str
    surface: str  # windows and docks
    raised: str  # buttons, headers
    sunken: str  # inputs, trees, lists
    text: str
    muted: str  # hints, secondary text
    border: str
    accent: str  # selection, default buttons, checked tools
    accent_text: str
    focus: str  # the keyboard focus ring
    done: str
    needed: str
    warning: str
    error: str
    running: str
    view_top: str  # the 3D view's gradient, top to bottom
    view_bottom: str
    view_plain: str
    view_text: str  # legend and labels drawn in the 3D view
    # Tree and Properties headings by depth (the project, then sections, then what is
    # under them, ...); items that don't expand keep the plain text colour.
    levels: tuple[str, ...] = ()


LIGHT = Tokens(
    "light", surface="#f3f4f6", raised="#e8eaed", sunken="#ffffff", text="#1f2328",
    muted="#6a737d", border="#c9ced6", accent="#3d7bd9", accent_text="#ffffff",
    focus="#3d7bd9", done="#2e9d57", needed="#c08a1e", warning="#c08a1e", error="#c53030",
    running="#3d7bd9", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#1d5fbf", "#0f7a6b", "#7a4bc2", "#a15c00"),
)  # fmt: skip
DARK = Tokens(
    "dark", surface="#2b2d31", raised="#383b41", sunken="#1e1f22", text="#e6e8eb",
    muted="#8a96a3", border="#4a4e55", accent="#3d7bd9", accent_text="#ffffff",
    focus="#6aa1f0", done="#4caf50", needed="#d4a72c", warning="#d4a72c", error="#e5534b",
    running="#4f8fe6", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#6aa8ff", "#4fd1b5", "#c39bff", "#f0b45a"),
)  # fmt: skip
# The 3D view stays light in the dark theme: result colours read best on a light
# ground, as in Ansys and COMSOL. High contrast is black and white with yellow focus.
HIGH_CONTRAST = Tokens(
    "high_contrast", surface="#000000", raised="#000000", sunken="#000000", text="#ffffff",
    muted="#ffffff", border="#ffffff", accent="#ffd400", accent_text="#000000",
    focus="#ffd400", done="#7CFC00", needed="#ffd400", warning="#ffd400", error="#ff6b6b",
    running="#7fdbff", view_top="#ffffff", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#000000", levels=("#ffd400", "#7fdbff", "#7cfc00", "#ff9cf0"),
)  # fmt: skip
SETS = {"light": LIGHT, "dark": DARK, "high_contrast": HIGH_CONTRAST}

_current: Tokens = LIGHT


def current() -> Tokens:
    """The tokens in use (light until `apply` runs)."""
    return _current


def chosen(preference: str | None = None) -> Tokens:
    """The tokens Settings > Interface > Theme asks for; "system" follows the OS."""
    from puzzle.settings import current as settings

    name = preference or settings().get("interface.theme")
    if name in SETS:
        return SETS[name]
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - no application or an old Qt: light
        dark = False
    return DARK if dark else LIGHT


def palette(t: Tokens):
    from PySide6.QtGui import QColor, QPalette

    p = QPalette()
    roles = QPalette.ColorRole
    for role, colour in (
        (roles.Window, t.surface),
        (roles.WindowText, t.text),
        (roles.Base, t.sunken),
        (roles.AlternateBase, t.raised),
        (roles.Text, t.text),
        (roles.Button, t.raised),
        (roles.ButtonText, t.text),
        (roles.BrightText, t.error),
        (roles.Highlight, t.accent),
        (roles.HighlightedText, t.accent_text),
        (roles.ToolTipBase, t.raised),
        (roles.ToolTipText, t.text),
        (roles.PlaceholderText, t.muted),
        (roles.Link, t.accent),
        (roles.Mid, t.border),
        (roles.Midlight, t.raised),
        (roles.Dark, t.border),
        (roles.Light, t.raised),
    ):
        p.setColor(role, QColor(colour))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted))
    return p


def style_sheet(t: Tokens) -> str:
    """The application's one style sheet: roles, focus, tooltips, dock titles."""
    return f"""
QToolTip {{ color: {t.text}; background: {t.raised}; border: 1px solid {t.border};
            padding: 3px 6px; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="title"] {{ font-weight: 600; font-size: 14px; }}
QLabel[role="error"] {{ color: {t.error}; }}
QLabel[role="needed"] {{ color: {t.needed}; }}
QLabel[role="done"] {{ color: {t.done}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {t.focus}; }}
QDockWidget::title {{ background: {t.raised}; padding: 4px 6px; }}
QPushButton:default {{ background: {t.accent}; color: {t.accent_text};
                       border: 1px solid {t.accent}; border-radius: 3px; padding: 4px 12px; }}
"""


def role(widget, name: str):
    """Give a widget a styled role (muted, title, error, needed, done)."""
    widget.setProperty("role", name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def apply(app=None, preference: str | None = None) -> Tokens:
    """Fusion, the palette and the style sheet for the chosen theme, on the app."""
    global _current
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    t = chosen(preference)
    _current = t
    if app is None:
        return t
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setStyleSheet(style_sheet(t))
    return t


def view_background(plotter, t: Tokens | None = None, kind: str | None = None) -> None:
    """The 3D view's background: a soft gradient (default) or plain (Settings >
    Interface > 3D background)."""
    from puzzle.settings import current as settings

    t = t or current()
    kind = kind or settings().get("interface.view_background")
    if kind == "plain":
        plotter.set_background(t.view_plain)
    else:
        plotter.set_background(t.view_bottom, top=t.view_top)


def colour_levels(tree, depth_of_top: int = 0) -> None:
    """Headings coloured by depth in a QTreeWidget (the model tree, Properties): an
    item that has children takes its level's colour and a medium weight; one without
    keeps the plain text colour. Settings > Interface > Heading colours turns it off."""
    from PySide6.QtGui import QBrush, QColor

    from puzzle.settings import current as settings

    on = settings().get("interface.heading_colours") is not False
    levels = current().levels
    stack = [(tree.invisibleRootItem(), depth_of_top - 1)]
    while stack:
        parent, depth = stack.pop()
        for i in range(parent.childCount()):
            item = parent.child(i)
            font = item.font(0)
            if on and item.childCount() and levels:
                item.setForeground(0, QBrush(QColor(levels[(depth + 1) % len(levels)])))
                font.setWeight(font.Weight.DemiBold)
            else:
                item.setData(0, Qt.ItemDataRole.ForegroundRole, None)  # the palette's
                font.setWeight(font.Weight.Normal)
            item.setFont(0, font)
            stack.append((item, depth + 1))
