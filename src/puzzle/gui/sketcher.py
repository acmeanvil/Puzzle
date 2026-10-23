"""The sketch editor (G5): draw a profile, constrain it, dimension it.

Tools: Select (click to pick, shift-click to add, drag a point to move what is still
free), Line (click points; click the first point again, or double-click, to finish),
Rectangle (two corners; it comes horizontal and vertical), Circle (centre, then a
point on it) and Arc (centre, start, end, counter-clockwise). Clicking near an
existing point reuses it, so outlines join by themselves.

Constraint buttons turn on for what is selected: Horizontal and Vertical (lines),
Parallel, Perpendicular, Equal (two lines, or two arcs and circles), Coincident (two
points), Fix (a point), Tangent (an arc and a line that meet), On (a point and a
line or circle), Midpoint (a point and a line). Dimension measures what is selected:
a line's length, two points' distance (or its horizontal or vertical part), a radius,
the angle between two lines; values are typed in the display length unit (Settings)
or degrees, or as a parameter's
name. Every change re-solves at once, and the status line says how many degrees of
freedom are left, or which constraints conflict.

Edge names become face names on the extruded part ("Plate/bottom"), so rename edges
to what they are; the editor's own names are line-1, arc-1, circle-1.
"""

from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.geometry.sketch import DIMENSIONS, SketchError, describe, solve

MM = 1000.0  # the canvas is drawn in mm; values are shown in the display length unit


def length_unit() -> tuple[str, float]:
    from puzzle.settings import length_unit as unit

    return unit()


INK, FREE, PICKED, CONSTRUCTION, BAD = "#1f2933", "#2b6cb0", "#dd6b20", "#8a94a3", "#c53030"


# --- the sketch as data ----------------------------------------------------------------


