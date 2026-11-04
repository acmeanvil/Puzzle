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

    return QColor(STATE_DRAWINGS.get(state, STATE_DRAWINGS["complete"])[0])


# What a wire may be drawn from: a study (into a study: a transfer), and anything with
# an output an operator takes (ADR 0062).
WIRES_FROM = ("study", "runs", "result", "operator", "parameters")


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


def _fraction(low: float, high: float, value: float) -> float:
    return min(max((value - low) / (high - low), 0.0), 1.0) if high > low else 0.0


def _flow_colour(carries: str) -> QColor:
    return QColor(sch.FLOW_COLOURS[sch.flow_of(carries)]) if carries else QColor("#8a94a3")


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
        self.setToolTip(tip)
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
        state = _state_colour(node.state)
        painter.fillRect(QRectF(0, 0, 4, self.h), state)
        bold, plain, small = QFont(painter.font()), QFont(painter.font()), QFont(painter.font())
        bold.setBold(True)
        small.setPointSizeF(max(plain.pointSizeF() - 1.5, 7.0))
        metrics = painter.fontMetrics()
        for r in rows(node, self.unfolded):
            band = QRectF(10, r.top, self.w - 20, r.height)
            if r.kind == "title":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(state)
                painter.drawEllipse(QPointF(self.w - 12, TITLE / 2), 4, 4)
                painter.setFont(bold)
                painter.setPen(QColor(t.text))
                text = metrics.elidedText(r.text, Qt.TextElideMode.ElideRight, int(self.w - 40))
                painter.drawText(band, Qt.AlignmentFlag.AlignVCenter, text)
            elif r.kind == "value":  # an operator's value; a check's verdict in its colour
                painter.setFont(bold)
                colour = sch.VERDICT_COLOURS.get(r.key)
                painter.setPen(QColor(colour) if colour else QColor(t.text))
                text = metrics.elidedText(r.text, Qt.TextElideMode.ElideRight, int(self.w - 20))
                painter.drawText(band, Qt.AlignmentFlag.AlignVCenter, text)
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
            elif r.kind in ("subtitle", "badge", "used", "fold", "part"):
                painter.setFont(small)
                painter.setPen(QColor(t.muted))
                text = metrics.elidedText(r.text, Qt.TextElideMode.ElideRight, int(self.w - 20))
                painter.drawText(band, Qt.AlignmentFlag.AlignVCenter, text)
            elif r.kind == "chip":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.raised))
                painter.drawRoundedRect(QRectF(8, r.top + 2, self.w - 16, r.height - 4), 7, 7)
                painter.setFont(small)
                painter.setPen(QColor(t.muted))
                painter.drawText(QRectF(14, r.top, self.w - 28, r.height),
                                 Qt.AlignmentFlag.AlignVCenter, r.text)  # fmt: skip
            elif r.kind in ("in", "out"):
                port = node.port(r.key)
                colour = QColor(sch.port_colour(port)) if port is not None and (
                    port.type or port.carries) else _flow_colour("")  # fmt: skip
                x = self.w if r.kind == "out" else 0.0
                painter.setPen(QPen(QColor(t.sunken), 1.5))
                painter.setBrush(colour)
                _socket(painter, sch.socket_shape(port), QPointF(x, r.middle))
                painter.setFont(plain)
                painter.setPen(QColor(t.text))
                align = (
                    Qt.AlignmentFlag.AlignRight if r.kind == "out" else Qt.AlignmentFlag.AlignLeft
                )
                painter.drawText(band, align | Qt.AlignmentFlag.AlignVCenter, r.text)

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
        super().mouseReleaseEvent(event)
        fold = next((r for r in rows(self.node, self.unfolded) if r.kind == "fold"), None)
        start, self._pressed_at = self._pressed_at, None
        if start is not None and start != self.pos():
            self.panel.dropped(self, start)
        elif fold is not None and fold.top <= event.pos().y() < fold.top + fold.height:
            self.panel.toggle_parts(self.node.id)
        self.panel.chosen(self.node)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.panel.open_node(self.node)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.panel.menu_for(self.node, event.screenPos())


class WireItem(QGraphicsPathItem):
    def __init__(self, wire: sch.Wire, a: NodeItem, b: NodeItem) -> None:
        super().__init__()
        self.wire, self.a, self.b = wire, a, b
        self.setZValue(1 if wire.kind == "data" else 0)
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

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.panel.drawing is not None:
            self.panel.end_wire(event.scenePos())
            return
        if self.itemAt(event.scenePos(), self.views()[0].transform()) is None:
            self.panel.chosen(None)
        super().mouseReleaseEvent(event)


