"""The 3D viewport: meshes, highlighted selections and results (pyvistaqt, ADR 0003).

Where VTK cannot draw (Qt's offscreen platform, as in tests) a label stands in, and
the viewport still records what it would show, so tests can check it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from puzzle.gui.tasks import rendering_available
from puzzle.mesh.mesh import Mesh
from puzzle.post.legend import bar_format
from puzzle.post.quantities import compute
from puzzle.post.view import DEFAULT_SCALAR
from puzzle.post.vtk import pick_step, to_grid
from puzzle.results.model import Results

# Surface element -> how many of its nodes are corners.
CORNERS = {"tri3": 3, "tri6": 3, "quad4": 4, "quad8": 4, "quad9": 4}
HIGHLIGHT = "#ff8c00"
PICKED = "#e53e3e"


def entity_surface(mesh: Mesh):
    """Every surface element as one pickable surface; cell data `entity` indexes the
    returned list of Gmsh entities (dim, tag) the faces belong to."""
    import pyvista as pv

    order = np.argsort(mesh.node_ids)
    faces, owners, entities = [], [], []
    for b in mesh.blocks:
        if b.dim != 2 or b.element not in CORNERS:
            continue
        if b.entity not in entities:
            entities.append(b.entity)
        corners = b.connectivity[:, : CORNERS[b.element]]
        index = order[np.searchsorted(mesh.node_ids, corners, sorter=order)]
        faces.append(np.hstack([np.full((len(index), 1), index.shape[1]), index]).ravel())
        owners.append(np.full(len(index), entities.index(b.entity)))
    if not faces:
        return None, []
    surface = pv.PolyData(mesh.coords, np.concatenate(faces))
    surface.cell_data["entity"] = np.concatenate(owners)
    return surface, entities


def entity_lines(mesh: Mesh):
    """Every edge element as pickable lines; cell data `entity` indexes the returned list
    of Gmsh edge entities (1, tag)."""
    import pyvista as pv

    order = np.argsort(mesh.node_ids)
    segments, owners, entities = [], [], []
    for b in mesh.blocks:
        if b.dim != 1 or not b.element.startswith("line"):
            continue
        if b.entity not in entities:
            entities.append(b.entity)
        ends = order[np.searchsorted(mesh.node_ids, b.connectivity[:, :2], sorter=order)]
        segments.append(np.hstack([np.full((len(ends), 1), 2), ends]).ravel())
        owners.append(np.full(len(ends), entities.index(b.entity)))
    if not segments:
        return None, []
    lines = pv.PolyData(mesh.coords, lines=np.concatenate(segments))
    lines.cell_data["entity"] = np.concatenate(owners)
    return lines, entities


def selection_surface(mesh: Mesh, group: str):
    """A pyvista surface of a face selection, from the mesh's surface elements."""
    import pyvista as pv

    g = mesh.groups[group]
    entities = set(g.entities)
    order = np.argsort(mesh.node_ids)
    faces = []
    for b in mesh.blocks:
        if b.dim != g.dim or b.entity not in entities or b.element not in CORNERS:
            continue
        corners = b.connectivity[:, : CORNERS[b.element]]
        index = order[np.searchsorted(mesh.node_ids, corners, sorter=order)]
        faces.append(np.hstack([np.full((len(index), 1), index.shape[1]), index]).ravel())
    if not faces:
        return None
    return pv.PolyData(mesh.coords, np.concatenate(faces))


def _display(values, kind: str | None):
    """SI values in the display unit of their kind (ADR 0024), and that unit's name."""
    from puzzle.model.units import DIMENSIONS, pretty, registry
    from puzzle.settings import current

    if kind is None:
        return values, None
    si, target = DIMENSIONS[kind][1], current().display_units()[kind]
    if target == si:
        return values, pretty(si)
    converted = registry().Quantity(np.asarray(values, float), si).to(target).magnitude
    return np.asarray(converted), pretty(target)


KIND_DIM = {"vertex": 0, "edge": 1, "face": 2, "body": 3}


def _warp_name(data) -> str | None:
    """The global displacement in sampled data: its copy when the results are in a
    coordinate system (ADR 0049), else the field itself; None without one."""
    for name in ("displacement_global", "displacement"):
        if name in data:
            return name
    return None


def _mode(modifiers, plain: str) -> str:
    """A click's pick mode (ADR 0034): Shift adds, Ctrl or ⌘ removes, plain `plain`."""
    if modifiers is None:
        return plain
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return "add"
    if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
        return "remove"
    return plain


def _number(v: float) -> str:
    """A value as a label: three to four figures, plain between 1e-3 and 1e5."""
    a = abs(v)
    if a == 0 or 1e-3 <= a < 1e5:
        return f"{v:.4g}" if a < 1e4 else f"{v:.0f}"
    return f"{v:.3e}"


def words(name: str) -> str:
    """A field name as a title: "von_mises" -> "Von Mises", "displacement_magnitude" ->
    "Displacement magnitude"."""
    from puzzle.gui.choices import label

    return {"von_mises": "Von Mises stress"}.get(name, label(name))


