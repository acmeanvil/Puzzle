"""Post-processing (ADR 0008): VTK grids from results, an on-screen or off-screen viewer,
and export to .vtu/.pvd for ParaView. Needs pyvista (`pip install puzzle-fem[post]`)."""

from puzzle.post.sample import Sample, component, sample
from puzzle.post.view import View, render
from puzzle.post.vtk import export, pick_step, to_grid, von_mises

__all__ = [
    "Sample",
    "View",
    "component",
    "export",
    "pick_step",
    "render",
    "sample",
    "to_grid",
    "von_mises",
]
