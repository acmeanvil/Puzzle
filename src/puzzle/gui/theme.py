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
DENSITIES = [("Comfortable", "comfortable"), ("Compact", "compact")]
TEXT_SIZES = [("As the system", "system"), ("Larger", "larger"), ("Largest", "largest")]
TEXT_SCALE = {"system": 1.0, "larger": 1.15, "largest": 1.3}
# Padding in pixels (on the 4-pixel grid) per density: (controls, tree rows, buttons).
PADDING = {"comfortable": (4, 3, 12), "compact": (2, 1, 8)}


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
    # A contact's initial state in the view (ADR 0056): closed, near, open, far,
    # overhanging. The view stays light in every theme.
    contact: tuple[str, ...] = ("#2e9d57", "#d4b106", "#f0883e", "#c53030", "#8250df")
    # Over the 3D view (its bars, labels) and in the window's chrome.
    overlay: str = "rgba(40, 44, 52, 160)"
    overlay_text: str = "#ffffff"
    overlay_checked: str = "rgba(79, 143, 230, 180)"
    separator: str = "#3c3f44"
    banner_error: str = "rgba(229, 83, 75, 40)"
    banner_error_text: str = "#c53030"
    banner_info: str = "rgba(79, 143, 230, 30)"
    rule: str = "rgba(128, 128, 128, 90)"
    # What the 3D view draws (light in every theme, as its ground): geometry before
    # meshing, mesh edges, beams, highlights, picks, construction marks.
    geometry_face: str = "#c9d3dd"
    geometry_edge: str = "#7a8794"
    geometry_line: str = "#5b6875"
    mesh_edge: str = "#4a5560"
    faded: str = "#b8c2cc"
    beam_face: str = "#9aa7b4"
    beam_edge: str = "#3d4650"
    highlight: str = "#ff8c00"
    picked: str = "#e53e3e"
    marked: str = "#ffcc33"
    path: str = "#4f8fe6"
    # Drawing canvases (the sketcher, the material builder's curves, plots): light, as
    # the 3D view.
    canvas: str = "#fbfcfd"
    canvas_grid: str = "#e3e8ee"
    canvas_axis: str = "#b8c2cc"
    canvas_ink: str = "#1f2933"
    canvas_text: str = "#4a5560"
    canvas_muted: str = "#8a94a3"
    sketch_free: str = "#2b6cb0"
    sketch_picked: str = "#dd6b20"
    sketch_solved: str = "#2f855a"
    sketch_bad: str = "#c53030"
    plot: str = "#4f7cac"
    series: tuple[str, ...] = ("#2b6cb0", "#dd6b20", "#2f855a")  # curves on a canvas
    # Fasteners in tension and compression, their labels, a triad's x, y, z.
    tension: str = "#e5534b"
    compression: str = "#3d7bd9"
    tension_text: str = "#ff8a7a"
    compression_text: str = "#7ab8ff"
    axes: tuple[str, str, str] = ("#e5534b", "#4caf50", "#4f8fe6")
    label_overlay: str = "rgba(30, 30, 30, 190)"
    maximum: str = "#e5534b"  # the extreme markers
    minimum: str = "#4f8fe6"
    probe_text: str = "#f5f5f5"
    beyond: str = "#d62ad1"  # imported field points past the mapping distance