class SketchModel:
    """A sketch spec (metres) with the edits the editor makes; positions are always the
    last solved ones."""

    def __init__(self, spec: dict | None = None, params: dict[str, float] | None = None):
        spec = copy.deepcopy(spec or {})
        self.spec = {
            "points": spec.get("points", {}),
            "lines": spec.get("lines", {}),
            "arcs": spec.get("arcs", {}),
            "circles": spec.get("circles", {}),
            "construction": spec.get("construction", []),
            "constraints": spec.get("constraints", []),
        }
        self.params = params or {}
        self.solved = None
        self.error = ""
        self.resolve()

    # names
    def _new(self, prefix: str, taken) -> str:
        i = 1
        while f"{prefix}{i}" in taken:
            i += 1
        return f"{prefix}{i}"

    def entities(self) -> dict[str, str]:
        """Every named thing: name -> kind (point, line, arc, circle)."""
        out = {n: "point" for n in self.spec["points"]}
        for kind in ("lines", "arcs", "circles"):
            out.update({n: kind[:-1] for n in self.spec[kind]})
        return out

    # drawing
    def add_point(self, u: float, v: float) -> str:
        name = self._new("P", self.spec["points"])
        self.spec["points"][name] = [u, v]
        return name

    def add_line(self, a: str, b: str) -> str:
        name = self._new("line-", self.entities())
        self.spec["lines"][name] = [a, b]
        return name

    def add_circle(self, centre: str, r: float) -> str:
        name = self._new("circle-", self.entities())
        self.spec["circles"][name] = [centre, r]
        return name

    def add_arc(self, centre: str, start: str, end: str) -> str:
        name = self._new("arc-", self.entities())
        self.spec["arcs"][name] = [centre, start, end]
        return name

    def constrain(self, constraint: list) -> None:
        self.spec["constraints"].append(constraint)
        self.resolve()

    def remove_constraint(self, index: int) -> None:
        del self.spec["constraints"][index]
        self.resolve()

    def delete(self, name: str) -> None:
        """An edge or a point, with the constraints that name it (and a point's edges)."""
        kinds = self.entities()
        gone = {name}
        if kinds.get(name) == "point":
            for kind in ("lines", "arcs", "circles"):
                for n, refs in list(self.spec[kind].items()):
                    if name in refs:
                        gone.add(n)
        for n in gone:
            for kind in ("points", "lines", "arcs", "circles"):
                self.spec[kind].pop(n, None)
        self.spec["construction"] = [n for n in self.spec["construction"] if n not in gone]
        self.spec["constraints"] = [
            c
            for c in self.spec["constraints"]
            if not gone & {x for x in c[1:] if isinstance(x, str)}
        ]
        used = {p for k in ("lines", "arcs") for refs in self.spec[k].values() for p in refs}
        used |= {c for c, _ in self.spec["circles"].values()}
        for p in [p for p in self.spec["points"] if p not in used]:
            del self.spec["points"][p]  # a point nothing uses any more
        self.resolve()

    def rename(self, old: str, new: str) -> None:
        if new in self.entities():
            raise SketchError(f"'{new}' is already a name in this sketch")
        if not new.replace("-", "").replace("_", "").isalnum():
            raise SketchError("names are letters, digits, - and _")
        for kind in ("points", "lines", "arcs", "circles"):
            if old in self.spec[kind]:
                self.spec[kind][new] = self.spec[kind].pop(old)
        for kind in ("lines", "arcs", "circles"):
            for n, refs in self.spec[kind].items():
                self.spec[kind][n] = [new if r == old else r for r in refs]
        self.spec["construction"] = [new if n == old else n for n in self.spec["construction"]]
        self.spec["constraints"] = [
            [c[0], *(new if x == old else x for x in c[1:])] for c in self.spec["constraints"]
        ]

    def toggle_construction(self, name: str) -> None:
        cons = self.spec["construction"]
        if name in cons:
            cons.remove(name)
        else:
            cons.append(name)

    # solving
    def resolve(self, dragged: str | None = None) -> None:
        """Solve and keep the solved positions (a dragged point is held where it is)."""
        if not self.spec["points"]:
            self.solved, self.error = None, ""
            return
        spec = self.spec
        if dragged:
            spec = copy.deepcopy(spec)
            spec["constraints"].append(["fix", dragged])
        try:
            solved = solve(spec, self.params)
        except SketchError as exc:
            self.error = str(exc)
            return
        self.error = ""
        if dragged and solved.status != "ok":
            return  # it can't go there: leave it where it was
        self.solved = solved
        if solved.status == "ok":
            for n, (u, v) in solved.points.items():
                self.spec["points"][n] = [u, v]
            for n, r in solved.radii.items():
                self.spec["circles"][n][1] = r

    def status(self) -> tuple[str, str]:
        """(text, colour) for the status line."""
        if self.error:
            return self.error, BAD
        if self.solved is None:
            return "Draw an outline: Line, Rectangle, Circle or Arc", INK
        text = describe(self.solved, self.spec)
        if self.solved.status != "ok":
            return text, BAD
        return text, "#2f855a" if self.solved.dof == 0 else FREE

    # measuring, for new dimensions
    def point(self, name: str) -> tuple[float, float]:
        return tuple(self.spec["points"][name])

    def length(self, line: str) -> float:
        a, b = self.spec["lines"][line]
        return math.dist(self.point(a), self.point(b))

    def radius(self, curve: str) -> float:
        if curve in self.spec["circles"]:
            return self.spec["circles"][curve][1]
        c, s, _ = self.spec["arcs"][curve]
        return math.dist(self.point(c), self.point(s))

    def angle(self, l1: str, l2: str) -> float:
        (a, b), (c, d) = self.spec["lines"][l1], self.spec["lines"][l2]
        (ax, ay), (bx, by) = self.point(a), self.point(b)
        (cx, cy), (dx, dy) = self.point(c), self.point(d)
        t = math.atan2(dy - cy, dx - cx) - math.atan2(by - ay, bx - ax)
        return abs(math.degrees(t)) % 180 or 0.0


