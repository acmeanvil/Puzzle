"""Structural dynamics (ADR 0041): a cantilever's tip in time, against its modes.

The 200 x 10 x 10 mm steel bar, clamped at one end, gets a 100 N tip load. Its tip moves
as the sum of its Euler-Bernoulli modes (β L = 1.875, 4.694, 7.855, ...; the first at
209 Hz), each a single-degree-of-freedom oscillator loaded by P φ(L) = 2P:

- **suddenly applied, undamped:** u = Σ s_n (1 - cos ω_n t), s_n = 4 P / (ρ A L ω_n²);
- **with Rayleigh mass damping** a = 2 ζ ω_1 (ζ = 5 % in the first mode, ζ_n = a / 2 ω_n):
  u = Σ s_n (1 - e^(-ζ_n ω_n t) (cos ω_dn t + ζ_n / √(1 - ζ_n²) sin ω_dn t));
- **ramped up over one period** by an amplitude (a load that follows a curve in time):
  u = Σ s_n (t/T_r - sin(ω_n t)/(ω_n T_r)), then the same less its value at t - T_r.

Newmark's average acceleration (no numerical damping), 60 steps a period (200 undamped,
so the second mode is followed). The history is checked to 4 % of the largest
deflection and the peak to 2 %; CalculiX and Code_Aster give the same history.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.model.schema import Amplitude, Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

P, E, RHO = 100.0, 210e9, 7850.0
L, B = BAR[0], BAR[1]
I, A = B**4 / 12, B * B  # noqa: E741
BETAS = [1.875104069, 4.694091133, 7.854757438, 10.99554073, 14.13716839]
W = np.array([b**2 * np.sqrt(E * I / (RHO * A * L**4)) for b in BETAS])
S = 4 * P / (RHO * A * L * W**2)  # each mode's static share of the tip deflection
T1 = 2 * np.pi / W[0]
ZETA = 0.05


def sudden(t, zeta=None):
    t = np.asarray(t)[:, None]
    if zeta is None:
        return -(S * (1 - np.cos(W * t))).sum(axis=1)
    z = zeta  # per mode
    wd = W * np.sqrt(1 - z**2)
    decay = np.exp(-z * W * t) * (np.cos(wd * t) + z / np.sqrt(1 - z**2) * np.sin(wd * t))
    return -(S * (1 - decay)).sum(axis=1)


def ramped(t, tr):
    def up(t):
        t = np.clip(t, 0, None)[:, None]
        return (S * (t / tr - np.sin(W * t) / (W * tr))).sum(axis=1)

    t = np.asarray(t)
    return -(up(t) - up(t - tr))


def _run(tmp_path, solver, end, settings=None, amplitude=None, per_period=60):
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-P, unit="N")})
    physics = Physics(
        type="solid_mechanics",
        features=[Feature(type="fixed", selection="Bar/Root"), force],
        settings={"temperature_dependent_materials": False},
    )
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5.0)
    if amplitude is not None:
        amp = project.add(Amplitude(name="Ramp", points=amplitude))
        force.amplitude = amp.id
    step = SolveStep(
        type="transient",
        physics=["solid_mechanics"],
        settings={"time_step": T1 / per_period, "end_time": end, **(settings or {})},
    )
    study = project.add(Study(name="Dynamics", component=comp.id, solver=solver, steps=[step]))
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    pts = [
        (s.value, s.fields["displacement"].at(tip)[:, 1].mean())
        for s in run.results.steps
        if "displacement" in s.fields and s.value > 0
    ]
    return np.array(pts).T


def _check(name, solver, t, u, ref):
    peak = np.max(np.abs(ref))
    worst = float(np.max(np.abs(u - ref)) / peak)
    print(f"{solver} {name}: {len(t)} steps, largest error {100 * worst:.2f} % of the peak, "
          f"peak {np.min(u):.5g} m against {np.min(ref):.5g} m")  # fmt: skip
    assert worst < 4e-2
    assert np.min(u) == pytest.approx(np.min(ref), rel=2e-2)


@over("calculix", "code_aster")
def test_sudden_load(tmp_path, solver):
    pytest.importorskip("OCP")
    # Undamped, the second mode (2.5 % of the deflection) must be followed too: with 60
    # steps a period Newmark stretches its period by 3.6 % and the peak reads 2.5 % low.
    t, u = _run(tmp_path, solver, 1.5 * T1, per_period=200)
    _check("sudden load, undamped", solver, t, u, sudden(t))


@over("calculix", "code_aster")
def test_rayleigh_damping(tmp_path, solver):
    pytest.importorskip("OCP")
    a = 2 * ZETA * W[0]  # mass-proportional: 5 % in the first mode
    t, u = _run(tmp_path, solver, 3 * T1, {"rayleigh_alpha": a})
    _check("sudden load, 5 % damping", solver, t, u, sudden(t, a / (2 * W)))
    late = t > 2 * T1  # the oscillation has died down towards the static deflection
    assert np.ptp(u[late]) < 0.75 * np.ptp(sudden(t[late]))


@over("calculix", "code_aster")
def test_ramped_load_follows_its_amplitude(tmp_path, solver):
    pytest.importorskip("OCP")
    t, u = _run(tmp_path, solver, 2 * T1, amplitude=[(0.0, 0.0), (T1, 1.0), (3 * T1, 1.0)])
    _check("load ramped over one period", solver, t, u, ramped(t, T1))


def harmonic(f, zeta=None):
    """The tip's complex displacement under a 100 N tip force (-y) at f Hz."""
    r = 2 * np.pi * f / W
    z = np.zeros_like(W) if zeta is None else zeta
    return -(S / (1 - r**2 + 2j * z * r)).sum()


