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
    "save_as": "copy",
    "save_version": "device-floppy",
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
    # Model tree sections and tools (ADR 0045 decision 4).
    "project": "folder", "parameters": "adjustments-horizontal", "component": "puzzle",
    "study": "flask", "transfer": "transfer", "import_field": "file-import",
    "amplitude": "wave-sine", "construction": "ruler-2", "operator": "math-function",
    "optimise": "target", "convergence": "chart-line", "presets": "adjustments",
    "results": "chart-area-line", "cylinder": "cylinder", "cone": "cone", "sphere": "sphere",
    "extrude": "arrow-autofit-up", "revolve": "rotate-360", "hole": "circle-dashed",
    "line": "line", "sheet": "square", "polygon": "polygon", "spline": "vector-spline",
    "sweep": "route-2", "loft": "stack-2", "patch": "shape-3", "thicken": "layers-subtract",
    "subd": "vector-triangle", "move": "arrows-move", "rotate": "rotate",
    "mirror": "flip-vertical", "scale": "resize", "deform": "transform",
    "pattern": "layout-grid", "split": "cut", "split_face": "scissors", "heal": "bandage",
    "erase": "eraser", "fragment": "components", "enclosure": "box-padding",
    "sketch": "pencil", "check": "checklist", "link": "link", "measure": "ruler-measure",
    "seeds": "grid-dots", "edge_count": "line-dashed", "probe": "crosshair",
    "chart": "chart-line", "animate": "movie", "legend": "palette",
    "temperature": "temperature", "convection": "wind", "heat": "flame",
    "voltage": "bolt", "magnet": "magnet", "inlet": "arrow-bar-to-right",
    "outlet": "arrow-bar-right", "wall": "wall", "equation": "math",
}  # fmt: skip
# Puzzle's own engineering icons, in Tabler's grid and stroke.
DRAWINGS = {
    "mesh": '<path d="M4 20L12 4l8 16z"/><path d="M8 12h8M12 4v16M8 12l4 8 4-8"/>',
    "wireframe": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l16 9M20 7.5l-16 9'
    'M12 3v18"/>',
    # An I-beam's end: beams drawn with their sections (ADR 0055).
    "beams": '<path d="M6 4h12v3h-4.5v10H18v3H6v-3h4.5V7H6z"/>',
    # A panel's title bar: float it out of the window, or dock it back in.
    "float": '<rect x="3" y="8" width="13" height="13" rx="2"/><path d="M11 3h10v10M21 3l-9 9"/>',
    "dock": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15h18"/>'
    '<path d="M12 6v6M9.5 9.5 12 12l2.5-2.5"/>',
    # Supports and loads (ADR 0045 decision 4), drawn as the view's glyphs are.
    "fixed": '<rect x="7" y="4" width="10" height="9" rx="1"/><path d="M4 16h16"/>'
    '<path d="M6 20l2-4M10 20l2-4M14 20l2-4M18 20l2-4"/>',
    "support": '<path d="M12 5l6 9H6z"/><path d="M4 17h16M6 21l2-4M11 21l2-4M16 21l2-4"/>',
    "displacement": '<rect x="3" y="8" width="9" height="9" rx="1" stroke-dasharray="2 2"/>'
    '<path d="M14 12.5h7M18 9.5l3 3-3 3"/>',
    "force": '<path d="M4 20h16"/><path d="M12 3v13M8 12l4 4 4-4"/>',
    "pressure": '<path d="M3 20h18"/><path d="M6 4v11M4 13l2 2 2-2M12 4v11M10 13l2 2 2-2'
    'M18 4v11M16 13l2 2 2-2"/>',
    "spring": '<path d="M4 21h16"/><path d="M12 3v3l-4 1.5 8 3-8 3 8 3-4 1.5V21"/>',
    "remote_force": '<path d="M4 4v16"/><path d="M4 7l9 5M4 17l9-5M4 12h9"/>'
    '<circle cx="14" cy="12" r="1.5"/><path d="M15.5 12H21M18 9l3 3-3 3"/>',
    "gravity": '<circle cx="12" cy="7" r="4"/><path d="M12 13v8M9 18l3 3 3-3"/>',
    "acceleration": '<path d="M4 12h15M8 7l5 5-5 5M14 7l5 5-5 5"/>',
    "heat_flux": '<path d="M3 20h18"/><path d="M8 4c-1.5 2 1.5 4 0 6s1.5 4 0 6'
    'M16 4c-1.5 2 1.5 4 0 6s1.5 4 0 6"/><path d="M6 14l2 2 2-2M14 14l2 2 2-2"/>',
    "coil": '<path d="M3 16c1.5 0 1.5-8 3-8s1.5 8 3 8 1.5-8 3-8 1.5 8 3 8 1.5-8 3-8 1.5 8 3 8"/>',
    "ground": '<path d="M12 3v9M5 12h14M8 16h8M11 20h2"/>',
    "bolt": '<path d="M7 3h10v4H7z"/><path d="M10 7v14h4V7"/><path d="M10 10l4 1.5'
    'M10 13.5l4 1.5M10 17l4 1.5"/>',
    "contact": '<rect x="3" y="5" width="8" height="14" rx="1"/>'
    '<rect x="13" y="5" width="8" height="14" rx="1"/><path d="M12 9v6"/>',
    "triad": '<path d="M5 19V5M5 19h14M5 19l8-8"/><path d="M3 7l2-2 2 2M17 17l2 2-2 2M10 11h3v3"/>',
    "fillet": '<path d="M4 20V12a8 8 0 0 1 8-8h8"/><path d="M4 4h4M4 4v4" stroke-dasharray="2 2"/>',
    "chamfer": '<path d="M4 20V10l6-6h10"/><path d="M4 4h4M4 4v4" stroke-dasharray="2 2"/>',
}
# Feature types (model/catalog.py) -> icon, for the tree and the forms.
FEATURES = {
    "fixed": "fixed", "displacement": "displacement", "remote_displacement": "displacement",
    "elastic_support": "spring", "compression_only_support": "support",
    "force": "force", "traction": "force", "pressure": "pressure",
    "remote_force": "remote_force", "remote_force_rigid": "remote_force",
    "gravity": "gravity", "acceleration": "acceleration", "body_force": "acceleration",
    "rotation": "revolve", "initial_stress": "scale",
    "temperature": "temperature", "body_temperature": "temperature",
    "heat_flux": "heat_flux", "convection": "convection", "heat_generation": "heat",
    "bolt_pretension": "bolt", "bolt_adjustment": "bolt",
    "electric_potential": "voltage", "surface_charge": "voltage", "ground": "ground",
    "coil_current": "coil", "stranded_coil": "coil", "coil_start": "coil",
    "coil_end": "coil", "magnetic_insulation": "magnet", "magnetic_symmetry": "magnet",
    "symmetry": "mirror", "velocity_inlet": "inlet", "mass_flow_inlet": "inlet",
    "pressure_outlet": "outlet", "wall": "wall", "dirichlet": "equation",
    "neumann": "equation",
}  # fmt: skip
# Model tree sections (gui/tree.py SECTIONS) -> icon.
SECTIONS = {
    "Project": "project", "Parameters": "parameters", "Geometry": "iso",
    "Materials": "material", "Meshes": "mesh", "Components": "component",
    "Studies": "study", "Transfers": "transfer", "Imported fields": "import_field",
    "Amplitudes": "amplitude", "Coordinate systems": "triad",
    "Construction geometry": "construction", "Operators": "operator",
    "Optimisations": "optimise", "Mesh studies": "convergence", "Bolt types": "bolt",
    "Solver presets": "presets", "Results": "results",
}  # fmt: skip
# Ribbon tools (geometry history steps, mesh controls, result tools) -> icon.
TOOLS = {
    "box": "iso", "cylinder": "cylinder", "cone": "cone", "sphere": "sphere",
    "extrude": "extrude", "revolve": "revolve", "hole": "hole", "polyline": "line",
    "sheet": "sheet", "face": "polygon", "section": "section", "curved_sheet": "spline",
    "rectangle": "sheet", "circle": "hole", "annulus": "hole", "polygon": "polygon",
    "slot": "hole", "offset": "wall",
    "spline": "spline", "sweep": "sweep", "pipe": "cylinder", "loft": "loft",
    "patch": "patch", "thicken": "thicken", "subd": "subd", "fillet": "fillet",
    "chamfer": "chamfer", "move": "move", "rotate": "rotate", "mirror": "mirror",
    "scale": "scale", "deform": "deform", "pattern": "pattern", "split": "split",
    "split_face": "split_face", "heal": "heal", "remove": "erase", "fragment": "fragment",
    "enclosure": "enclosure", "sketch": "sketch", "check": "check", "from_picks": "pick",
    "reattach": "link", "measure": "measure",
    "size": "scale", "refine_near": "seeds", "ball": "sphere", "transfinite": "edge_count",
    "boundary_layer": "loft", "mesh": "mesh", "seeds": "seeds", "gmsh_options": "settings",
    "probe": "probe", "reaction": "fixed", "selection": "pick", "path": "line",
    "linearized": "chart", "plane": "sheet", "cut": "section", "animate": "animate",
    "legend": "legend", "plots": "chart",
}  # fmt: skip