class SceneView(QWidget):
    camera_moved = Signal()
    drawn = Signal()  # a frame was painted: overlays placed on 3D points follow

    """A VTK scene rendered off screen and painted as an image, with its own mouse
    handling: left-drag rotates, shift- or middle-drag pans, right-drag, the wheel
    (two-finger scroll) or a trackpad pinch zooms, and a double-click reports the
    picked point to `on_double_click`. `camera_moved` says the user turned, panned or
    zoomed it (linked views follow).

    On-screen OpenGL inside Qt windows leaves unrefreshed rows along the bottom of the
    view on some Macs (Intel Iris here, with every embedding tried: native window,
    QOpenGLWidget); VTK's off-screen renders are always clean, and ~10 ms a frame.
    """

    def __init__(self, parent=None) -> None:
        import pyvista as pv

        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.plotter = pv.Plotter(off_screen=True, window_size=[400, 300])
        self.plotter.show(auto_close=False)  # sets up the render window once
        self.on_double_click = None
        self.on_click = None  # (point, modifiers) for a click that didn't drag
        self.on_box = None  # (QRect, modifiers) for a box dragged in box mode
        self.box_mode = False
        self._band = None
        self._press = None
        self.modifiers = Qt.KeyboardModifier.NoModifier  # of the last double-click
        self._image = None
        self._last = None
        self._pending = False

    # rendering ---------------------------------------------------------------------------

    def refresh(self) -> None:
        """Render soon (once per event-loop pass, however many changes)."""
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._draw)

    def _draw(self) -> None:
        from PySide6.QtGui import QImage

        self._pending = False
        if getattr(self.plotter, "render_window", None) is None:
            return  # closed with its window: a late paint has nothing to draw with
        dpr = self.devicePixelRatioF()
        size = [max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr))]
        if list(self.plotter.window_size) != size:
            self.plotter.window_size = size
        self.plotter.render()  # screenshot() alone returns the previous frame once rendered
        pixels = np.ascontiguousarray(self.plotter.screenshot(return_img=True))
        h, w, _ = pixels.shape
        image = QImage(pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        image.setDevicePixelRatio(dpr)
        self._image = image
        self.update()
        self.drawn.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        from PySide6.QtGui import QPainter

        if self._image is not None:
            # Mid-resize the last frame is stretched to fit; the real one follows.
            QPainter(self).drawImage(self.rect(), self._image)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """While a dock's separator is dragged the view is resized many times a second;
        a full render each time (a Retina-sized frame read back) made the drag stick.
        So it renders once the resizing pauses."""
        super().resizeEvent(event)
        if self._image is None:
            self.refresh()
            return
        if not hasattr(self, "_settle"):
            self._settle = QTimer(self)
            self._settle.setSingleShot(True)
            self._settle.setInterval(150)
            self._settle.timeout.connect(self.refresh)
        self._settle.start()
        self.update()

    # mouse -------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last = event.position()
        self._press = event.position()
        if self.box_mode and event.button() == Qt.MouseButton.LeftButton:
            from PySide6.QtCore import QRect, QSize
            from PySide6.QtWidgets import QRubberBand

            self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._band.setGeometry(QRect(event.position().toPoint(), QSize()))
            self._band.show()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._last = None
        start, self._press = self._press, None
        if self._band is not None:
            rect = self._band.geometry()
            self._band.hide()
            self._band = None
            if self.on_box is not None and rect.width() > 3 and rect.height() > 3:
                self.on_box(rect, event.modifiers())
            return
        moved = start is None or (event.position() - start).manhattanLength() > 4
        if not moved and self.on_click is not None and event.button() == Qt.MouseButton.LeftButton:
            point = self.pick_point(event.position())
            if point is not None:
                self.on_click(point, event.modifiers())

    def pick_point(self, position):
        """The 3D point of the model under a widget position, or None."""
        import vtk

        dpr = self.devicePixelRatioF()
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.0005)
        x, y = position.x() * dpr, (self.height() - position.y()) * dpr
        if picker.Pick(x, y, 0, self.plotter.renderer) and picker.GetCellId() >= 0:
            return picker.GetPickPosition()
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._band is not None:  # box mode: the box, not a rotation
            from PySide6.QtCore import QRect

            self._band.setGeometry(
                QRect(self._press.toPoint(), event.position().toPoint()).normalized()
            )
            return
        if self._last is None:
            return
        pos = event.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        buttons, mods = event.buttons(), event.modifiers()
        camera = self.plotter.camera
        pan = buttons & Qt.MouseButton.MiddleButton or (
            buttons & Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.ShiftModifier
        )
        if pan:
            self._pan(dx, dy)
        elif buttons & Qt.MouseButton.LeftButton:
            camera.Azimuth(-dx * 0.5)
            camera.Elevation(dy * 0.5)
            camera.OrthogonalizeViewUp()
        elif buttons & Qt.MouseButton.RightButton:
            camera.Dolly(1.01 ** (-dy))
        self.plotter.renderer.ResetCameraClippingRange()
        self.refresh()
        self.camera_moved.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.plotter.camera.Dolly(1.1 ** (event.angleDelta().y() / 120))
        self.plotter.renderer.ResetCameraClippingRange()
        self.refresh()
        self.camera_moved.emit()

    def event(self, event) -> bool:
        """Trackpad pinch zooms (macOS sends it as a native gesture, not a wheel)."""
        from PySide6.QtCore import QEvent

        pinch = (
            event.type() == QEvent.Type.NativeGesture
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        )
        if pinch:
            self.plotter.camera.Dolly(1.0 + event.value())
            self.plotter.renderer.ResetCameraClippingRange()
            self.refresh()
            self.camera_moved.emit()
            return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.on_double_click is None:
            return
        import vtk

        dpr = self.devicePixelRatioF()
        x = event.position().x() * dpr
        y = (self.height() - event.position().y()) * dpr  # VTK counts from the bottom
        self.modifiers = event.modifiers()
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.0005)
        if picker.Pick(x, y, 0, self.plotter.renderer) and picker.GetCellId() >= 0:
            self.on_double_click(picker.GetPickPosition())

    def screen_point(self, xyz) -> tuple[float, float] | None:
        """Where a 3D point shows in this widget (logical pixels), or None behind the eye."""
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(*xyz, 1.0)
        renderer.WorldToDisplay()
        x, y, z = renderer.GetDisplayPoint()
        if not 0 <= z <= 1:
            return None
        dpr = self.devicePixelRatioF()
        return x / dpr, self.height() - y / dpr

    def _pan(self, dx: float, dy: float) -> None:
        """Move the camera and its focal point in the view plane, by pixels."""
        camera = self.plotter.camera
        position, focal = np.array(camera.position), np.array(camera.focal_point)
        distance = np.linalg.norm(position - focal)
        scale = 2 * distance * np.tan(np.radians(camera.view_angle) / 2) / max(self.height(), 1)
        forward = (focal - position) / distance
        up = np.array(camera.up)
        right = np.cross(forward, up)
        shift = (-dx * right + dy * up) * scale
        camera.position, camera.focal_point = tuple(position + shift), tuple(focal + shift)