def constraint_text(c: list, labels=None) -> str:
    """A constraint in words: "Length of bottom = 60 mm", "line-2 vertical"."""
    kind, *r = c

    def val(x, unit="mm"):
        if isinstance(x, (int, float)):
            if unit == "deg":
                return f"{x:g}°"
            name, scale = length_unit()
            return f"{x * scale:.4g} {name}"
        return str(x)  # a parameter

    text = {
        "fix": lambda: f"{r[0]} fixed",
        "coincident": lambda: f"{r[0]} on {r[1]}",
        "horizontal": lambda: f"{r[0]} horizontal",
        "vertical": lambda: f"{r[0]} vertical",
        "length": lambda: f"Length of {r[0]} = {val(r[1])}",
        "distance": lambda: f"{r[0]} to {r[1]} = {val(r[2])}",
        "hdistance": lambda: f"{r[0]} to {r[1]} across = {val(r[2])}",
        "vdistance": lambda: f"{r[0]} to {r[1]} up = {val(r[2])}",
        "parallel": lambda: f"{r[0]} parallel to {r[1]}",
        "perpendicular": lambda: f"{r[0]} perpendicular to {r[1]}",
        "equal": lambda: f"{r[0]} equal to {r[1]}",
        "angle": lambda: f"Angle {r[0]} to {r[1]} = {val(r[2], 'deg')}",
        "radius": lambda: f"Radius of {r[0]} = {val(r[1])}",
        "diameter": lambda: f"Diameter of {r[0]} = {val(r[1])}",
        "on": lambda: f"{r[0]} on {r[1]}",
        "midpoint": lambda: f"{r[0]} at the middle of {r[1]}",
        "tangent": lambda: f"{r[0]} tangent to {r[1]}",
        "symmetric": lambda: f"{r[0]} and {r[1]} symmetric about {r[2]}",
    }.get(kind)
    return text() if text else kind


# --- the canvas --------------------------------------------------------------------------


