"""Shells (ADR 0027): sheet bodies with a thickness, against plate theory and the
MacNeal-Harder Scordelis-Lo roof.

Plates: a 1 m square steel plate, 10 mm thick, under 1 kPa. Its centre deflects
α q a⁴ / D with D = E t³ / 12 (1 - ν²) (Timoshenko and Woinowsky-Krieger, table 8 and
35): α = 0.00406 simply supported, 0.00126 clamped. CalculiX's S6 (second order,
expanded into solids) and Code_Aster's DKT (Kirchhoff, three-node) are within 1.5 %.

Scordelis-Lo roof: a cylindrical panel, R = 25, L = 50, 80° wide, t = 0.25, E = 4.32e8,
ν = 0, under its own weight of 90 per unit area, on rigid diaphragms at its ends. The
middle of a free edge drops 0.3024 (MacNeal and Harder 1985); both solvers within 2 %.
Elmer and FEniCSx refuse sheet bodies.
"""

import numpy as np
import pytest
from solvers import available

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    HistoryStep,
    Material,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    ShellSection,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
ZERO = q(0.0, "m")


def _case(solver, geometry, selections, features, material, thickness, size, order):
    p = Project(name="shell")
    geo = p.add(Geometry(name="Sheet", history=[geometry], selections=selections))
    mat = p.add(material)
    mesh = p.add(MeshRecipe(name="M", global_size=size, element_order=order))
    comp = p.add(
        Component(
            name="C",
            parts=[PartInstance(name="S", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=mat.id, selection="S")],
            shell_sections=[ShellSection(selection="S", thickness=thickness)],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=features,
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    steps = [SolveStep(type="stationary")]
    return p, p.add(Study(name="Load", component=comp.id, solver=solver, steps=steps))


def _solve(tmp_path, p, study):
    root = tmp_path / "shell"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


def _nearest(run, point):
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    return u.values[int(np.argmin(np.linalg.norm(xyz - point, axis=1)))]


A, T, P, E, NU = 1.0, 0.01, 1000.0, 210e9, 0.3
STEEL = Material(
    name="Steel",
    properties={
        "youngs_modulus": q(E, "Pa"),
        "poissons_ratio": q(NU, "dimensionless"),
        "density": q(7850.0, "kg/m^3"),
    },
)
EDGES = {"X0": ("x", 0.0), "X1": ("x", A), "Y0": ("y", 0.0), "Y1": ("y", A)}


def _plate(solver, clamped, order, size_mm):
    square = [[0, 0, 0], [A, 0, 0], [A, A, 0], [0, A, 0]]
    sheet = HistoryStep(kind="sheet", name="P", args={"points": square})
    selections = [NamedSelection(name="Top", entity="face", members=["P/face"])] + [
        NamedSelection(name=n, entity="edge", rule={"plane": {"axis": ax, "at": at}})
        for n, (ax, at) in EDGES.items()
    ]
    if clamped:
        features = [Feature(type="fixed", selection=f"S/{n}") for n in EDGES]
    else:  # held across the plate along every edge; in its plane at two edges
        features = [
            Feature(type="displacement", selection=f"S/{n}", values={"z": ZERO}) for n in EDGES
        ]
        features += [
            Feature(type="displacement", selection="S/X0", values={"x": ZERO}),
            Feature(type="displacement", selection="S/Y0", values={"y": ZERO}),
        ]
    features.append(Feature(type="pressure", selection="S/Top", values={"p": q(P, "Pa")}))
    return _case(solver, sheet, selections, features, STEEL, q(T * 1e3, "mm"),
                 q(size_mm, "mm"), order)  # fmt: skip


PLATES = [
    pytest.param(solver, order, size, clamped, id=f"{solver}-{'clamped' if clamped else 'simple'}",
                 marks=pytest.mark.skipif(not available(solver), reason=f"{solver} not available"))
    for solver, order, size in (("calculix", 2, 25.0), ("code_aster", 1, 25.0))
    for clamped in (False, True)
]  # fmt: skip


@pytest.mark.parametrize("solver, order, size, clamped", PLATES)
def test_square_plate_under_pressure(tmp_path, solver, order, size, clamped):
    pytest.importorskip("OCP")
    p, study = _plate(solver, clamped, order, size)
    run = _solve(tmp_path, p, study)
    w = float(_nearest(run, [A / 2, A / 2, 0.0])[2])
    D = E * T**3 / (12 * (1 - NU**2))
    alpha = 0.0012653 if clamped else 0.0040624
    expected = -alpha * P * A**4 / D  # a positive pressure pushes against the normal (+z)
    print(f"{solver} {'clamped' if clamped else 'simply supported'} plate: w {w:.6g} m, "
          f"plate theory {expected:.6g} m, {100 * (w / expected - 1):+.2f} %")  # fmt: skip
    assert w == pytest.approx(expected, rel=1.5e-2)


ROOF = [
    pytest.param(solver, order, size, id=solver,
                 marks=pytest.mark.skipif(not available(solver), reason=f"{solver} not available"))
    for solver, order, size in (("calculix", 2, 2.5), ("code_aster", 1, 0.625))
]  # fmt: skip


@pytest.mark.parametrize("solver, order, size", ROOF)
def test_scordelis_lo_roof(tmp_path, solver, order, size):
    pytest.importorskip("OCP")
    R, L, angle, t = 25.0, 50.0, 80.0, 0.25
    panel = HistoryStep(kind="curved_sheet", name="R", args={"r": R, "length": L, "angle": angle})
    plane = lambda at: {"plane": {"axis": "x", "at": at}}  # noqa: E731
    selections = [
        NamedSelection(name="End0", entity="edge", rule=plane(0.0)),
        NamedSelection(name="End1", entity="edge", rule=plane(L)),
        NamedSelection(name="Corners", entity="vertex", rule=plane(0.0)),
    ]
    features = [  # rigid diaphragms: the ends held in their own plane
        Feature(type="displacement", selection=f"S/{n}", values={"y": ZERO, "z": ZERO})
        for n in ("End0", "End1")
    ]
    features += [
        Feature(type="displacement", selection="S/Corners", values={"x": ZERO}),
        # Its weight, 90 per unit area: ρ t g = 360 × 0.25 × 1.
        Feature(type="gravity", selection="S", values={"gz": q(-1.0, "m/s^2")}),
    ]
    material = Material(
        name="Roof",
        properties={
            "youngs_modulus": q(4.32e8, "Pa"),
            "poissons_ratio": q(0.0, "dimensionless"),
            "density": q(360.0, "kg/m^3"),
        },
    )
    p, study = _case(solver, panel, selections, features, material, q(t, "m"), q(size, "m"), order)
    run = _solve(tmp_path, p, study)
    half = np.radians(angle / 2)
    edge = [L / 2, R * np.sin(half), R * np.cos(half)]
    w = float(_nearest(run, edge)[2])
    print(f"{solver} Scordelis-Lo roof: free edge {w:.5g}, reference -0.3024, "
          f"{100 * (w / -0.3024 - 1):+.2f} %")  # fmt: skip
    assert w == pytest.approx(-0.3024, rel=2e-2)


@pytest.mark.parametrize("solver", ["elmer", "fenicsx"])
def test_solid_only_solvers_refuse_shells(tmp_path, solver):
    pytest.importorskip("OCP")
    from puzzle.errors import PuzzleError

    p, study = _plate(solver, True, 2, 100.0)
    with pytest.raises(PuzzleError, match="shells"):
        _solve(tmp_path, p, study)


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_calculix_refuses_three_node_shells(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.errors import PuzzleError

    p, study = _plate("calculix", True, 1, 100.0)
    with pytest.raises(PuzzleError, match="three-node shells"):
        _solve(tmp_path, p, study)