LIGHT = Tokens(
    "light", surface="#f3f4f6", raised="#e8eaed", sunken="#ffffff", text="#1f2328",
    muted="#6a737d", border="#c9ced6", accent="#3d7bd9", accent_text="#ffffff",
    focus="#3d7bd9", done="#2e9d57", needed="#a87614", warning="#a87614", error="#c53030",
    running="#3d7bd9", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#1d5fbf", "#0f7a6b", "#7a4bc2", "#a15c00"),
)  # fmt: skip
DARK = Tokens(
    "dark", surface="#2b2d31", raised="#383b41", sunken="#1e1f22", text="#e6e8eb",
    muted="#8a96a3", border="#4a4e55", accent="#3d7bd9", accent_text="#ffffff",
    focus="#6aa1f0", done="#4caf50", needed="#d4a72c", warning="#d4a72c", error="#e5534b",
    running="#4f8fe6", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#6aa8ff", "#4fd1b5", "#c39bff", "#f0b45a"),
    banner_error_text="#ff8a80",
)  # fmt: skip
# The 3D view stays light in the dark theme: result colours read best on a light
# ground, as in Ansys and COMSOL. High contrast is black and white with yellow focus.
HIGH_CONTRAST = Tokens(
    "high_contrast", surface="#000000", raised="#000000", sunken="#000000", text="#ffffff",
    muted="#ffffff", border="#ffffff", accent="#ffd400", accent_text="#000000",
    focus="#ffd400", done="#7CFC00", needed="#ffd400", warning="#ffd400", error="#ff6b6b",
    running="#7fdbff", view_top="#ffffff", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#000000", levels=("#ffd400", "#7fdbff", "#7cfc00", "#ff9cf0"),
    contact=("#00a000", "#e0c000", "#ff8000", "#ff0000", "#a000ff"),
    overlay="rgba(0, 0, 0, 230)", overlay_checked="#ffd400", separator="#ffffff",
    banner_error="#000000", banner_error_text="#ff6b6b", banner_info="#000000",
    rule="#ffffff", geometry_edge="#000000", mesh_edge="#000000", beam_edge="#000000",
    canvas="#ffffff", canvas_grid="#c0c0c0", canvas_axis="#000000", canvas_ink="#000000",
    canvas_text="#000000", canvas_muted="#404040",
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


def style_sheet(t: Tokens, density: str = "comfortable", title_pt: float = 14.0) -> str:
    """The application's one style sheet: roles, the focus ring, density, tooltips,
    dock titles, separators, the 3D view's bars and the form banners."""
    pad, row, button = PADDING.get(density, PADDING["comfortable"])
    return f"""
QToolTip {{ color: {t.text}; background: {t.raised}; border: 1px solid {t.border};
            padding: 3px 6px; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="title"] {{ font-weight: 600; font-size: {title_pt:.1f}pt; }}
QLabel[role="error"] {{ color: {t.error}; }}
QLabel[role="needed"] {{ color: {t.needed}; }}
QLabel[role="done"] {{ color: {t.done}; }}
QLabel[role="running"] {{ color: {t.running}; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="description"] {{ color: {t.muted}; border-top: 1px solid {t.rule}; padding: 6px; }}
QLabel[role="banner_error"] {{ background: {t.banner_error}; color: {t.banner_error_text};
                               padding: 6px; border-radius: 4px; }}
QLabel[role="banner_info"] {{ background: {t.banner_info}; padding: 6px; border-radius: 4px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding: {pad // 2}px {pad}px; }}
QTreeView::item, QListView::item {{ padding: {row}px 0; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {t.focus}; }}
QPushButton {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
              border-radius: 3px; padding: {pad}px {button}px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:disabled {{ color: {t.muted}; }}
QPushButton:default {{ background: {t.accent}; color: {t.accent_text};
                       border: 1px solid {t.accent}; }}
QPushButton:focus, QToolButton:focus, QCheckBox:focus, QRadioButton:focus {{
    outline: none; border: 2px solid {t.focus}; border-radius: 3px; }}
QTreeView:focus, QListView:focus, QTableView:focus, QTabBar:focus {{
    border: 1px solid {t.focus}; }}
QDockWidget::title {{ background: {t.raised}; padding: 4px 6px; }}
QMainWindow::separator {{ background: {t.separator}; width: 6px; height: 6px; }}
QMainWindow::separator:hover {{ background: {t.accent}; }}
QWidget#view_bar, QWidget#cut_bar {{ background: {t.overlay}; border-radius: 6px; }}
QWidget#view_bar QToolButton, QWidget#cut_bar QToolButton {{ color: {t.overlay_text}; }}
QWidget#view_bar QToolButton:checked {{ background: {t.overlay_checked}; border-radius: 4px; }}
QFrame#toast {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 6px; }}
QFrame#toast[state="error"] {{ border-color: {t.error}; }}
QFrame#toast[state="done"] {{ border-left: 4px solid {t.done}; }}
"""


def density() -> str:
    from puzzle.settings import current as settings

    return settings().get("interface.density") or "comfortable"


def text_scale() -> float:
    from puzzle.settings import current as settings

    return TEXT_SCALE.get(settings().get("interface.text_size") or "system", 1.0)


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
    _scale_font(app)
    base = app.font().pointSizeF()
    app.setStyleSheet(style_sheet(t, density(), base * 1.1 if base > 0 else 14.0))
    return t


_SYSTEM_FONT: dict = {}


def _scale_font(app) -> None:
    """The system UI font, scaled by Settings > Interface > Text size (the system's own
    text scaling already reaches Qt through the font it reports)."""
    from PySide6.QtGui import QFont

    if "font" not in _SYSTEM_FONT:
        _SYSTEM_FONT["font"] = QFont(app.font())
    font = QFont(_SYSTEM_FONT["font"])
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * text_scale())
    app.setFont(font)


def view_background(plotter, t: Tokens | None = None, kind: str | None = None) -> None:
    """The 3D view's background: a soft gradient (default) or plain (Settings >
    Interface > 3D background)."""
    from puzzle.settings import current as settings

    t = t or current()
    kind = kind or settings().get("interface.view_background")
    if kind == "plain":
        plotter.set_background(t.view_plain)
    elif kind == "colour":  # the user's own (Settings > Interface, Window menu)
        plotter.set_background(settings().get("interface.view_colour"))
    elif kind == "colour_gradient":
        plotter.set_background(settings().get("interface.view_colour"),
                               top=settings().get("interface.view_colour_top"))  # fmt: skip
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


def tab_in_order(container) -> list:
    """Tab moves through a form's controls as they sit on screen, top to bottom, then
    left to right (ADR 0045 decision 9): Qt's default is the order they were made in,
    which drifts as rows are inserted. Returns the controls in their new order."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    def takes(w) -> bool:
        return (
            (w.focusPolicy() & Qt.FocusPolicy.TabFocus)
            and w.isVisibleTo(container)
            and (w.focusProxy() is None)
        )

    controls = [w for w in container.findChildren(QWidget) if takes(w)]
    # Leave a control's own parts (a spin box's line edit) to it.
    controls = [w for w in controls if not any(o is not w and o.isAncestorOf(w)
                                               for o in controls)]  # fmt: skip

    def at(w):
        p = w.mapTo(container, QPoint(0, 0))
        return (round(p.y() / 8), p.x())  # rows within 8 px read as one row

    controls.sort(key=at)
    for a, b in zip(controls, controls[1:], strict=False):
        QWidget.setTabOrder(a, b)
    return controls


def same_size(widgets, width: bool = True, height: bool = True) -> None:
    """Peers made one size, the largest one's (ADR 0045: a row of buttons reads as a
    set only when they match)."""
    widgets = list(widgets)
    if not widgets:
        return
    hints = [w.sizeHint() for w in widgets]
    w_max = max(h.width() for h in hints)
    h_max = max(h.height() for h in hints)
    for w in widgets:
        if width:
            w.setFixedWidth(w_max)
        if height:
            w.setFixedHeight(h_max)
