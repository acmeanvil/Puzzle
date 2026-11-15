"""Explicit dynamics on OpenCourant (ADR 0085), against closed forms:

- the 1D wave: an elastic bar (ν = 0) held at one end and driven at v₀ at the other
  carries σ = ρ c v₀ behind a front travelling at c = √(E/ρ), and nothing ahead of it;
- an elastic bar thrown at a rigid wall: it touches for 2 L / c and leaves at v₀. A
  kinematic wall stops the nodes that reach it, so a little momentum goes each impact:
  0.964 v₀ on 40 elements along, 0.976 on 80 (measured 2026-11-14);
- a load applied suddenly to a cantilever: the tip's history against CalculiX's
  implicit transient (direct, no numerical damping) on the same bar. Twice the static
  deflection at half the first period holds for one degree of freedom only: the step
  rings the higher modes too, so the history is compared whole;
- a bar pulled slowly (its end's velocity ramped over many wave transits) on a flat
  plasticity curve: past yield the stress holds at the yield stress. (A bar struck past
  yield carries less than σy ahead of its plastic zone in 3D, which lateral inertia
  confines; that is the bar's dynamics, not the material's, so it isn't the benchmark.)

Each runs through Puzzle's whole path (project, mesh, deck, the pinned image, results).
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material
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
from puzzle.model.schema import HardeningCurve, HistoryStep, MaterialModels, Plasticity
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
E, RHO = 200e9, 7850.0
L, W = 0.2, 0.01


def _bar(solver, features, settings, *, nu=0.0, size_mm=5.0, shape="hexahedra", order=1,
         plastic=None, length=L, width=W):  # fmt: skip
    pytest.importorskip("OCP")
    p = Project(name="explicit")
    geo = p.add(Geometry(name="Bar", history=[HistoryStep(kind="box", name="B", args={
        "dx": length, "dy": width, "dz": width})], selections=[
        NamedSelection(name="Body", entity="body", members=["Body"]),
        NamedSelection(name="Start", entity="face", members=["B/-x"]),
        NamedSelection(name="End", entity="face", members=["B/+x"])]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    steel.properties["youngs_modulus"] = q(E, "Pa")
    steel.properties["poissons_ratio"] = q(nu, "dimensionless")
    steel.properties["density"] = q(RHO, "kg/m^3")
    steel.tables = {}
    steel.models = MaterialModels(plasticity=Plasticity(curves=[HardeningCurve(points=plastic)])) \
        if plastic else MaterialModels()  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=q(size_mm, "mm"), element_shape=shape,
                            element_order=order))  # fmt: skip
    comp = p.add(Component(name="C", parts=[PartInstance(name="S", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=steel.id,
                                                                       selection="S/Body")],
                           physics=[Physics(type="solid_mechanics", settings={
                               "temperature_dependent_materials": False},
                               features=features)]))  # fmt: skip
    study = p.add(Study(name="Impact", component=comp.id, solver=solver,
                        steps=[SolveStep(type="explicit", settings=settings)]))  # fmt: skip
    return p, study


def _run(tmp_path, p, study):
    root = tmp_path / "p"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)


def _wave(v0=1.0, nu=0.0):
    return math.sqrt(E / RHO) if nu == 0 else math.sqrt(
        E * (1 - nu) / (RHO * (1 + nu) * (1 - 2 * nu)))  # fmt: skip


@over("opencourant")
@pytest.mark.parametrize("shape", ["hexahedra", "tet4"])
def test_a_wave_carries_rho_c_v0_behind_its_front(tmp_path, solver, shape):
    v0 = 1.0
    c = math.sqrt(E / RHO)
    end = 0.75 * L / c  # the front three quarters of the way along
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(-v0, "m/s")}),
    ]
    p, study = _bar(solver, features, {"end_time": end, "frames": 6},
                    shape="hexahedra" if shape == "hexahedra" else "tetrahedra",
                    size_mm=5.0 if shape == "hexahedra" else 2.5)  # fmt: skip
    run = _run(tmp_path, p, study)
    frames = [s for s in run.results.steps if "stress" in s.fields]
    assert len(frames) >= 6 and frames[-1].value == pytest.approx(end, rel=0.05)
    last = frames[-1]
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    front = L - c * last.value
    behind = stress.values[x > front + 0.02, 0]
    ahead = stress.values[x < front - 0.02, 0]
    expected = -RHO * c * v0
    print(f"{shape}: t {last.value:.4g} s, sigma_xx behind {behind.mean() / 1e6:.5g} MPa "
          f"(rho c v0 {expected / 1e6:.5g}), spread {behind.std() / -expected:.3f}, ahead "
          f"{np.abs(ahead).max() / 1e6:.3g} MPa")  # fmt: skip
    assert behind.mean() == pytest.approx(expected, rel=0.01)
    assert np.abs(ahead).max() < 0.02 * abs(expected)


@over("opencourant")
def test_a_bar_thrown_at_a_rigid_wall_leaves_at_its_speed(tmp_path, solver):
    v0 = 10.0
    c = math.sqrt(E / RHO)
    contact = 2 * L / c
    features = [Feature(type="initial_velocity", selection="S/Body", values={"vx": q(-v0, "m/s")}),
                Feature(type="rigid_wall", selection="S/Body", values={
                    "x0": q(-1e-6, "mm"), "nx": q(1.0, "dimensionless"),
                    "nz": q(0.0, "dimensionless")})]  # fmt: skip
    speeds = []
    for size in (5.0, 2.5):
        p, study = _bar(solver, features, {"end_time": 1.5 * contact, "frames": 60},
                        size_mm=size)  # fmt: skip
        run = _run(tmp_path / f"{size:g}", p, study)
        frames = [s for s in run.results.steps if "velocity" in s.fields]
        first = run.mesh.node_coords(frames[0].fields["velocity"].ids)[:, 0]
        at_wall = first < 1e-9
        separated = next(s.value for s in frames
                         if s.fields["velocity"].values[at_wall, 0].mean() > 0.5 * v0)  # fmt: skip
        mean_v = frames[-1].fields["velocity"].values[:, 0].mean()
        speeds.append(mean_v)
        print(f"{size:g} mm: separated at {separated:.4g} s (2 L / c {contact:.4g}), leaves at "
              f"{mean_v:.4g} m/s")  # fmt: skip
        assert separated == pytest.approx(contact, rel=0.04)
        assert mean_v == pytest.approx(v0, rel=0.05)
    assert abs(speeds[1] - v0) < abs(speeds[0] - v0)  # closer on the finer mesh


@over("opencourant")
def test_a_suddenly_applied_load_against_an_implicit_transient(tmp_path, solver):
    """The tip's swing after a force switched on at t = 0, OpenCourant against CalculiX's
    direct Newmark (average acceleration, no damping, T1/400 steps) on the same bar."""
    from solvers import available

    if not available("calculix"):
        pytest.skip("CalculiX gives the implicit reference")
    nu, force = 0.3, 100.0
    load = [Feature(type="fixed", selection="S/Start"),
            Feature(type="force", selection="S/End", values={"z": q(force, "N")})]  # fmt: skip
    inertia = W**4 / 12
    f1 = 1.8751**2 / (2 * math.pi) * math.sqrt(E * inertia / (RHO * W * W * L**4))
    period = 1 / f1
    end = 0.75 * period
    ref, ref_study = _bar("calculix", load, {}, nu=nu, size_mm=2.5, order=2)
    ref_study.steps = [SolveStep(type="transient", settings={
        "time_step": period / 400, "end_time": end, "method": "direct"})]  # fmt: skip
    ref_run = _run(tmp_path / "ref", ref, ref_study)
    p, study = _bar(solver, load, {"end_time": end, "frames": 60}, nu=nu, size_mm=2.5)
    run = _run(tmp_path / "x", p, study)

    def tip_history(r):
        out = []
        for s in r.results.steps:
            d = s.fields.get("displacement")
            if d is None:
                continue
            at = np.abs(r.mesh.node_coords(d.ids)[:, 0] - L) < 1e-9
            out.append((s.value, float(d.values[at, 2].mean())))
        return np.array(sorted(out))

    implicit, explicit = tip_history(ref_run), tip_history(run)
    reference = np.interp(explicit[:, 0], implicit[:, 0], implicit[:, 1])
    peak = implicit[:, 1].max()
    worst = float(np.abs(explicit[:, 1] - reference).max())
    print(f"tip peak: explicit {explicit[:, 1].max():.5g} m, implicit {peak:.5g} m; largest "
          f"difference over the swing {worst / peak:.2%} of the peak")  # fmt: skip
    assert explicit[:, 1].max() == pytest.approx(peak, rel=0.02)
    assert worst < 0.03 * peak


@over("opencourant")
def test_a_bar_pulled_slowly_past_yield_holds_the_yield_stress(tmp_path, solver):
    from puzzle.model.schema import Amplitude

    yield_stress = 250e6
    c = math.sqrt(E / RHO)
    ramp = 25 * L / c  # the end's speed ramps up over 25 wave transits: a slow pull
    p_features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(1.0, "m/s")}),
    ]
    flat = [(0.0, yield_stress), (1.0, yield_stress)]
    end = 3 * ramp  # the end moves 2.5 ramp x 1 m/s: about ten times the yield strain
    p, study = _bar(solver, p_features, {"end_time": end, "frames": 10}, plastic=flat)
    amp = p.add(Amplitude(name="ramp", points=[(0.0, 0.0), (ramp, 1.0), (end, 1.0)]))
    p.objects[study.component].physics[0].features[1].amplitude = amp.id
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1].fields
    sxx = last["stress"].values[:, 0]
    x = run.mesh.node_coords(last["stress"].ids)[:, 0]
    middle = sxx[(x > 0.1 * L) & (x < 0.9 * L)]
    strain = 2.5 * ramp * 1.0 / L
    print(f"strain {strain:.4g} ({strain * E / yield_stress:.2f} x the yield strain): sigma_xx "
          f"{middle.mean() / 1e6:.5g} MPa (yield {yield_stress / 1e6:g}), spread "
          f"{middle.std() / yield_stress:.3f}; plastic strain max "
          f"{last['plastic_strain_equivalent'].values.max():.3g}")  # fmt: skip
    assert middle.mean() == pytest.approx(yield_stress, rel=0.02)
    assert last["plastic_strain_equivalent"].values.max() > 0.5 * (strain - yield_stress / E)
