"""OpenCourant's elements (the Radioss reference guide's element cards, ADR 0085): the
explicit solids Puzzle writes, and the shells of step 2."""

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
        "(the mid-edge nodes halve the spacing).",
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
        "",
        False,
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
        False,
        REF + ", /SH3N",
    ),
]
