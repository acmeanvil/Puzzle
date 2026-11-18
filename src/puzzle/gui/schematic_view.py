"""The project schematic in the window (ADR 0058): nodes and wires on a Qt graphics
scene, drawn from `puzzle.schematic.build` and laid out by `puzzle.schematic.layout`.

It never holds anything itself: a click selects the object in the tree (and so in
Properties), a double-click opens it where it is edited, a right-click gives the tree's
own menu, dragging a node only moves it (remembered per user, `schematic.view_state`),
and drawing a wire from a study's output onto another study opens the transfer form, and
any output onto an operator's input wires it (ADR 0062); dropping a geometry on a
component adds it as a part. All are ordinary commands, so they are undoable and
logged. Tab, or a double-click on the empty canvas, finds an operator by name.

The window shows it twice: the Schematic tab beside Model, Geometry and Mesh puts it in
the centre in place of the 3D view, and Window → Schematic docks one beside the view.
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle import schematic as sch
from puzzle.gui.theme import current as tokens
from puzzle.schematic.layout import TITLE, layout, rows

DIM = 0.35  # what is not upstream (or downstream) of the selection
STATES = [
    ("Everything", ""),
    ("Out of date", "out_of_date"),
    ("Missing input", "missing"),
    ("Failed", "failed"),
    ("Not run yet", "not_run"),
    ("Up to date", "up_to_date"),
]


def _colours():
    from puzzle.gui import theme

    return theme.current()


def _state_colour(state: str) -> QColor:
    from puzzle.gui.icons import STATE_DRAWINGS

    return QColor(getattr(tokens(), STATE_DRAWINGS.get(state, STATE_DRAWINGS["complete"])[0]))


# What a wire may be drawn from: a study (into a study: a transfer), and anything with
# an output an operator takes (ADR 0062).
WIRES_FROM = (
    "study",
    "runs",
    "result",
    "operator",
    "parameters",
    "geometry",
    "material",
    "mesh",
    "component",
    "axes",
    "amplitude",
    "bolt_type",
    "construction",
    "field",
    "probe",
)  # ADR 0071 decision 8: each can be wired into something


def _socket(painter: QPainter, shape: str, at: QPointF) -> None:
    """A port's socket in its type's shape (ADR 0062 item 2)."""
    from PySide6.QtGui import QPolygonF

    x, y = at.x(), at.y()
    if shape == "diamond":
        painter.drawPolygon(QPolygonF([QPointF(x, y - 5.5), QPointF(x + 5.5, y),
                                       QPointF(x, y + 5.5), QPointF(x - 5.5, y)]))  # fmt: skip
    elif shape == "square":
        painter.drawRect(QRectF(x - 4.5, y - 4.5, 9, 9))
    elif shape == "triangle":
        painter.drawPolygon(QPolygonF([QPointF(x - 4.5, y - 5.5), QPointF(x + 5.5, y),
                                       QPointF(x - 4.5, y + 5.5)]))  # fmt: skip
    else:
        painter.drawEllipse(at, 4.5, 4.5)


def _text(
    painter: QPainter,
    font: QFont,
    colour: QColor,
    band: QRectF,
    text: str,
    width: float,
    align=Qt.AlignmentFlag.AlignLeft,
) -> None:
    """Text in `band`, cut short with "…" by its own font's measure (SCH-5: bold text
    measured as plain overran the box and was clipped mid-letter)."""
    from PySide6.QtGui import QFontMetricsF

    painter.setFont(font)
    painter.setPen(colour)
    shown = QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, max(width, 8.0))
    painter.drawText(band, align | Qt.AlignmentFlag.AlignVCenter, shown)


def _title_tint(node, look: str) -> QBrush | None:
    """The title bar's mark of what will happen (SCH-UX-8): hatched amber when out of
    date, red when failed or missing, a check's verdict when current; else none."""
    t = _colours()
    if node.state == "out_of_date" or look == "stale":
        return QBrush(QColor(t.needed), Qt.BrushStyle.BDiagPattern)
    if node.state in ("failed", "missing") or look == "error":
        colour = QColor(t.error)
    elif look in ("pass", "fail"):
        colour = QColor(t.verdicts[look])
    else:
        return None
    colour.setAlpha(48)
    return QBrush(colour)


def _fraction(low: float, high: float, value: float) -> float:
    return min(max((value - low) / (high - low), 0.0), 1.0) if high > low else 0.0


def _flow_colour(carries: str) -> QColor:
    return QColor(sch.FLOW_COLOURS[sch.flow_of(carries)]) if carries else QColor(tokens().muted)


