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
        "Plate / Quad (MITC4)",
        "shell",
        ("quad4",),
        "thick_shell",
        ("static",),
        "Rectangular plates and MITC4 quadrilaterals for walls and slabs.",
        "",
        False,
        REF + ".add_plate, add_quad",
    ),
]