class SchematicCanvas(QGraphicsView):
    """Pan by dragging the background, zoom with the wheel."""

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(_colours().surface)))

    def event(self, event) -> bool:
        """Tab opens the operator palette (ADR 0062), not the next widget's focus."""
        from PySide6.QtCore import QEvent

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

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self.search = QLineEdit(placeholderText="Find by name or kind")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.highlight())
        self.search.returnPressed.connect(self._go_to_found)
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
        for button in (self.operators, self.reach, self.fit, self.tidy, self.export):
            button.setMinimumWidth(36)  # its text clips rather than set the minimum
        for combo in (self.state_filter, self.uses):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(6)
        for w in (self.operators, self.search, self.state_filter, self.uses, self.reach):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.fit, self.tidy, self.export):
            bar.addWidget(w)
        self.scene = SchematicScene(self)
        self.canvas = SchematicCanvas(self.scene)
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
        try:
            runs = self.window._store().runs()
        except Exception:  # noqa: BLE001 - an old store: the model still shows
            runs = []
        states = getattr(self.window, "states", None)
        group_ids = {str(o.id) for o in session.project.objects.values() if o.kind == "group"}
        if self.view.prune(self._node_ids(session.project, runs), group_ids):
            view_state.save(session.root, self.view)
        try:
            store = self.window._store()
        except Exception:  # noqa: BLE001 - no store: operators show no values
            store = None
        self.schematic = sch.build(session.project, runs, states, root=session.root,
                                   folded=frozenset(self.view.folded),
                                   fields=bool(current().get("connections.schematic")),
                                   store=store)  # fmt: skip
        self._draw()
        self.canvas.setVisible(bool(self.schematic.nodes))
        self.empty.setVisible(not self.schematic.nodes)
        if not self.schematic.nodes:
            self.empty.setText("Nothing to show yet: import geometry or start a new part.")

    def _node_ids(self, project, runs) -> set[str]:
        """Every node id the project can have, folded or not, so pruning keeps the
        places of nodes inside folded groups."""
        built = sch.build(project, runs, states={})
        groups = {f"group:{o.id}" for o in project.objects.values() if o.kind == "group"}
        return set(built.nodes) | groups

    def _draw(self) -> None:
        keep = self.canvas.transform(), self.canvas.horizontalScrollBar().value(), \
            self.canvas.verticalScrollBar().value()  # fmt: skip
        first = not self.items
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
        if self.current in self.items:
            self.items[self.current].setSelected(True)
        else:
            self.current = ""
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
        words = self.search.text().strip().lower()
        if words:
            match = {
                k
                for k, n in self.schematic.nodes.items()
                if words in n.label.lower() or words in sch.KIND_WORDS.get(n.kind, "").lower()
            }
            found = match if found is None else found & match | {self.current}
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

    def _go_to_found(self) -> None:
        lit = self.lit()
        for nid in self.items:
            if lit is not None and nid in lit and nid != self.current:
                self.canvas.centerOn(self.items[nid])
                return

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

        target = self._node_under(item.sceneBoundingRect().center(), not_item=item)
        if item.node.kind == "geometry" and target is not None and target.node.kind == "component":
            item.setPos(start)
            self.add_part(item.node, target.node)
            return
        pos = item.pos()
        self.view.positions[item.node.id] = (round(pos.x(), 1), round(pos.y(), 1))  # as saved
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

    def wire_to(self, point: QPointF) -> None:
        item, port, path = self.drawing
        p0 = item.anchor(port, "out")
        shape = QPainterPath(p0)
        bend = max(40.0, abs(point.x() - p0.x()) / 2)
        shape.cubicTo(p0.x() + bend, p0.y(), point.x() - bend, point.y(), point.x(), point.y())
        path.setPath(shape)

    def end_wire(self, point: QPointF) -> None:
        item, port, path = self.drawing
        self.drawing = None
        self.scene.removeItem(path)
        self.canvas.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        target = self._node_under(point, not_item=item)
        if target is None:
            return
        if target.node.kind == "operator":  # ADR 0062: into one of its inputs
            self.wire_operator(item.node, port, target.node, target.input_near(point))
            return
        if item.node.kind in ("operator", "parameters") and target.node.kind == "geometry":
            self.drive_geometry(item.node, port, target.node)  # a history argument
            return
        if item.node.kind == "operator" and target.node.kind == "study":
            self.sweep_study(item.node, port, target.node)  # a sweep over it
            return
        if target.node.kind != "study" or item.node.kind != "study":
            self.window.statusBar().showMessage(
                "A study's result goes into another study, or any output into an "
                "operator's input: drop the wire there", 8000)  # fmt: skip
            return
        self.make_transfer(item.node, port, target.node)

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
        specs = TYPES[op.type].inputs
        chosen = next((p for p in specs if p.key == key), None)
        if chosen is None or not accepts(chosen, given):
            chosen = next((p for p in specs if accepts(p, given)), None)
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

    # --- the rest of the window ---------------------------------------------------

    def chosen(self, node: sch.Node | None) -> None:
        """A click: the node selected here and its object in the tree and Properties."""
        self.current = node.id if node is not None else ""
        for nid, item in self.items.items():
            item.setSelected(nid == self.current)
        self.highlight()
        self.selected.emit(self.current)
        if node is not None:
            self.window.select_in_tree(self._tree_key(node))

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
            Path(path).write_text(to_svg(self.schematic, lay, uses, title))
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
        title = theme.role(QLabel("Schematic"), "title")
        column.addWidget(title)
        legend = QLabel(
            "Thick coloured wires carry results (transfers, imported fields, runs into "
            "derived results), coloured by what flows. Thin grey wires say what each "
            "thing is built from.\n\nClick a node to select it; double-click to open it; "
            "right-click for its menu. Drag a study's output onto another study to make a "
            "transfer, a geometry onto a component to add a part.\n\nOperators compute "
            "from results: press Tab (or double-click the empty canvas) to add one, wired "
            "from the selected node; drag any output onto an operator's input to wire it. "
            "Socket shapes say what a port carries: circles results and fields, diamonds "
            "numbers, squares tables, triangles verdicts."
        )
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
        )
