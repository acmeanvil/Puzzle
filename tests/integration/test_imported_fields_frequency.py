"""Imported fields varying with frequency (ADR 0044), in a harmonic step (ADR 0041).

The 200 x 10 x 10 mm steel bar, clamped at its root, gets a pressure on its top face
whose size depends on frequency: a CSV with a frequency column gives it at 0, 200, 400
and 800 Hz (10, 20, 5 and 15 kPa), and the step asks for 100, 150, 300 and 600 Hz, so
each takes the pressure linearly between the two given about it (15, 17.5, 12.5, 10 kPa).

- **Closed form:** a uniform load q = p b on an Euler-Bernoulli cantilever moves its tip
  by Σ φ_n(L) ∫ q φ_n dx / (ρ A ∫ φ_n² dx (ω_n² - ω²)) at ω, the first five modes
  (the tip's amplitude within 1 %, as `test_dynamics.test_harmonic_response`).
- **Against the uniform feature:** each frequency's tip equals a uniform 10 kPa run's
  scaled by p(f) / 10 kPa, to 1e-5 (the .frd's digits): the field changes the load per frequency and
  nothing else.

CalculiX writes an amplitude against frequency per face (its *STEADY STATE DYNAMICS
step reads amplitudes so); Code_Aster one charge per given frequency, each multiplied by
a hat function of FREQ in DYNA_VIBRA, which interpolates the same way.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.geometry import import_step
from puzzle.model import Feature, FieldUse, ImportedField, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import Geometry, NamedSelection
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
E, RHO = 210e9, 7850.0
I, A = B * H**3 / 12, B * H  # noqa: E741
BETAS = np.array([1.875104069, 4.694091133, 7.854757438, 10.99554073, 14.13716839]) / L
GIVEN = [(0.0, 10e3), (200.0, 20e3), (400.0, 5e3), (800.0, 15e3)]  # (Hz, Pa)
ASKED = [100.0, 150.0, 300.0, 600.0]
P0 = 10e3


def pressure_at(f):
    return float(np.interp(f, *zip(*GIVEN, strict=True)))


def tip(f, p):
    """The tip's deflection (-y) under a uniform pressure p on the top face at f Hz."""
    x = np.linspace(0, L, 4001)
    out = 0.0
    for beta in BETAS:
        bl = beta * L
        sigma = (np.cosh(bl) + np.cos(bl)) / (np.sinh(bl) + np.sin(bl))
        phi = np.cosh(beta * x) - np.cos(beta * x) - sigma * (np.sinh(beta * x) - np.sin(beta * x))
        w = beta**2 * np.sqrt(E * I / (RHO * A))
        out += (
            phi[-1]
            * np.trapezoid(p * B * phi, x)
            / (RHO * A * np.trapezoid(phi**2, x) * (w**2 - (2 * np.pi * f) ** 2))
        )
    return -out


def _csv(path):
    g = np.linspace(-0.002, 0.202, 18)
    zs = np.linspace(-0.002, 0.012, 4)
    rows = ["x [m],y [m],z [m],f [Hz],p [kPa]"]
    for f, p in GIVEN:
        rows += [f"{x:.10g},{B:.10g},{z:.10g},{f:g},{p / 1e3:g}" for x in g for z in zs]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _run(tmp_path, solver, use_field, settings=None):
    tmp_path.mkdir(parents=True)
    pressure = Feature(type="pressure", selection="Bar/Top", values={"p": Quantity(value=P0,
                       unit="Pa")})  # fmt: skip
    physics = Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"), pressure],
        settings={"temperature_dependent_materials": False})  # fmt: skip
    step_file = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step_file, [physics], mesh_size_mm=5.0)
    geo = next(iter(project.of_type(Geometry)))
    top = import_step(step_file).faces_on_plane("y", B)
    geo.selections.append(NamedSelection(name="Top", entity="face", members=top))
    if use_field:
        data = new_field_data(tmp_path, _csv(tmp_path / "spectrum.csv"), "Spectrum")
        field = project.add(ImportedField(**data))
        assert field.along() == "frequency"
        pressure.fields["p"] = FieldUse(field=field.id, column="p")
    settings = settings or {"frequencies": ", ".join(f"{f:g}" for f in ASKED)}
    step = SolveStep(type="frequency_domain", physics=["solid_mechanics"], settings=settings)
    study = project.add(Study(name="Harmonic", component=comp.id, solver=solver, steps=[step]))
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    ids = run.mesh.groups["Bar/Tip"].node_ids
    out = {}
    for s in run.results.steps:
        amp = s.fields["displacement_amplitude"].at(ids)[:, 1].mean()
        phase = s.fields["displacement_phase"].at(ids)[:, 1].mean()
        out[round(s.value, 6)] = amp * np.sign(np.cos(np.radians(phase)))
    return out


@over("calculix", "code_aster", "fenicsx")
def test_a_pressure_varying_with_frequency_applies_per_frequency(tmp_path, solver):
    field = _run(tmp_path / "field", solver, True)
    uniform = _run(tmp_path / "uniform", solver, False)
    assert sorted(field) == ASKED
    for f in ASKED:
        p = pressure_at(f)
        want = tip(f, p)
        print(f"{solver} {f:g} Hz: p {p / 1e3:g} kPa, tip {field[f]:.6g} m (closed form "
              f"{want:.6g}, uniform scaled {uniform[f] * p / P0:.6g})")  # fmt: skip
        assert field[f] == pytest.approx(uniform[f] * p / P0, rel=1e-5)  # .frd: 6 digits
        assert field[f] == pytest.approx(want, rel=1e-2)


@over("calculix")
def test_a_swept_range_reads_the_amplitudes_against_frequency(tmp_path, solver):
    """CalculiX's range sweep (one *STEADY STATE DYNAMICS card, its own frequencies
    between the eigenfrequencies, the first of them 209 Hz) takes the field's amplitude
    at each of them; 2 % mass damping in the first mode keeps the resonance finite."""
    w1 = BETAS[0] ** 2 * np.sqrt(E * I / (RHO * A))
    sweep = {"start_frequency": 120.0, "end_frequency": 700.0, "points": 4,
             "rayleigh_alpha": 2 * 0.02 * w1}  # fmt: skip
    field = _run(tmp_path / "field", solver, True, sweep)
    uniform = _run(tmp_path / "uniform", solver, False, sweep)
    assert field.keys() == uniform.keys() and len(field) >= 4
    for f, u in field.items():
        assert u == pytest.approx(uniform[f] * pressure_at(f) / P0, rel=1e-5), f
    print(f"{len(field)} frequencies from {min(field):g} to {max(field):g} Hz")
