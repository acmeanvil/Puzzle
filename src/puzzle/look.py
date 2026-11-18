"""Puzzle's colours as data (ADR 0045): the design tokens for light, dark and high
contrast, with no Qt, so the core's renderers (post/symbols.py, post/legend.py,
schematic/) read them in scripts and off-screen renders with no application.
`puzzle.gui.theme` makes the Qt palette and style sheet from them (ADR 0090 package
16: the core never imports `puzzle.gui`)."""

from __future__ import annotations

from dataclasses import dataclass, field


def _table(**colours):
    """A token holding named colours (left out of the tokens' hash)."""
    return field(hash=False, compare=False, default_factory=lambda: dict(colours))


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
    banner_error_text: str = "#a82828"
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
    canvas_muted: str = "#616b77"  # also text ("Two points or more"): 4.5:1
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
    # Supports and loads drawn in the view (post/symbols.py, ADR 0035): supports,
    # mechanical loads, heat, flow, other conditions, electromagnetics; bolts (ADR 0050).
    symbols: tuple[str, ...] = ("#3d7bd9", "#e5534b", "#e8912d", "#3fa564", "#8a94a3",
                                "#9b6ad6")  # fmt: skip
    bolt: str = "#d9a520"
    # The schematic (ADR 0058) on its light ground, as the SVG export draws it: what a
    # data wire carries, an object's state, an operator's verdict.
    flows: dict = _table(temperature="#d9480f", displacement="#1c7ed6", stress="#9c36b5",
                         pressure="#0c8599", velocity="#2f9e44", other="#5c677d")  # fmt: skip
    states: dict = _table(complete="#4caf50", up_to_date="#4caf50", missing="#e5534b",
                          failed="#e5534b", out_of_date="#d4a72c", running="#4f8fe6",
                          not_run="#8a94a3", unused="#8a94a3")  # fmt: skip
    verdicts: dict = _table(**{"pass": "#2f9e44", "fail": "#e03131"})
    schematic_header: str = "#eef1f5"  # the SVG's node headers
    schematic_use: str = "#a0a7b4"  # its "built from" wires


# The legend's one colour above or below a custom range (Settings > Results): named
# colours the user picks, the same in every theme.
OUTSIDE_COLOURS = {"grey": "#a6a6a6", "black": "#000000", "white": "#ffffff",
                   "magenta": "#d62ad1"}  # fmt: skip


LIGHT = Tokens(
    "light", surface="#f3f4f6", raised="#e8eaed", sunken="#ffffff", text="#1f2328",
    muted="#5c6570", border="#c9ced6", accent="#2e66c0", accent_text="#ffffff",
    focus="#2e66c0", done="#1b7339", needed="#8a5f00", warning="#8a5f00", error="#c53030",
    running="#2e66c0", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#1d5fbf", "#0f7a6b", "#7a4bc2", "#a15c00"),
)  # fmt: skip
DARK = Tokens(
    "dark", surface="#2b2d31", raised="#383b41", sunken="#1e1f22", text="#e6e8eb",
    muted="#a0abb7", border="#4a4e55", accent="#3a74d4", accent_text="#ffffff",
    focus="#6aa1f0", done="#5cc46a", needed="#d4a72c", warning="#d4a72c", error="#ff8178",
    running="#79aef5", view_top="#dfe6ee", view_bottom="#ffffff", view_plain="#ffffff",
    view_text="#1f2328", levels=("#6aa8ff", "#4fd1b5", "#c39bff", "#f0b45a"),
    banner_error_text="#ffa198",
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