class NodeItem(QGraphicsObject):
    """One node: a box with its title, state, chips and ports, as `layout.rows` says."""

    def __init__(self, panel: SchematicPanel, node: sch.Node, size) -> None:
        super().__init__()
        self.panel, self.node = panel, node
        self.w, self.h = size
        self.unfolded = node.id in panel.view.unfolded
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)  # fmt: skip
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(2)
        tip = f"{sch.KIND_WORDS.get(node.kind, node.kind)}: {node.label}"
        from puzzle.project.status import WORDS

        tip += f"\n{WORDS.get(node.state, node.state)}" + (f": {node.reason}" if node.reason
                                                            else "")  # fmt: skip
        for chip, text in node.tips.items():
            tip += f"\n{chip}: {text.lower()}"
        if node.value:  # the whole value: the node may show it cut short (SCH-5)
            tip += "\n" + sch.value_look(node)[1]
        if node.help:  # an operator: what it does, an example, its ports (ADR 0071)
            tip += "\n\n" + node.help
        self.setToolTip(tip)
        self._tip = tip
        self._pressed_at: QPointF | None = None

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt's name
        return QRectF(-6, -2, self.w + 12, self.h + 4)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        t = _colours()
        node = self.node
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0, 0, self.w, self.h)
        selected = self.isSelected()
        painter.setPen(QPen(QColor(t.accent if selected else t.border), 2 if selected else 1))
        painter.setBrush(QColor(t.sunken))
        painter.drawRoundedRect(box, 6, 6)
        head = QPainterPath()
        head.addRoundedRect(QRectF(0.5, 0.5, self.w - 1, TITLE), 6, 6)
        painter.fillPath(head, QColor(t.raised))
        look, value_text = sch.value_look(node)
        tint = _title_tint(node, look)  # SCH-UX-8: the state on the title, not a 4 px bar
        if tint is not None:
            painter.fillPath(head, tint)
        state = _state_colour(node.state)
        painter.fillRect(QRectF(0, 0, 4, self.h), state)
        bold, plain, small = QFont(painter.font()), QFont(painter.font()), QFont(painter.font())
        bold.setBold(True)
        small.setPointSizeF(max(plain.pointSizeF() - 1.5, 7.0))
        for r in rows(node, self.unfolded):
            band = QRectF(10, r.top, self.w - 20, r.height)
            if r.kind == "title":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(state)
                painter.drawEllipse(QPointF(self.w - 12, TITLE / 2), 4, 4)
                _text(painter, bold, QColor(t.text), band, r.text, self.w - 40)
            elif r.kind == "value":  # SCH-3: a verdict's colour only when it is current
                font = QFont(bold)
                if look in ("stale", "working", "not_run"):
                    font.setBold(False)
                    font.setItalic(True)
                colour = {"pass": t.verdicts["pass"], "fail": t.verdicts["fail"],
                          "error": t.error, "stale": t.muted, "working": t.muted,
                          "not_run": t.muted}.get(look, t.text)  # fmt: skip
                _text(painter, font, QColor(colour), band, value_text, self.w - 20)
            elif r.kind == "slider" and node.slider is not None:  # its knob (ADR 0062)
                low, high, value = node.slider
                left, right = 14.0, self.w - 14.0
                painter.setPen(QPen(QColor(t.border), 3))
                painter.drawLine(QPointF(left, r.middle), QPointF(right, r.middle))
                at = left + (right - left) * _fraction(low, high, value)
                painter.setPen(QPen(QColor(t.accent), 3))
                painter.drawLine(QPointF(left, r.middle), QPointF(at, r.middle))
                painter.setPen(QPen(QColor(t.sunken), 1.5))
                painter.setBrush(QColor(t.accent))
                painter.drawEllipse(QPointF(at, r.middle), 6, 6)
            elif r.kind == "sketch":  # a curve's small plot (ADR 0071)
                frame = QRectF(12, r.top + 4, self.w - 24, r.height - 8)
                painter.setPen(QPen(QColor(t.border), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(frame)
                series = t.series
                for k, line in enumerate(node.sketch):
                    painter.setPen(QPen(QColor(series[k % len(series)]), 1.5))
                    q = [
                        QPointF(
                            frame.left() + x * frame.width(), frame.bottom() - y * frame.height()
                        )
                        for x, y in line
                    ]
                    for a, b in zip(q, q[1:], strict=False):
                        painter.drawLine(a, b)
            elif r.kind in ("subtitle", "badge", "used", "fold", "part"):
                _text(painter, small, QColor(t.muted), band, r.text, self.w - 20)
            elif r.kind == "chip":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.raised))
                painter.drawRoundedRect(QRectF(8, r.top + 2, self.w - 16, r.height - 4), 7, 7)
                _text(painter, small, QColor(t.muted), QRectF(14, r.top, self.w - 28, r.height),
                      r.text, self.w - 28)  # fmt: skip
            elif r.kind in ("in", "out"):
                port = node.port(r.key)
                colour = QColor(sch.port_colour(port)) if port is not None and (
                    port.type or port.carries) else _flow_colour("")  # fmt: skip
                x = self.w if r.kind == "out" else 0.0
                lit = self.panel.socket_lit(node.id, r.key, r.kind)
                if lit is False:  # a wire is drawn that it doesn't take (SCH-UX-3)
                    colour.setAlpha(60)
                painter.setPen(QPen(QColor(t.accent if lit else t.sunken), 2.5 if lit else 1.5))
                painter.setBrush(colour)
                if lit:
                    painter.save()
                    painter.translate(x, r.middle)
                    painter.scale(1.4, 1.4)
                    _socket(painter, sch.socket_shape(port), QPointF(0, 0))
                    painter.restore()
                else:
                    _socket(painter, sch.socket_shape(port), QPointF(x, r.middle))
                align = (
                    Qt.AlignmentFlag.AlignRight if r.kind == "out" else Qt.AlignmentFlag.AlignLeft
                )
                _text(painter, plain, QColor(t.text), band, r.text, self.w - 20, align)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Over a socket, what that port takes or gives; elsewhere, the node's card."""
        pos, tip = event.pos(), self._tip
        for r in rows(self.node, self.unfolded):  # anywhere along a port's row
            if r.kind in ("in", "out") and abs(pos.y() - r.middle) <= r.height / 2:
                port = self.node.port(r.key)
                tip = port.help if port is not None and port.help else self._tip
                break
        if tip != self.toolTip():
            self.setToolTip(tip)
        super().hoverMoveEvent(event)

    def port_at(self, pos: QPointF, side: str = "out"):
        """The port on `side` under `pos` (in the item's coordinates), if any."""
        x = self.w if side == "out" else 0.0
        for r in rows(self.node, self.unfolded):
            if r.kind == side and abs(pos.x() - x) <= 9 and abs(pos.y() - r.middle) <= 8:
                return r.key
        return None

    def input_near(self, scene_pos: QPointF):
        """The input port whose row is under a scene point (anywhere along the row, so
        a wire dropped on the node's label finds it)."""
        pos = self.mapFromScene(scene_pos)
        for r in rows(self.node, self.unfolded):
            if r.kind == "in" and r.top <= pos.y() < r.top + r.height:
                return r.key
        return None

    def anchor(self, port: str, side: str) -> QPointF:
        middle = TITLE / 2
        if port:
            row = next((r for r in rows(self.node, self.unfolded)
                        if r.key == port and r.kind in ("in", "out")), None)  # fmt: skip
            if row is not None:
                middle = row.middle
        return self.mapToScene(QPointF(self.w if side == "out" else 0.0, middle))

    def itemChange(self, change, value):  # noqa: N802 - Qt's name
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.panel.node_moved(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        port = self.port_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else None
        if port is not None and self.node.kind in WIRES_FROM:
            self.panel.start_wire(self, port)
            event.accept()
            return
        knob = self.knob_row()
        if knob is not None and knob.top <= event.pos().y() < knob.top + knob.height:
            self._sliding = True  # dragging the knob, not the node
            self.panel.slide(self.node, self.knob_value(event.pos().x()))
            event.accept()
            return
        self._pressed_at = self.pos()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # SCH-UX-1: Shift-click adds to the selection as Ctrl-click does (Qt's own
            # Ctrl toggles the item and keeps the rest).
            event.setModifiers(event.modifiers() | Qt.KeyboardModifier.ControlModifier)
        super().mousePressEvent(event)

    def knob_row(self):
        if self.node.slider is None:
            return None
        return next((r for r in rows(self.node, self.unfolded) if r.kind == "slider"), None)

    def knob_value(self, x: float) -> float:
        """The value under x along the knob's track (SI)."""
        low, high, _ = self.node.slider
        left, right = 14.0, self.w - 14.0
        f = min(max((x - left) / max(right - left, 1.0), 0.0), 1.0)
        return low + f * (high - low)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.panel.drawing is not None:
            self.panel.wire_to(event.scenePos())
            return
        if getattr(self, "_sliding", False):
            self.panel.slide(self.node, self.knob_value(event.pos().x()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.panel.drawing is not None:
            self.panel.end_wire(event.scenePos())
            return
        if getattr(self, "_sliding", False):
            self._sliding = False
            self.panel.slide_done(self.node)
            return
        adding = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                           | Qt.KeyboardModifier.ShiftModifier))  # fmt: skip
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            event.setModifiers(event.modifiers() | Qt.KeyboardModifier.ControlModifier)
        super().mouseReleaseEvent(event)
        fold = next((r for r in rows(self.node, self.unfolded) if r.kind == "fold"), None)
        start, self._pressed_at = self._pressed_at, None
        if start is not None and start != self.pos():
            self.panel.dropped(self, start)  # every selected node went with it
            self.panel.chosen(self.node, add=True)
            return
        if fold is not None and fold.top <= event.pos().y() < fold.top + fold.height:
            self.panel.toggle_parts(self.node.id)
        if adding and not self.isSelected():  # Ctrl-clicked off: the rest stay chosen
            self.panel._scene_selected()
            return
        self.panel.chosen(self.node, add=adding)

    def row_at(self, pos: QPointF):
        """The row under a point in the item's coordinates."""
        return next((r for r in rows(self.node, self.unfolded)
                     if r.top <= pos.y() < r.top + r.height), None)  # fmt: skip

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """On a typed input's row, or an operator's own setting, edit it here (SCH-UX-2);
        elsewhere open the node where it is edited."""
        row = self.row_at(event.pos())
        if row is not None and self.panel.edit_on_node(self, row):
            event.accept()
            return
        self.panel.open_node(self.node)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.panel.menu_for(self.node, event.screenPos())


def _open(op, key: str) -> bool:
    """Whether an operator's input is neither wired nor typed in."""
    inp = (op.inputs or {}).get(key)
    return inp is None or (inp.source is None and inp.value is None)


def _uuid_of(text: str):
    from uuid import UUID

    return UUID(text)


class WireItem(QGraphicsPathItem):
    def __init__(self, wire: sch.Wire, a: NodeItem, b: NodeItem) -> None:
        super().__init__()
        self.wire, self.a, self.b = wire, a, b
        self.setZValue(1 if wire.kind == "data" else 0)
        if wire.kind != "drives":  # chosen with a click, removed with Delete (SCH-2)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        if wire.kind == "data":
            self.setPen(QPen(QColor(sch.wire_colour(wire)), 2.5))
            if wire.label:
                self.setToolTip(wire.label)
        elif wire.kind == "drives":  # an optimisation runs its study (ADR 0063)
            self.setPen(QPen(QColor(_colours().accent), 1.5, Qt.PenStyle.DashLine))
            self.setToolTip("Runs this study once per point it chooses")
        else:
            self.setPen(QPen(QColor(_colours().muted), 1))
        self.label = None
        self.update_path()

    def shape(self) -> QPainterPath:
        """A 10 px band along the wire, so a thin one can be hit (SCH-2)."""
        from PySide6.QtGui import QPainterPathStroker

        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """A use wire (a reference): Remove it (ADR 0071 decision 8); a data wire: what
        it carries unwired, or its ends gone to (SCH-2)."""
        if self.wire.kind == "use":
            self.a.panel.unlink(self.a.node, self.b.node, event.screenPos())
            event.accept()
            return
        if self.wire.kind == "data":
            self.a.panel.wire_menu(self, event.screenPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def update_path(self) -> None:
        p0 = self.a.anchor(self.wire.source_port, "out")
        p1 = self.b.anchor(self.wire.target_port, "in")
        bend = max(40.0, abs(p1.x() - p0.x()) / 2)
        path = QPainterPath(p0)
        path.cubicTo(p0.x() + bend, p0.y(), p1.x() - bend, p1.y(), p1.x(), p1.y())
        self.setPath(path)
        if self.label is not None:
            mid = path.pointAtPercent(0.5)
            rect = self.label.boundingRect()
            self.label.setPos(mid.x() - rect.width() / 2, mid.y() - rect.height() - 1)


class FrameItem(QGraphicsObject):
    """A tree group: a dashed frame around its nodes; a click on its title folds it."""

    def __init__(self, panel: SchematicPanel, group: sch.Group, items: list[NodeItem]) -> None:
        super().__init__()
        self.panel, self.group, self.items = panel, group, items
        self.setZValue(-1)
        self.setToolTip(f"Group {group.label}: click its title to fold it into one node")
        self.rect = QRectF()
        self.refit()

    def refit(self) -> None:
        self.prepareGeometryChange()
        boxes = [i.sceneBoundingRect() for i in self.items]
        r = boxes[0]
        for b in boxes[1:]:
            r = r.united(b)
        self.rect = r.adjusted(-8, -24, 8, 8)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt's name
        return self.rect

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        t = _colours()
        pen = QPen(QColor(t.border), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect, 8, 8)
        painter.setPen(QColor(t.muted))
        painter.drawText(self.rect.adjusted(10, 4, -10, 0), Qt.AlignmentFlag.AlignTop,
                         f"▾ {self.group.label}")  # fmt: skip

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.pos().y() < self.rect.top() + 22:
            self.panel.toggle_group(self.group.id)
            event.accept()
        else:
            event.ignore()


class SchematicScene(QGraphicsScene):
    def __init__(self, panel: SchematicPanel) -> None:
        super().__init__()
        self.panel = panel

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.panel.drawing is not None:
            self.panel.wire_to(event.scenePos())
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self._pressed = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.panel.drawing is not None:
            self.panel.end_wire(event.scenePos())
            return
        pressed = getattr(self, "_pressed", None)
        clicked = pressed is not None and (event.scenePos() - pressed).manhattanLength() < 4
        if clicked and self.itemAt(event.scenePos(), self.views()[0].transform()) is None:
            self.panel.chosen(None)  # a click on nothing; a rubber band keeps its choice
        super().mouseReleaseEvent(event)


# The keys the canvas keeps for itself while it has the focus (SCH-UX-1), before the
# window's own shortcuts (Delete, Duplicate) see them.
CANVAS_KEYS = {
    (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_F, Qt.KeyboardModifier.ShiftModifier),
    (Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
}


def _key_of(event) -> tuple:
    mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    if mods & Qt.KeyboardModifier.MetaModifier and not mods & Qt.KeyboardModifier.ControlModifier:
        mods = Qt.KeyboardModifier.ControlModifier  # ⌘ on macOS reads as Meta under Qt
    return (Qt.Key(event.key()), Qt.KeyboardModifier(mods))


class SchematicCanvas(QGraphicsView):
    """Drag the background to choose with a rubber band; pan with the middle button or
    Space and drag; zoom with the wheel; right-click the background for New (what the
    tree can add), placed there. Keys: Delete, Ctrl+A, Ctrl+C, Ctrl+V, Ctrl+D, F or
    Home (fit all), Shift+F (fit the selection), Ctrl+F (find), Esc, Left and Right
    (along the wires)."""

    panel = None
    MODE = QGraphicsView.DragMode.RubberBandDrag

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.MiddleButton:  # pan, whatever the mode
            self._pan_from(event)
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.MiddleButton and getattr(self, "_panning", False):
            from PySide6.QtGui import QMouseEvent

            self._panning = False
            super().mouseReleaseEvent(QMouseEvent(
                event.type(), event.position(), event.globalPosition(),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, event.modifiers()))  # fmt: skip
            self.setDragMode(self.MODE)
            return
        super().mouseReleaseEvent(event)

    def _pan_from(self, event) -> None:
        """Qt's hand drag pans with the left button only: the middle press is handed on
        as a left one with that mode on, put back on release."""
        from PySide6.QtGui import QMouseEvent

        self._panning = True
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(QMouseEvent(
            event.type(), event.position(), event.globalPosition(), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, event.modifiers()))  # fmt: skip

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # Space + drag pans
            return
        inline = getattr(self.panel, "inline", None) if self.panel is not None else None
        if inline is not None:  # typing on a node: its own keys; Esc leaves it unchanged
            if event.key() == Qt.Key.Key_Escape:
                inline._finish(False)
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if self.panel is not None and self.panel.key(_key_of(event)):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(self.MODE)
            return
        super().keyReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.itemAt(event.pos()) is None and self.panel is not None:
            menu = QMenu(self)
            menu.setToolTipsVisible(True)
            self.panel.fill_new_menu(menu, self.mapToScene(event.pos()))
            menu.exec(event.globalPos())
            return
        super().contextMenuEvent(event)

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(self.MODE)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(_colours().surface)))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def event(self, event) -> bool:
        """Tab opens the operator palette (ADR 0062), not the next widget's focus; the
        canvas's own keys are kept from the window's shortcuts while it has the focus."""
        from PySide6.QtCore import QEvent

        editing = self.panel is not None and getattr(self.panel, "inline", None) is not None
        if editing and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # an edit on a node takes every key, Delete and Ctrl+C too
            return True
        if event.type() == QEvent.Type.ShortcutOverride and _key_of(event) in CANVAS_KEYS:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            panel = getattr(self.scene(), "panel", None)
            if panel is not None and panel.window.session is not None:
                centre = self.mapToScene(self.viewport().rect().center())
                panel.open_palette(centre)
                return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """A double-click on the empty canvas opens the operator palette there."""
        panel = getattr(self.scene(), "panel", None)
        if self.itemAt(event.position().toPoint()) is None and panel is not None:
            panel.open_palette(self.mapToScene(event.position().toPoint()))
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom(1.15**steps)

    def zoom(self, by: float) -> None:
        now = self.transform().m11()
        by = min(max(by, 0.1 / now), 3.0 / now)
        self.scale(by, by)


class Minimap(QGraphicsView):
    """The whole picture, small, with the part shown in the main view outlined; a click
    moves the main view there."""

    def __init__(self, scene: QGraphicsScene, main: SchematicCanvas) -> None:
        super().__init__(scene, main)
        self.main = main
        self.setInteractive(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedSize(180, 110)
        self.setToolTip("The whole schematic: click to go there")
        main.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        main.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())

    def refit(self) -> None:
        self.fitInView(self.scene().itemsBoundingRect().adjusted(-20, -20, 20, 20),
                       Qt.AspectRatioMode.KeepAspectRatio)  # fmt: skip

    def drawForeground(self, painter: QPainter, _rect) -> None:  # noqa: N802 - Qt's name
        shown = self.main.mapToScene(self.main.viewport().rect()).boundingRect()
        painter.setPen(QPen(QColor(_colours().accent), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(shown)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.main.centerOn(self.mapToScene(event.position().toPoint()))


# Worked-out values by `_value_key`, shared by the centre's panel and the dock's, and
# the panels waiting on a worker for each key (ADR 0090 package 13, SCH-1).
_SHOWN: dict = {}
_PENDING: dict = {}
_CLIPBOARD: list = []  # operators copied with Ctrl+C, as stored (SCH-UX-1)


def _value_key(project, runs, root) -> str:
    """What the operators' values follow: every object's content hash (an operator's
    covers what it reads, files included), the runs stored, the display units their
    texts are in and the folder files are read from. The same key, the same values:
    a refresh after a selection, a fold or another panel evaluates nothing."""
    import hashlib
    import json

    from puzzle.project.graph import content_hashes
    from puzzle.settings import current

    try:
        hashes = sorted((str(k), v) for k, v in content_hashes(project, root).items())
    except ValueError:  # a transfer loop: no key, so worked out every time
        import uuid

        return uuid.uuid4().hex
    stored = sorted((r.run_id, r.run_key, r.status) for r in runs)
    payload = [hashes, stored, repr(current().display_units()), str(root)]
    return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()


def _evaluate(project, store, keys, root) -> dict:
    """`schematic.values` on a worker. Operators that read the exact geometry hold
    OpenCASCADE's lock while they are worked out (Gotchas: OCC is serialised)."""
    from puzzle.operators import reads_geometry

    if any(o.kind == "operator" and reads_geometry(project, o)
           for o in project.objects.values()):  # fmt: skip
        from puzzle.geometry.session import occ_lock

        with occ_lock:
            return sch.values(project, store, keys, root)
    return sch.values(project, store, keys, root)


def unfold(node, port: str | None):
    """The member node and port a folded group's port stands for (ADR 0071: a cluster's
    ports are its members', keyed "<member id>::<port>"); anything else as given."""
    if node.kind != "group" or not port or "::" not in port:
        return node, port
    member, _, key = port.partition("::")
    inner = next((n for n in getattr(node, "inside", []) if n.id == member), None)
    return (inner, key) if inner is not None else (node, port)


class SchematicPanel(QWidget):
    """The schematic with its bar: add operator, search, state filter, use wires, what
    a change reaches, fit, tidy, export."""

    selected = Signal(str)  # a node id, "" for none

    def __init__(self, window) -> None:
        super().__init__()
        from puzzle.schematic.view_state import ViewState

        self.window = window
        self.schematic = sch.Schematic()
        self.items: dict[str, NodeItem] = {}
        self.wires: list[WireItem] = []
        self.frames: list[FrameItem] = []
        self.view = ViewState()
        self.current: str = ""  # the selected node
        self.drawing = None  # (item, port, temporary path) while a wire is drawn
        self._root = None
        self._fitted = False
        self._key = None  # the values this panel waits for or shows (`_value_key`)
        self.inline = None  # a line edit open on a node (SCH-UX-2)
        self.fits = None  # while a wire is drawn: the sockets that take it (SCH-UX-3)
        self.builds = self.evaluations = 0  # counted, so a test holds SCH-1

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self.search = QLineEdit(placeholderText="Find (Ctrl+F): name, kind, value, state")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._searched())
        self.search.returnPressed.connect(lambda: self._go_to_found())
        self.found = QLabel("")  # "3 of 12" (SCH-6)
        from puzzle.gui import theme as _theme

        _theme.role(self.found, "muted")
        self.state_filter = QComboBox()
        for text, value in STATES:
            self.state_filter.addItem(text, value)
        self.state_filter.setToolTip("Show only nodes in this state; the rest are dimmed")
        self.state_filter.currentIndexChanged.connect(lambda _: self.highlight())
        self.uses = QComboBox()
        from puzzle.settings import BY_KEY

        for text, value in BY_KEY["schematic.uses"].choices:
            self.uses.addItem(f"Use wires: {text.lower()}", value)
        self.uses.setToolTip(BY_KEY["schematic.uses"].help)
        self.uses.currentIndexChanged.connect(self._uses_changed)
        self.reach = QToolButton(text="What a change reaches", checkable=True)
        self.reach.setToolTip(
            "With a node selected: highlight what would go out of date if it changed "
            "(downstream), instead of what it is built from"
        )
        self.reach.toggled.connect(lambda _: self.highlight())
        self.operators = QToolButton(text="Add operator ▾")  # ADR 0062, beside Tab
        # Qt's own arrow overlaps the text on a button sized to it; the label has one
        self.operators.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.operators.setToolTip(
            "Add an operator (numbers, sliders, expressions, tables; reduce, check, "
            "plot, export), wired from the selected node's output when it fits"
        )
        self.operators.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.operators.setMenu(QMenu(self.operators))
        self.operators.menu().setToolTipsVisible(True)
        self.operators.menu().aboutToShow.connect(self._operator_menu)
        self.new = QToolButton(text="New ▾")  # ADR 0071 decision 8: what the tree adds
        self.new.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.new.setToolTip(
            "Add anything the model tree can add (geometry, materials, components, studies, "
            "coordinate systems, result tools ...), placed in view; right-click the empty "
            "canvas to place it there"
        )
        self.new.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.new.setMenu(QMenu(self.new))
        self.new.menu().setToolTipsVisible(True)
        self.new.menu().aboutToShow.connect(lambda: self.fill_new_menu(self.new.menu()))
        self.update_button = QPushButton("Bring up to date")
        self.update_button.setToolTip(
            "Run the studies the selection needs (everything, with nothing selected), "
            "sources of transfers first: values shown as out of date become current"
        )
        self.update_button.clicked.connect(lambda: self.bring_up_to_date())
        self.fit = QPushButton("Fit")
        self.fit.clicked.connect(self.fit_all)
        self.tidy = QPushButton("Tidy layout")
        self.tidy.setToolTip("Forget the places you dragged nodes to and lay them all out again")
        self.tidy.clicked.connect(self.tidy_layout)
        self.export = QPushButton("Export…")
        self.export.setToolTip("Save the picture as SVG or PNG, for a report")
        self.export.clicked.connect(lambda: self.export_picture())
        # Narrow windows squeeze the bar, never widen the window (the centre's minimum).
        self.search.setMinimumWidth(60)
        for button in (self.new, self.operators, self.reach, self.update_button, self.fit,
                       self.tidy, self.export):  # fmt: skip
            button.setMinimumWidth(36)  # its text clips rather than set the minimum
        for combo in (self.state_filter, self.uses):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(6)
        for w in (self.new, self.operators, self.search, self.found, self.state_filter,
                  self.uses, self.reach):  # fmt: skip
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.update_button, self.fit, self.tidy, self.export):
            bar.addWidget(w)
        self.scene = SchematicScene(self)
        self.scene.selectionChanged.connect(self._scene_selected)
        self.canvas = SchematicCanvas(self.scene)
        self.canvas.panel = self
        self._place_next: tuple[float, float] | None = None  # where the next new node goes
        self.minimap = Minimap(self.scene, self.canvas)
        self.empty = QLabel("Open a project to see how it fits together.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from puzzle.gui import theme

        theme.role(self.empty, "muted")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addLayout(bar)
        column.addWidget(self.canvas, 1)
        column.addWidget(self.empty, 1)
        self.canvas.hide()

    # --- building -----------------------------------------------------------------

    def refresh(self) -> None:
        """Build the schematic again from the project as it is now."""
        from puzzle.schematic import view_state
        from puzzle.settings import current

        session = self.window.session
        if session is None:
            self.canvas.hide()
            self.empty.show()
            return
        if self._root != session.root:
            self._root = session.root
            self.view = view_state.load(session.root)
        self._select_uses(current().get("schematic.uses"))
        project = session.project
        try:
            store = self.window._store()
        except Exception:  # noqa: BLE001 - no store: operators show no values
            store = None
        # The window's refresh worked out the runs and their keys for this project once
        # (SCH-1); a refresh of the panel alone (a fold, the tab shown) reads them here.
        fresh = getattr(self.window, "run_records_of", None) is project
        runs = self.window.run_records if fresh else None
        keys = self.window.run_keys if fresh else None
        if runs is None:
            try:
                runs = store.runs() if store is not None else []
            except Exception:  # noqa: BLE001 - an old store: the model still shows
                runs = []
        states = getattr(self.window, "states", None)
        before = set(self.schematic.nodes) if getattr(self, "schematic", None) else None
        old = {n.id: n for n in self._all_nodes()} if before is not None else {}
        self.schematic = sch.build(project, runs, states, root=session.root,
                                   folded=frozenset(self.view.folded),
                                   fields=bool(current().get("connections.schematic")),
                                   store=store, keys=keys, evaluate=False)  # fmt: skip
        self.builds += 1
        self._carry_over(old)
        group_ids = {str(o.id) for o in project.objects.values() if o.kind == "group"}
        if self.view.prune(self._node_ids(project), group_ids):
            view_state.save(session.root, self.view)
        self._values_for(project, runs, store, keys, session.root)
        if self._place_next is not None and before is not None:  # made from the New menu
            made = [i for i in self.schematic.nodes if i not in before]
            if made:
                self.view.positions[made[0]] = self._place_next
                self._place_next = None
                view_state.save(session.root, self.view)
        self._draw()
        self.canvas.setVisible(bool(self.schematic.nodes))
        self.empty.setVisible(not self.schematic.nodes)
        if not self.schematic.nodes:
            self.empty.setText("Nothing to show yet: import geometry or start a new part.")

    def _node_ids(self, project) -> set[str]:
        """Every node id the project has, folded or not (a folded group keeps its
        members), so pruning keeps the places of nodes inside folded groups; from the
        one build, not a second (SCH-1)."""
        s = self.schematic
        inside = {m.id for n in s.nodes.values() if n.kind == "group" for m in n.inside}
        groups = {f"group:{o.id}" for o in project.objects.values() if o.kind == "group"}
        return set(s.nodes) | inside | groups

    # --- values, worked out off the GUI thread (SCH-1) ----------------------------

    def _values_for(self, project, runs, store, keys, root) -> None:
        """The operators' values: the ones already worked out for this project, its runs
        and the display units (`_value_key`), else worked out on a worker while the
        nodes show "working…"; nothing is evaluated on the GUI thread."""
        if not any(n.value_state == "working" for n in self._all_nodes()):
            self._key = None
            return
        key = _value_key(project, runs, root)
        self._key = key
        found = _SHOWN.get(key)
        if found is not None:
            sch.fill(self.schematic, found)
            return
        self.evaluations += 1
        waiting = _PENDING.setdefault(key, [])
        start = not waiting
        waiting.append(self)
        if not start:  # the other panel's worker is on it
            return
        where = store.root if store is not None else None

        def work(_emit):
            from puzzle.results import ResultStore

            own = ResultStore(where) if where is not None else None  # its own reader
            return _evaluate(project, own, keys, root)

        def done(found) -> None:
            _SHOWN[key] = found
            while len(_SHOWN) > 4:
                _SHOWN.pop(next(iter(_SHOWN)))
            for panel in _PENDING.pop(key, []):
                with contextlib.suppress(RuntimeError):  # its window has gone
                    panel._arrived(key, found)

        def failed(_exc) -> None:
            _PENDING.pop(key, None)

        self.window.background("Working out the schematic's values…", work, done,
                               failed=failed, lock=False, quiet=True)  # fmt: skip

    def _carry_over(self, old: dict) -> None:
        """While a node's value is worked out again, the one it had is shown muted as
        "working" (never in a verdict's colour), so the canvas doesn't blank on each
        edit, and a slider's knob can still be dragged."""
        for node in self._all_nodes():
            was = old.get(node.id)
            if node.value_state == "working" and was is not None and was.value:
                node.value, node.verdict, node.sketch = was.value, was.verdict, was.sketch
                node.slider = was.slider

    def _arrived(self, key, found) -> None:
        """Values back from the worker: drawn if they are still the ones wanted (a later
        change has asked for its own)."""
        if key != self._key or self.window.session is None:
            return
        sch.fill(self.schematic, found)
        self._redraw_when_free(key)

    def _redraw_when_free(self, key) -> None:
        """Redraw for values that arrived, but not under a wire being drawn, a node being
        dragged or an edit open on a node: tried again shortly."""
        if key != self._key:
            return
        dragging = any(getattr(i, "_pressed_at", None) is not None for i in self.items.values())
        if self.drawing is not None or self.inline is not None or dragging:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(150, self, lambda: self._redraw_when_free(key))
            return
        self._draw()
        self.selected.emit(self.current)

    def _all_nodes(self):
        for n in self.schematic.nodes.values():
            yield n
            yield from n.inside

    def wait(self, timeout: float = 30.0) -> None:
        """Until the values asked for are drawn (tests, scripts, a picture export)."""
        import time

        from PySide6.QtWidgets import QApplication

        end = time.monotonic() + timeout
        while self._key is not None and self._key in _PENDING and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.002)

    def _draw(self) -> None:
        keep = self.canvas.transform(), self.canvas.horizontalScrollBar().value(), \
            self.canvas.verticalScrollBar().value()  # fmt: skip
        first = not self.items
        chosen = set(self.selection()) if self.items else set()
        self._syncing = True  # clearing the scene drops its selection: kept below
        self.scene.clear()
        self.items, self.wires, self.frames = {}, [], []
        s = self.schematic
        lay = layout(s, self.view.positions, frozenset(self.view.unfolded))
        self.lay = lay
        t = _colours()
        for name, x in lay.columns:
            heading = self.scene.addText(name)
            heading.setDefaultTextColor(QColor(t.muted))
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            heading.setPos(x, -34)
        for nid, node in s.nodes.items():
            item = NodeItem(self, node, lay.sizes[nid])
            item.setPos(*lay.positions[nid])
            self.scene.addItem(item)
            self.items[nid] = item
        for w in s.wires:
            a, b = self.items.get(w.source), self.items.get(w.target)
            if a is None or b is None:
                continue
            item = WireItem(w, a, b)
            self.scene.addItem(item)
            if w.kind == "data" and w.label:
                label = self.scene.addSimpleText(w.label)
                label.setBrush(QColor(sch.wire_colour(w)))
                font = label.font()
                font.setPointSizeF(max(font.pointSizeF() - 1.5, 7.0))
                label.setFont(font)
                label.setZValue(3)
                item.label = label
                item.update_path()
            self.wires.append(item)
        for g in s.groups:
            members = [self.items[m] for m in g.members if m in self.items]
            if members and not g.folded:
                frame = FrameItem(self, g, members)
                self.scene.addItem(frame)
                self.frames.append(frame)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-200, -200, 200, 200))
        for nid in chosen | {self.current}:
            if nid in self.items:
                self.items[nid].setSelected(True)
        if self.current not in self.items:
            self.current = ""
        self._syncing = False
        self.highlight()
        if first:
            self.fit_all()
        else:
            self.canvas.setTransform(keep[0])
            self.canvas.horizontalScrollBar().setValue(keep[1])
            self.canvas.verticalScrollBar().setValue(keep[2])
        self.minimap.refit()
        self._place_minimap()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._place_minimap()

    def _place_minimap(self) -> None:
        big = len(self.items) > 12
        self.minimap.setVisible(big)
        vp = self.canvas.viewport()
        self.minimap.move(vp.width() - self.minimap.width() - 8,
                          vp.height() - self.minimap.height() - 8)  # fmt: skip

    # --- what is shown ------------------------------------------------------------

    def _select_uses(self, value) -> None:
        i = self.uses.findData(value)
        if i >= 0 and i != self.uses.currentIndex():
            self.uses.blockSignals(True)
            self.uses.setCurrentIndex(i)
            self.uses.blockSignals(False)

    def _uses_changed(self) -> None:
        from puzzle.settings import current

        prefs = current()
        prefs.set("schematic.uses", self.uses.currentData())
        with contextlib.suppress(OSError):  # a read-only home: it holds for this session
            prefs.save()
        self.highlight()

    def lit(self) -> set[str] | None:
        """The nodes drawn in full: the selection and what it is built from (or what it
        reaches), the search's matches, the state filter's; None: all."""
        found: set[str] | None = None
        if self.current and self.current in self.schematic.nodes:
            s = self.schematic
            reach = s.downstream if self.reach.isChecked() else s.upstream
            # Its own wires' other ends too: selecting a geometry shows its users.
            ends = {w.target if w.source == self.current else w.source for w in s.wires
                    if self.current in (w.source, w.target)}  # fmt: skip
            found = {self.current} | reach(self.current) | ends
        match = self.matches()
        if match is not None:  # a search lights what it finds, wherever it is (SCH-6)
            found = set(match)
        state = self.state_filter.currentData()
        if state:
            match = {k for k, n in self.schematic.nodes.items() if n.state == state}
            found = match if found is None else found & match
        return found

    def highlight(self) -> None:
        lit = self.lit()
        for nid, item in self.items.items():
            item.setOpacity(1.0 if lit is None or nid in lit else DIM)
        uses = self.uses.currentData() or "all"
        around = {self.current} | (self.schematic.upstream(self.current)
                                   | self.schematic.downstream(self.current)
                                   if self.current else set())  # fmt: skip
        for w in self.wires:
            own = bool(self.current) and self.current in (w.wire.source, w.wire.target)
            on = lit is None or own or (w.wire.source in lit and w.wire.target in lit)
            shown = True
            if w.wire.kind == "use":
                if uses == "none":
                    shown = False
                elif uses == "selected":  # the selection's chain, up and down
                    shown = bool(self.current) and w.wire.source in around \
                        and w.wire.target in around  # fmt: skip
            w.setVisible(shown)
            w.setOpacity(1.0 if on else DIM)
            if w.label is not None:
                w.label.setVisible(shown)
                w.label.setOpacity(1.0 if on else DIM)
            if w.wire.kind == "use":  # the selection's own wires come forward
                w.setZValue(1.5 if own else 0)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        if not self._fitted:  # the first fit wants the panel's real size
            self.fit_all()

    def fit_all(self) -> None:
        self._fitted = self.isVisible() and bool(self.items)
        if self.items:
            self.canvas.fitInView(self.scene.itemsBoundingRect().adjusted(-30, -50, 30, 30),
                                  Qt.AspectRatioMode.KeepAspectRatio)  # fmt: skip
            if self.canvas.transform().m11() > 1.0:
                self.canvas.resetTransform()
                self.canvas.centerOn(self.scene.itemsBoundingRect().center())

    def matches(self) -> list[str] | None:
        """The nodes the search box finds, top to bottom, left to right; None without a
        search. Every word must be in the node's name, kind, subtitle, value, badge or
        state (SCH-6: "pass", "MPa", "out of date" and an operator's type find nodes)."""
        from puzzle.project.status import WORDS

        words = self.search.text().strip().lower().split()
        if not words:
            return None
        found = []
        for k, n in self.schematic.nodes.items():
            hay = " ".join((n.label, sch.KIND_WORDS.get(n.kind, ""), n.subtitle, n.value,
                            n.badge, WORDS.get(n.state, ""), n.verdict)).lower()  # fmt: skip
            if all(w in hay for w in words):
                found.append(k)

        def place(k: str):
            item = self.items.get(k)
            return (item.pos().x(), item.pos().y()) if item is not None else (0.0, 0.0)

        return sorted(found, key=place)

    def _go_to_found(self, back: bool | None = None) -> str:
        """Enter: the next match (Shift+Enter the one before), centred and selected; the
        count beside the box says which of how many."""
        from PySide6.QtWidgets import QApplication

        found = self.matches() or []
        if back is None:
            back = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not found:
            self.found.setText("none" if self.search.text().strip() else "")
            return ""
        at = found.index(self.current) if self.current in found else -1
        nid = found[(at - 1) % len(found)] if back else found[(at + 1) % len(found)]
        self.found.setText(f"{found.index(nid) + 1} of {len(found)}")
        if nid in self.items:
            self.canvas.centerOn(self.items[nid])
            self.chosen(self.items[nid].node)
            self.search.setFocus()
        return nid

    def _searched(self) -> None:
        found = self.matches()
        self.found.setText("" if found is None else (f"{len(found)} found" if found
                                                       else "none"))  # fmt: skip
        self.highlight()

    def tidy_layout(self) -> None:
        from puzzle.schematic import view_state

        self.view.positions.clear()
        if self._root is not None:
            view_state.save(self._root, self.view)
        self._draw()
        self.fit_all()

    def toggle_parts(self, node_id: str) -> None:
        self._toggle(self.view.unfolded, node_id)

    def toggle_group(self, group_id: str) -> None:
        self._toggle(self.view.folded, group_id)
        self.refresh()

    def _toggle(self, held: set[str], key: str) -> None:
        from puzzle.schematic import view_state

        held.symmetric_difference_update({key})
        if self._root is not None:
            view_state.save(self._root, self.view)
        if held is self.view.unfolded:
            self._draw()

    # --- moving -------------------------------------------------------------------

    def node_moved(self, item: NodeItem) -> None:
        for w in self.wires:
            if w.a is item or w.b is item:
                w.update_path()
        for f in self.frames:
            if item in f.items:
                f.refit()

    def dropped(self, item: NodeItem, start: QPointF) -> None:
        """A node dragged: kept there for this user, or, a geometry dropped on a
        component, offered as a new part of it."""
        from puzzle.schematic import view_state

        moved = [i for i in self.items.values() if i.isSelected() or i is item]
        target = self._node_under(item.sceneBoundingRect().center(), not_item=item)
        if len(moved) == 1 and target is not None and self._linkable(item.node, target.node):
            item.setPos(start)  # dropped on what it can be used by: wire it in
            self.link(item.node, target.node)
            return
        for each in moved:  # every selected node went with it (SCH-UX-1)
            pos = each.pos()
            self.view.positions[each.node.id] = (round(pos.x(), 1), round(pos.y(), 1))
        if self._root is not None:
            view_state.save(self._root, self.view)

    def _node_under(self, point: QPointF, not_item=None) -> NodeItem | None:
        for found in self.scene.items(point):
            while found is not None and not isinstance(found, NodeItem):
                found = found.parentItem()
            if isinstance(found, NodeItem) and found is not not_item:
                return found
        return None

    # --- drawing wires (phase 2) --------------------------------------------------

    def start_wire(self, item: NodeItem, port: str) -> None:
        path = QGraphicsPathItem()
        port_obj = item.node.port(port)
        pen = QPen(QColor(sch.port_colour(port_obj)), 2.5, Qt.PenStyle.DashLine)
        path.setPen(pen)
        path.setZValue(4)
        self.scene.addItem(path)
        self.drawing = (item, port, path)
        self.canvas.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.fits = self.sockets_for(item.node, port)
        for each in self.items.values():  # SCH-UX-3: what takes it lit, the rest dimmed
            each.update()

    def sockets_for(self, node: sch.Node, port: str) -> dict:
        """While a wire is drawn from `node`'s `port`: {node id: the input ports that
        take it ("" for the node itself: a reference it can be used in)}, so those
        sockets are lit and the rest dimmed (SCH-UX-3)."""
        from uuid import UUID

        from puzzle.operators import TYPES, accepts, output_type

        project = self.window.session.project
        source, port = unfold(node, port)
        oid, oport = self.source_of(source, port)
        given = None
        if oid:
            with contextlib.suppress(ValueError):
                given = output_type(project, UUID(oid), oport)
        out: dict[str, set] = {}
        for nid, item in self.items.items():
            if nid == node.id:
                continue
            target = item.node
            if target.kind == "operator" and given is not None:
                op = project.objects.get(UUID(target.object))
                if op is not None:
                    keys = {p.key for p in TYPES[op.type].ports_for(op) if accepts(p, given)}
                    if keys:
                        out[nid] = keys
            elif source.kind == "study" and target.kind == "study":
                out[nid] = {""}  # a transfer
            elif source.kind in ("operator", "parameters") and target.kind in ("geometry", "study"):
                out[nid] = {""}  # a history argument, a sweep
            elif source.kind != "operator" and self._linkable(source, target):
                out[nid] = {""}
        return out

    def socket_lit(self, node_id: str, key: str, side: str):
        """None when no wire is being drawn; else whether this socket takes it (an input
        that accepts it, or any input of a node it can be used in)."""
        fits = getattr(self, "fits", None)
        if fits is None:
            return None
        if side != "in":
            return None
        found = fits.get(node_id, set())
        return key in found or "" in found

    def refusal(self, target: NodeItem, point: QPointF) -> str:
        """Why the input under a dragged wire won't take it, in words; "" when it will."""
        from uuid import UUID

        from puzzle.operators import TYPE_WORDS, TYPES, accepts, output_type

        if self.drawing is None or target.node.kind != "operator":
            return ""
        item, port, _ = self.drawing
        key = target.input_near(point)
        if key is None:
            return ""
        project = self.window.session.project
        oid, oport = self.source_of(*unfold(item.node, port))
        op = project.objects.get(UUID(target.node.object))
        spec = TYPES[op.type].input(key) if op is not None else None
        given = output_type(project, UUID(oid), oport) if oid else None
        if spec is None or given is None or accepts(spec, given):
            return ""
        takes = " or ".join(TYPE_WORDS[t].lower() for t in spec.types)
        return f"{spec.label} takes a {takes}; this is a {TYPE_WORDS[given].lower()}"

    def wire_to(self, point: QPointF) -> None:
        item, port, path = self.drawing
        p0 = item.anchor(port, "out")
        shape = QPainterPath(p0)
        bend = max(40.0, abs(point.x() - p0.x()) / 2)
        shape.cubicTo(p0.x() + bend, p0.y(), point.x() - bend, point.y(), point.x(), point.y())
        path.setPath(shape)
        over = self._node_under(point, not_item=item)
        why = self.refusal(over, point) if over is not None else ""
        if why:
            self.window.statusBar().showMessage(why, 3000)

    def end_wire(self, point: QPointF) -> None:
        item, port, path = self.drawing
        self.drawing = None
        self.scene.removeItem(path)
        self.canvas.setDragMode(self.canvas.MODE)
        self.fits = None
        for each in self.items.values():
            each.update()
        target = self._node_under(point, not_item=item)
        if target is None:
            if item.node.kind == "component":  # dragged out to the canvas: a new study
                self.study_from(item.node)
                return
            # SCH-UX-3: let go on nothing, the palette there, what fits first, wired in
            source, sport = self.source_of(*unfold(item.node, port))
            if source and self.window.session is not None:
                from puzzle.gui import operators

                return operators.open_palette(self.window, (point.x(), point.y()), source,
                                              sport)  # fmt: skip
            return
        source, port = unfold(item.node, port)  # a folded cluster: its member's port
        into, key = unfold(target.node, target.input_near(point))
        if into.kind == "operator":  # ADR 0062: into one of its inputs
            self.wire_operator(source, port, into, key)
            return
        if source.kind in ("operator", "parameters") and into.kind == "geometry":
            self.drive_geometry(source, port, into)  # a history argument
            return
        if source.kind == "operator" and into.kind == "study":
            self.sweep_study(source, port, into)  # a sweep over it
            return
        if into.kind == "study" and source.kind == "study":
            self.make_transfer(source, port, into)
            return
        self.link(source, into)  # any other reference (ADR 0071 decision 8)

    def study_from(self, component: sch.Node):
        """A wire dragged out of a component onto the empty canvas: the New study form
        with that component chosen (ADR 0071 decision 4)."""
        from puzzle.gui.menus import new_study

        return new_study(self.window, component.label)

    def source_of(self, node: sch.Node, port: str) -> tuple[str | None, str]:
        """(object id, port) an operator reads for a node's output: a results node's
        study, a derived result's or operator's own."""
        if node.kind == "runs":
            return node.id.removeprefix("runs:"), port or "result"
        if node.kind in ("study", "result", "operator", "parameters"):
            return node.object, port or ("result" if node.kind in ("study", "result")
                                         else "out")  # fmt: skip
        return None, port

    def wire_operator(self, source: sch.Node, port: str, target: sch.Node, key: str | None):
        """A wire dropped on an operator: into the input under it, else the first that
        takes it. One that fits nowhere says why and changes nothing."""
        from uuid import UUID

        from puzzle.gui.operators import wire
        from puzzle.operators import TYPE_WORDS, TYPES, accepts, output_type

        project = self.window.session.project
        oid, port = self.source_of(source, port)
        op = project.objects.get(UUID(target.object))
        given = output_type(project, UUID(oid), port) if oid else None
        if op is None or given is None:
            return None
        specs = TYPES[op.type].ports_for(op)
        chosen = next((p for p in specs if p.key == key), None)
        if chosen is None or not accepts(chosen, given):
            fits = [p for p in specs if accepts(p, given)]
            # SCH-4: off the row of an input, only an open one is taken; a wired one is
            # replaced only when the wire is let go on its own row.
            chosen = next((p for p in fits if _open(op, p.key)), None)
            if chosen is None and fits:
                names = ", ".join(p.label for p in fits)
                self.window.statusBar().showMessage(
                    f"{op.name}'s {names} {'is' if len(fits) == 1 else 'are'} set already "
                    "(wired or typed): let go on its row to replace it", 8000)  # fmt: skip
                return None
        if chosen is None:
            self.window.statusBar().showMessage(
                f"{op.name} takes no {TYPE_WORDS[given].lower()}", 8000)  # fmt: skip
            return None
        return wire(self.window, str(op.id), chosen.key, oid, port)

    def drive_geometry(self, source: sch.Node, port: str, target: sch.Node):
        """A number dropped on a geometry: which history argument it drives (one form)."""
        from puzzle.gui.operators import drive_argument

        return drive_argument(self.window, source, port, target.object)

    def sweep_study(self, source: sch.Node, port: str, target: sch.Node):
        """An operator's list (or a Number or Slider) dropped on a study: swept over."""
        from puzzle.gui.operators import sweep_over

        return sweep_over(self.window, source.object, port, target.object)

    def slide(self, node: sch.Node, value: float) -> None:
        """A slider's knob dragged: the value previewed, never written (item 4)."""
        self.window.slide(node.object, value)
        item = self.items.get(node.id)
        if item is not None:
            low, high, _ = node.slider
            node.slider = (low, high, self.window.slider_value(node.object))
            node.value = self.window.slider_text(node.object)
            item.update()

    def slide_done(self, node: sch.Node) -> None:
        self.window.slide_done(node.object)

    def palette_source(self) -> tuple[str | None, str]:
        """What the palette wires a new operator from: the selected node's result or
        value (a parameter set has several: none)."""
        node = self.schematic.nodes.get(self.current)
        if node is None or node.kind == "parameters":
            return None, "out"
        if node.kind == "operator":
            outs = node.outputs()
            return (node.object, outs[0].key) if outs else (None, "out")
        return self.source_of(node, "")

    def _operator_menu(self) -> None:
        """The toolbar's Add operator menu, built when opened: Find an operator… (the
        palette), what fits the selected node, the recently used, then every type by
        its group, in the order work flows (ADR 0064 item 6)."""
        from puzzle.gui import operators as ops
        from puzzle.operators import TYPES

        menu = self.operators.menu()
        menu.clear()
        # Submenus are made here and kept on the panel: Qt's clear() deletes a submenu
        # it made with addMenu(title) while its entry can outlive it.
        for old in getattr(self, "_submenus", []):
            old.deleteLater()
        self._submenus: list[QMenu] = []

        def submenu(title: str) -> QMenu:
            sub = QMenu(title)
            sub.setToolTipsVisible(True)
            self._submenus.append(sub)
            menu.addMenu(sub)
            return sub

        if self.window.session is None:
            menu.addAction("Open a project first").setEnabled(False)
            return
        menu.addAction("Find an operator…  (Tab)", lambda: self.open_palette(self._centre()))
        source, port = self.palette_source()
        for title, keys in (("Fits the selection", ops.fitting(self.window, source, port)),
                            ("Recently used", ops.recent(self.window))):  # fmt: skip
            if keys:
                sub = submenu(title)
                for k in keys:
                    sub.addAction(TYPES[k].label, lambda k=k: self.add_operator(k)
                                  ).setToolTip(TYPES[k].help)  # fmt: skip
        from puzzle.operators import clusters

        found = clusters.templates()
        for title, keys in (
            ("Recipes", [k for k in found if not k.startswith("mine:")]),
            ("My clusters", [k for k in found if k.startswith("mine:")]),
        ):
            if keys:
                sub = submenu(title)
                for k in keys:
                    sub.addAction(
                        found[k]["name"],
                        lambda k=k: ops.insert_template(self.window, k, source, port),
                    ).setToolTip(found[k].get("help", ""))
        blocks = submenu("Share blocks")  # ADR 0071: user operators, shared as files
        blocks.addAction("Import a block…", lambda: ops.import_block(self.window))
        blocks.addAction("Export one of my clusters…", lambda: ops.export_block(self.window))
        menu.addSeparator()
        groups: dict[str, QMenu] = {}
        for t in ops.ordered():
            if t.group not in groups:
                groups[t.group] = submenu(t.group)
            action = groups[t.group].addAction(t.label, lambda k=t.key: self.add_operator(k))
            action.setToolTip(t.help)

    def _centre(self) -> QPointF:
        return self.canvas.mapToScene(self.canvas.viewport().rect().center())

    def add_operator(self, type_: str) -> str | None:
        """An operator from the toolbar: wired from the selected node when its output
        fits, placed in the middle of the view."""
        from puzzle.gui.operators import new_operator

        source, port = self.palette_source()
        at = self._centre()
        return new_operator(self.window, type_, source, port, (at.x(), at.y()))

    def open_palette(self, at: QPointF | None = None):
        """The operator palette (Tab, or a double-click on the empty canvas): the new
        operator placed where asked."""
        where = (at.x(), at.y()) if at is not None else None
        return self.window.open_operator_palette(where)

    def make_transfer(self, source: sch.Node, port: str, target: sch.Node):
        from puzzle.gui.menus import new_transfer

        return new_transfer(self.window, source.object, port, target.object)

    def add_part(self, geometry: sch.Node, component: sch.Node):
        from puzzle.gui.menus import add_part

        return add_part(self.window, geometry.object, component.object)

    # --- references as wires (ADR 0071 decision 8) -----------------------------------

    def _objects(self, a: sch.Node, b: sch.Node):
        from uuid import UUID

        project = self.window.session.project
        ids = []
        for node in (a, b):
            oid = node.object or (node.id.removeprefix("runs:") if node.kind == "runs" else "")
            try:
                ids.append(project.objects.get(UUID(oid)) if oid else None)
            except ValueError:
                ids.append(None)
        return ids

    def _linkable(self, a: sch.Node, b: sch.Node) -> bool:
        from puzzle.schematic import links

        if a.kind == "feature":
            return False
        source, target = self._objects(a, b)
        within = b.path if b.kind == "feature" else ""
        return bool(source and target and links.choices(self.window.session.project,
                                                        source, target, within))  # fmt: skip

    def link(self, a: sch.Node, b: sch.Node, pick: int | None = None):
        """A wire (or a node dropped) from `a` onto `b`: the reference it means set, as
        the tree's Properties would; asked which when it could mean several (which
        part, which load); refused, with what `b` takes, when it means none."""
        from puzzle.gui.quick_form import Ask
        from puzzle.schematic import links

        source, target = self._objects(a, b)
        if source is None or target is None:
            return None
        project = self.window.session.project
        within = b.path if b.kind == "feature" else ""  # onto one load's own node
        found = links.choices(project, source, target, within)
        if not found:
            self.window.statusBar().showMessage(links.why_not(project, source, target), 8000)
            return None
        if pick is None and found[0].op == "add_part":  # a geometry on a component: a part
            return self.add_part(a, b)
        if pick is None and len(found) > 1:
            got = self.window.ask_form(
                f"Use {source.name} in {target.name}",
                [Ask("which", "As", choices=[(c.label, i) for i, c in enumerate(found)])],
            )
            if not got:
                return None
            pick = got["which"]
        choice = found[pick or 0]
        if choice.op == "add_part":
            return self.add_part(a, b)
        cmds = links.commands(choice, source, target)
        return self.window.submit_batch(cmds) if len(cmds) > 1 else self.window.submit(cmds[0])

    def unlink(self, a: sch.Node, b: sch.Node, at=None):
        """A use wire's right-click: remove the reference it stands for."""
        from puzzle.schematic import links

        menu = QMenu(self.canvas)
        if a.kind == "feature":  # a load's wire into its component: the load itself
            from puzzle.gui.menus import remove

            comp = self.window.session.project.objects.get(_uuid_of(a.object))
            action = menu.addAction(f"Remove {a.label}")
            action.triggered.connect(lambda: remove(self.window, comp, a.path))
            if at is not None:
                menu.exec(at)
            return menu
        source, target = self._objects(a, b)
        if source is None or target is None:
            return None
        within = b.path if b.kind == "feature" else ""
        made = links.existing(self.window.session.project, source.id, target, within)
        for c in made:
            cmds = links.clear_commands(c, target)
            what = "Remove the part" if c.link.path.startswith("parts") else "Remove"
            action = menu.addAction(f"{what}: {c.label}")
            if cmds:
                action.triggered.connect(lambda _=False, c=cmds: self.window.submit(c[0]))
            else:
                action.setEnabled(False)
                action.setToolTip(f"{target.name} needs one: wire another in to replace it")
        if not made:
            menu.addAction("Nothing to remove here").setEnabled(False)
        if at is not None:
            menu.exec(at)
        return menu

    def wire_removal(self, item) -> tuple[list[dict], str]:
        """(commands, label) removing what a data wire stands for, or ([], why not)."""
        from puzzle.schematic import links

        target = item.b.node
        return links.data_wire_removal(self.window.session.project, item.wire,
                                       target.object, target.path)  # fmt: skip

    def remove_wire(self, item) -> object:
        """A data wire's Remove: one undoable command (SCH-2)."""
        cmds, why = self.wire_removal(item)
        if not cmds:
            self.window.statusBar().showMessage(f"This wire can't be removed here: {why}", 8000)
            return None
        return self.window.submit_batch(cmds) if len(cmds) > 1 else self.window.submit(cmds[0])

    def wire_menu(self, item, at=None) -> QMenu:
        """A data wire's right-click (SCH-2): Remove what it carries, or go to an end."""
        menu = QMenu(self.canvas)
        menu.setToolTipsVisible(True)
        cmds, label = self.wire_removal(item)
        action = menu.addAction(label if cmds else "Remove")
        if cmds:
            action.triggered.connect(lambda _=False: self.remove_wire(item))
        else:
            action.setEnabled(False)
            action.setToolTip(f"Can't be removed here: {label}")
        menu.addSeparator()
        for words, end in (("Go to its source", item.a), ("Go to what it feeds", item.b)):
            menu.addAction(f"{words}: {end.node.label}",
                           lambda _=False, e=end: self._go_to(e))  # fmt: skip
        if at is not None:
            menu.exec(at)
        return menu

    def _go_to(self, item: NodeItem) -> None:
        self.canvas.centerOn(item)
        self.chosen(item.node)

    # --- New: what the tree can add ------------------------------------------------

    def fill_new_menu(self, menu: QMenu, at: QPointF | None = None) -> QMenu:
        """The tree's own add menus, one per section, so the schematic adds whatever the
        tree adds; the new node goes where asked (`at`), else the view's middle."""
        from puzzle.gui.menus import build_menu
        from puzzle.gui.tree import SECTIONS
        from puzzle.gui.tree import Node as TreeNode

        menu.clear()
        if self.window.session is None:
            menu.addAction("Open a project first").setEnabled(False)
            return menu
        place = at if at is not None else self._centre()
        for heading in [title for title, _cls in SECTIONS] + ["Results"]:
            found = build_menu(self.window, TreeNode("section", label=heading))
            if found is None or not found.actions():
                continue
            found.setTitle(heading)
            found.setParent(menu, found.windowFlags())  # kept alive by the menu
            found.aboutToShow.connect(
                lambda p=place: setattr(self, "_place_next", (round(p.x(), 1), round(p.y(), 1)))
            )
            for action in found.actions():
                action.triggered.connect(lambda _=False, p=place: setattr(
                    self, "_place_next", (round(p.x(), 1), round(p.y(), 1))))  # fmt: skip
            menu.addMenu(found)
        return menu

    # --- the rest of the window ---------------------------------------------------

    def chosen(self, node: sch.Node | None, add: bool = False) -> None:
        """A click: the node selected here and its object in the tree and Properties.
        With `add` (Ctrl or Shift, or a drag of several) the rest stay selected and the
        tree follows the last one clicked (SCH-UX-1)."""
        self._syncing = True
        try:
            if add and node is not None:
                item = self.items.get(node.id)
                if item is not None:
                    item.setSelected(True)
            else:
                for nid, item in self.items.items():
                    item.setSelected(node is not None and nid == node.id)
                for w in self.wires:
                    w.setSelected(False)
        finally:
            self._syncing = False
        self.current = node.id if node is not None else ""
        self.highlight()
        self.selected.emit(self.current)
        if node is not None:
            self.window.select_in_tree(self._tree_key(node))

    def selection(self) -> list[str]:
        """The selected nodes' ids, the one clicked last at the end."""
        found = [nid for nid, item in self.items.items() if item.isSelected()]
        if self.current in found:
            found.remove(self.current)
            found.append(self.current)
        return found

    def _scene_selected(self) -> None:
        """A rubber band (or Qt's own Ctrl-click) changed the selection."""
        if getattr(self, "_syncing", False) or self.drawing is not None:
            return
        found = self.selection()
        if self.current not in found:
            self.current = found[-1] if found else ""
            self.highlight()
            self.selected.emit(self.current)

    # --- keys (SCH-UX-1) ------------------------------------------------------------

    def key(self, pressed: tuple) -> bool:
        """A key the canvas keeps (`CANVAS_KEYS`); True when it did something."""
        k, mods = pressed
        ctrl = mods == Qt.KeyboardModifier.ControlModifier
        plain = mods == Qt.KeyboardModifier.NoModifier
        if self.window.session is None:
            return False
        if plain and k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
        elif ctrl and k == Qt.Key.Key_A:
            self.select_all()
        elif ctrl and k == Qt.Key.Key_C:
            self.copy_selection()
        elif ctrl and k == Qt.Key.Key_V:
            self.paste()
        elif ctrl and k == Qt.Key.Key_D:
            self.copy_selection()
            self.paste()
        elif ctrl and k == Qt.Key.Key_F:
            self.search.setFocus()
            self.search.selectAll()
        elif plain and k in (Qt.Key.Key_F, Qt.Key.Key_Home):
            self.fit_all()
        elif mods == Qt.KeyboardModifier.ShiftModifier and k == Qt.Key.Key_F:
            self.fit_selection()
        elif plain and k == Qt.Key.Key_Escape:
            self.chosen(None)
        elif plain and k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.step_along(forward=k == Qt.Key.Key_Right)
        else:
            return False
        return True

    def select_all(self) -> None:
        self._syncing = True
        try:
            for item in self.items.values():
                item.setSelected(True)
        finally:
            self._syncing = False
        self._scene_selected()

    def fit_selection(self) -> None:
        chosen = [self.items[n] for n in self.selection()]
        if not chosen:
            return self.fit_all()
        rect = chosen[0].sceneBoundingRect()
        for item in chosen[1:]:
            rect = rect.united(item.sceneBoundingRect())
        self.canvas.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        if self.canvas.transform().m11() > 1.0:
            self.canvas.resetTransform()
            self.canvas.centerOn(rect.center())
        return None

    def step_along(self, forward: bool) -> None:
        """Right: the first node the selection feeds; Left: the first it is fed by."""
        if not self.current:
            return
        ends = [w.target if forward else w.source for w in self.schematic.wires
                if (w.source if forward else w.target) == self.current
                and w.kind in ("data", "use")]  # fmt: skip
        ends = [e for e in ends if e in self.items]
        if ends:
            ends.sort(key=lambda e: self.items[e].pos().y())
            self._go_to(self.items[ends[0]])

    def _tree_nodes(self, ids) -> list:
        """The tree's items standing for these nodes, for `gui.deleting.plan`: never a
        run node (its study's runs: deleted from the tree, one by one) or a file."""
        from puzzle.gui.tree import Node as TreeNode

        out = []
        for nid in ids:
            node = self.schematic.nodes.get(nid)
            if node is None or node.kind in ("runs", "file"):
                continue
            kind, obj, *path = self._tree_key(node)
            out.append(TreeNode(kind, object=obj, path=path[0] if path else "",
                                label=node.label))  # fmt: skip
        return out

    def delete_selection(self):
        """Delete what is selected here as one batch, one undo (SCH-UX-1): the nodes as
        the tree deletes them; with no node selected, the selected wires' references."""
        from puzzle.gui import deleting

        project = self.window.session.project
        ids = self.selection()
        cmds: list = []
        names: list[str] = []
        if ids:
            plan = deleting.plan(project, self._tree_nodes(ids))
            cmds, names = list(plan.commands), list(plan.names)
            skipped = [self.schematic.nodes[n].label for n in ids
                       if self.schematic.nodes[n].kind in ("runs", "file")]  # fmt: skip
            if skipped and not cmds:
                self.window.statusBar().showMessage(
                    "Results and files are deleted from the Model tab, not here", 8000
                )
                return None
        else:
            for w in [w for w in self.wires if w.isSelected()]:
                if w.wire.kind == "use":
                    source, target = self._objects(w.a.node, w.b.node)
                    if source is None or target is None:
                        continue
                    from puzzle.schematic import links

                    within = w.b.node.path if w.b.node.kind == "feature" else ""
                    for c in links.existing(project, source.id, target, within):
                        cmds += links.clear_commands(c, target)[:1]
                        names.append(c.label)
                else:
                    found, label = self.wire_removal(w)
                    cmds += found
                    names += [label] if found else []
        if not cmds:
            return None
        if len(names) > 1 and not self.window.ask_yes("Delete", deleting.question(
                deleting.Plan(cmds, [], names))):  # fmt: skip
            return None
        done = self.window.submit_batch(cmds) if len(cmds) > 1 else self.window.submit(cmds[0])
        if done is not None:
            what = names[0] if len(names) == 1 else f"{len(names)} items"
            self.window.statusBar().showMessage(f"Deleted {what}; Undo brings it back", 8000)
        return done

    def copy_selection(self) -> int:
        """The selected operators, kept to paste (Ctrl+C); how many."""
        from uuid import UUID

        from puzzle.operators import clusters

        project = self.window.session.project
        ids = []
        for nid in self.selection():
            node = self.schematic.nodes.get(nid)
            if node is not None and node.kind == "operator":
                ids.append(UUID(node.object))
        global _CLIPBOARD
        _CLIPBOARD = clusters.copied(project, ids)
        positions = {o["id"]: self.items[o["id"]].pos() for o in _CLIPBOARD
                     if o["id"] in self.items}  # fmt: skip
        self._copied_at = {k: (p.x(), p.y()) for k, p in positions.items()}
        if not _CLIPBOARD and ids == []:
            self.window.statusBar().showMessage("Only operators are copied here", 6000)
        return len(_CLIPBOARD)

    def paste(self):
        """Copies of what Ctrl+C kept, with the wires among them, as one batch; placed
        beside the originals (SCH-UX-1)."""
        from puzzle.operators import clusters
        from puzzle.schematic import view_state

        if not _CLIPBOARD:
            return None
        cmds, new = clusters.paste_commands(self.window.session.project, _CLIPBOARD)
        at = getattr(self, "_copied_at", {})
        for old, made in new.items():
            if old in at:
                x, y = at[old]
                self.view.positions[made] = (round(x + 40, 1), round(y + 40, 1))
        if self._root is not None:
            view_state.save(self._root, self.view)
        done = self.window.submit_batch(cmds) if len(cmds) > 1 else self.window.submit(cmds[0])
        if done is not None:
            self._syncing = True
            for item in self.items.values():
                item.setSelected(item.node.id in new.values())
            self._syncing = False
            self._scene_selected()
        return done

    # --- editing on the node (SCH-UX-2) ---------------------------------------------

    def edit_on_node(self, item: NodeItem, row) -> bool:
        """A double-click on a typed input's row or an operator's value row: a line edit
        over it, its text committed with Enter as one Edit (Esc leaves it). True when it
        opened one."""
        from uuid import UUID

        from puzzle.gui.operators import _typed_text
        from puzzle.operators import TYPES

        node = item.node
        if node.kind != "operator" or self.window.session is None:
            return False
        op = self.window.session.project.objects.get(UUID(node.object))
        if op is None:
            return False
        if row.kind == "in":
            spec = TYPES[op.type].input(row.key)
            inp = op.inputs.get(row.key)
            if spec is None or not spec.typed or (inp is not None and inp.source is not None):
                return False
            text = _typed_text(inp.value) if inp is not None and inp.value is not None else ""
            self._inline(item, row, text, lambda typed: self._commit_typed(op, row.key, typed))
            return True
        if row.kind == "value" and op.type == "expression":  # a Formula's own text
            self._inline(item, row, op.expression or "",
                         lambda text: self._commit_setting(op, "expression", text))  # fmt: skip
            return True
        return False

    def _inline(self, item: NodeItem, row, text: str, commit) -> None:
        from PySide6.QtWidgets import QGraphicsProxyWidget

        editor = QLineEdit(text)
        editor.setFrame(True)
        proxy = QGraphicsProxyWidget(item)
        proxy.setWidget(editor)
        proxy.setGeometry(QRectF(8, row.top, item.w - 16, max(row.height, 20)))
        proxy.setZValue(10)
        self.inline = editor
        done = {"once": False}

        def finish(save: bool) -> None:
            if done["once"]:
                return
            done["once"] = True
            value = editor.text()
            self.inline = None
            proxy.setWidget(None)
            editor.deleteLater()
            if proxy.scene() is not None:
                proxy.scene().removeItem(proxy)
            if save and value != text:
                commit(value)

        editor.returnPressed.connect(lambda: finish(True))
        editor.editingFinished.connect(lambda: finish(False))
        editor._finish = finish  # tests and Esc
        editor.setFocus()
        editor.selectAll()

    def _commit_typed(self, op, key: str, text: str):
        from puzzle.commands.model import Edit, EditOp
        from puzzle.gui.operators import parse_typed

        try:
            typed = parse_typed(text)
        except ValueError as exc:
            self.window.statusBar().showMessage(f"{op.name}: {exc}", 8000)
            return None
        value = {} if typed is None else {"value": typed}
        return self.window.submit(Edit(object=str(op.id), ops=[EditOp(
            op="set", path=f"inputs.{key}", value=value)]))  # fmt: skip

    def _commit_setting(self, op, field: str, text: str):
        from puzzle.commands.model import Edit, EditOp

        return self.window.submit(Edit(object=str(op.id), ops=[EditOp(
            op="set", path=field, value=text)]))  # fmt: skip

    # --- Bring up to date (SCH-UX-8) ------------------------------------------------

    def needs_running(self) -> list[str]:
        """The studies to run to bring the selection (or everything an operator reads,
        with nothing selected) up to date: those not current, in the order they run."""
        s = self.schematic
        ids = self.selection()
        if ids:
            reach = set(ids)
            for nid in ids:
                reach |= s.upstream(nid)
        else:
            reach = set(s.nodes)
        out = []
        for nid in reach:
            node = s.nodes.get(nid)
            if node is not None and node.kind == "study" and node.state in (
                    "out_of_date", "not_run", "failed"):  # fmt: skip
                out.append(node.object)
        return out

    def bring_up_to_date(self):
        """Run what the selection needs (the run queue: sources of transfers first). It
        is asked for, never automatic: a solve can take hours."""
        studies = self.needs_running()
        if not studies:
            self.window.statusBar().showMessage("Everything shown is up to date", 6000)
            return []
        self.window.run_studies(studies)
        return studies

    def _tree_key(self, node: sch.Node) -> tuple[str, str]:
        """(tree node kind, its object) for a schematic node; a file is its reader's."""
        if node.kind == "file":
            s = self.schematic
            reader = next((s.nodes[w.target] for w in s.wires
                           if w.source == node.id and w.target in s.nodes), None)  # fmt: skip
            return self._tree_key(reader) if reader is not None else ("", "")
        if node.kind == "runs":
            return ("run", node.object)
        if node.kind == "group":
            return ("group", node.object)
        if node.kind == "feature":  # a load or support: its own item in the tree
            return ("feature", node.object, node.path)
        return ("derived" if node.kind == "result" else "object", node.object)

    def open_node(self, node: sch.Node) -> None:
        """A double-click: open it where it is edited."""
        w = self.window
        kind, obj = self._tree_key(node)
        target = self.schematic.nodes.get(node.id)
        if node.kind == "file" and target is not None:
            node = next((self.schematic.nodes[x.target] for x in self.schematic.wires
                         if x.source == node.id), node)  # fmt: skip
        if node.kind == "group":  # a folded group opens again
            self.toggle_group(node.object)
        elif node.kind == "geometry":
            w.left_tabs.setCurrentWidget(w.workbench)
            w.workbench.show_part(node.object)
        elif node.kind == "runs":
            w.show_results_of(node.object)
        elif node.kind == "mesh":
            w.left_tabs.setCurrentWidget(w.mesh_bench)
        elif node.kind == "operator" and node.subtitle == "Plot":  # its table, plotted
            from uuid import UUID

            w.select_in_tree((kind, obj))
            w.plots.show_operator(w.session.project.objects.get(UUID(obj)))
        else:
            w.left_tabs.setCurrentIndex(0)
            w.select_in_tree((kind, obj))

    def menu_for(self, node: sch.Node, at) -> None:
        """The tree's own menu for the node's object, so every action there is here."""
        from puzzle.gui.menus import build_menu

        self.chosen(node)
        tree_node = self.window.tree.current()
        if tree_node is None:
            return
        menu = build_menu(self.window, tree_node)
        if menu is not None and menu.actions():
            menu.exec(at)

    def export_picture(self, path: str | None = None) -> str | None:
        """The picture as SVG (from `schematic.svg`) or PNG (the scene as drawn)."""
        from pathlib import Path

        if path is None:
            name = self.window.session.project.name if self.window.session else "schematic"
            path = self.window.ask_save_path("Export the schematic", f"{name} schematic.svg",
                                             "SVG picture (*.svg);;PNG image (*.png)")  # fmt: skip
        if not path:
            return None
        if path.lower().endswith(".png"):
            from PySide6.QtGui import QImage

            rect = self.scene.itemsBoundingRect().adjusted(-20, -40, 20, 20)
            image = QImage(int(rect.width() * 2), int(rect.height() * 2),
                           QImage.Format.Format_ARGB32)  # fmt: skip
            image.fill(QColor(_colours().surface))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.scene.render(painter, QRectF(image.rect()), rect)
            painter.end()
            image.save(path)
        else:
            from puzzle.schematic.svg import to_svg

            positions = {k: (i.pos().x(), i.pos().y()) for k, i in self.items.items()}
            lay = layout(self.schematic, positions, frozenset(self.view.unfolded))
            uses = "none" if self.uses.currentData() == "none" else "all"
            title = self.window.session.project.name if self.window.session else ""
            Path(path).write_text(to_svg(self.schematic, lay, uses, title), encoding="utf-8")
        self.window.statusBar().showMessage(f"Saved {path}", 8000)
        return path


