"""OpenFOAM's cells: a cell-centred finite-volume code (polyMesh), so the cell is the
element. Puzzle writes Gmsh's first-order cells through gmshToFoam (2D as one cell thick,
empty or wedge patches)."""

from puzzle.elements import Element

ELEMENTS = [
    Element(
        "polyMesh cells",
        "fluid",
        ("tet4", "hex8", "wedge6", "pyramid5", "polyhedron"),
        "finite_volume",
        ("flow", "heat"),
        "Any first-order cell: simpleFoam's steady laminar and RANS flow, a passive temperature.",
        "quadratic cells.",
        True,
        "OpenFOAM User Guide, 4.1 Mesh description",
        ("fluid_flow", "heat_transfer"),
    ),
]
