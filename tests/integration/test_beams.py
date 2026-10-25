"""Beams (ADR 0027): line bodies with a section, against beam theory.

Cantilever: a 1 m steel beam of a 20 x 40 mm rectangle, clamped at one end, a 100 N
load across its stiff direction at the other. The tip moves PL³/(3EI) + PL/(κGA)
(Timoshenko, κ = 5/6), within 1 % on CalculiX (B31 and B32, expanded into solids) and
Code_Aster (POU_D_T, two-node beams). Code_Aster's section forces at the root are the
statics: shear P, moment P L.

Portal frame: two 1 m columns and a 1 m beam of the same section, rigidly joined, feet
clamped, pushed sideways by H at the top. The sway is checked against a three-member
matrix analysis (Euler-Bernoulli, exact for these members) within 1 %.
"""

import numpy as np
import pytest
from solvers import available

from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

B, H = 0.02, 0.04  # section: width along the first axis (y), height across (in the plane)
E, NU = 210e9, 0.3
I_PLANE = B * H**3 / 12
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _frame(solver, points, supports, loads, order):
    p = Project(name="frame")
    plane = lambda axis, at: {"plane": {"axis": axis, "at": at}}  # noqa: E731
    geo = p.add(
        Geometry(
            name="Frame",
            history=[HistoryStep(kind="polyline", name="L", args={"points": points})],
            selections=[NamedSelection(name=n, entity="vertex", rule=plane(*r)) for n, r in
                        {**supports, **loads}.items()],
        )
    )  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=q(50, "mm"), element_order=order))
    features = [Feature(type="fixed", selection=f"F/{n}") for n in supports]
    comp = p.add(
        Component(
            name="Frame",
            parts=[PartInstance(name="F", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="F")],
            beam_sections=[
                BeamSection(
                    selection="F",
                    shape="rectangle",
                    width=q(B * 1e3, "mm"),
                    height=q(H * 1e3, "mm"),
                    orientation=(0.0, 1.0, 0.0),
                )
            ],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=features,
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(
        Study(name="S", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study, comp


def _solve(tmp_path, p, study):
    root = tmp_path / "frame"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


CASES = [
    pytest.param(
        solver,
        order,
        id=label,
        marks=pytest.mark.skipif(not available(solver), reason=f"{solver} not available"),
    )
    for solver, order, label in [
        ("calculix", 1, "calculix-B31"),
        ("calculix", 2, "calculix-B32"),
        ("code_aster", 1, "code_aster"),
    ]
]


@pytest.mark.parametrize("solver, order", CASES)
def test_cantilever_beam(tmp_path, solver, order):
    pytest.importorskip("OCP")
    P, L = 100.0, 1.0
    p, study, comp = _frame(
        solver, [[0, 0, 0], [L, 0, 0]], {"Root": ("x", 0.0)}, {"Tip": ("x", L)}, order
    )
    comp.physics[0].features.append(
        Feature(type="force", selection="F/Tip", values={"z": q(-P, "N")})
    )
    run = _solve(tmp_path, p, study)
    u = run.results.last("displacement")
    tip = np.isclose(run.mesh.node_coords(u.ids)[:, 0], L)
    G, area = E / (2 * (1 + NU)), B * H
    expected = P * L**3 / (3 * E * I_PLANE) + P * L / (5 / 6 * G * area)
    got = float(-u.values[tip, 2][0])
    print(f"{solver} order {order} cantilever: {got:.6g} m, Timoshenko {expected:.6g} m, "
          f"{100 * (got / expected - 1):+.2f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-2)
    if solver == "code_aster":
        f = run.results.last("beam_forces")
        root = np.isclose(run.mesh.node_coords(f.ids)[:, 0], 0.0)
        c = {k: i for i, k in enumerate(f.components)}
        shear = abs(float(f.values[root, c["Vz"]].mean()))
        moment = abs(float(f.values[root, c["My"]].mean()))
        print(f"code_aster root: shear {shear:.6g} N, moment {moment:.6g} N m (P L {P * L:g})")
        assert shear == pytest.approx(P, rel=5e-3) and moment == pytest.approx(P * L, rel=5e-3)


def _portal_sway(h: float, span: float, push: float) -> float:
    """The top's sideways movement of a clamped-feet portal frame (x along, z up), H
    shared between the two top corners, by the direct stiffness method."""
    nodes = np.array([[0, 0], [0, h], [span, h], [span, 0]], float)
    members = [(0, 1), (1, 2), (2, 3)]
    area = B * H
    K = np.zeros((12, 12))
    for i, j in members:
        d = nodes[j] - nodes[i]
        L = float(np.hypot(*d))
        c, s = d / L
        k = np.zeros((6, 6))
        ea, ei = E * area / L, E * I_PLANE
        k[np.ix_([0, 3], [0, 3])] = ea * np.array([[1, -1], [-1, 1]])
        bend = ei / L**3 * np.array(
            [[12, 6 * L, -12, 6 * L], [6 * L, 4 * L**2, -6 * L, 2 * L**2],
             [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L**2, -6 * L, 4 * L**2]]
        )  # fmt: skip
        k[np.ix_([1, 2, 4, 5], [1, 2, 4, 5])] = bend
        R = np.zeros((6, 6))
        for b in (0, 3):
            R[b : b + 3, b : b + 3] = [[c, s, 0], [-s, c, 0], [0, 0, 1]]
        dofs = [3 * i, 3 * i + 1, 3 * i + 2, 3 * j, 3 * j + 1, 3 * j + 2]
        K[np.ix_(dofs, dofs)] += R.T @ k @ R
    f = np.zeros(12)
    f[3] = f[6] = push / 2  # x at the two top corners
    free = [3, 4, 5, 6, 7, 8]
    u = np.linalg.solve(K[np.ix_(free, free)], f[free])
    return float(u[0])


@pytest.mark.parametrize("solver, order", CASES)
def test_portal_frame(tmp_path, solver, order):
    pytest.importorskip("OCP")
    h, span, push = 1.0, 1.0, 200.0
    points = [[0, 0, 0], [0, 0, h], [span, 0, h], [span, 0, 0]]
    p, study, comp = _frame(solver, points, {"Feet": ("z", 0.0)}, {"Top": ("z", h)}, order)
    comp.physics[0].features.append(
        Feature(type="force", selection="F/Top", values={"x": q(push, "N")})
    )
    run = _solve(tmp_path, p, study)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    corner = np.isclose(xyz[:, 2], h) & np.isclose(xyz[:, 0], 0.0)
    got = float(u.values[corner, 0][0])
    expected = _portal_sway(h, span, push)
    print(f"{solver} order {order} portal sway: {got:.6g} m, matrix analysis {expected:.6g} m, "
          f"{100 * (got / expected - 1):+.2f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-2)


@pytest.mark.parametrize("solver", ["elmer", "fenicsx"])
def test_solid_only_solvers_refuse_beams(tmp_path, solver):
    """Solvers without beam elements refuse a frame rather than drop its elements."""
    pytest.importorskip("OCP")
    from puzzle.errors import PuzzleError

    p, study, comp = _frame(solver, [[0, 0, 0], [1, 0, 0]], {"Root": ("x", 0.0)}, {}, 1)
    with pytest.raises(PuzzleError, match="beams"):
        _solve(tmp_path, p, study)