def _colour() -> str:
    app = QApplication.instance()
    if app is None:
        from puzzle.gui.theme import current

        return current().text
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
# Colours are the theme's tokens, by name.
STATE_DRAWINGS = {
    "complete": ("done", '<circle cx="12" cy="12" r="4" fill="{c}" stroke="none"/>'),
    "up_to_date": ("done", '<path d="M5 12l5 5 9-10"/>'),
    "missing": ("error", '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>'),
    "failed": ("error", '<circle cx="12" cy="12" r="9"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>'),
    "out_of_date": ("needed", '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>'),
    "running": ("running", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "not_run": ("muted", '<circle cx="12" cy="12" r="7"/>'),
    "unused": ("muted", '<path d="M6 12h12"/>'),
}


def state_icon(state: str) -> QIcon:
    """The small icon a tree item shows for its state, in the theme's colours."""
    from puzzle.gui.theme import current

    token, _body = STATE_DRAWINGS.get(state, STATE_DRAWINGS["complete"])
    return _state_icon(state, getattr(current(), token))


@cache
def _state_icon(state: str, colour: str) -> QIcon:

    _token, body = STATE_DRAWINGS.get(state, STATE_DRAWINGS["complete"])
    head = _HEAD.format(c=colour).replace(f'stroke-width="{STROKE}"', 'stroke-width="2.4"')
    svg = (head + body.format(c=colour) + "</svg>").encode()
    return _render(svg, (12, 16, 24, 32))


def _render(svg: bytes, sizes) -> QIcon:
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg))
    out = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        out.addPixmap(pixmap)
    return out
