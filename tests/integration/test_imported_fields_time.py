"""Imported fields varying in time (ADR 0044), on the 200 x 10 x 10 mm steel bar:

- **two static steps:** a tip pressure given at t = 0 (0 MPa) and t = 2 (20 MPa) in two
  files; step 1 takes it at time 1 (10 MPa), step 2 at time 2 (20 MPa), and each moves
  the tip as a uniform pressure of that size does (u = -p L / E, the mesh's own error
  shared). CalculiX writes both steps in one deck; Code_Aster runs one job per step.
- **a transient heat step:** a tip heat flux given at 0, 5 and 10 s follows the same
  history as a uniform flux on an Amplitude through those points, to every digit
  printed (CalculiX: an amplitude per face).
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
from puzzle.model.schema import Amplitude
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
E = library_material("structural_steel").properties["youngs_modulus"].to_si()
CONSTANT = {"temperature_dependent_materials": False}


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _tip_csv(path, value, header):
    g = np.linspace(-0.002, 0.012, 6)
    yy, zz = np.meshgrid(g, g)
    rows = [header] + [f"{L:.10g},{y:.10g},{z:.10g},{value:.10g}"
                       for y, z in zip(yy.ravel(), zz.ravel(), strict=True)]  # fmt: skip
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _tips(run):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    states = [s for s in run.results.steps if "displacement" in s.fields]
    return [float(s.fields["displacement"].at(tip)[:, 0].mean()) for s in states]


def _static(tmp_path, solver, use_field):
    tmp_path.mkdir(parents=True)
    pressure = Feature(type="pressure", selection="Bar/Tip", values={"p": qty(10, "MPa")})
    physics = Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"), pressure], settings=CONSTANT)  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    steps = [SolveStep(type="stationary"), SolveStep(type="stationary")]
    if use_field:
        start = _tip_csv(tmp_path / "p0.csv", 0.0, "x,y,z,p [MPa]")
        end = _tip_csv(tmp_path / "p2.csv", 20.0, "x,y,z,p [MPa]")
        data = new_field_data(tmp_path, start, "Ramp", files=[end], times=[0.0, 2.0])
        field = project.add(ImportedField(**data))
        pressure.fields["p"] = FieldUse(field=field.id, column="p")
    study = project.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    return _tips(run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900))


@over("calculix", "code_aster")
def test_each_static_step_takes_the_pressure_at_its_time(tmp_path, solver):
    ramp = _static(tmp_path / "field", solver, True)
    uniform = _static(tmp_path / "uniform", solver, False)  # 10 MPa in both steps
    print(f"{solver}: tips {ramp} m, uniform 10 MPa {uniform} m, closed form "
          f"{-10e6 * L / E:.6g} m")  # fmt: skip
    assert len(ramp) == 2
    assert ramp[0] == pytest.approx(uniform[0], rel=1e-6)
    assert ramp[1] == pytest.approx(2 * uniform[1], rel=1e-6)
    assert ramp[0] == pytest.approx(-10e6 * L / E, rel=0.01)


def _transient(tmp_path, use_field):
    tmp_path.mkdir(parents=True)
    q0, history = 5e4, [(0.0, 0.0), (5.0, 1.0), (10.0, 0.5)]  # W/m², (s, factor)
    flux = Feature(type="heat_flux", selection="Bar/Tip", values={"q": qty(q0, "W/m^2")})
    root = Feature(type="temperature", selection="Bar/Root", values={"T": qty(20, "degC")})
    heat = Physics(type="heat_transfer", features=[root, flux],
                   settings={"initial_temperature": qty(20, "degC"), **CONSTANT})  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [heat], mesh_size_mm=5)
    if use_field:
        paths = [_tip_csv(tmp_path / f"q{int(t)}.csv", q0 * a, "x,y,z,q [W/m^2]")
                 for t, a in history]  # fmt: skip
        data = new_field_data(tmp_path, paths[0], "Flux", files=paths[1:],
                              times=[t for t, _ in history])  # fmt: skip
        field = project.add(ImportedField(**data))
        flux.fields["q"] = FieldUse(field=field.id, column="q")
    else:
        flux.amplitude = project.add(Amplitude(name="A", points=history)).id
    step = SolveStep(type="transient", physics=["heat_transfer"],
                     settings={"time_step": qty(1, "s"), "end_time": qty(10, "s")})  # fmt: skip
    study = project.add(Study(name="t", component=comp.id, solver="calculix", steps=[step]))
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return {round(s.value, 6): float(s.fields["temperature"].at(tip)[:, 0].mean())
            for s in run.results.steps if "temperature" in s.fields}  # fmt: skip


@over("calculix")
def test_a_transient_flux_follows_the_field_as_an_amplitude_would(tmp_path, solver):
    field = _transient(tmp_path / "field", True)
    amplitude = _transient(tmp_path / "amplitude", False)
    deck = next((tmp_path / "field" / "run").rglob("*.inp")).read_text(encoding="utf-8")
    assert "*AMPLITUDE, NAME=FIELD1" in deck and "AMPLITUDE=FIELD1" in deck
    print(f"tip at 5 s: {field[5.0]:.6f} K (amplitude {amplitude[5.0]:.6f} K), at 10 s: "
          f"{field[10.0]:.6f} K ({amplitude[10.0]:.6f} K)")  # fmt: skip
    assert field.keys() == amplitude.keys() and field[10.0] > 293.15 + 1
    for t in field:
        assert field[t] == pytest.approx(amplitude[t], rel=1e-9)
