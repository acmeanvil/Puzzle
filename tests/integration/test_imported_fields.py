"""Imported fields in studies (ADR 0044), on the 200 x 10 x 10 mm steel bar:

- **a thermal load:** an imported linear temperature T = T0 + g x on the bar (its root
  held), against the free expansion u(L) = α (g L² / 2 + (T0 - Tref) L);
- **a pressure:** an imported uniform 10 MPa on the tip face gives what the uniform
  feature gives (u = -p L / E along the bar), and so does a copy of the data moved away
  and rotated in its file, placed back by the field's placement;
- **a convection coefficient:** an imported h on the tip gives the uniform h's answer.

CalculiX takes the values per face; Code_Aster a temperature load, and a pressure as
consistent nodal forces (both give CalculiX's numbers to every digit printed).
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import library_material
from puzzle.model import (
    Feature,
    FieldUse,
    ImportedField,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
STEEL = library_material("structural_steel").properties
E = STEEL["youngs_modulus"].to_si()
ALPHA = STEEL["thermal_expansion"].to_si()
CONSTANT = {"temperature_dependent_materials": False}


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _csv(path, points, values, header):
    rows = [header] + [
        ",".join(f"{c:.10g}" for c in (*p, v)) for p, v in zip(points, values, strict=True)
    ]
    path.write_text("\n".join(rows) + "\n")
    return path


def _add_field(project, root, path, **extra):
    data = new_field_data(root, path, **extra)
    return project.add(ImportedField(**data))


def _solve(tmp_path, project, comp, solver, physics=("solid_mechanics",)):
    steps = [SolveStep(type="stationary", physics=list(physics))]
    study = project.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    return run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=600)


def _tip(run, name="displacement", component=0):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return run.results.last(name).at(tip)[:, component].mean()


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_an_imported_temperature_expands_the_bar(tmp_path, solver):
    T0, g, Tref = 350.0, 500.0, 293.15  # K, K/m
    x = np.linspace(-0.01, L + 0.01, 23)
    y, z = np.meshgrid([-0.005, 0.015], [-0.005, 0.015])
    points = np.array(
        [(xi, yi, zi) for xi in x for yi, zi in zip(y.ravel(), z.ravel(), strict=True)]
    )
    path = _csv(
        tmp_path / "temps.csv", points * 1000, T0 + g * points[:, 0], "x [mm],y [mm],z [mm],T [K]"
    )
    temperature = Feature(
        type="body_temperature", selection="Bar/Body", values={"T": qty(20, "degC")}
    )
    physics = Physics(type="solid_mechanics", features=[
        Feature(type="displacement", selection="Bar/Root", values={"x": qty(0, "mm")}),
        Feature(type="displacement", selection="Bar/Root",
                values={"y": qty(0, "mm"), "z": qty(0, "mm")}),
        temperature], settings=CONSTANT)  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    field = _add_field(project, tmp_path, path, name="Oven")
    temperature.fields["T"] = FieldUse(field=field.id, column="T")
    run = _solve(tmp_path, project, comp, solver)
    exact = ALPHA * (g * L**2 / 2 + (T0 - Tref) * L)
    print(f"{solver}: tip moves {_tip(run):.6g} m (free expansion {exact:.6g} m)")
    assert _tip(run) == pytest.approx(exact, rel=0.01)


def _pressure_case(tmp_path, solver, placement=None, rotate=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = 10e6
    g = np.linspace(-0.002, 0.012, 8)
    yy, zz = np.meshgrid(g, g)
    points = np.column_stack([np.full(yy.size, L), yy.ravel(), zz.ravel()])
    if rotate is not None:  # the file holds the points turned and moved away
        points = rotate(points)
    path = _csv(tmp_path / "walls.csv", points, np.full(len(points), p / 1e6), "x,y,z,p [MPa]")
    pressure = Feature(type="pressure", selection="Bar/Tip", values={"p": qty(0, "MPa")})
    physics = Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"), pressure], settings=CONSTANT)  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    field = _add_field(project, tmp_path, path, name="Walls", placement=placement)
    pressure.fields["p"] = FieldUse(field=field.id, column="p")
    return _tip(_solve(tmp_path, project, comp, solver), component=0), p


@over("calculix", "code_aster")
def test_an_imported_pressure_is_the_uniform_one_and_survives_a_move(tmp_path, solver):
    u, p = _pressure_case(tmp_path / "a", solver)
    # The same data written turned 90° about z and 1 m along y, then placed back.
    turn = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])  # +90° about z

    def away(points):
        return points @ turn.T + [0.0, 1.0, 0.0]

    u_moved, _ = _pressure_case(
        tmp_path / "b", solver, placement=_undo_turn_then_move(), rotate=away
    )
    print(f"{solver}: tip {u:.6g} m, moved copy {u_moved:.6g} m (closed form {-p * L / E:.6g})")
    assert u == pytest.approx(-p * L / E, rel=0.01)
    assert u_moved == pytest.approx(u, rel=1e-6)


def _undo_turn_then_move():
    """Points p' = R p + t (R +90° about z, t = (0, 1, 0)) are put back by p = R⁻¹ (p' - t):
    a rotation of -90° about the point t, then a move of -t."""
    return {
        "rotation": (0.0, 0.0, -90.0),
        "about": (0.0, 1.0, 0.0),
        "translation": (0.0, -1.0, 0.0),
    }


@over("calculix")  # Code_Aster: refused so far (its ECHANGE takes one value per group)
def test_an_imported_film_coefficient_is_the_uniform_one(tmp_path, solver):
    h, T_inf = 250.0, 300.0
    g = np.linspace(-0.002, 0.012, 6)
    yy, zz = np.meshgrid(g, g)
    points = np.column_stack([np.full(yy.size, L), yy.ravel(), zz.ravel()])
    path = _csv(tmp_path / "htc.csv", points, np.full(len(points), h), "x,y,z,h [W/(m^2*K)]")

    def case(use_field):
        values = {"h": qty(0 if use_field else h, "W/(m^2*K)"), "T_inf": qty(T_inf, "K")}
        film = Feature(type="convection", selection="Bar/Tip", values=values)
        root = Feature(type="temperature", selection="Bar/Root", values={"T": qty(400, "K")})
        physics = Physics(type="heat_transfer", settings=CONSTANT, features=[root, film])
        where = tmp_path / ("field" if use_field else "plain")
        where.mkdir()
        project, comp = bar_project(write_bar_step(where / "bar.step"), [physics], mesh_size_mm=5)
        if use_field:
            field = _add_field(project, where, path, name="HTC")
            film.fields["h"] = FieldUse(field=field.id, column="h")
        return _tip(_solve(where, project, comp, solver, ("heat_transfer",)), "temperature")

    assert case(True) == pytest.approx(case(False), rel=1e-9)


def test_what_a_solver_cannot_take_from_a_field_is_refused(tmp_path):
    from puzzle.adapters import available_adapters

    def get_adapter(name):
        return available_adapters()[name]()

    physics = Physics(type="heat_transfer", features=[
        Feature(type="convection", selection="Bar/Tip",
                values={"h": qty(10, "W/(m^2*K)"), "T_inf": qty(300, "K")})])  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=10)
    path = _csv(tmp_path / "h.csv", np.zeros((4, 3)) + [[L, 0, 0]], np.ones(4), "x,y,z,h")
    field = _add_field(project, tmp_path, path, name="HTC")
    physics.features[0].fields["h"] = FieldUse(field=field.id, column="h")
    study = project.add(Study(name="s", component=comp.id, solver="code_aster",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    problems = get_adapter("code_aster").check(project, study)
    assert any("convection's h from an imported field" in p for p in problems)
    assert (
        get_adapter("calculix").check(project, study.model_copy(update={"solver": "calculix"}))
        == []
    )
