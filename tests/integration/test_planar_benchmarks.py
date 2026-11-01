"""2D models (ADR 0048) against closed forms, on every solver that writes them.

Steel (the generic structural steel, constant properties), meshed in the XY plane:

- **plane stress, tension:** a 40 x 20 mm strip 2 mm thick pulled by 4 kN at x = L:
  σx = F / (H t), εx = σx / E, εy = -ν σx / E (the force spread over length x thickness);
- **plane stress, Kirsch:** a quarter of a 200 x 200 mm plate with a 10 mm hole under
  10 MPa: σx at the hole's top is 3.00 σ (Peterson's finite-width correction at
  d/W = 0.05 is +0.1 %);
- **plane strain, Lamé:** a quarter ring a = 10, b = 20 mm under 100 MPa inside;
- **axisymmetric, Lamé:** the same ring as a thick cylinder, its ends held axially
  (plane strain along the axis), so the same closed form; and a solid bar r = 10 mm,
  50 mm long, pulled by 10 kN along its axis: σy = F / (π r²), uy = F L / (E π r²);
- **heat, planar and axisymmetric:** a strip from 300 K with 5 kW/m² leaving at the far
  end (T(L) = T0 - q L / k); a hollow cylinder at 400 K inside and 300 K outside
  (T = Ta + (Tb - Ta) ln(r / a) / ln(b / a)).

Lamé, with A = p a² / (b² - a²) and B = p a² b² / (b² - a²): σθ(r) = A + B / r²,
u(r) = (1 + ν) / E ((1 - 2ν) A r + B / r).
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material, library_material
from puzzle.model import (
    Component,
    Feature,
    Geometry,
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
from puzzle.model.schema import HistoryStep, ShellSection
from puzzle.solve import run_study

pytest.importorskip("OCP")

STEEL = library_material("structural_steel").properties
E, NU, K = (STEEL[k].to_si() for k in ("youngs_modulus", "poissons_ratio", "thermal_conductivity"))
A_IN, B_OUT, P = 0.010, 0.020, 100e6
CONSTANT = {"temperature_dependent_materials": False}


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def mm(v):
    return qty(v, "mm")


def _project(steps, edges, dimension, physics, size=2.0, local=None, **comp):
    """A 2D part from history steps; `edges`: selection name -> edge names."""
    p = Project(name="2d")
    selections = [NamedSelection(name="Region", entity="face", members=[f"{steps[0].name}/face"])]
    selections += [NamedSelection(name=n, entity="edge", members=m) for n, m in edges.items()]
    geo = p.add(Geometry(name="Part", history=steps, selections=selections))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=mm(size), local_sizes=local or {}))
    c = p.add(Component(name="Part", dimension=dimension, mesh=mesh.id,
                        parts=[PartInstance(name="Part", geometry=geo.id)],
                        materials=[MaterialAssignment(material=steel.id, selection="Part/Region")],
                        physics=physics, **comp))  # fmt: skip
    return p, c


def _rectangle(name, x0, y0, w, h):
    rect = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]]
    return HistoryStep(kind="face", name=name, args={"profile": {"points": rect}})


def _solve(tmp_path, p, c, solver, step=None):
    step = step or SolveStep(type="stationary")
    study = p.add(Study(name="s", component=c.id, solver=solver, steps=[step]))
    return run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=600)


def _at(run, name, group):
    f = run.results.last(name)
    return f.at(run.mesh.groups[group].node_ids), run.mesh.node_coords(
        run.mesh.groups[group].node_ids
    )


def _structure(features):
    return [Physics(type="solid_mechanics", features=features, settings=CONSTANT)]


# Sides of a rectangle face: side-1 bottom (y = y0), side-2 right, side-3 top, side-4 left.
SIDES = {"Bottom": ["S/side-1"], "Right": ["S/side-2"], "Top": ["S/side-3"], "Left": ["S/side-4"]}


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_plane_stress_strip_in_tension(tmp_path, solver):
    L, H, t, F = 0.04, 0.02, 0.002, 4000.0
    features = [
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="force", selection="Part/Right", values={"x": qty(F, "N")}),
    ]
    p, c = _project([_rectangle("S", 0, 0, L, H)], SIDES, "2d_planar", _structure(features),
                    shell_sections=[ShellSection(selection="Part", thickness=mm(2))])  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    sigma = F / (H * t)
    u, xyz = _at(run, "displacement", "Part/Right")
    assert u[:, 0].mean() == pytest.approx(sigma / E * L, rel=2e-3)
    u, xyz = _at(run, "displacement", "Part/Top")
    assert np.polyfit(xyz[:, 0], u[:, 1], 0)[0] == pytest.approx(-NU * sigma / E * H, rel=2e-3)
    s = run.results.last("stress").values
    assert np.median(s[:, 0]) == pytest.approx(sigma, rel=2e-3)


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_plane_stress_kirsch_hole(tmp_path, solver):
    """σx at the top of the hole: 3 σ."""
    half, a, sigma = 0.1, 0.005, 10e6
    steps = [
        _rectangle("S", 0, 0, half, half),
        HistoryStep(kind="face", name="Hole",
                    args={"profile": {"circle": [0, 0, a]}, "mode": "cut"}),
    ]  # fmt: skip
    edges = SIDES | {"Hole": ["Hole/side-1"]}
    features = [
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="pressure", selection="Part/Right", values={"p": qty(-sigma, "Pa")}),
    ]  # fmt: skip
    p, c = _project(steps, edges, "2d_planar", _structure(features), size=5.0,
                    local={"Part/Hole": mm(0.25)},
                    shell_sections=[ShellSection(selection="Part", thickness=mm(1))])  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    s, xyz = _at(run, "stress", "Part/Hole")
    top = int(np.argmax(xyz[:, 1]))
    assert xyz[top, 1] == pytest.approx(a)
    print(f"{solver}: Kirsch σx at the hole's top {s[top, 0] / sigma:.4f} σ")
    assert s[top, 0] / sigma == pytest.approx(3.003, rel=0.02)


def _ring_steps():
    """A quarter ring a..b in the first quadrant."""
    return [
        HistoryStep(kind="face", name="Ring", args={"profile": {"circle": [0, 0, B_OUT]}}),
        HistoryStep(kind="face", name="Bore", args={"profile": {"circle": [0, 0, A_IN]},
                                                    "mode": "cut"}),
        HistoryStep(kind="face", name="Q", args={"profile": {"points": [[0, 0], [1, 0], [1, 1],
                                                                        [0, 1]]},
                                                 "mode": "intersect"}),
    ]  # fmt: skip


def _lame(r):
    a2, b2 = A_IN**2, B_OUT**2
    A, B = P * a2 / (b2 - a2), P * a2 * b2 / (b2 - a2)
    return A + B / r**2, (1 + NU) / E * ((1 - 2 * NU) * A * r + B / r)


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_plane_strain_lame_ring(tmp_path, solver):
    features = [
        Feature(type="displacement", selection="Part/Xaxis", values={"y": mm(0)}),
        Feature(type="displacement", selection="Part/Yaxis", values={"x": mm(0)}),
        Feature(type="pressure", selection="Part/Bore", values={"p": qty(P, "Pa")}),
    ]  # fmt: skip
    edges = {"Bore": ["Bore/side-1"], "Outer": ["Ring/side-1"], "Xaxis": ["Q/side-1"],
             "Yaxis": ["Q/side-4"]}  # fmt: skip
    steps = _ring_steps()
    steps[0] = steps[0].model_copy(update={"name": "Ring"})
    p, c = _project(steps, edges, "2d_planar", _structure(features), size=1.0,
                    planar_model="plane_strain")  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    u, xyz = _at(run, "displacement", "Part/Outer")
    radial = (u[:, 0] * xyz[:, 0] + u[:, 1] * xyz[:, 1]) / np.hypot(xyz[:, 0], xyz[:, 1])
    assert radial.mean() == pytest.approx(_lame(B_OUT)[1], rel=5e-3)
    s, xyz = _at(run, "stress", "Part/Bore")
    on_y = int(np.argmax(xyz[:, 1]))  # (0, a): the hoop stress is σx there
    print(f"{solver}: Lamé plane strain, hoop at the bore {s[on_y, 0] / 1e6:.2f} MPa "
          f"(exact {_lame(A_IN)[0] / 1e6:.2f})")  # fmt: skip
    assert s[on_y, 0] == pytest.approx(_lame(A_IN)[0], rel=0.02)


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_axisymmetric_lame_cylinder(tmp_path, solver):
    h = 0.01
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="displacement", selection="Part/Top", values={"y": mm(0)}),
        Feature(type="pressure", selection="Part/Left", values={"p": qty(P, "Pa")}),
    ]  # fmt: skip
    p, c = _project([_rectangle("S", A_IN, 0, B_OUT - A_IN, h)], SIDES, "2d_axisymmetric",
                    _structure(features), size=1.0)  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    u, _ = _at(run, "displacement", "Part/Right")
    assert u[:, 0].mean() == pytest.approx(_lame(B_OUT)[1], rel=5e-3)
    s, _ = _at(run, "stress", "Part/Left")
    print(f"{solver}: axisymmetric Lamé, hoop at the bore {s[:, 2].mean() / 1e6:.2f} MPa "
          f"(exact {_lame(A_IN)[0] / 1e6:.2f})")  # fmt: skip
    assert s[:, 2].mean() == pytest.approx(_lame(A_IN)[0], rel=0.02)  # zz: the hoop stress


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_axisymmetric_bar_pulled_along_its_axis(tmp_path, solver):
    """A force on an axisymmetric edge is the whole revolution's."""
    r, L, F = 0.01, 0.05, 10e3
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="force", selection="Part/Top", values={"y": qty(F, "N")}),
    ]
    p, c = _project([_rectangle("S", 0, 0, r, L)], SIDES, "2d_axisymmetric",
                    _structure(features), size=2.0)  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    u, _ = _at(run, "displacement", "Part/Top")
    area = math.pi * r**2
    print(f"{solver}: axisymmetric bar, uy {u[:, 1].mean():.5g} m (exact {F * L / (E * area):.5g})")
    assert u[:, 1].mean() == pytest.approx(F * L / (E * area), rel=5e-3)
    s = run.results.last("stress").values
    assert np.median(s[:, 1]) == pytest.approx(F / area, rel=5e-3)


