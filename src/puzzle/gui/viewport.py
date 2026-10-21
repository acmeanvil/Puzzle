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
            from pyvistaqt import QtInteractor

            self.plotter = QtInteractor(self)
            self.plotter.set_background("white", top="#dfe6ee")
            # Once only (every add_axes makes another widget), and lifted off the bottom
            # edge: on macOS Retina displays a widget viewport touching the edge leaves
            # stray marks along the bottom rows of the view.
            self.plotter.add_axes(viewport=(0.0, 0.04, 0.16, 0.24))
            layout.addWidget(self.plotter.interactor)
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
        dim = highlight is not None and mesh.groups[highlight].dim == 3
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
        self._frame()

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
        if on:
            self.plotter.track_click_position(self._clicked, side="left", double=True)
        else:
            self.plotter.untrack_click_position(side="left")

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
            self._frame()
        return self.shown

    # The OpenGL view is not redrawn when Qt resizes it (docks settling after the window
    # opens, the user dragging a splitter), which leaves stale pixels on macOS: redraw.
    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        QTimer.singleShot(0, self._render)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        QTimer.singleShot(0, self._render)

    def _render(self) -> None:
        if self.plotter is not None:
            self.plotter.render()

    def clear(self) -> None:
        self.shown = {}
        self.mesh, self.surface, self.entities, self.picked = None, None, [], []
        if self.plotter is not None:
            self.plotter.clear()

    def screenshot(self, path) -> None:
        if self.plotter is not None:
            self.plotter.screenshot(str(path))

    def close_plotter(self) -> None:
        if self.plotter is not None:
            self.plotter.close()

    def _frame(self) -> None:
        if not getattr(self, "_framed", False):
            self.plotter.view_isometric()
            self._framed = True
        self.plotter.reset_camera()
