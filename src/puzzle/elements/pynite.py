"""PyNite's elements (its documentation, FEModel3D): members, springs, plates."""

from puzzle.elements import Element

REF = "PyNite, FEModel3D"

ELEMENTS = [
    Element(
        "Member",
        "beam",
        ("line2",),
        "euler_bernoulli",
        ("static", "large_deflection"),
        "The 12-dof frame member with end releases: Puzzle's frames (ADR 0079), "
        "P-Delta and the Direct Analysis Method.",
        "stocky members: no shear deflection.",
        True,
        REF + ".add_member",
    ),
    Element(
        "Spring support",
        "discrete",
        ("point",),
        "discrete",
        ("static",),
        "A node held by a spring in a dof (node_spring).",
        "",
        True,
        REF + ".def_support_spring",
    ),
    Element(
        "Spring",
        "discrete",
        ("line2",),
        "discrete",
        ("static",),
        "A two-node axial spring, tension or compression only.",
        "",
        False,
        REF + ".add_spring",
    ),
    Element(
        "Quad (MITC4)",
        "shell",
        ("quad4",),
        "thick_shell",
        ("static",),
        "MITC4 quadrilaterals for walls and slabs, thick or thin: sheets meshed with the "
        "recipe's hexahedra, with pressure on them; frames and plates share nodes.",
        "self weight on plates and their stress results: not written yet.",
        True,
        REF + ".add_quad",
    ),
    Element(
        "Plate (rectangular)",
        "shell",
        ("quad4",),
        "thin_shell",
        ("static",),
        "Rectangular thin plates (12-term bending), better corner stresses.",
        "distorted or thick plates: the MITC4 quad.",
        False,
        REF + ".add_plate",
    ),
]