def _heat(features):
    return [Physics(type="heat_transfer", features=features, settings=CONSTANT)]


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_planar_conduction_with_a_flux(tmp_path, solver):
    L, q, T0 = 0.04, 5000.0, 300.0
    features = [
        Feature(type="temperature", selection="Part/Left", values={"T": qty(T0, "K")}),
        Feature(type="heat_flux", selection="Part/Right", values={"q": qty(-q, "W/m^2")}),
    ]  # fmt: skip
    p, c = _project([_rectangle("S", 0, 0, L, 0.02)], SIDES, "2d_planar", _heat(features),
                    shell_sections=[ShellSection(selection="Part", thickness=mm(2))])  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    T, _ = _at(run, "temperature", "Part/Right")
    assert T[:, 0].mean() == pytest.approx(T0 - q * L / K, abs=1e-3 * q * L / K)


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_axisymmetric_conduction_through_a_wall(tmp_path, solver):
    Ta, Tb = 400.0, 300.0
    features = [
        Feature(type="temperature", selection="Part/Left", values={"T": qty(Ta, "K")}),
        Feature(type="temperature", selection="Part/Right", values={"T": qty(Tb, "K")}),
    ]  # fmt: skip
    p, c = _project([_rectangle("S", A_IN, 0, B_OUT - A_IN, 0.01)], SIDES, "2d_axisymmetric",
                    _heat(features), size=1.0)  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    T = run.results.last("temperature")
    r = run.mesh.node_coords(T.ids)[:, 0]
    exact = Ta + (Tb - Ta) * np.log(r / A_IN) / np.log(B_OUT / A_IN)
    assert np.max(np.abs(T.values[:, 0] - exact)) < 1e-3 * (Ta - Tb)
