"""The 3D viewport: meshes, highlighted selections and results (pyvistaqt, ADR 0003).

Where VTK cannot draw (Qt's offscreen platform, as in tests) a label stands in, and
the viewport still records what it would show, so tests can check it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from puzzle.gui.tasks import rendering_available
from puzzle.mesh.mesh import Mesh
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


def _number(v: float) -> str:
    """A value as a label: three to four figures, plain between 1e-3 and 1e5."""
    a = abs(v)
    if a == 0 or 1e-3 <= a < 1e5:
        return f"{v:.4g}" if a < 1e4 else f"{v:.0f}"
    return f"{v:.3e}"


def bar_format(lo: float, hi: float) -> str:
    """Colour-bar labels as people write numbers (ADR 0030): plain, to three significant
    figures of the largest, between 1e-3 and 1e5; else with an exponent."""
    top = max(abs(lo), abs(hi))
    if not np.isfinite(top) or top == 0:
        return "%.3g"
    if 1e-3 <= top < 1e5:
        decimals = max(0, 2 - int(np.floor(np.log10(top))))
        return f"%.{decimals}f"
    return "%.3e"


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
            QPainter(self).drawImage(0, 0, self._image)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.refresh()

    # mouse -------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last = event.position()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._last = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
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
        add("extremes", "Max and min markers", viewport.show_extremes, checkable=True)
        self.actions_by_name["extremes"].setChecked(True)
        add("clear", "Clear probes", viewport.clear_probes, "Esc")
        add("copy", "Copy image", self.copy_image)
        add("camera", "Save image…", self.save_image)
        self.adjustSize()

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


class Viewport(QWidget):
    picked_changed = Signal(list)  # the picked entities, [(dim, tag), ...]
    probes_changed = Signal(list)  # the view's probes (ADR 0032)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mesh: Mesh | None = None
        self.highlight: str | None = None
        self.highlight_entities: list[tuple[int, int]] = []
        self.surface, self.entities = None, []
        self.picked: list[tuple[int, int]] = []
        self.picking = False
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
            self.plotter.set_background("white", top="#dfe6ee")
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
        }
        # The shown result, for markers and probes (ADR 0032).
        self.result_state: dict | None = None
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
        """Double-click picks or un-picks the face under the cursor (a drag still rotates)."""
        self.picking = on
        self._update_click()

    def _update_click(self) -> None:
        """Double-click picks while picking, probes a shown result, else does nothing."""
        if self.plotter is None:
            return
        if self.picking:
            self.view.on_double_click = self._clicked
        elif self.result_state is not None:
            self.view.on_double_click = self._probe_click
        else:
            self.view.on_double_click = None

    def toggle_pick(self, entity: tuple[int, int]) -> None:
        if entity in self.picked:
            self.picked.remove(entity)
        else:
            self.picked.append(entity)
        if self.mesh is not None:
            self.show_mesh(self.mesh, self.highlight, self.highlight_entities)
        self.picked_changed.emit(list(self.picked))

    def clear_picks(self) -> None:
        self.picked.clear()
        if self.mesh is not None:
            self.show_mesh(self.mesh, self.highlight, self.highlight_entities)
        self.picked_changed.emit([])

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
    ) -> dict:
        """A result field on the (deformed) mesh; returns what was shown. `item` (from
        `post.quantities.catalog`) chooses one of the results the field gives; `clim`
        fixes the colour scale (SI), so several views can share one."""
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
        title = (
            f"{item.label} ({unit})"
            if item is not None and unit
            else (item.label if item is not None else words(name))
        )
        base = grid  # undeformed: positions where values are read and reported
        scale = 0.0
        if deform and "displacement" in grid.point_data:
            u = np.nan_to_num(grid.point_data["displacement"])
            umax = float(np.linalg.norm(u, axis=1).max())
            scale = 0.1 * grid.length / umax if umax > 0 else 0.0
            if scale:
                grid = grid.warp_by_vector("displacement", factor=scale)
        label = f"mode {step.mode}: {step.value:.4g} Hz" if step.mode else f"t = {step.value:g}"
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
            "max_at": where[hi].tolist(),
            "min_at": where[lo].tolist(),
        }
        tail = f" {unit}" if unit else ""
        self._extremes = [
            (shown_at[hi], f"Max {_number(values[hi])}{tail}", "#e5534b"),
            (shown_at[lo], f"Min {_number(values[lo])}{tail}", "#4f8fe6"),
        ]
        self.probes = []
        self.probes_changed.emit([])
        if self.plotter is not None:
            self.plotter.clear()
            self._mark_actors = []
            self.plotter.add_mesh(
                grid,
                scalars=name,
                cmap="turbo",
                clim=clim,
                scalar_bar_args={
                    # The unit only: the result's name is in the results bar, and VTK
                    # centres a long title over the bar, past the view's edge.
                    "title": unit or (item.label if item is not None else words(name)),
                    # Top left, as Ansys has its legend: clear of the part (centred) and
                    # of the axes marker (bottom left).
                    "vertical": True,
                    "position_x": 0.03,
                    "position_y": 0.35,
                    "width": 0.035,
                    "height": 0.55,
                    "fmt": bar_format(*(clim or (self.shown["min"], self.shown["max"]))),
                    "color": "black",
                    "title_font_size": 14,
                    "label_font_size": 12,
                },
            )
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
            if "displacement" in u:
                point = point - state["scale"] * np.asarray(u["displacement"][0])
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
            if "displacement" in u:
                shown = shown + state["scale"] * np.asarray(u["displacement"][0])
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

    def _marks(self) -> list[tuple]:
        marks = list(self._extremes) if self.style["extremes"] and self.result_state else []
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