@over("calculix", "code_aster")
@pytest.mark.parametrize("damped", [False, True], ids=["undamped", "damped"])
def test_harmonic_response(tmp_path, solver, damped):
    """Below and above the first mode, undamped and with 5 % Rayleigh mass damping in
    the first mode: the tip's amplitude within 1 % and its phase within 2 degrees.
    Code_Aster solves the full system; CalculiX superposes its first 20 modes."""
    pytest.importorskip("OCP")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-P, unit="N")})
    physics = Physics(
        type="solid_mechanics",
        features=[Feature(type="fixed", selection="Bar/Root"), force],
        settings={"temperature_dependent_materials": False},
    )
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5.0)
    a = 2 * ZETA * W[0]
    settings = {"frequencies": "100, 150, 300, 600"}
    if damped:
        settings["rayleigh_alpha"] = a
    step = SolveStep(type="frequency_domain", physics=["solid_mechanics"], settings=settings)
    study = project.add(Study(name="Harmonic", component=comp.id, solver=solver, steps=[step]))
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    assert [s.kind for s in run.results.steps] == ["frequency_domain"] * 4
    for s in run.results.steps:
        expected = harmonic(s.value, a / (2 * W) if damped else None)
        amp = s.fields["displacement_amplitude"].at(tip)[:, 1].mean()
        phase = s.fields["displacement_phase"].at(tip)[:, 1].mean()
        want = np.degrees(np.angle(expected))
        print(f"{solver} {s.label()}: amplitude {amp:.5g} m (theory {abs(expected):.5g}), "
              f"phase {phase:.1f}° (theory {want:.1f}°)")  # fmt: skip
        assert amp == pytest.approx(abs(expected), rel=1e-2)
        assert abs((phase - want + 180) % 360 - 180) < 2.0


@over("calculix")
def test_modal_superposition_transient(tmp_path, solver):
    """The sudden load by superposing the first 20 modes (method modal) instead of
    solving the whole system: the same history."""
    pytest.importorskip("OCP")
    t, u = _run(tmp_path, solver, 1.5 * T1, {"method": "modal", "modes": 20}, per_period=200)
    _check("sudden load, modal superposition", solver, t, u, sudden(t))


def test_what_a_solver_cannot_do_is_refused(tmp_path):
    """A damping ratio without modes, and Code_Aster's modal transient, are refused."""
    pytest.importorskip("OCP")
    from puzzle.adapters.calculix.inp import write_inp
    from puzzle.adapters.code_aster.comm import write_case
    from puzzle.errors import PuzzleError
    from puzzle.mesh import generate_mesh

    physics = Physics(
        type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root")]
    )
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=10.0)
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    timing = {"time_step": 1e-4, "end_time": 1e-3}
    ratio = SolveStep(
        type="transient", physics=["solid_mechanics"], settings=timing | {"damping_ratio": 0.02}
    )
    study = project.add(Study(name="R", component=comp.id, solver="calculix", steps=[ratio]))
    with pytest.raises(PuzzleError, match="modal damping ratio needs modal superposition"):
        write_inp(project, study, mesh)
    modal = SolveStep(
        type="transient", physics=["solid_mechanics"], settings=timing | {"method": "modal"}
    )
    study.steps = [modal]
    with pytest.raises(PuzzleError, match="Code_Aster solves transients directly"):
        write_case(project, study, mesh, tmp_path / "ca")
