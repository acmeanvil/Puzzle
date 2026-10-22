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


class SceneView(QWidget):
    """A VTK scene rendered off screen and painted as an image, with its own mouse
    handling: left-drag rotates, shift- or middle-drag pans, right-drag, the wheel
    (two-finger scroll) or a trackpad pinch zooms, and a double-click reports the
    picked point to `on_double_click`.

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

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.plotter.camera.Dolly(1.1 ** (event.angleDelta().y() / 120))
        self.plotter.renderer.ResetCameraClippingRange()
        self.refresh()

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
            return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.on_double_click is None:
            return
        import vtk

        dpr = self.devicePixelRatioF()
        x = event.position().x() * dpr
        y = (self.height() - event.position().y()) * dpr  # VTK counts from the bottom
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.0005)
        if picker.Pick(x, y, 0, self.plotter.renderer) and picker.GetCellId() >= 0:
            self.on_double_click(picker.GetPickPosition())

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


class Viewport(QWidget):
    picked_changed = Signal(list)  # the picked entities, [(dim, tag), ...]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mesh: Mesh | None = None
        self.highlight: str | None = None
        self.highlight_entities: list[tuple[int, int]] = []
        self.surface, self.entities = None, []
        self.picked: list[tuple[int, int]] = []
        self.picking = False
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

    def show_mesh(
        self, mesh: Mesh, highlight: str | None = None, entities: list | None = None
    ) -> None:
        """The mesh's volume, with a selection highlighted (a mesh group `Part/Name`, or
        its face `entities` when it was made after meshing) and picked faces marked."""
        if mesh is not self.mesh:
            self.mesh = mesh
            self.surface, self.entities = entity_surface(mesh)
            self.picked = [e for e in self.picked if e in self.entities]
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
            index = [self.entities.index(e) for e in group if e in self.entities]
            if index and self.surface is not None:
                mask = np.isin(self.surface.cell_data["entity"], index)
                self._overlay(self.surface.extract_cells(mask), colour)
        self._frame(mesh)

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
        if self.plotter is None:
            return
        self.view.on_double_click = self._clicked if on else None

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

    def measure(self, entities) -> dict:
        """Area of the given faces and, for two, the distance between their centres."""
        if self.surface is None:
            return {}
        out, centres = {"area": 0.0}, []
        for e in entities:
            if e not in self.entities:
                continue
            mask = self.surface.cell_data["entity"] == self.entities.index(e)
            part = self.surface.extract_cells(np.flatnonzero(mask)).compute_cell_sizes()
            areas = np.asarray(part.cell_data["Area"])
            out["area"] += float(areas.sum())
            centres.append((areas[:, None] * part.cell_centers().points).sum(axis=0) / areas.sum())
        if len(centres) == 2:
            out["distance"] = float(np.linalg.norm(centres[1] - centres[0]))
        return out

    def entity_at(self, point) -> tuple[int, int] | None:
        """The face entity nearest a point in space (where a click landed)."""
        if self.surface is None:
            return None
        cell = self.surface.find_closest_cell(np.asarray(point, float))
        return self.entities[int(self.surface.cell_data["entity"][cell])] if cell >= 0 else None

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
    ) -> dict:
        """A result field on the (deformed) mesh; returns what was shown."""
        steps = [s for s in results.steps if field in s.fields]
        step = steps[step_index] if steps else pick_step(results, field)
        grid = to_grid(mesh, step)
        name = scalar or DEFAULT_SCALAR.get(field, field)
        arrays = set(grid.point_data) | set(grid.cell_data)
        if name not in arrays:
            name = field if field in arrays else f"{field}_magnitude"
        values = grid.point_data[name] if name in grid.point_data else grid.cell_data[name]
        scale = 0.0
        if deform and "displacement" in grid.point_data:
            u = np.nan_to_num(grid.point_data["displacement"])
            umax = float(np.linalg.norm(u, axis=1).max())
            scale = 0.1 * grid.length / umax if umax > 0 else 0.0
            if scale:
                grid = grid.warp_by_vector("displacement", factor=scale)
        label = f"mode {step.mode}: {step.value:.4g} Hz" if step.mode else f"t = {step.value:g}"
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
            "title": f"{name}, {label}" + (f", deformation ×{scale:.3g}" if scale else ""),
        }
        if self.plotter is not None:
            self.plotter.clear()
            self.plotter.add_mesh(
                grid,
                scalars=name,
                cmap="turbo",
                scalar_bar_args={
                    "title": name.replace("_", " "),
                    "vertical": True,
                    "position_x": 0.8,
                    "position_y": 0.1,
                    "width": 0.06,
                    "height": 0.8,
                    "fmt": "%.3g",
                    "color": "black",
                    "title_font_size": 14,
                    "label_font_size": 12,
                },
            )
            self._frame(mesh)
        return self.shown

    def _render(self) -> None:
        if self.plotter is not None:
            self.view.refresh()

    def clear(self) -> None:
        self.shown = {}
        self.mesh, self.surface, self.entities, self.picked = None, None, [], []
        if self.plotter is not None:
            self.plotter.clear()
            self._render()

    def screenshot(self, path) -> None:
        if self.plotter is not None:
            self.plotter.screenshot(str(path))

    def close_plotter(self) -> None:
        if self.plotter is not None:
            self.plotter.close()

    def _frame(self, mesh) -> None:
        """Fit the camera to a newly shown mesh; keep the user's view on redraws of the
        same one (picks, highlights, another field)."""
        if getattr(self, "_framed", None) is not mesh:
            if getattr(self, "_framed", None) is None:
                self.plotter.view_isometric()
            self.plotter.reset_camera()
            self._framed = mesh
        self._render()
