"""OpenCourant's elements (the Radioss reference guide's element cards, ADR 0085): the
explicit solids Puzzle writes, the shells of step 2 and the beams of step 5."""

from puzzle.elements import Element

REF = "OpenCourant (Radioss) reference guide"

ELEMENTS = [
    Element(
        "BRICK, Isolid 24 (HEPH)",
        "solid_3d",
        ("hex8",),
        "reduced",
        ("explicit",),
        "Eight-node bricks with one integration point and physical hourglass stiffness: "
        "the default for impact and crash, cheap per cycle.",
        "bending through one element's thickness; watch the hourglass energy.",
        True,
        REF + ", /PROP/SOLID Isolid 24",
    ),
    Element(
        "TETRA4",
        "solid_3d",
        ("tet4",),
        "full",
        ("explicit",),
        "Four-node tetrahedra for any shape the mesher fills; no hourglass.",
        "bending and near incompressibility: stiff; prefer TETRA10 or bricks.",
        True,
        REF + ", /TETRA4",
    ),
    Element(
        "TETRA10",
        "solid_3d",
        ("tet10",),
        "full",
        ("explicit",),
        "Ten-node tetrahedra: bending right on a free mesh, at a smaller time step "
        "(0.31 of its corner tetrahedron's, measured).",
        "pressure on its faces: not written yet (first-order faces only).",
        True,
        REF + ", /TETRA10",
    ),
    Element(
        "SHELL, Ishell 24 (QEPH)",
        "shell",
        ("quad4",),
        "reduced",
        ("explicit",),
        "Four-node shells with physical hourglass, for thin walls in crash (ADR 0085 step 2).",
        "offsets and thicknesses from a field: not written yet.",
        True,
        REF + ", /PROP/SHELL Ishell 24",
    ),
    Element(
        "SH3N",
        "shell",
        ("tri3",),
        "thin_shell",
        ("explicit",),
        "Three-node shells (ADR 0085 step 2).",
        "",
        True,
        REF + ", /SH3N",
    ),
    Element(
        "BEAM (type 3)",
        "beam",
        ("line2",),
        "timoshenko",
        ("explicit",),
        "Two-node beams, shear included, from the section's area and inertias (ADR 0085 "
        "step 5): frames and stiffeners in an impact.",
        "second-order lines (line3): refused; beam forces aren't read back yet.",
        True,
        REF + ", /BEAM, /PROP/BEAM",
    ),
]
