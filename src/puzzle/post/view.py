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
    "electric_potential": "electric_potential",
    "magnetic_flux_density": "magnetic_flux_density_magnitude",
}


@dataclass
class View:
    field: str = "displacement"
    scalar: str | None = None  # an array name; default from DEFAULT_SCALAR
    step: int | None = None
    mode: int | None = None
    deform: float | str | None = "auto"  # scale factor, "auto", or None for undeformed
    show_edges: bool = False
    cut: object = None  # a post.cuts.Cut (ADR 0038)


def render(
    mesh: Mesh,
    results: Results,
    view: View = View(),  # noqa: B008
    *,
    screenshot: Path | None = None,
    window_size: tuple[int, int] = (1200, 800),
    animate: Path | None = None,
) -> dict:
    """Render the view; with `screenshot`, off screen to that PNG; with `animate`, one
    loop of the deformation to that GIF (ADR 0038). Returns what was shown."""
    import pyvista as pv

    step = pick_step(results, view.field, step=view.step, mode=view.mode)
    grid = to_grid(mesh, step)
    scalar = view.scalar or DEFAULT_SCALAR.get(view.field, view.field)
    available = set(grid.point_data) | set(grid.cell_data)
    if scalar not in available:
        raise KeyError(f"no '{scalar}' to show; available: {sorted(available)}")

    base = grid
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
    values = grid.point_data[scalar] if scalar in grid.point_data else grid.cell_data[scalar]
    clim = (float(np.nanmin(values)), float(np.nanmax(values)))
    off = screenshot is not None or animate is not None
    plotter = pv.Plotter(off_screen=off, window_size=list(window_size))

    def shown_grid(factor: float):
        g = base.warp_by_vector("displacement", factor=scale * factor) if scale else base
        if view.cut is not None:
            from puzzle.post.cuts import clip

            g, _ = clip(g, view.cut, base.bounds)
        return g

    plotter.add_mesh(
        shown_grid(1.0), scalars=scalar, cmap="turbo", clim=clim, scalar_bar_args={"title": scalar}
    )
    if view.show_edges:
        # Outline the real element edges; show_edges on quadratic cells draws every
        # sub-triangle VTK tessellates them into and darkens the whole model.
        edges = grid.linear_copy().extract_surface(algorithm="dataset_surface").extract_all_edges()
        plotter.add_mesh(edges, color="black", opacity=0.25, line_width=0.5)
    plotter.add_text(title, font_size=10)
    plotter.add_axes()
    plotter.view_isometric()
    if animate is not None:
        from puzzle.post.cuts import deformation_frames, save_gif

        frames = []
        for factor in deformation_frames():
            plotter.clear_actors()
            plotter.add_mesh(
                shown_grid(factor), scalars=scalar, cmap="turbo", clim=clim, show_scalar_bar=False
            )
            frames.append(plotter.screenshot(return_img=True))
        save_gif(frames, animate)
        plotter.close()
    elif screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        plotter.screenshot(str(screenshot))
        plotter.close()
    else:
        plotter.show()
    return {
        "scalar": scalar,
        "min": float(np.nanmin(values)),
        "max": float(np.nanmax(values)),
        "scale": scale,
        "title": title,
    }