class SketchCanvas(QGraphicsView):
    """Draws the sketch in mm (y up) and turns mouse actions into editor calls."""

    clicked = Signal(float, float, bool)  # u, v in m; shift held
    double_clicked = Signal(float, float)
    dragged = Signal(str, float, float)  # point, u, v in m
    released = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # A light sheet whatever the system theme: the ink colours are chosen for it.
        self.setBackgroundBrush(QBrush(QColor("#fbfcfd")))
        self.scale(4.0, -4.0)  # 4 px per mm, y up
        self.setMinimumSize(560, 420)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.model: SketchModel | None = None
        self.selected: list[str] = []
        self.preview: list[tuple[float, float]] = []  # the tool's points so far (m)
        self.hover: tuple[float, float] | None = None
        self._drag: str | None = None

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        f = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def _at(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return p.x() / MM, p.y() / MM

    def pick_radius(self) -> float:
        """6 pixels, in m."""
        return 6.0 / self.transform().m11() / MM

    def mousePressEvent(self, event) -> None:  # noqa: N802
        u, v = self._at(event)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        editor = self.parent_editor()
        if editor is not None and editor.tool == "select":
            near = editor.nearest(u, v, points_only=True)
            if near and not shift:
                self._drag = near
        self.clicked.emit(u, v, shift)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        u, v = self._at(event)
        self.hover = (u, v)
        if self._drag and event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(self._drag, u, v)
        else:
            self.redraw()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            self._drag = None
            self.released.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit(*self._at(event))

    def parent_editor(self):
        w = self.parent()
        while w is not None and not isinstance(w, SketchWorkspace):
            w = w.parent()
        return w

    def redraw(self) -> None:
        scene = self.scene()
        scene.clear()
        m = self.model
        if m is None:
            return
        pts = {n: QPointF(u * MM, v * MM) for n, (u, v) in m.spec["points"].items()}
        extent = QRectF(-10, -10, 120, 80)
        for p in pts.values():
            extent = extent.united(QRectF(p.x() - 20, p.y() - 20, 40, 40))
        self._grid(scene, extent)
        bad = set()
        if m.solved is not None and m.solved.status != "ok":
            for i in m.solved.failed:
                bad |= {x for x in m.spec["constraints"][i][1:] if isinstance(x, str)}
        width = 2.0 / self.transform().m11()
        cons = set(m.spec["construction"])

        def pen(name):
            colour = (
                PICKED
                if name in self.selected
                else BAD
                if name in bad
                else CONSTRUCTION
                if name in cons
                else INK
            )
            p = QPen(QColor(colour), width * (1.6 if name in self.selected else 1.0))
            if name in cons:
                p.setStyle(Qt.PenStyle.DashLine)
            return p

        for n, (a, b) in m.spec["lines"].items():
            scene.addLine(pts[a].x(), pts[a].y(), pts[b].x(), pts[b].y(), pen(n))
        for n, (c, r) in m.spec["circles"].items():
            rr = r * MM
            scene.addEllipse(pts[c].x() - rr, pts[c].y() - rr, 2 * rr, 2 * rr, pen(n))
        for n, (c, s, e) in m.spec["arcs"].items():
            scene.addPath(_arc_path(pts[c], pts[s], pts[e]), pen(n))
        dot = 3.0 / self.transform().m11()
        fixed = {c[1] for c in m.spec["constraints"] if c[0] == "fix"}
        for n, p in pts.items():
            colour = PICKED if n in self.selected else (INK if n in fixed else FREE)
            scene.addEllipse(
                p.x() - dot,
                p.y() - dot,
                2 * dot,
                2 * dot,
                QPen(Qt.PenStyle.NoPen),
                QBrush(QColor(colour)),
            )
        self._labels(scene, m, pts)
        if self.preview and self.hover:
            ghost = QPen(QColor(FREE), width, Qt.PenStyle.DotLine)
            path = [QPointF(u * MM, v * MM) for u, v in [*self.preview, self.hover]]
            for a, b in zip(path, path[1:], strict=False):
                scene.addLine(a.x(), a.y(), b.x(), b.y(), ghost)
        scene.setSceneRect(extent)

    def _grid(self, scene, extent: QRectF) -> None:
        step = 10.0 if self.transform().m11() < 6 else 5.0
        faint = QPen(QColor("#e3e8ee"), 0)
        x = math.floor(extent.left() / step) * step
        while x <= extent.right():
            scene.addLine(x, extent.top(), x, extent.bottom(), faint)
            x += step
        y = math.floor(extent.top() / step) * step
        while y <= extent.bottom():
            scene.addLine(extent.left(), y, extent.right(), y, faint)
            y += step
        axis = QPen(QColor("#b8c2cc"), 0)
        scene.addLine(extent.left(), 0, extent.right(), 0, axis)
        scene.addLine(0, extent.top(), 0, extent.bottom(), axis)

    def _labels(self, scene, m: SketchModel, pts) -> None:
        """Edge names and dimension values, upright whatever the zoom."""
        font = QFont()
        font.setPointSize(9)

        def put(text, where: QPointF, colour=CONSTRUCTION):
            item = scene.addSimpleText(text, font)
            item.setBrush(QBrush(QColor(colour)))
            item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setPos(where)

        for n, (a, b) in m.spec["lines"].items():
            put(n, (pts[a] + pts[b]) / 2)
        for n, (c, r) in m.spec["circles"].items():
            put(n, pts[c] + QPointF(-r * MM, r * MM * 1.4))  # above, clear of its radius
        for c in m.spec["constraints"]:
            if c[0] not in DIMENSIONS:
                continue
            refs = [x for x in c[1:-1] if x in pts or x in m.spec["lines"]]
            anchor = None
            if refs and refs[0] in m.spec["lines"]:
                a, b = m.spec["lines"][refs[0]]
                anchor = (pts[a] + pts[b]) / 2 + QPointF(0, -4)
            elif len(refs) >= 2 and all(r in pts for r in refs[:2]):
                anchor = (pts[refs[0]] + pts[refs[1]]) / 2 + QPointF(0, 4)
            elif c[1] in m.spec["circles"]:
                cc, r = m.spec["circles"][c[1]]
                anchor = pts[cc] + QPointF(r * MM, 0)
            elif c[1] in m.spec["arcs"]:
                anchor = pts[m.spec["arcs"][c[1]][0]]
            if anchor is not None:
                value = c[-1]
                text = (
                    value
                    if isinstance(value, str)
                    else (f"{value:g}°" if c[0] == "angle" else f"{value * length_unit()[1]:.4g}")
                )
                put(
                    ("R" if c[0] == "radius" else "⌀" if c[0] == "diameter" else "") + text,
                    anchor,
                    "#2f855a",
                )


def _arc_path(c: QPointF, s: QPointF, e: QPointF) -> QPainterPath:
    """Counter-clockwise from s to e around c (in y-up coordinates)."""
    r = math.hypot(s.x() - c.x(), s.y() - c.y())
    a0 = math.degrees(math.atan2(s.y() - c.y(), s.x() - c.x()))
    a1 = math.degrees(math.atan2(e.y() - c.y(), e.x() - c.x()))
    sweep = (a1 - a0) % 360 or 360
    path = QPainterPath(s)
    # Qt angles run counter-clockwise on screen (y down); the view flips y, so negate.
    path.arcTo(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), -a0, -sweep)
    return path


