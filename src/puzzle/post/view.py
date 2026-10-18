"""Show a result on the (deformed) model, in a window or off screen to an image."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from puzzle.mesh.mesh import Mesh
from puzzle.post.vtk import pick_step, to_grid
from puzzle.results.model import Results

# What each result shows by default.
DEFAULT_SCALAR = {
    "displacement": "displacement_magnitude",
    "stress": "von_mises",
    "temperature": "temperature",
    "heat_flux": "heat_flux_magnitude",
    "strain": "strain",
    "velocity": "velocity_magnitude",
    "pressure": "pressure",
}


@dataclass
class View:
    field: str = "displacement"
    scalar: str | None = None  # an array name; default from DEFAULT_SCALAR
    step: int | None = None
    mode: int | None = None
    deform: float | str | None = "auto"  # scale factor, "auto", or None for undeformed
    show_edges: bool = False


def render(
    mesh: Mesh,
    results: Results,
    view: View = View(),  # noqa: B008
    *,
    screenshot: Path | None = None,
    window_size: tuple[int, int] = (1200, 800),
) -> dict:
    """Render the view; with `screenshot`, off screen to that PNG. Returns what was shown."""
    import pyvista as pv

    step = pick_step(results, view.field, step=view.step, mode=view.mode)
    grid = to_grid(mesh, step)
    scalar = view.scalar or DEFAULT_SCALAR.get(view.field, view.field)
    available = set(grid.point_data) | set(grid.cell_data)
    if scalar not in available:
        raise KeyError(f"no '{scalar}' to show; available: {sorted(available)}")

    scale = 0.0
    if view.deform is not None and "displacement" in grid.point_data:
        u = np.nan_to_num(grid.point_data["displacement"])
        umax = float(np.linalg.norm(u, axis=1).max())
        if view.deform == "auto":
            scale = 0.1 * grid.length / umax if umax > 0 else 0.0
        else:
            scale = float(view.deform)
        if scale:
            grid = grid.warp_by_vector("displacement", factor=scale)

    label = f"mode {step.mode}: {step.value:.4g} Hz" if step.mode else f"step {step.index}"
    title = f"{scalar} ({label})" + (f", deformation x{scale:.3g}" if scale else "")
    plotter = pv.Plotter(off_screen=screenshot is not None, window_size=list(window_size))
    plotter.add_mesh(grid, scalars=scalar, cmap="turbo", scalar_bar_args={"title": scalar})
    if view.show_edges:
        # Outline the real element edges; show_edges on quadratic cells draws every
        # sub-triangle VTK tessellates them into and darkens the whole model.
        edges = grid.linear_copy().extract_surface(algorithm="dataset_surface").extract_all_edges()
        plotter.add_mesh(edges, color="black", opacity=0.25, line_width=0.5)
    plotter.add_text(title, font_size=10)
    plotter.add_axes()
    plotter.view_isometric()
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        plotter.screenshot(str(screenshot))
        plotter.close()
    else:
        plotter.show()
    values = grid.point_data[scalar] if scalar in grid.point_data else grid.cell_data[scalar]
    return {
        "scalar": scalar,
        "min": float(np.nanmin(values)),
        "max": float(np.nanmax(values)),
        "scale": scale,
        "title": title,
    }