__all__ = ["SchematicPanel"]


class SchematicSide(QWidget):
    """The Schematic tab's page beside the picture: how to read it, and the selected
    node's state, what it is built from and what a change to it reaches."""

    def __init__(self, panel: SchematicPanel) -> None:
        super().__init__()
        from puzzle.gui import theme

        self.panel = panel
        column = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(theme.role(QLabel("Schematic"), "title"))
        head.addStretch(1)
        self.help_button = QToolButton(text="?")  # UX-35: the help page, not a page of text
        self.help_button.setToolTip("How to read and use the schematic: wires, sockets, "
                                    "states, keys (F1)")  # fmt: skip
        self.help_button.setAccessibleName("Schematic help")
        self.help_button.clicked.connect(lambda: self.open_help())
        head.addWidget(self.help_button)
        column.addLayout(head)
        legend = QLabel("Click a node: what it is built from and what a change to it "
                        "reaches. Tab adds an operator.")  # fmt: skip
        legend.setWordWrap(True)
        theme.role(legend, "muted")
        column.addWidget(legend)
        column.addSpacing(12)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        column.addWidget(self.details)
        column.addStretch(1)
        panel.selected.connect(self.show_node)

    def open_help(self):
        from puzzle.gui.help_viewer import show_help

        return show_help(self.panel.window, "panel:schematic")

    def show_node(self, node_id: str) -> None:
        from puzzle.project.status import WORDS

        s = self.panel.schematic
        node = s.nodes.get(node_id)
        if node is None:
            self.details.setText("")
            return

        def names(ids) -> str:
            return ", ".join(sorted(s.nodes[k].label for k in ids)) or "nothing"

        state = WORDS.get(node.state, node.state) + (f": {node.reason}" if node.reason else "")
        self.details.setText(
            f"{sch.KIND_WORDS.get(node.kind, node.kind)}: {node.label}\n{state}\n\n"
            f"Built from: {names(s.upstream(node_id))}\n\n"
            f"A change reaches: {names(s.downstream(node_id))}"
            + (f"\n\n{cost}" if (cost := self.cost(node_id)) else "")
        )

    def cost(self, node_id: str) -> str:
        """What a change to the node would cost (SCH-UX-13): the studies it would put
        out of date, with their last solve's time, and the operators worked out again."""
        s = self.panel.schematic
        reach = s.downstream(node_id)
        studies = sorted((s.nodes[k] for k in reach if s.nodes[k].kind == "study"),
                         key=lambda n: n.label)  # fmt: skip
        ops = sum(1 for k in reach if s.nodes[k].kind == "operator")
        if not studies and not ops:
            return ""
        runs = getattr(self.panel.window, "run_records", None) or []
        last: dict[str, float] = {}
        for r in sorted(runs, key=lambda r: r.recorded_at):
            last[r.study_id] = r.duration_s
        timed = [f"{n.label}, last solve {_seconds(last[n.object])}" if n.object in last
                 else n.label for n in studies]  # fmt: skip
        parts = []
        if studies:
            word = "study" if len(studies) == 1 else "studies"
            parts.append(f"{len(studies)} {word} out of date ({'; '.join(timed)})")
        if ops:
            parts.append(f"{ops} operator{'s' if ops != 1 else ''} worked out again")
        return "A change here puts " + " and ".join(parts)


def _seconds(t: float) -> str:
    return f"{t:.0f} s" if t < 120 else f"{t / 60:.0f} min"
