"""Elmer's elements (Elmer Solver Manual, Appendix D, element types by code: 303 the
linear triangle, 510 the quadratic tetrahedron...). Elmer's physics is its solvers'
(StressSolver, HeatSolver, StatElecSolver, WhitneyAVSolver); the element is the cell and
its basis. Puzzle writes Lagrange cells of both orders and WhitneyAV's edge elements."""

from puzzle.elements import Element

STRUCT = ("static", "modal", "transient", "harmonic")
REF = "Elmer Solver Manual, Appendix D"

ELEMENTS = [
    Element(
        "504/510",
        "solid_3d",
        ("tet4", "tet10"),
        "lagrange",
        STRUCT + ("heat", "electrostatics"),
        "Tetrahedra for StressSolver, HeatSolver and StatElecSolver.",
        "",
        True,
        REF,
        ("solid_mechanics", "heat_transfer", "electrostatics"),
    ),
    Element(
        "808/820/827",
        "solid_3d",
        ("hex8", "hex20", "hex27"),
        "lagrange",
        STRUCT + ("heat",),
        "Hexahedra.",
        "",
        True,
        REF,
        ("solid_mechanics", "heat_transfer"),
    ),
    Element(
        "706/715",
        "solid_3d",
        ("wedge6", "wedge15"),
        "lagrange",
        STRUCT + ("heat",),
        "Wedges.",
        "",
        True,
        REF,
        ("solid_mechanics", "heat_transfer"),
    ),
    Element(
        "303/306/404/408",
        "solid_2d",
        ("tri3", "tri6", "quad4", "quad8"),
        "lagrange",
        STRUCT + ("heat", "electrostatics", "magnetics"),
        "2D cells: plane stress, plane strain (Cartesian 2D) and axisymmetric (Axi "
        "Symmetric); A_z and A_phi magnetics.",
        "",
        True,
        REF,
        ("solid_mechanics", "heat_transfer", "electrostatics", "magnetic_fields"),
    ),
    Element(
        "WhitneyAV edge elements",
        "electromagnetic",
        ("tet4", "hex8", "wedge6"),
        "edge",
        ("magnetics",),
        "Lowest-order edge elements for the magnetic vector potential "
        "(magnetostatics, eddy currents).",
        "",
        True,
        "Elmer Models Manual, WhitneyAVSolver",
        ("magnetic_fields",),
    ),
    Element(
        "p-elements",
        "solid_3d",
        ("tet4", "hex8"),
        "lagrange",
        STRUCT,
        'Hierarchic higher-order bases (Element = "p:2"...).',
        "",
        False,
        "Elmer Solver Manual, p-elements",
    ),
    Element(
        "ShellSolver",
        "shell",
        ("tri3", "quad4", "tri6", "quad8"),
        "thick_shell",
        ("static", "modal"),
        "Elmer's Reissner-Mindlin shells.",
        "",
        False,
        "Elmer Models Manual, ShellSolver",
    ),
    Element(
        "BeamSolver3D",
        "beam",
        ("line2",),
        "timoshenko",
        ("static",),
        "Elmer's 3D beams.",
        "",
        False,
        "Elmer Models Manual, BeamSolver3D",
    ),
]