class ViewBar(QWidget):
    """The view toolbar over the 3D view's top-right corner (ADR 0031): fit, standard
    views, edges, wireframe, orthographic, image. Shortcuts work while the view has
    focus: F fit, I isometric, 1–6 the axis views."""

    VIEWS = [("+x", "1"), ("-x", "2"), ("+y", "3"), ("-y", "4"), ("+z", "5"), ("-z", "6")]

    def __init__(self, viewport: Viewport) -> None:
        from PySide6.QtGui import QAction, QKeySequence
        from PySide6.QtWidgets import QHBoxLayout, QToolButton

        from puzzle.gui.icons import icon

        super().__init__(viewport)
        self.viewport = viewport
        self.setObjectName("view_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#view_bar { background: rgba(40, 44, 52, 150); border-radius: 6px; }"
            "QToolButton { color: white; padding: 2px; border: none; }"
            "QToolButton:checked { background: rgba(79, 143, 230, 180); border-radius: 4px; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(1)
        self.actions_by_name: dict[str, QAction] = {}
        # Shortcuts fire while the view (or this bar) has focus, never while typing.
        target = viewport.view if viewport.plotter is not None else viewport

        def add(key, text, slot, shortcut=None, checkable=False, label=None):
            action = QAction(icon(key), label or text, self)
            action.setCheckable(checkable)
            action.setToolTip(f"{text}  ({shortcut})" if shortcut else text)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                target.addAction(action)
            action.triggered.connect(slot)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            if label:  # the axis views read as text: +X, -Y, ...
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            row.addWidget(button)
            self.actions_by_name[key] = action

        add("fit", "Fit all", viewport.fit, "F")
        add("iso", "Isometric", lambda: viewport.look("iso"), "I")
        for direction, shortcut in self.VIEWS:
            add(
                direction,
                f"View from {direction.upper()}",
                lambda _=False, d=direction: viewport.look(d),
                shortcut,
                label=direction.upper(),
            )
        add("edges", "Element edges", lambda on: viewport.set_style(edges=on), checkable=True)
        add("wireframe", "Wireframe", lambda on: viewport.set_style(wireframe=on), checkable=True)
        add(
            "projection",
            "Orthographic",
            lambda on: viewport.set_style(parallel=on),
            checkable=True,
        )
        add("section", "Section cut", self.toggle_cut_bar, checkable=True)
        add("extremes", "Max and min markers", viewport.show_extremes, checkable=True)
        self.actions_by_name["extremes"].setChecked(True)
        add("symbols", "Supports and loads", viewport.set_symbols_shown, checkable=True)
        self.actions_by_name["symbols"].setChecked(True)
        add("clear", "Clear probes", viewport.clear_probes, "Esc")
        add("copy", "Copy image", self.copy_image)
        add("camera", "Save image…", self.save_image)
        self.adjustSize()

    def toggle_cut_bar(self, on: bool) -> None:
        """The section cut's controls under this bar; off removes the cut."""
        if on:
            if getattr(self, "cut_bar", None) is None:
                self.cut_bar = CutBar(self.viewport)
            self.cut_bar.show()
            self.cut_bar.apply()
            self.place()
        else:
            if getattr(self, "cut_bar", None) is not None:
                self.cut_bar.hide()
            self.viewport.set_cut(None)

    def copy_image(self) -> None:
        from PySide6.QtWidgets import QApplication

        image = self.viewport.image()
        if image is not None:
            QApplication.clipboard().setImage(image)

    def save_image(self) -> None:
        image = self.viewport.image()
        if image is None:
            return
        path = self.ask_path()
        if path:
            image.save(path)

    def ask_path(self) -> str | None:
        """Where to save the image (tests replace this)."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(self, "Save image", "view.png", "PNG (*.png)")
        return path or None

    def place(self) -> None:
        self.adjustSize()
        parent = self.parentWidget()
        self.move(max(0, parent.width() - self.width() - 8), 8)
        self.raise_()
        bar = getattr(self, "cut_bar", None)
        if bar is not None and bar.isVisible():
            bar.adjustSize()
            bar.move(max(0, parent.width() - bar.width() - 8), self.y() + self.height() + 4)
            bar.raise_()


class _Sources(QComboBox):
    """The cut's plane choices, listed afresh each time the list opens (selections and
    construction planes come and go)."""

    def __init__(self, bar) -> None:
        super().__init__()
        self.bar = bar

    def showPopup(self) -> None:  # noqa: N802 - Qt's name
        self.bar.refresh_sources()
        super().showPopup()


class CutBar(QWidget):
    """A section cut's controls (ADR 0038): the plane, where it is, which side is kept,
    and a ghost of the rest.

    The plane is across X, Y or Z at a place across the model (the slider), or placed:
    a face picked on the shown result, a face selection, or a construction plane (ADR
    0046), each moved along its normal by an offset in the display length unit."""

    AXES = (("Across X", "axis:x"), ("Across Y", "axis:y"), ("Across Z", "axis:z"))

    def __init__(self, viewport) -> None:
        from PySide6.QtWidgets import (
            QCheckBox,
            QDoubleSpinBox,
            QHBoxLayout,
            QPushButton,
            QSlider,
        )

        super().__init__(viewport)
        self.viewport = viewport
        self.setObjectName("cut_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#cut_bar { background: rgba(40, 44, 52, 170); border-radius: 6px; }"
            "QLabel, QCheckBox { color: white; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.axis = _Sources(self)
        self.face = None  # the picked face, (2, tag)
        self.refresh_sources()
        self.pick = QPushButton("Pick face")
        self.pick.setToolTip("Double-click a face of the result to cut along it")
        self.pick.clicked.connect(self.pick_face)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 100)
        self.position.setValue(50)
        self.position.setFixedWidth(160)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e9, 1e9)
        self.offset.setDecimals(4)
        self.offset.setToolTip("Moves the plane along its normal")
        self.flip = QCheckBox("Flip")
        self.ghost = QCheckBox("Ghost")
        self.ghost.setChecked(True)
        for w in (self.axis, self.pick, self.position, self.offset, self.flip, self.ghost):
            row.addWidget(w)
        self.axis.currentIndexChanged.connect(self.apply)
        self.position.valueChanged.connect(self.apply)
        self.offset.valueChanged.connect(self.apply)
        self.flip.toggled.connect(self.apply)
        self.ghost.toggled.connect(self.apply)
        self._modes()

    def choice(self) -> tuple[str, str | None]:
        """The chosen plane as (kind, ref): axis x/y/z, face, selection Part/Name, plane id.
        Items hold "kind:ref" text: Qt doesn't match Python tuples as item data."""
        kind, _, ref = (self.axis.currentData() or "axis:x").partition(":")
        return kind, ref or None

    def sources(self) -> list[tuple[str, str]]:
        """(name, "kind:ref") for every plane the cut can follow."""
        out = list(self.AXES)
        out.append(("Picked face", "face:"))
        state = self.viewport.result_state
        mesh = state["mesh"] if state is not None else self.viewport.mesh
        if mesh is not None:
            for name, g in sorted(mesh.groups.items()):
                if g.dim == 2 and "/" in name:
                    part, sel = name.split("/", 1)
                    out.append((f"Face selection: {sel} ({part})", f"selection:{name}"))
        planes = getattr(self.viewport, "construction_planes", None)
        for name, key, _origin, _normal in planes() if planes else []:
            out.append((f"Construction plane: {name}", f"plane:{key}"))
        return out

    def refresh_sources(self) -> None:
        current = self.axis.currentData()
        self.axis.blockSignals(True)
        self.axis.clear()
        for text, data in self.sources():
            self.axis.addItem(text, data)
        i = self.axis.findData(current) if current is not None else 0
        self.axis.setCurrentIndex(max(i, 0))
        self.axis.blockSignals(False)

    def _modes(self) -> None:
        kind = self.choice()[0]
        self.position.setVisible(kind == "axis")
        self.offset.setVisible(kind != "axis")
        self.pick.setVisible(kind == "face")
        from puzzle.settings import length_unit

        self.offset.setSuffix(f" {length_unit()[0]}")
        self.adjustSize()

    def pick_face(self) -> None:
        """The next double-click on the shown result picks the face to cut along."""
        self.viewport.capture_point(self._face_at, snap="none")

    def _face_at(self, point) -> None:
        from puzzle.post.cuts import face_at

        state = self.viewport.result_state
        mesh = state["mesh"] if state is not None else self.viewport.mesh
        if point is None or mesh is None:
            return
        self.face, self.fixed = face_at(mesh, point), None
        self.apply()

    def use_face(self, entity) -> None:
        """Cut along a face (2, tag), as if it had been picked."""
        self.face = tuple(entity)
        i = self.axis.findData("face:")
        self.axis.setCurrentIndex(i)
        self.apply()

    def plane(self):
        """The chosen placed plane's (origin, normal), or None for an axis or nothing yet."""
        from puzzle.post.cuts import face_plane

        kind, ref = self.choice()
        state = self.viewport.result_state
        mesh = state["mesh"] if state is not None else self.viewport.mesh
        if kind == "face":
            if self.face is None and getattr(self, "fixed", None) is not None:
                origin, normal = self.fixed  # a recalled section's plane
                return np.asarray(origin, float), np.asarray(normal, float)
            if self.face is None and self.viewport.picked:  # the faces picked in the mesh view
                faces = [e for e in self.viewport.picked if e[0] == 2]
                self.face = faces[0] if faces else None
            if self.face is None or mesh is None:
                return None
            return face_plane(mesh, [self.face])
        if kind == "selection" and mesh is not None and ref in mesh.groups:
            return face_plane(mesh, mesh.groups[ref].entities)
        if kind == "plane":
            planes = getattr(self.viewport, "construction_planes", None)
            for _name, key, origin, normal in planes() if planes else []:
                if key == ref:
                    return np.asarray(origin, float), np.asarray(normal, float)
        return None

    def saved(self) -> dict | None:
        """The cut as a saved section's fields (`SectionCut`), None with nothing to cut
        along yet. A picked face is kept as its plane: mesh faces aren't kept."""
        from puzzle.settings import length_unit

        kind, ref = self.choice()
        out = {"flip": self.flip.isChecked(), "ghost": self.ghost.isChecked()}
        if kind == "axis":
            return out | {"plane": ref, "fraction": self.position.value() / 100}
        out["offset"] = self.offset.value() / length_unit()[1]
        if kind == "selection":
            return out | {"plane": "selection", "selection": ref}
        if kind == "plane":
            return out | {"plane": "construction", "construction": ref}
        placed = self.plane()
        if placed is None:
            return None
        origin, normal = placed
        return out | {"plane": "fixed", "origin": tuple(origin.tolist()),
                      "normal": tuple(normal.tolist())}  # fmt: skip

    def recall(self, saved) -> None:
        """Set the controls to a saved section (`SectionCut`) and cut along it."""
        from puzzle.settings import length_unit

        self.refresh_sources()
        widgets = (self.axis, self.position, self.offset, self.flip, self.ghost)
        for w in widgets:
            w.blockSignals(True)
        try:
            if saved.plane in ("x", "y", "z"):
                data = f"axis:{saved.plane}"
                self.position.setValue(round(saved.fraction * 100))
            elif saved.plane == "selection":
                data = f"selection:{saved.selection}"
            elif saved.plane == "construction":
                data = f"plane:{saved.construction}"
            else:
                data = "face:"
            i = self.axis.findData(data)
            if i < 0 and saved.plane != "fixed":
                raise ValueError(f"section '{saved.name}': its plane isn't here")
            self.axis.setCurrentIndex(max(i, 0) if saved.plane != "fixed" else
                                      self.axis.findData("face:"))  # fmt: skip
            self.offset.setValue(saved.offset * length_unit()[1])
            self.flip.setChecked(saved.flip)
            self.ghost.setChecked(saved.ghost)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self.fixed = (saved.origin, saved.normal) if saved.plane == "fixed" else None
        self.apply()

    def apply(self, *_) -> None:
        from puzzle.post.cuts import Cut
        from puzzle.settings import length_unit

        self._modes()
        kind, ref = self.choice()
        flip, ghost = self.flip.isChecked(), self.ghost.isChecked()
        if kind == "axis":
            cut = Cut(ref, self.position.value() / 100, flip, ghost)
        else:
            try:
                placed = self.plane()
            except ValueError:
                placed = None
            if placed is None:  # nothing to cut along yet (no face picked)
                self.viewport.set_cut(None)
                return
            origin, normal = placed
            offset = self.offset.value() / length_unit()[1]  # display unit -> m
            cut = Cut(flip=flip, ghost=ghost, origin=tuple(origin.tolist()),
                      normal=tuple(normal.tolist()), offset=offset, source=kind)  # fmt: skip
        self.viewport.set_cut(cut)


