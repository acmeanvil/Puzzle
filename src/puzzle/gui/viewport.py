"""The 3D viewport: meshes, highlighted selections and results (pyvistaqt, ADR 0003).

Where VTK cannot draw (Qt's offscreen platform, as in tests) a label stands in, and
the viewport still records what it would show, so tests can check it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from puzzle.gui.tasks import rendering_available
from puzzle.mesh.mesh import Mesh
from puzzle.post.view import DEFAULT_SCALAR
from puzzle.post.vtk import pick_step, to_grid
from puzzle.results.model import Results

# Surface element -> how many of its nodes are corners.
CORNERS = {"tri3": 3, "tri6": 3, "quad4": 4, "quad8": 4, "quad9": 4}
HIGHLIGHT = "#ff8c00"


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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.shown: dict = {}  # what is on screen: kind, scalar, range, highlight
        self.plotter = None
        if rendering_available():
            from pyvistaqt import QtInteractor

            self.plotter = QtInteractor(self)
            self.plotter.set_background("white", top="#dfe6ee")
            layout.addWidget(self.plotter.interactor)
        else:
            self.placeholder = QLabel("3D view unavailable here (no OpenGL)")
            self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.placeholder)

    def show_mesh(self, mesh: Mesh, highlight: str | None = None) -> None:
        """The mesh's volume, with a face selection (`Part/Selection`) highlighted."""
        grid = to_grid(mesh)
        self.shown = {"kind": "mesh", "cells": grid.n_cells, "highlight": highlight}
        if self.plotter is None:
            return
        self.plotter.clear()
        dim = highlight is not None and mesh.groups[highlight].dim == 3
        self.plotter.add_mesh(
            grid, color="#c9d3dd", show_edges=True, edge_color="#7a8794", opacity=0.35 if dim else 1
        )
        if highlight is not None:
            surface = selection_surface(mesh, highlight)
            if surface is not None:
                self.plotter.add_mesh(surface, color=HIGHLIGHT, show_edges=False)
        self._frame()

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
        }
        if self.plotter is not None:
            self.plotter.clear()
            self.plotter.add_mesh(
                grid,
                scalars=name,
                cmap="turbo",
                scalar_bar_args={
                    "title": name,
                    "vertical": True,
                    "position_x": 0.86,
                    "position_y": 0.1,
                    "width": 0.06,
                    "height": 0.8,
                    "fmt": "%.3g",
                    "color": "black",
                },
            )
            title = f"{name}, {label}" + (f", deformation ×{scale:.3g}" if scale else "")
            self.plotter.add_text(title, font_size=9, color="black")
            self._frame()
        return self.shown

    def clear(self) -> None:
        self.shown = {}
        if self.plotter is not None:
            self.plotter.clear()

    def screenshot(self, path) -> None:
        if self.plotter is not None:
            self.plotter.screenshot(str(path))

    def close_plotter(self) -> None:
        if self.plotter is not None:
            self.plotter.close()

    def _frame(self) -> None:
        self.plotter.add_axes()
        if not getattr(self, "_framed", False):
            self.plotter.view_isometric()
            self._framed = True
        self.plotter.reset_camera()
