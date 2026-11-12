"""Code_Saturne's cells: a cell-centred finite-volume solver, so the cell is the element
and any polyhedron is one. Puzzle meshes first-order cells with Gmsh (2D as one cell
thick), read per cell."""

from puzzle.elements import Element

ELEMENTS = [
    Element(
        "Polyhedral cells",
        "fluid",
        ("tet4", "hex8", "wedge6", "pyramid5", "polyhedron"),
        "finite_volume",
        ("flow", "heat"),
        "Any first-order cell, joined by faces: laminar and RANS flow, conjugate heat.",
        "quadratic cells: meshes are first order.",
        True,
        "Code_Saturne theory guide, finite-volume discretisation",
        ("fluid_flow", "heat_transfer"),
    ),
]