class Viewport(QWidget):
    picked_changed = Signal(list)  # the picked entities, [(dim, tag), ...]
    probes_changed = Signal(list)  # the view's probes (ADR 0032)
    redraw_needed = Signal()  # a setting changed that the window's result must be redrawn for

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mesh: Mesh | None = None
        self.highlight: str | None = None
        self.highlight_entities: list[tuple[int, int]] = []
        self.surface, self.entities = None, []
        self.picked: list[tuple[int, int]] = []
        self.pick_history: list[list[tuple[int, int]]] = []  # undo while picking
        self.picking = False
        self.capture = None  # a tool waiting for a double-clicked point
        self.pick_kind = "face"  # the selection filter: face, edge, vertex or body
        self.lines, self.line_entities = None, []
        self.vertices: dict = {}  # (0, tag) -> position, set by the window per mesh
        self.boundaries: dict = {}  # (3, tag) -> its faces, set by the window per mesh
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.shown: dict = {}  # what is on screen: kind, scalar, range, highlight
        self.plotter = None
        if rendering_available():
            self.view = SceneView(self)
            self.plotter = self.view.plotter
            from puzzle.gui.theme import view_background

            view_background(self.plotter)  # a soft gradient, or plain (ADR 0045)
            self.plotter.add_axes()  # once: every add_axes makes another marker
            layout.addWidget(self.view)
        else:
            self.placeholder = QLabel("3D view unavailable here (no OpenGL)")
            self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.placeholder)
        # How the scene is drawn, whatever is shown (ADR 0031's view toolbar).
        self.style = {
            "edges": False,
            "wireframe": False,
            "parallel": False,
            "view": "iso",
            "extremes": True,
            "symbols": True,
        }
        self.glyphs: list = []  # the supports' and loads' symbols (ADR 0035)
        self._glyph_actors: list[str] = []
        # The shown result, for markers and probes (ADR 0032).
        self.result_state: dict | None = None
        self.cut = None  # a section cut on results (post.cuts.Cut, ADR 0038)
        from puzzle.post.legend import Legend

        self.legend = Legend.from_settings()  # the colour bar's look (Settings > Results)
        self.ranges: dict[str, tuple[float, float]] = {}  # result key -> custom range
        self._extremes: list[tuple] = []
        self._mark_actors: list[str] = []
        self.probes: list[dict] = []
        self.labels: list[tuple] = []  # (xyz shown, text, colour, QLabel)
        if self.plotter is not None:
            self.view.drawn.connect(self._place_labels)
        self.view_bar = ViewBar(self)

    def show_mesh(
        self, mesh: Mesh, highlight: str | None = None, entities: list | None = None
    ) -> None:
        """The mesh's volume, with a selection highlighted (a mesh group `Part/Name`, or
        its face `entities` when it was made after meshing) and picked faces marked."""
        self.result_state, self.probes, self._mark_actors = None, [], []
        self.glyphs, self._glyph_actors, self.pins = [], [], []
        self._clear_labels()
        self._update_click()
        if mesh is not self.mesh:
            self.mesh = mesh
            self.surface, self.entities = entity_surface(mesh)
            self.lines, self.line_entities = entity_lines(mesh)
            known = set(self.entities) | set(self.line_entities) | set(self.vertices)
            self.picked = [e for e in self.picked if e in known or e in self.boundaries]
        self.highlight, self.highlight_entities = highlight, list(entities or [])
        try:
            grid = to_grid(mesh)
        except ValueError:  # a surface-only preview of the geometry: nothing meshed yet
            grid = None
        cells = grid.n_cells if grid is not None else (self.surface.n_cells if self.surface else 0)
        self.shown = {
            "kind": "mesh" if grid is not None else "preview",
            "cells": cells,
            "highlight": highlight,
            "highlighted": list(self.highlight_entities),
            "picked": list(self.picked),
        }
        if self.plotter is None:
            return
        self.plotter.clear()
        dim = highlight is not None and highlight in mesh.groups and mesh.groups[highlight].dim == 3
        if grid is not None:
            self.plotter.add_mesh(
                grid,
                color="#c9d3dd",
                show_edges=True,
                edge_color="#7a8794",
                opacity=0.35 if dim else 1,
            )
            from puzzle.post.vtk import beam_lines

            if (lines := beam_lines(grid)) is not None:
                self.plotter.add_mesh(lines, color="#5b6875", line_width=5)
        elif self.surface is not None:
            self.plotter.add_mesh(self.surface, color="#c9d3dd", smooth_shading=False)
            edges = self.surface.extract_feature_edges(
                30, boundary_edges=True, manifold_edges=False
            )
            self.plotter.add_mesh(edges, color="#4a5560", line_width=1.5)
        if highlight is not None:
            surface = selection_surface(mesh, highlight)
            if surface is not None:
                self._overlay(surface, HIGHLIGHT)
        for group, colour in ((self.highlight_entities, HIGHLIGHT), (self.picked, PICKED)):
            self._draw_entities(group, colour)
        self._frame(mesh)

    def _draw_entities(self, group, colour: str) -> None:
        """Faces as an overlay, edges as thick lines, vertices as dots, bodies by their faces."""
        import pyvista as pv

        faces = [e for e in group if e[0] == 2]
        faces += [f for e in group if e[0] == 3 for f in self.boundaries.get(e, [])]
        index = [self.entities.index(e) for e in faces if e in self.entities]
        if index and self.surface is not None:
            mask = np.isin(self.surface.cell_data["entity"], index)
            self._overlay(self.surface.extract_cells(mask), colour)
        index = [self.line_entities.index(e) for e in group if e in self.line_entities]
        if index and self.lines is not None:
            mask = np.isin(self.lines.cell_data["entity"], index)
            edges = self.lines.extract_cells(np.flatnonzero(mask))
            self.plotter.add_mesh(edges, color=colour, line_width=5, render_lines_as_tubes=True)
        points = [self.vertices[e] for e in group if e in self.vertices]
        if points:
            cloud = pv.PolyData(np.asarray(points, float))
            self.plotter.add_mesh(cloud, color=colour, point_size=14, render_points_as_spheres=True)

    def _overlay(self, surface, colour: str) -> None:
        """Faces drawn over the mesh's own: pulled towards the camera in depth, or the
        two coplanar surfaces fight and the colour comes out speckled."""
        actor = self.plotter.add_mesh(surface, color=colour, show_edges=False)
        mapper = actor.GetMapper()
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
        mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)

    # --- picking -----------------------------------------------------------------------

    def set_picking(self, on: bool) -> None:
        """Picking (ADR 0034): a click picks (Shift adds, Ctrl or ⌘ removes), a
        double-click toggles one, a drag still rotates (or boxes, in box mode)."""
        self.picking = on
        if not on:
            self.pick_history.clear()
            self.set_box_mode(False)
        self._update_click()

    def capture_point(self, callback, snap: str = "corner") -> None:
        """Hand the next double-clicked point on the model to `callback(xyz)` (metres,
        undeformed), then go back to what double-click did before; None cancels. For
        tools that need a point anywhere, not only a corner (construction geometry).

        `snap`: "node" moves it to the nearest node of the shown mesh or result (none in
        a quick preview: then corners), "corner" to a CAD corner within 1% of the
        model's size, "none" keeps the surface point."""
        self.capture = callback
        self.capture_snap = snap
        self.shown["capturing"] = callback is not None
        self._update_click()

    def node_mesh(self):
        """The real mesh shown (meshed, or a result's), whose nodes a pick can snap to;
        None for a quick preview."""
        if self.result_state is not None:
            return self.result_state["mesh"]
        if self.shown.get("kind") == "mesh":
            return getattr(self, "mesh", None)
        return None

    def _captured(self, point) -> None:
        import pyvista as pv

        callback, self.capture = self.capture, None
        snap = getattr(self, "capture_snap", "corner")
        self.shown["capturing"] = False
        self._update_click()
        point = np.asarray(point, float)
        state = self.result_state
        if state is not None and state.get("scale"):  # the same spot, undeformed
            u = pv.PolyData(point.reshape(1, 3)).sample(state["grid"]).point_data
            name = _warp_name(u)
            if name:
                point = point - state["scale"] * np.asarray(u[name][0])
        mesh = self.node_mesh() if snap == "node" else None
        self.shown["snapped"] = None
        if mesh is not None and len(mesh.coords):
            i = int(np.argmin(np.linalg.norm(mesh.coords - point, axis=1)))
            point = np.asarray(mesh.coords[i], float)
            self.shown["snapped"] = int(mesh.node_ids[i]) if hasattr(mesh, "node_ids") else i
        elif snap in ("node", "corner"):
            surface = getattr(self, "surface", None)
            if self.vertices and surface is not None:
                size = float(np.linalg.norm(np.ptp(surface.points, axis=0))) or 1.0
                near = min(self.vertices.values(),
                           key=lambda v: np.linalg.norm(np.asarray(v) - point))  # fmt: skip
                if np.linalg.norm(np.asarray(near) - point) <= 0.01 * size:
                    point = np.asarray(near, float)
        if callback is not None:
            callback(tuple(float(v) for v in point))

    def _update_click(self) -> None:
        """Double-click picks while picking, probes a shown result, else does nothing;
        a tool waiting for a point (`capture_point`) takes it first."""
        if self.plotter is None:
            return
        if getattr(self, "capture", None) is not None:
            self.view.on_double_click = self._captured
            self.view.on_click = self.view.on_box = None
        elif self.picking:
            self.view.on_double_click = self._clicked
            self.view.on_click = self._click
            self.view.on_box = self.box_pick
        elif self.result_state is not None:
            self.view.on_double_click = self._probe_click
            self.view.on_click = self.view.on_box = None
        else:
            self.view.on_double_click = self.view.on_click = self.view.on_box = None

    def toggle_pick(self, entity: tuple[int, int]) -> None:
        mode = "remove" if entity in self.picked else "add"
        self.pick([entity], mode)

    def pick(self, entities, mode: str = "replace") -> None:
        """Change the picks (ADR 0034): "replace" them, "add" to them or "remove" from
        them; each change can be undone while picking (`undo_pick`)."""
        entities = [e for e in entities if e[0] == KIND_DIM[self.pick_kind] or (
            self.pick_kind == "body" and e[0] == 3)]  # fmt: skip
        before = list(self.picked)
        if mode == "replace":
            self.picked = list(dict.fromkeys(entities))
        elif mode == "add":
            self.picked = list(dict.fromkeys([*self.picked, *entities]))
        elif mode == "remove":
            self.picked = [e for e in self.picked if e not in entities]
        else:
            raise ValueError(f"pick modes are replace, add and remove, not '{mode}'")
        if self.picked == before:
            return
        self.pick_history.append(before)
        self._picks_changed()

    def undo_pick(self) -> bool:
        """Step back one pick change; False when there is none."""
        if not self.pick_history:
            return False
        self.picked = self.pick_history.pop()
        self._picks_changed()
        return True

    def _picks_changed(self) -> None:
        if self.mesh is not None:
            self.show_mesh(self.mesh, self.highlight, self.highlight_entities)
        self.picked_changed.emit(list(self.picked))

    def extend_picks(self, max_angle: float = 20.0) -> None:
        """Faces: add every face reached across smooth joins (tangent); edges: follow the
        chain of edges that continue each other."""
        from puzzle.mesh.adjacency import edge_chain, tangent_faces

        if self.mesh is None or not self.picked:
            return
        dim = self.picked[0][0]
        seeds = {t for d, t in self.picked if d == dim}
        if dim == 2:
            found = tangent_faces(self.mesh, seeds, max_angle)
        elif dim == 1:
            found = edge_chain(self.mesh, seeds, max_angle)
        else:
            return
        self.pick([(dim, t) for t in sorted(found)], "add")

    def box_pick(self, rect, modifiers=None) -> None:
        """Entities of the filter's kind seen inside `rect` (widget pixels): one of the
        points spread over each (its element centres) shows inside the box, and a ray
        there reaches that entity, so hidden ones aren't picked."""
        from PySide6.QtCore import QPointF

        from puzzle.mesh.adjacency import samples

        if self.mesh is None or self.plotter is None:
            return
        dim = KIND_DIM[self.pick_kind]
        if dim == 0:
            spots = {t: np.asarray([xyz]) for (d, t), xyz in self.vertices.items() if d == 0}
        elif dim == 3:
            faces = samples(self.mesh, 2)
            spots = {
                tag: np.concatenate([faces[t] for _, t in fs if t in faces])
                for (d, tag), fs in self.boundaries.items()
                if d == 3 and any(t in faces for _, t in fs)
            }
        else:
            spots = samples(self.mesh, dim)
        size = float(np.linalg.norm(np.ptp(self.mesh.coords, axis=0)))
        inside = []
        for tag, pts in spots.items():
            for xyz in pts:
                at = self.view.screen_point(xyz)
                if at is None or not rect.contains(int(at[0]), int(at[1])):
                    continue
                hit = self.view.pick_point(QPointF(*at))
                if hit is None:
                    continue
                if dim == 3:
                    seen = any(self.entity_at(hit) == f for f in self.boundaries[(3, tag)])
                else:
                    seen = self.entity_at(hit) == (dim, tag)
                if seen or np.linalg.norm(np.asarray(hit) - xyz) <= 1e-3 * size:
                    inside.append((dim, tag))
                    break
        self.pick(inside, _mode(modifiers, "add"))

    def set_box_mode(self, on: bool) -> None:
        if self.plotter is not None:
            self.view.box_mode = on

    def _click(self, point, modifiers) -> None:
        if self.shown.get("kind") not in ("mesh", "preview"):
            return
        entity = self.entity_at(point)
        if entity is not None:
            self.pick([entity], _mode(modifiers, "replace"))

    def clear_picks(self) -> None:
        self.pick([], "replace")

    def set_pick_kind(self, kind: str) -> None:
        """The selection filter; picks of another kind are cleared."""
        if kind not in ("face", "edge", "vertex", "body"):
            raise ValueError(f"pick faces, edges, vertices or bodies, not '{kind}'")
        if kind != self.pick_kind:
            self.pick_kind = kind
            self.clear_picks()

    def measure(self, entities) -> dict:
        """Faces: area, and for two the distance between their centres. Edges: length.
        Vertices: for two, the distance between them."""
        out, centres = {}, []
        for e in entities:
            if e[0] == 2 and self.surface is not None and e in self.entities:
                mask = self.surface.cell_data["entity"] == self.entities.index(e)
                part = self.surface.extract_cells(np.flatnonzero(mask)).compute_cell_sizes()
                areas = np.asarray(part.cell_data["Area"])
                out["area"] = out.get("area", 0.0) + float(areas.sum())
                centres.append(
                    (areas[:, None] * part.cell_centers().points).sum(axis=0) / areas.sum()
                )
            elif e[0] == 1 and self.lines is not None and e in self.line_entities:
                mask = self.lines.cell_data["entity"] == self.line_entities.index(e)
                part = self.lines.extract_cells(np.flatnonzero(mask)).compute_cell_sizes()
                out["length"] = out.get("length", 0.0) + float(np.sum(part.cell_data["Length"]))
            elif e[0] == 0 and e in self.vertices:
                centres.append(np.asarray(self.vertices[e], float))
        if len(centres) == 2:
            out["distance"] = float(np.linalg.norm(centres[1] - centres[0]))
        return out

    def entity_at(self, point, kind: str | None = None) -> tuple[int, int] | None:
        """The entity of the filter's kind nearest a point in space (where a click landed).
        Edges and vertices must be within 2% of the model's size of it."""
        kind = kind or self.pick_kind
        point = np.asarray(point, float)
        if self.surface is None:
            return None
        size = float(np.linalg.norm(np.ptp(self.surface.points, axis=0))) or 1.0
        if kind in ("face", "body"):
            cell = self.surface.find_closest_cell(point)
            if cell < 0:
                return None
            face = self.entities[int(self.surface.cell_data["entity"][cell])]
            if kind == "face":
                return face
            return next((b for b, faces in self.boundaries.items() if face in faces), None)
        if kind == "edge":
            if self.lines is None:
                return None
            cell, closest = self.lines.find_closest_cell(point, return_closest_point=True)
            if cell < 0 or np.linalg.norm(np.asarray(closest) - point) > 0.02 * size:
                return None
            return self.line_entities[int(self.lines.cell_data["entity"][cell])]
        if kind == "vertex" and self.vertices:
            near = min(
                self.vertices, key=lambda e: np.linalg.norm(np.asarray(self.vertices[e]) - point)
            )
            if np.linalg.norm(np.asarray(self.vertices[near]) - point) <= 0.02 * size:
                return near
        return None

    def _clicked(self, point) -> None:
        if self.shown.get("kind") not in ("mesh", "preview"):
            return  # picking works on the mesh view, not on results
        entity = self.entity_at(point)
        if entity is not None:
            self.toggle_pick(entity)

    def show_result(
        self,
        mesh: Mesh,
        results: Results,
        field: str,
        scalar: str | None = None,
        step_index: int = -1,
        deform: bool = True,
        item=None,
        clim: tuple[float, float] | None = None,
        deform_factor: float = 1.0,
    ) -> dict:
        """A result field on the (deformed) mesh; returns what was shown. `item` (from
        `post.quantities.catalog`) chooses one of the results the field gives; `clim`
        fixes the colour scale (SI), so several views can share one."""
        self.pins = []  # labels belong to the scene they were made for
        steps = [s for s in results.steps if field in s.fields]
        step = steps[step_index] if steps else pick_step(results, field)
        grid = to_grid(mesh, step)
        unit = None
        if item is not None:  # a listed result (post.quantities), in the display units
            f = step.fields[field]
            data = grid.point_data if f.location == "node" else grid.cell_data
            if field not in data:
                raise KeyError(f"no '{field}' on the mesh")
            raw = data[field]
            name = item.label
            values = compute(item, raw, f.components)
            values, unit = _display(values, item.unit)
            data[name] = values
            if clim is not None:
                clim = tuple(float(c) for c in _display(np.asarray(clim, float), item.unit)[0])
        else:
            name = scalar or DEFAULT_SCALAR.get(field, field)
            arrays = set(grid.point_data) | set(grid.cell_data)
            if name not in arrays:
                name = field if field in arrays else f"{field}_magnitude"
            values = grid.point_data[name] if name in grid.point_data else grid.cell_data[name]
        key = item.key if item is not None else (scalar or field)
        custom = clim is None and key in self.ranges  # the legend's range for this result
        if custom:
            clim = self.ranges[key]
        title = (
            f"{item.label} ({unit})"
            if item is not None and unit
            else (item.label if item is not None else words(name))
        )
        base = grid  # undeformed: positions where values are read and reported
        scale = 0.0
        # Results in a coordinate system (ADR 0049) keep global displacements for this.
        warp = "displacement_global" if "displacement_global" in grid.point_data else "displacement"
        if deform and warp in grid.point_data:
            u = np.nan_to_num(grid.point_data[warp])
            umax = float(np.linalg.norm(u, axis=1).max())
            scale = 0.1 * grid.length / umax if umax > 0 else 0.0
            if scale and deform_factor != 1.0:  # an animation frame (ADR 0038)
                scale *= deform_factor
            if scale:
                grid = grid.warp_by_vector(warp, factor=scale)
        label = step.label()
        on_points = name in grid.point_data
        where = np.asarray(base.points if on_points else base.cell_centers().points)
        shown_at = np.asarray(grid.points if on_points else grid.cell_centers().points)
        hi, lo = int(np.nanargmax(values)), int(np.nanargmin(values))
        self.result_state = {
            "mesh": mesh,
            "step": step,
            "item": item,
            "grid": grid,
            "base": base,
            "scale": scale,
            "unit": unit,
            "name": name,
        }
        self.shown = {
            "kind": "result",
            "field": field,
            "scalar": name,
            "step": label,
            "min": float(np.nanmin(values)),
            "max": float(np.nanmax(values)),
            "scale": scale,
            # Shown by the window's results bar: VTK's 2D text leaves a clipped copy along
            # the view's bottom edge on Retina displays.
            "title": f"{title}, {label}" + (f", deformation ×{scale:.3g}" if scale else ""),
            "unit": unit,
            "clim": clim,
            "key": key,
            "custom_range": custom,
            "max_at": where[hi].tolist(),
            "min_at": where[lo].tolist(),
            "cut": None,
        }
        tail = f" {unit}" if unit else ""
        self._extremes = [
            (shown_at[hi], f"Max {_number(values[hi])}{tail}", "#e5534b"),
            (shown_at[lo], f"Min {_number(values[lo])}{tail}", "#4f8fe6"),
        ]
        self.probes = []
        self.probes_changed.emit([])
        self.glyphs, self._glyph_actors = [], []  # results show no symbols
        away = None
        if self.cut is not None:  # a section cut (ADR 0038), placed on the undeformed model
            from puzzle.post.cuts import clip

            grid, away = clip(grid, self.cut, base.bounds)
            origin, normal = self.cut.plane(base.bounds)
            self.shown["cut"] = {"axis": self.cut.axis, "fraction": self.cut.fraction,
                                 "source": self.cut.source or "axis",
                                 "origin": origin.tolist(), "normal": normal.tolist()}  # fmt: skip
        if self.plotter is not None:
            self.plotter.clear()
            self._mark_actors = []
            if away is not None and self.cut.ghost and away.n_cells:
                self.plotter.add_mesh(away, color="#b8c2cc", opacity=0.15, reset_camera=False)
            # The legend (post/legend.py): colours, bands, one colour outside a custom
            # range, and the bar's place and size. The title is the unit only: the
            # result's name is in the results bar, and VTK centres a long title over
            # the bar, past the view's edge.
            colours = self.legend.mesh_args(custom)
            bar = self.legend.bar_args(
                unit or (item.label if item is not None else words(name)),
                bar_format(*(clim or (self.shown["min"], self.shown["max"]))),
            )
            self.plotter.add_mesh(grid, scalars=name, clim=clim, scalar_bar_args=bar, **colours)
            self.legend.finish(self.plotter)
            from puzzle.post.view import draw_beams

            draw_beams(self.plotter, grid, scalars=name, clim=clim, **colours)
            self._frame(mesh)
        self._draw_marks()  # recorded in `shown` without OpenGL too
        self._update_click()
        self._render()
        return self.shown

    def _render(self) -> None:
        if self.plotter is not None:
            self.view.refresh()

    def clear(self) -> None:
        self.result_state = None
        self.probes = []
        self._clear_labels()
        self.shown = {}
        self.mesh, self.surface, self.entities, self.picked = None, None, [], []
        self.lines, self.line_entities = None, []
        if self.plotter is not None:
            self.plotter.clear()
            self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.view_bar.place()

    def screenshot(self, path) -> None:
        if self.plotter is not None:
            self.plotter.screenshot(str(path))

    def close_plotter(self) -> None:
        if self.plotter is not None:
            self.plotter.close()

    # --- supports and loads (ADR 0035) ---------------------------------------------------

    def show_symbols(self, glyphs) -> None:
        """Draw `post.symbols` glyph sets over the mesh shown (sized to the model)."""
        self.glyphs = list(glyphs)
        self._draw_symbols()
        self._render()

    def _draw_symbols(self) -> None:
        on = self.style["symbols"] and self.shown.get("kind") in ("mesh", "preview")
        self.shown["symbols"] = (
            [(g.feature, g.kind, len(g.points) or len(g.faces)) for g in self.glyphs] if on else []
        )
        if self.plotter is None:
            return
        import pyvista as pv

        for name in self._glyph_actors:
            self.plotter.remove_actor(name)
        self._glyph_actors = []
        if not on or self.mesh is None:
            return
        size = 0.06 * float(np.linalg.norm(np.ptp(self.mesh.coords, axis=0)))
        for i, g in enumerate(self.glyphs):
            name = f"puzzle-symbol-{i}"
            if g.kind == "tint":
                index = [self.entities.index(e) for e in g.faces if e in self.entities]
                if index and self.surface is not None:
                    mask = np.isin(self.surface.cell_data["entity"], index)
                    actor = self.plotter.add_mesh(
                        self.surface.extract_cells(mask), color=g.colour, opacity=0.55,
                        name=name, reset_camera=False,
                    )  # fmt: skip
                    actor.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()
                    self._glyph_actors.append(name)
                continue
            if not len(g.points):
                continue
            if g.kind == "tube":  # a fastener (ADR 0027): a bar between its two ends
                a, b = (np.asarray(p, float) for p in g.points[:2])
                tube = pv.Tube(pointa=a, pointb=b, radius=g.radius, n_sides=16)
                self.plotter.add_mesh(tube, color=g.colour, name=name, reset_camera=False)
                self._glyph_actors.append(name)
                continue
            if g.kind == "arrow":  # arrows end on the face: their tails stand off it
                starts = g.points - g.directions * size
                geom = pv.Arrow(tip_length=0.3, tip_radius=0.12, shaft_radius=0.04)
                factor = size
            else:  # cones press on the face from outside
                starts = g.points - g.directions * size * 0.25
                geom = pv.Cone(direction=(1, 0, 0), height=1.0, radius=0.45, resolution=12)
                factor = size * 0.35
            cloud = pv.PolyData(np.asarray(starts, float))
            cloud["vectors"] = np.asarray(g.directions, float)
            arrows = cloud.glyph(orient="vectors", scale=False, factor=factor, geom=geom)
            self.plotter.add_mesh(arrows, color=g.colour, name=name, reset_camera=False)
            self._glyph_actors.append(name)

    def set_cut(self, cut) -> None:
        """A section cut on results (`post.cuts.Cut`), or None; the result is redrawn."""
        self.cut = cut
        self.redraw_needed.emit()

    def set_symbols_shown(self, on: bool) -> None:
        self.style["symbols"] = on
        self._draw_symbols()
        self._render()

    # --- extremes and probes (ADR 0032) --------------------------------------------------

    def probe(self, xyz_shown, add: bool = False) -> dict | None:
        """Read the shown result where the (deformed) view shows `xyz_shown`: the value
        in display units, the point in the undeformed geometry. `add` keeps the others."""
        import pyvista as pv

        state = self.result_state
        if state is None or state["item"] is None:
            return None
        point = np.asarray(xyz_shown, float)
        if state["scale"]:  # back to the undeformed geometry: the same spot in the cell
            u = pv.PolyData(point.reshape(1, 3)).sample(state["grid"]).point_data
            name = _warp_name(u)
            if name:
                point = point - state["scale"] * np.asarray(u[name][0])
        return self._probe_at(point, xyz_shown, add)

    def probe_undeformed(self, point, add: bool = False) -> dict | None:
        """A probe at a point of the undeformed geometry (a kept probe), read there and
        drawn where the deformed view shows it."""
        import pyvista as pv

        state = self.result_state
        if state is None or state["item"] is None:
            return None
        shown = np.asarray(point, float)
        if state["scale"]:
            u = pv.PolyData(shown.reshape(1, 3)).sample(state["base"]).point_data
            name = _warp_name(u)
            if name:
                shown = shown + state["scale"] * np.asarray(u[name][0])
        return self._probe_at(np.asarray(point, float), shown, add)

    def _probe_at(self, point, shown, add: bool) -> dict:
        from puzzle.post.probes import at_point

        state = self.result_state
        found = at_point(state["mesh"], state["step"], state["item"], point)
        shown_value, unit = _display(np.array([found["value"]]), state["item"].unit)
        probe = found | {
            "shown": float(shown_value[0]),
            "unit": unit,
            "at": np.asarray(shown, float).tolist(),
            "label": state["item"].label,
            "quantity": state["item"].key,
        }
        self.probes = [*self.probes, probe] if add else [probe]
        self._draw_marks()
        self._render()
        self.probes_changed.emit(list(self.probes))
        return probe

    def clear_probes(self) -> None:
        self.probes = []
        self._draw_marks()
        self._render()
        self.probes_changed.emit([])

    def _probe_click(self, xyz) -> None:
        add = bool(self.view.modifiers & Qt.KeyboardModifier.ShiftModifier)
        self.probe(xyz, add=add)

    def show_seeds(self, points) -> None:
        """The edges' future nodes as small dots over the scene (ADR 0019 edge seeds)."""
        points = np.asarray(points, float).reshape(-1, 3)
        self.shown["seeds"] = len(points)
        if self.plotter is None or not len(points):
            return
        self.plotter.add_points(
            points,
            color="#ffcc33",
            point_size=7,
            render_points_as_spheres=True,
            name="puzzle-seeds",
            reset_camera=False,
        )
        self.view.refresh()

    def show_fasteners(self, tubes) -> None:
        """Fasteners over a result: (glyph, label, in tension) each; the label pinned at
        the tube's middle (ADR 0027)."""
        self.pins = [
            (np.asarray(g.points, float).mean(axis=0), text, "#ff8a7a" if pull else "#7ab8ff")
            for g, text, pull in tubes
        ]
        self.shown["fasteners"] = [text for _g, text, _p in tubes]
        if self.plotter is None:
            return
        import pyvista as pv

        for i, (g, _text, pull) in enumerate(tubes):
            a, b = (np.asarray(p, float) for p in g.points[:2])
            tube = pv.Tube(pointa=a, pointb=b, radius=g.radius, n_sides=16)
            colour = "#e5534b" if pull else "#3d7bd9"
            self.plotter.add_mesh(
                tube, color=colour, name=f"puzzle-fastener-{i}", reset_camera=False
            )
        self._draw_marks()
        self.view.refresh()

    def show_construction(self, kind: str, a, b, size: float) -> None:
        """A construction path (a line from `a` to `b`) or plane (through `a`, normal
        `b`, `size` across) over the scene, until the next one (ADR 0046)."""
        self.shown["construction"] = kind
        a, b = np.asarray(a, float), np.asarray(b, float)
        self.pins = [(a, "Start", "#ffcc33"), (b, "End", "#ffcc33")] if kind == "path" else []
        if self.plotter is not None:
            import pyvista as pv

            if kind == "path":
                shape = pv.Line(a, b)
                self.plotter.add_mesh(shape, color="#ffcc33", line_width=4,
                                      name="puzzle-construction", reset_camera=False)  # fmt: skip
            else:
                shape = pv.Plane(center=a, direction=b, i_size=size, j_size=size)
                self.plotter.add_mesh(shape, color="#ffcc33", opacity=0.35, show_edges=True,
                                      name="puzzle-construction", reset_camera=False)  # fmt: skip
        self._draw_marks()
        if self.plotter is not None:
            self.view.refresh()

    def show_triad(self, system, size: float) -> None:
        """A coordinate system's x, y, z arrows (red, green, blue) at its origin; for a
        cylindrical or spherical one, its axis too (ADR 0049)."""
        self.shown["triad"] = system.type
        origin = system.centre()
        axes = system.orientation()
        names = {"cartesian": "xyz", "cylindrical": ("θ=0", "", "z"),
                 "spherical": ("φ=0", "", "z")}[system.type]  # fmt: skip
        colours = ("#e5534b", "#4caf50", "#4f8fe6")
        self.pins = [
            (origin + size * axes[:, i], names[i], c) for i, c in enumerate(colours) if names[i]
        ]
        if self.plotter is not None:
            import pyvista as pv

            for i, colour in enumerate(("#e5534b", "#4caf50", "#4f8fe6")):
                if system.type != "cartesian" and i == 1:
                    continue  # θ and φ turn about the axis: no fixed y to draw
                arrow = pv.Arrow(start=origin, direction=axes[:, i], scale=size)
                self.plotter.add_mesh(arrow, color=colour, name=f"puzzle-triad-{i}",
                                      reset_camera=False)  # fmt: skip
            if system.type != "cartesian":  # the axis, both ways
                line = pv.Line(origin - 2 * size * axes[:, 2], origin + 2 * size * axes[:, 2])
                self.plotter.add_mesh(line, color="#4f8fe6", line_width=2,
                                      name="puzzle-triad-axis", reset_camera=False)  # fmt: skip
        self._draw_marks()
        if self.plotter is not None:
            self.view.refresh()

    def mark_point(self, xyz, text: str) -> None:
        """A labelled point pinned in the view until the next scene (the Mesh tab's
        worst elements)."""
        self.pins = [(np.asarray(xyz, float), text, "#ffcc33")]
        self.shown["pins"] = [text]
        self._draw_marks()
        if self.plotter is not None:
            self.view.refresh()

    def _marks(self) -> list[tuple]:
        marks = list(self._extremes) if self.style["extremes"] and self.result_state else []
        marks += getattr(self, "pins", [])
        unit = lambda p: f" {p['unit']}" if p["unit"] else ""  # noqa: E731
        marks += [
            (np.asarray(p["at"]), f"{_number(p['shown'])}{unit(p)}", "#f5f5f5") for p in self.probes
        ]
        return marks

    def _draw_marks(self) -> None:
        """Markers at the extremes and the probes, and their labels (Qt overlays)."""
        marks = self._marks()
        self.shown["marks"] = [text for _, text, _ in marks]
        self._clear_labels()
        if self.plotter is None:
            return
        for name in self._mark_actors:
            self.plotter.remove_actor(name)
        self._mark_actors = []
        for i, (xyz, text, colour) in enumerate(marks):
            name = f"puzzle-mark-{i}"
            self.plotter.add_points(
                np.asarray(xyz, float).reshape(1, 3),
                color=colour,
                point_size=11,
                render_points_as_spheres=True,
                name=name,
                reset_camera=False,
            )
            self._mark_actors.append(name)
            label = QLabel(text, self.view)
            label.setStyleSheet(
                f"QLabel {{ background: rgba(30,30,30,190); color: {colour}; padding: 1px 4px;"
                " border-radius: 3px; }"
            )
            label.adjustSize()
            self.labels.append((np.asarray(xyz, float), text, colour, label))
        self._place_labels()

    def _clear_labels(self) -> None:
        for *_rest, label in self.labels:
            label.hide()  # at once: deleteLater waits for the event loop's next turn
            label.deleteLater()
        self.labels = []

    def _place_labels(self) -> None:
        if self.plotter is None:
            return
        for xyz, _text, _colour, label in self.labels:
            at = self.view.screen_point(xyz)
            label.setVisible(at is not None)
            if at is not None:
                label.move(int(at[0]) + 8, int(at[1]) - label.height() - 4)
                label.raise_()

    def show_extremes(self, on: bool) -> None:
        self.style["extremes"] = on
        if self.result_state is not None:
            self._draw_marks()
            self._render()

    # --- the view toolbar's actions (ADR 0031) ------------------------------------------

    def fit(self) -> None:
        if self.plotter is not None:
            self.plotter.reset_camera()
            self._render()

    def look(self, direction: str) -> None:
        """A standard view: "iso", or from "+x", "-x", "+y", "-y", "+z", "-z"."""
        self.style["view"] = direction
        if self.plotter is None:
            return
        if direction == "iso":
            self.plotter.view_isometric()
        else:
            axis, negative = direction[1], direction[0] == "-"
            {"x": self.plotter.view_yz, "y": self.plotter.view_xz, "z": self.plotter.view_xy}[axis](
                negative=negative
            )
        self.plotter.reset_camera()
        self._render()

    def set_style(self, **changes) -> None:
        """Edges on results and previews, wireframe, orthographic: kept across redraws."""
        self.style.update(changes)
        self._apply_style()
        self._render()

    def _apply_style(self) -> None:
        self.shown["style"] = dict(self.style)
        if self.plotter is None:
            return
        import vtk

        for actor in self.plotter.renderer.actors.values():
            if not isinstance(actor, vtk.vtkActor):
                continue
            prop = actor.GetProperty()
            if self.style["wireframe"]:
                prop.SetRepresentationToWireframe()
            else:
                prop.SetRepresentationToSurface()
            if self.style["edges"]:
                prop.SetEdgeVisibility(True)
        # Only on a change: pyvista moves the camera each time perspective is set.
        if self.style["parallel"] != bool(self.plotter.camera.parallel_projection):
            if self.style["parallel"]:
                self.plotter.enable_parallel_projection()
            else:
                self.plotter.disable_parallel_projection()

    def pixels(self):
        """The view as an RGB array (an animation frame), or None without a 3D view."""
        if self.plotter is None:
            return None
        self.plotter.render()
        return np.ascontiguousarray(self.plotter.screenshot(return_img=True))

    def image(self):
        """The view as a QImage (None without a 3D view)."""
        if self.plotter is None:
            return None
        from PySide6.QtGui import QImage

        self.plotter.render()
        pixels = np.ascontiguousarray(self.plotter.screenshot(return_img=True))
        h, w, _ = pixels.shape
        return QImage(pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

    def _frame(self, mesh) -> None:
        """Fit the camera to a newly shown mesh; keep the user's view on redraws of the
        same one (picks, highlights, another field)."""
        if getattr(self, "_framed", None) is not mesh:
            if getattr(self, "_framed", None) is None:
                self.plotter.view_isometric()
            self.plotter.reset_camera()
            self._framed = mesh
        self._apply_style()
        self._render()