# --- the dialog --------------------------------------------------------------------------

TOOLS = [
    ("select", "Select"),
    ("line", "Line"),
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc"),
]
RELATIONS = [
    ("horizontal", "Horizontal"),
    ("vertical", "Vertical"),
    ("parallel", "Parallel"),
    ("perpendicular", "Perpendicular"),
    ("equal", "Equal"),
    ("coincident", "Coincident"),
    ("fix", "Fix"),
    ("tangent", "Tangent"),
    ("on", "On"),
    ("midpoint", "Midpoint"),
]


class SketchWorkspace(QWidget):
    """A sketch to draw and constrain: tools above, canvas, constraints beside it. It
    sits in the main window's centre (ADR 0017) or in a dialog (`SketchEditor`);
    `sketch()` is the result (m, solved), `problem()` what stops it being used."""

    def __init__(self, spec: dict | None = None, params: dict | None = None, parent=None):
        super().__init__(parent)
        self.model = SketchModel(spec, params)
        self.params = dict(params or {})
        self.tool = "select"
        self._first_point = self._last_point = None  # the Line tool's chain
        self.canvas = SketchCanvas(self)
        self.canvas.model = self.model
        self.canvas.clicked.connect(self.click)
        self.canvas.double_clicked.connect(self._double)
        self.canvas.dragged.connect(self.drag)
        self.canvas.released.connect(self.changed)

        tools = QHBoxLayout()
        self.tool_buttons = QButtonGroup(self)
        for key, text in TOOLS:
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setChecked(key == "select")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_buttons.addButton(b)
            tools.addWidget(b)
        tools.addSpacing(16)
        self.construction = QPushButton("Construction")
        self.construction.setToolTip("Drawn and constrained, but not part of the outline")
        self.construction.clicked.connect(self._toggle_construction)
        tools.addWidget(self.construction)
        rename = QPushButton("Rename…")
        rename.setToolTip("Edge names become face names on the part")
        rename.clicked.connect(self._rename)
        tools.addWidget(rename)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_selected)
        tools.addWidget(delete)
        tools.addStretch()

        side = QVBoxLayout()
        side.addWidget(QLabel("Constrain the selection"))
        grid = QWidget()
        rows = QVBoxLayout(grid)
        rows.setContentsMargins(0, 0, 0, 0)
        self.relation_buttons: dict[str, QPushButton] = {}
        for i in range(0, len(RELATIONS), 2):
            row = QHBoxLayout()
            for key, text in RELATIONS[i : i + 2]:
                b = QPushButton(text)
                b.clicked.connect(lambda _=False, k=key: self.relate(k))
                self.relation_buttons[key] = b
                row.addWidget(b)
            rows.addLayout(row)
        side.addWidget(grid)
        self.dimension_button = QPushButton("Dimension…")
        self.dimension_button.setToolTip(
            "A line's length, two points' distance, a radius, or two lines' angle"
        )
        self.dimension_button.clicked.connect(self.dimension)
        side.addWidget(self.dimension_button)
        side.addWidget(QLabel("Constraints (double-click a dimension to change it)"))
        self.constraints = QListWidget()
        self.constraints.itemDoubleClicked.connect(self._edit_constraint)
        side.addWidget(self.constraints, 1)
        remove = QPushButton("Remove constraint")
        remove.clicked.connect(self._remove_constraint)
        side.addWidget(remove)

        self.status = QLabel("")

        body = QHBoxLayout()
        body.addWidget(self.canvas, 3)
        body.addLayout(side, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(tools)
        layout.addLayout(body, 1)
        layout.addWidget(self.status)
        self.changed()

    # --- questions (tests replace this) ---------------------------------------------

    def ask_value(self, title: str, label: str, default: str) -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

    # --- state -----------------------------------------------------------------------

    def sketch(self) -> dict:
        return copy.deepcopy(self.model.spec)

    def changed(self) -> None:
        text, colour = self.model.status()
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold")
        self.constraints.clear()
        bad = set(self.model.solved.failed) if self.model.solved else set()
        for i, c in enumerate(self.model.spec["constraints"]):
            item = QListWidgetItem(constraint_text(c))
            if i in bad:
                item.setForeground(QColor(BAD))
            self.constraints.addItem(item)
        self._enable()
        self.canvas.redraw()

    def _enable(self) -> None:
        kinds = [self.model.entities().get(n) for n in self.canvas.selected]
        k = sorted(kinds)
        allowed = {
            "horizontal": k == ["line"],
            "vertical": k == ["line"],
            "parallel": k == ["line", "line"],
            "perpendicular": k == ["line", "line"],
            "equal": k == ["line", "line"] or (len(k) == 2 and set(k) <= {"arc", "circle"}),
            "coincident": k == ["point", "point"],
            "fix": k == ["point"],
            "tangent": k == ["arc", "line"],
            "on": len(k) == 2 and "point" in k and bool({"line", "circle", "arc"} & set(k)),
            "midpoint": k == ["line", "point"],
        }
        for key, b in self.relation_buttons.items():
            b.setEnabled(allowed[key])
        self.dimension_button.setEnabled(
            k in (["line"], ["point", "point"], ["circle"], ["arc"], ["line", "line"])
        )

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.canvas.preview = []
        for b in self.tool_buttons.buttons():
            b.setChecked(b.text() == dict(TOOLS)[tool])

    # --- picking -----------------------------------------------------------------------

    def nearest(self, u: float, v: float, points_only: bool = False) -> str | None:
        """The point, else the edge, within a few pixels of (u, v)."""
        tol = self.canvas.pick_radius()
        best, best_d = None, tol
        for n, (pu, pv) in self.model.spec["points"].items():
            d = math.dist((u, v), (pu, pv))
            if d <= best_d:
                best, best_d = n, d
        if best or points_only:
            return best
        m = self.model
        for n, (a, b) in m.spec["lines"].items():
            d = _to_segment((u, v), m.point(a), m.point(b))
            if d <= best_d:
                best, best_d = n, d
        for n, (c, r) in m.spec["circles"].items():
            d = abs(math.dist((u, v), m.point(c)) - r)
            if d <= best_d:
                best, best_d = n, d
        for n, (c, s, _) in m.spec["arcs"].items():
            d = abs(math.dist((u, v), m.point(c)) - math.dist(m.point(s), m.point(c)))
            if d <= best_d:
                best, best_d = n, d
        return best

    def _point_at(self, u: float, v: float) -> str:
        """An existing point within reach, or a new one there."""
        return self.nearest(u, v, points_only=True) or self.model.add_point(u, v)

    # --- the tools ---------------------------------------------------------------------

    def click(self, u: float, v: float, shift: bool = False) -> None:
        """A click at (u, v) in m, as the current tool takes it."""
        m, c = self.model, self.canvas
        if self.tool == "select":
            hit = self.nearest(u, v)
            if shift and hit:
                c.selected = [n for n in c.selected if n != hit] + (
                    [] if hit in c.selected else [hit]
                )
            else:
                c.selected = [hit] if hit else []
        elif self.tool == "line":
            near = self.nearest(u, v, points_only=True)
            if c.preview:
                start = self._last_point
                end = near or m.add_point(u, v)
                if end != start:
                    m.add_line(start, end)
                closed = end == self._first_point
                self._last_point = end
                c.preview = [] if closed else [m.point(end)]
                m.resolve()
            else:
                self._first_point = self._last_point = near or m.add_point(u, v)
                c.preview = [m.point(self._last_point)]
        elif self.tool == "rect":
            if c.preview:
                (u0, v0) = c.preview[0]
                corners = [(u0, v0), (u, v0), (u, v), (u0, v)]
                names = [self._point_at(*p) for p in corners]
                lines = [m.add_line(names[i], names[(i + 1) % 4]) for i in range(4)]
                for i, ln in enumerate(lines):
                    m.spec["constraints"].append(["horizontal" if i % 2 == 0 else "vertical", ln])
                c.preview = []
                m.resolve()
            else:
                c.preview = [(u, v)]
        elif self.tool == "circle":
            if c.preview:
                centre = self._point_at(*c.preview[0])
                m.add_circle(centre, max(math.dist(c.preview[0], (u, v)), 1e-4))
                c.preview = []
                m.resolve()
            else:
                c.preview = [(u, v)]
        elif self.tool == "arc":
            c.preview.append((u, v))
            if len(c.preview) == 3:
                (cu, cv), s, e = c.preview
                r = math.dist((cu, cv), s)
                t = math.atan2(e[1] - cv, e[0] - cu)
                e = (cu + r * math.cos(t), cv + r * math.sin(t))  # on the arc's circle
                centre, start = self._point_at(cu, cv), self._point_at(*s)
                end = self._point_at(*e)
                m.add_arc(centre, start, end)
                c.preview = []
                m.resolve()
        self.changed()

    def _double(self, u: float, v: float) -> None:
        if self.tool == "line":
            self.canvas.preview = []
            self.changed()
        elif self.tool == "select":
            hit = self.nearest(u, v)
            if hit:
                self.canvas.selected = [hit]
                self._rename()

    def drag(self, point: str, u: float, v: float) -> None:
        """Move a point; what is still free follows. A fixed point, or a move the
        constraints can't follow, leaves everything where it was."""
        m = self.model
        if any(c[0] == "fix" and c[1] == point for c in m.spec["constraints"]):
            return  # "fix" holds a point where it is stored: don't move what it holds
        before = copy.deepcopy(m.spec["points"]), m.solved
        m.spec["points"][point] = [u, v]
        m.resolve(dragged=point)
        if m.solved is before[1]:  # not solved there
            m.spec["points"] = before[0]
        self.canvas.redraw()

    # --- constraints -------------------------------------------------------------------

    def relate(self, kind: str) -> None:
        """Constrain the selection by a relation (no value)."""
        sel = self.canvas.selected
        kinds = self.model.entities()
        if kind in ("tangent", "on", "midpoint"):
            first = {"tangent": "arc", "on": "point", "midpoint": "point"}[kind]
            sel = sorted(sel, key=lambda n: kinds[n] != first)
        self.model.constrain([kind, *sel])
        self.changed()

    def dimension(self) -> None:
        """A dimension on the selection, typed in the display length unit (or degrees),
        or a parameter."""
        m, sel = self.model, self.canvas.selected
        kinds = sorted(m.entities()[n] for n in sel)
        if kinds == ["line"]:
            kind, refs, now, unit = "length", sel, m.length(sel[0]), "mm"
        elif kinds == ["point", "point"]:
            choice = self.ask_value(
                "Dimension",
                "Distance: straight, across (horizontal) or up (vertical)?",
                "straight",
            )
            kind = {"across": "hdistance", "up": "vdistance"}.get(choice or "", "distance")
            (u0, v0), (u1, v1) = m.point(sel[0]), m.point(sel[1])
            now = {"hdistance": abs(u1 - u0), "vdistance": abs(v1 - v0)}.get(
                kind, math.dist((u0, v0), (u1, v1))
            )
            refs, unit = sel, "mm"
        elif kinds in (["circle"], ["arc"]):
            kind, refs, now, unit = "radius", sel, m.radius(sel[0]), "mm"
        elif kinds == ["line", "line"]:
            kind, refs, now, unit = "angle", sel, m.angle(*sel), "deg"
        else:
            return
        name, scale = length_unit()
        shown = f"{now * scale:.4g}" if unit == "mm" else f"{now:.4g}"
        label = name if unit == "mm" else "degrees"
        text = self.ask_value(
            "Dimension", f"{kind.capitalize()} ({label}, or a parameter's name):", shown
        )
        value = self._value(text, unit)
        if value is None:
            return
        m.constrain([kind, *refs, value])
        self.changed()

    def _value(self, text: str | None, unit: str):
        if text is None:
            return None
        try:
            number = float(text)
        except ValueError:
            if text in self.params:
                return text
            self.status.setText(f"'{text}' is neither a number nor a parameter")
            self.status.setStyleSheet(f"color: {BAD}; font-weight: bold")
            return None
        return number / length_unit()[1] if unit == "mm" else number

    def _edit_constraint(self, item) -> None:
        i = self.constraints.row(item)
        c = self.model.spec["constraints"][i]
        if c[0] not in DIMENSIONS:
            return
        unit = "deg" if c[0] == "angle" else "mm"
        now = c[-1]
        name, scale = length_unit()
        shown = (
            now if isinstance(now, str) else (f"{now:g}" if unit == "deg" else f"{now * scale:.4g}")
        )
        label = "degrees" if unit == "deg" else name
        value = self._value(self.ask_value("Dimension", f"New value ({label}):", shown), unit)
        if value is not None:
            c[-1] = value
            self.model.resolve()
            self.changed()

    def _remove_constraint(self) -> None:
        row = self.constraints.currentRow()
        if row >= 0:
            self.model.remove_constraint(row)
            self.changed()

    def delete_selected(self) -> None:
        for n in list(self.canvas.selected):
            if n in self.model.entities():
                self.model.delete(n)
        self.canvas.selected = []
        self.changed()

    def _toggle_construction(self) -> None:
        for n in self.canvas.selected:
            if self.model.entities().get(n) in ("line", "arc", "circle"):
                self.model.toggle_construction(n)
        self.changed()

    def _rename(self) -> None:
        sel = self.canvas.selected
        if len(sel) != 1:
            return
        new = self.ask_value("Rename", "Name (edges name the part's faces):", sel[0])
        if new and new != sel[0]:
            try:
                self.model.rename(sel[0], new)
            except SketchError as exc:
                self.status.setText(str(exc))
                return
            self.canvas.selected = [new]
            self.changed()

    def problem(self) -> str | None:
        """Why the sketch can't be used yet (shown in red), or None when it can."""
        from puzzle.geometry.sketch import loops

        m = self.model
        if m.solved is None or m.solved.status != "ok":
            text = "The sketch must solve before it can be used: " + m.status()[0]
        else:
            try:
                loops(m.spec, m.solved)
                return None
            except SketchError as exc:
                text = str(exc)
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {BAD}; font-weight: bold")
        return text


class SketchEditor(SketchWorkspace):
    """The workspace in a dialog of its own (a profile's inline sketch)."""

    def exec(self) -> bool:
        dialog = QDialog(self.parentWidget())
        dialog.setWindowTitle("Sketch")
        dialog.resize(1000, 640)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(lambda: None if self.problem() else dialog.accept())
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self)
        layout.addWidget(buttons)
        return bool(dialog.exec())


def _to_segment(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = (
        0.0
        if dx == dy == 0
        else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    )
    return math.dist(p, (ax + t * dx, ay + t * dy))


def summary(spec: dict) -> str:
    """A sketch in a few words, for the step dialog: "4 lines, 1 circle"."""
    parts = []
    for kind, one in (("lines", "line"), ("arcs", "arc"), ("circles", "circle")):
        n = len(spec.get(kind, {}))
        if n:
            parts.append(f"{n} {one}{'s' if n != 1 else ''}")
    return ", ".join(parts) or "empty"
