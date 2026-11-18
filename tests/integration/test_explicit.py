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
         plastic=None, length=L, width=W, johnson_cook=None):  # fmt: skip
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
        if plastic else MaterialModels(johnson_cook=johnson_cook)  # fmt: skip
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
    run = run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)
    listing = tmp_path / "w" / "puzzle_0000.out"
    if listing.exists():  # every card read as written: no field the Starter didn't place
        text = listing.read_text(encoding="utf-8", errors="replace")
        assert "WARNING ID : 100213" not in text and "WARNING ID : 100214" not in text, (
            "the Starter found a field it couldn't place (see puzzle_0000.out)"
        )
    return run


def _estimate_matches(run, p, study, what, within=0.25):
    """explicit.estimate's stable step (× the scale) against OpenCourant's own at the
    first frames (the time history's TIME STEP), within `within`."""
    from puzzle import explicit

    comp = p.objects[study.component]
    est = explicit.estimate(p, comp, run.mesh, 1.0)
    steps = [s.fields["time_step"].values[0, 0] for s in run.results.steps[1:3]
             if "time_step" in s.fields]  # fmt: skip
    assert est is not None and steps
    ours, theirs = est.time_step * est.scale, float(np.median(steps))
    print(f"{what}: time step estimated {ours:.4g} s, OpenCourant's {theirs:.4g} s "
          f"({ours / theirs - 1:+.1%})")  # fmt: skip
    assert ours == pytest.approx(theirs, rel=within)


def _wave(v0=1.0, nu=0.0):
    return math.sqrt(E / RHO) if nu == 0 else math.sqrt(
        E * (1 - nu) / (RHO * (1 + nu) * (1 - 2 * nu)))  # fmt: skip


@over("opencourant")
@pytest.mark.parametrize("shape", ["hexahedra", "tet4", "tet10"])
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
                    size_mm={"hexahedra": 5.0, "tet4": 2.5, "tet10": 5.0}[shape],
                    order=2 if shape == "tet10" else 1)  # fmt: skip
    run = _run(tmp_path, p, study)
    _estimate_matches(run, p, study, shape)
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
    # measured 2026-11-18: hex8 -0.265 %, tet4 -0.098 %, tet10 -0.227 % (CLAUDE.md: within
    # 0.27 %)
    assert behind.mean() == pytest.approx(expected, rel=0.004)
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
        # +2.70 % on both meshes, measured 2026-11-18 (CLAUDE.md: +2.7 %): the frame
        # after the separation, 60 frames over 1.5 x 2 L / c
        assert separated == pytest.approx(contact, rel=0.03)
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
    # measured 2026-11-18: peak -0.18 %, worst 0.28 % of it over the swing (CLAUDE.md:
    # within 0.3 %)
    assert explicit[:, 1].max() == pytest.approx(peak, rel=0.004)
    assert worst < 0.004 * peak


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


def _slow_pull(tmp_path, solver, **material):
    """The bar's end pulled at 1 m/s, its speed ramped over 25 wave transits, to about
    ten times the yield strain: the middle's stress, plastic strain and strain rate (the
    slope of the velocity along it: the pull isn't uniform, so not v / L)."""
    from puzzle.model.schema import Amplitude

    c = math.sqrt(E / RHO)
    ramp = 25 * L / c
    end = 3 * ramp
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(1.0, "m/s")}),
    ]
    p, study = _bar(solver, features, {"end_time": end, "frames": 10}, **material)
    amp = p.add(Amplitude(name="ramp", points=[(0.0, 0.0), (ramp, 1.0), (end, 1.0)]))
    p.objects[study.component].physics[0].features[1].amplitude = amp.id
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1].fields
    x = run.mesh.node_coords(last["stress"].ids)[:, 0]
    middle = (x > 0.1 * L) & (x < 0.9 * L)
    v = last["velocity"]
    vx = v.values[:, 0]
    xv = run.mesh.node_coords(v.ids)[:, 0]
    inside = (xv > 0.1 * L) & (xv < 0.9 * L)
    rate = float(np.polyfit(xv[inside], vx[inside], 1)[0])  # dv/dx: the strain rate there
    return (last["stress"].values[middle, 0], last["plastic_strain_equivalent"].at(
        last["stress"].ids[middle])[:, 0], rate)  # fmt: skip


@over("opencourant")
def test_johnson_cook_hardening_follows_a_plus_b_strain_to_the_n(tmp_path, solver):
    from puzzle.model.schema import JohnsonCook

    a, b, n = 250e6, 500e6, 0.5
    stress, eps, _rate = _slow_pull(tmp_path, solver,
                                    johnson_cook=JohnsonCook(a=a, b=b, n=n))  # fmt: skip
    expected = a + b * eps**n
    print(f"Johnson-Cook: sigma {stress.mean() / 1e6:.5g} MPa at eps_p {eps.mean():.4g}; "
          f"A + B eps^n {expected.mean() / 1e6:.5g} MPa")  # fmt: skip
    assert eps.mean() > 0.002
    assert stress.mean() == pytest.approx(expected.mean(), rel=0.02)


@over("opencourant")
def test_johnson_cook_rate_term_raises_the_flow_stress(tmp_path, solver):
    from puzzle.model.schema import JohnsonCook

    a, cc, ref = 250e6, 0.05, 1e-3
    # The bar rings axially at c / 2 L = 12.6 kHz; the law reads the rate's swings too
    # (+4 % unfiltered), so the rate is filtered below it (5 kHz) to read the mean rate.
    stress, _eps, rate = _slow_pull(tmp_path, solver, johnson_cook=JohnsonCook(
        a=a, c=cc, reference_strain_rate=ref, rate_cutoff=5e3))  # fmt: skip
    expected = a * (1 + cc * math.log(rate / ref))
    print(f"Johnson-Cook rate: sigma {stress.mean() / 1e6:.5g} MPa at {rate:g}/s; "
          f"A (1 + C ln(rate / rate0)) {expected / 1e6:.5g} MPa")  # fmt: skip
    assert stress.mean() == pytest.approx(expected, rel=0.03)


@over("opencourant")
def test_a_bar_pulled_past_its_failure_strain_breaks(tmp_path, solver):
    """Failure and erosion (ADR 0085 step 5): the slow pull with a failure strain D1 =
    0.005, below the ~0.011 the pull reaches, on linear hardening (H = E / 10: with
    ν = 0 nothing necks, so the strain stays uniform; on a flat curve it gathers where
    yield comes first and the bar broke at 0.69 mm of the 1.25 mm uniform). It breaks
    when its end has moved L ((σy + H D1) / E + D1): the first frame with eroded
    elements comes just past that, and every element left has a plastic strain under
    D1."""
    from puzzle.model.schema import Amplitude, Failure

    yield_stress, d1, hardening = 250e6, 0.005, E / 10
    c = math.sqrt(E / RHO)
    ramp = 25 * L / c
    end = 3 * ramp
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(1.0, "m/s")}),
    ]
    p, study = _bar(solver, features, {"end_time": end, "frames": 60},
                    plastic=[(0.0, yield_stress), (1.0, yield_stress + hardening)])  # fmt: skip
    p.objects[p.objects[study.component].materials[0].material].models.failure = Failure(strain=d1)
    amp = p.add(Amplitude(name="ramp", points=[(0.0, 0.0), (ramp, 1.0), (end, 1.0)]))
    p.objects[study.component].physics[0].features[1].amplitude = amp.id
    run = _run(tmp_path, p, study)

    def moved(t):  # the end's displacement: the ramp's area, then 1 m/s
        return 0.5 * min(t, ramp) ** 2 / ramp + max(0.0, t - ramp)

    breaks = L * ((yield_stress + hardening * d1) / E + d1)
    rows = []
    for s in run.results.steps:
        eroded = s.fields.get("eroded_elements")
        rows.append((s.value, moved(s.value), eroded.values[0, 0] if eroded else 0.0))
    first = next((r for r in rows if r[2] > 0), None)
    before = [r for r in rows if r[2] == 0]
    print(f"breaks at an end displacement of {breaks * 1e3:.4g} mm (uniform); last whole frame "
          f"{before[-1][1] * 1e3:.4g} mm, first broken {first and first[1] * 1e3:.4g} mm "
          f"({first and int(first[2])} elements gone)")  # fmt: skip
    assert first is not None
    assert before[-1][1] < breaks * 1.03 and first[1] > breaks * 0.97
    last = run.results.steps[-1].fields
    left = last["plastic_strain_equivalent"].values[:, 0]
    print(f"plastic strain left: largest {left.max():.4g} (D1 {d1:g})")
    assert left.max() < d1 * 1.05


def _two_bars(solver, features, settings, contact, *, gap=0.0, sizes=(5.0, 5.0)):
    """Bars A (0..L/2) and B (L/2 + gap..L + gap) along x, each its own part and mesh
    size, joined by `contact` (from A's +x face to B's -x face)."""
    from puzzle.model.schema import Contact

    pytest.importorskip("OCP")
    p = Project(name="two")
    steel = add_library_material(p, "structural_steel")
    steel.properties |= {"youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.0, "dimensionless"),
                         "density": q(RHO, "kg/m^3")}  # fmt: skip
    steel.tables, steel.models = {}, MaterialModels()
    parts, materials = [], []
    for name, x0 in (("A", 0.0), ("B", L / 2 + gap)):  # sizes: the recipe and a control
        geo = p.add(Geometry(name=name, history=[HistoryStep(kind="box", name="B", args={
            "dx": L / 2, "dy": W, "dz": W, "x0": x0})], selections=[
            NamedSelection(name="Body", entity="body", members=["Body"]),
            NamedSelection(name="Start", entity="face", members=["B/-x"]),
            NamedSelection(name="End", entity="face", members=["B/+x"])]))  # fmt: skip
        parts.append(PartInstance(name=name, geometry=geo.id))
        materials.append(MaterialAssignment(material=steel.id, selection=f"{name}/Body"))
    mesh = p.add(MeshRecipe(name="M", global_size=q(sizes[0], "mm"), element_shape="hexahedra",
                            element_order=1))  # fmt: skip
    if sizes[1] != sizes[0]:
        from puzzle.model.schema import SizeControl

        mesh.controls = [SizeControl(selection="B/Body", size=q(sizes[1], "mm"))]
    comp = p.add(Component(name="C", parts=parts, mesh=mesh.id, materials=materials,
                           contacts=[Contact(name="joint", source="A/End", target="B/Start",
                                             **contact)],
                           physics=[Physics(type="solid_mechanics", settings={
                               "temperature_dependent_materials": False},
                               features=features)]))  # fmt: skip
    study = p.add(Study(name="Two", component=comp.id, solver=solver,
                        steps=[SolveStep(type="explicit", settings=settings)]))  # fmt: skip
    return p, study


@over("opencourant")
def test_two_equal_bars_colliding_exchange_their_velocities(tmp_path, solver):
    v0, gap = 10.0, 1e-4
    c = math.sqrt(E / RHO)
    end = gap / v0 + 1.5 * 2 * (L / 2) / c  # touch, then 2 (L/2) / c in contact, then apart
    features = [Feature(type="initial_velocity", selection="A/Body", values={"vx": q(v0, "m/s")})]
    p, study = _two_bars(solver, features, {"end_time": end, "frames": 20},
                         {"type": "frictionless"}, gap=gap)  # fmt: skip
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1].fields["velocity"]
    x = run.mesh.node_coords(last.ids)[:, 0]
    a, b = last.values[x < L / 2 + 1e-9, 0].mean(), last.values[x > L / 2 + 1e-9, 0].mean()
    print(f"after: A {a:.4g} m/s, B {b:.4g} m/s (v0 {v0:g})")
    assert abs(a) < 0.05 * v0
    assert b == pytest.approx(v0, rel=0.05)


@over("opencourant")
@pytest.mark.parametrize("gap", [1e-4, 1e-3])
def test_a_self_contact_stops_a_part_passing_through_itself(tmp_path, solver, gap):
    """The colliding bars as one part of two bodies, with one contact on all its faces
    against themselves (ADR 0085 step 5): the bars exchange velocities as two parts do;
    with no contact they would pass through each other, B staying at rest. At 0.1 mm
    apart they start inside Radioss's own gap (0.33 mm here), so Puzzle sets a smaller
    one (`deck.self_gap`); at 1 mm they don't."""
    from puzzle.model.schema import Contact

    pytest.importorskip("OCP")
    v0 = 10.0
    c = math.sqrt(E / RHO)
    end = gap / v0 + 1.5 * 2 * (L / 2) / c
    p = Project(name="self")
    faces = [f"{b}/{s}" for b in "AB" for s in ("-x", "+x", "-y", "+y", "-z", "+z")]
    geo = p.add(Geometry(name="Bars", history=[
        HistoryStep(kind="box", name="A", args={"dx": L / 2, "dy": W, "dz": W, "body": "A"}),
        HistoryStep(kind="box", name="B", args={"dx": L / 2, "dy": W, "dz": W, "x0": L / 2 + gap,
                                                "mode": "add", "body": "B"})],
        selections=[NamedSelection(name="Bars", entity="body", members=["A", "B"]),
                    NamedSelection(name="A", entity="body", members=["A"]),
                    NamedSelection(name="Skin", entity="face", members=faces)]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    steel.properties |= {"youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.0, "dimensionless"),
                         "density": q(RHO, "kg/m^3")}  # fmt: skip
    steel.tables, steel.models = {}, MaterialModels()
    mesh = p.add(MeshRecipe(name="M", global_size=q(5.0, "mm"), element_shape="hexahedra",
                            element_order=1))  # fmt: skip
    comp = p.add(Component(name="C", parts=[PartInstance(name="S", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=steel.id,
                                                                       selection="S/Bars")],
                           contacts=[Contact(name="itself", type="frictionless",
                                             source="S/Skin", target="S/Skin")],
                           physics=[Physics(type="solid_mechanics", settings={
                               "temperature_dependent_materials": False}, features=[
                               Feature(type="initial_velocity", selection="S/A",
                                       values={"vx": q(v0, "m/s")})])]))  # fmt: skip
    study = p.add(Study(name="Self", component=comp.id, solver=solver,
                        steps=[SolveStep(type="explicit", settings={"end_time": end,
                                                                    "frames": 20})]))  # fmt: skip
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1].fields["velocity"]
    x = run.mesh.node_coords(last.ids)[:, 0]
    a, b = last.values[x < L / 2 + 1e-9, 0].mean(), last.values[x > L / 2 + 1e-9, 0].mean()
    print(f"self-contact {gap * 1e3:g} mm apart, after: A {a:.4g} m/s, B {b:.4g} m/s (v0 {v0:g})")
    assert abs(a) < 0.05 * v0
    assert b == pytest.approx(v0, rel=0.05)


@over("opencourant")
def test_a_bonded_split_bar_carries_the_wave_across_its_tie(tmp_path, solver):
    from solvers import available  # noqa: F401 - the same harness as the others

    v0 = 1.0
    c = math.sqrt(E / RHO)
    end = 0.75 * L / c  # the front past the tie (at L/2), three quarters along
    features = [
        Feature(type="fixed", selection="A/Start"),
        Feature(type="velocity", selection="B/End", values={"x": q(-v0, "m/s")}),
    ]
    p, study = _two_bars(
        solver,
        features,
        {"end_time": end, "frames": 6},
        {"type": "bonded", "meshing": "nonconformal"},
        sizes=(5.0, 4.0),
    )
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1]
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    front = L - c * last.value
    behind = stress.values[(x > front + 0.02) & (x < L / 2 - 0.01), 0]  # in A, past the tie
    expected = -RHO * c * v0
    print(f"behind the front in A: {behind.mean() / 1e6:.5g} MPa (rho c v0 "
          f"{expected / 1e6:.5g}), {len(behind)} nodes")  # fmt: skip
    assert len(behind) > 10
    assert behind.mean() == pytest.approx(expected, rel=0.02)


def _plate(solver, settings, *, order=1, size_mm=10.0, pressure=1000.0):
    """A clamped square steel plate (0.2 m, 2 mm thick, ν 0.3) with a pressure on it
    from t = 0, meshed in quadrilaterals."""
    from puzzle.model.schema import ShellSection

    pytest.importorskip("OCP")
    a = 0.2
    p = Project(name="plate")
    square = [[0, 0, 0], [a, 0, 0], [a, a, 0], [0, a, 0]]
    edges = {"X0": ("x", 0.0), "X1": ("x", a), "Y0": ("y", 0.0), "Y1": ("y", a)}
    geo = p.add(Geometry(name="Sheet", history=[HistoryStep(kind="sheet", name="P", args={
        "points": square})], selections=[
        NamedSelection(name="Top", entity="face", members=["P/face"]), *[
            NamedSelection(name=n, entity="edge", rule={"plane": {"axis": ax, "at": at}})
            for n, (ax, at) in edges.items()]]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    steel.properties |= {"youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.3, "dimensionless"),
                         "density": q(RHO, "kg/m^3")}  # fmt: skip
    steel.tables, steel.models = {}, MaterialModels()
    mesh = p.add(MeshRecipe(name="M", global_size=q(size_mm, "mm"), element_shape="hexahedra",
                            element_order=order))  # fmt: skip
    features = [Feature(type="fixed", selection=f"S/{n}") for n in edges]
    features.append(Feature(type="pressure", selection="S/Top", values={"p": q(pressure, "Pa")}))
    comp = p.add(Component(name="C", parts=[PartInstance(name="S", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=steel.id,
                                                                       selection="S")],
                           shell_sections=[ShellSection(selection="S", thickness=q(2, "mm"))],
                           physics=[Physics(type="solid_mechanics", settings={
                               "temperature_dependent_materials": False},
                               features=features)]))  # fmt: skip
    study = p.add(Study(name="Plate", component=comp.id, solver=solver,
                        steps=[SolveStep(type="explicit", settings=settings)]))  # fmt: skip
    return p, study, a


@over("opencourant")
def test_a_pressed_plate_against_an_implicit_transient(tmp_path, solver):
    """Shells (ADR 0085 step 2): a clamped plate's centre under a pressure applied at
    t = 0, OpenCourant's QEPH quadrilaterals against CalculiX's S8 shells (direct
    Newmark, no damping, T1/200 steps); the sign as well: a positive pressure pushes
    against the face's normal (+z), as on every solver."""
    from solvers import available

    if not available("calculix"):
        pytest.skip("CalculiX gives the implicit reference")
    d = E * 0.002**3 / (12 * (1 - 0.09))
    f1 = 35.99 / (2 * math.pi * 0.2**2) * math.sqrt(d / (RHO * 0.002))  # clamped square
    end = 0.75 / f1
    # 5 mm on both: CalculiX's S8 at 10 mm is 1.7 % stiff (at 5 mm it moves onto
    # OpenCourant's 10 mm answer, -2.866e-5 m at 1.187 ms, to four digits)
    ref, ref_study, a = _plate("calculix", {}, order=2, size_mm=5.0)
    ref_study.steps = [SolveStep(type="transient", settings={
        "time_step": 1 / f1 / 200, "end_time": end, "method": "direct"})]  # fmt: skip
    ref_run = _run(tmp_path / "ref", ref, ref_study)
    p, study, a = _plate(solver, {"end_time": end, "frames": 60}, size_mm=5.0)
    run = _run(tmp_path / "x", p, study)
    _estimate_matches(run, p, study, "shells")

    def centre(r):
        out = []
        for s in r.results.steps:
            u = s.fields.get("displacement")
            if u is None:
                continue
            xyz = r.mesh.node_coords(u.ids)
            k = int(np.argmin(np.linalg.norm(xyz - [a / 2, a / 2, 0], axis=1)))
            out.append((s.value, float(u.values[k, 2])))
        return np.array(sorted(out))

    implicit, explicit = centre(ref_run), centre(run)
    reference = np.interp(explicit[:, 0], implicit[:, 0], implicit[:, 1])
    peak = implicit[:, 1].min()  # downward: against +z
    worst = float(np.abs(explicit[:, 1] - reference).max())
    print(f"centre: explicit {explicit[:, 1].min():.5g} m, implicit {peak:.5g} m at its "
          f"lowest; largest difference {worst / abs(peak):.2%} of it")  # fmt: skip
    assert peak < 0
    assert explicit[:, 1].min() == pytest.approx(peak, rel=0.02)
    assert worst < 0.03 * abs(peak)
    # ADR 0090 OC-7: OpenCourant gives a shell's membrane stress only, a small part of a
    # bent plate's: the static edge bending stress 6 M / t², M = 0.0513 p a²
    # (Timoshenko & Woinowsky-Krieger, Table 35, ν 0.3), is 3.08 MPa; the membrane
    # stress peaked at 14 kPa (0.46 % of it). So it is `membrane_stress`, never `stress`.
    bending = 6 * 0.0513 * 1000.0 * a**2 / 0.002**2
    frames = [s for s in run.results.steps if "membrane_stress" in s.fields]
    assert frames and not any("stress" in s.fields for s in run.results.steps)
    membrane = max(np.abs(s.fields["membrane_stress"].values[:, :2]).max() for s in frames)
    print(f"membrane stress {membrane / 1e3:.4g} kPa at most, the edge bending stress "
          f"{bending / 1e6:.4g} MPa")  # fmt: skip
    assert membrane < 0.02 * bending


BETA_L = [1.8751041, 4.6940911, 7.8547574, 10.9955407] + [(2 * n - 1) * math.pi / 2
                                                          for n in range(5, 13)]  # fmt: skip


def _cantilever_tip(t, force, e, rho, area, inertia, length):
    """A cantilever's tip under a force switched on at t = 0 (Euler–Bernoulli, by its
    modes): each mode's φ(L)² = 4 when ∫φ² = L, so u = Σ 4 P / (ρ A L ωₙ²) (1 − cos ωₙ t),
    which sums to P L³ / 3 E I at rest."""
    base = math.sqrt(e * inertia / (rho * area * length**4))
    return sum(4 * force / (rho * area * length * (b**2 * base) ** 2)
               * (1 - np.cos(b**2 * base * t)) for b in BETA_L)  # fmt: skip


@over("opencourant")
@pytest.mark.parametrize("direction", ["z", "y"])
def test_a_beam_cantilever_swings_as_its_modes_say(tmp_path, solver, direction):
    """A 1 m cantilever of 20 two-node beams (10 mm along the section's first axis y,
    20 mm across), its tip force switched on at t = 0: the tip's history against the
    Euler–Bernoulli modal series (L/h = 50: shear and rotary inertia are under 0.1 %).
    Along z it bends about the first axis (I1 = w h³/12), along y about the second (I2,
    a quarter of it): the orientation is checked too (ADR 0085 step 5)."""
    from puzzle.model.schema import BeamSection

    pytest.importorskip("OCP")
    length, width, height, force = 1.0, 0.01, 0.02, 10.0
    p = Project(name="beam")
    plane = lambda at: {"plane": {"axis": "x", "at": at}}  # noqa: E731
    geo = p.add(Geometry(name="Beam", history=[HistoryStep(kind="polyline", name="L", args={
        "points": [[0, 0, 0], [length, 0, 0]]})], selections=[
        NamedSelection(name="Root", entity="vertex", rule=plane(0.0)),
        NamedSelection(name="Tip", entity="vertex", rule=plane(length))]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    steel.properties |= {"youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.3, "dimensionless"),
                         "density": q(RHO, "kg/m^3")}  # fmt: skip
    steel.tables, steel.models = {}, MaterialModels()
    mesh = p.add(MeshRecipe(name="M", global_size=q(50, "mm"), element_order=1))
    comp = p.add(Component(name="C", parts=[PartInstance(name="F", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=steel.id,
                                                                       selection="F")],
                           beam_sections=[BeamSection(selection="F", shape="rectangle",
                                                      width=q(10, "mm"), height=q(20, "mm"),
                                                      orientation=(0.0, 1.0, 0.0))],
                           physics=[Physics(type="solid_mechanics", settings={
                               "temperature_dependent_materials": False}, features=[
                               Feature(type="fixed", selection="F/Root"),
                               Feature(type="force", selection="F/Tip",
                                       values={direction: q(force, "N")})])]))  # fmt: skip
    area = width * height
    inertia = width * height**3 / 12 if direction == "z" else height * width**3 / 12
    period = 2 * math.pi / (BETA_L[0] ** 2 * math.sqrt(E * inertia / (RHO * area * length**4)))
    end = 0.75 * period
    study = p.add(Study(name="Swing", component=comp.id, solver=solver,
                        steps=[SolveStep(type="explicit", settings={"end_time": end,
                                                                    "frames": 60})]))  # fmt: skip
    run = _run(tmp_path, p, study)
    _estimate_matches(run, p, study, "beams")
    k = "xyz".index(direction)
    history = []
    for s in run.results.steps:
        d = s.fields["displacement"]
        at = np.abs(run.mesh.node_coords(d.ids)[:, 0] - length) < 1e-9
        history.append((s.value, float(d.values[at, k].mean())))
    history = np.array(sorted(history))
    expected = _cantilever_tip(history[:, 0], force, E, RHO, area, inertia, length)
    peak = expected.max()
    worst = float(np.abs(history[:, 1] - expected).max())
    print(f"beam along {direction}: tip peak {history[:, 1].max():.5g} m, modes {peak:.5g} m "
          f"(static {force * length**3 / (3 * E * inertia):.5g}); largest difference "
          f"{worst / peak:.2%} of the peak")  # fmt: skip
    assert history[:, 1].max() == pytest.approx(peak, rel=0.02)
    assert worst < 0.03 * peak


@over("opencourant")
def test_a_pressure_on_a_bar_end_sends_minus_p_down_it(tmp_path, solver):
    """A pressure pushes into the face (every solver's sign): p switched on at a free bar
    end sends a compressive wave of −p down it (σ = −p behind the front)."""
    pressure = 10e6
    c = math.sqrt(E / RHO)
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="pressure", selection="S/End", values={"p": q(pressure, "Pa")}),
    ]
    p, study = _bar(solver, features, {"end_time": 0.75 * L / c, "frames": 6})
    run = _run(tmp_path, p, study)
    last = run.results.steps[-1]
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    behind = stress.values[x > L - c * last.value + 0.02, 0]
    print(f"sigma_xx behind the front {behind.mean() / 1e6:.5g} MPa (-p {-pressure / 1e6:g})")
    assert behind.mean() == pytest.approx(-pressure, rel=0.02)


@over("opencourant")
def test_two_processes_give_the_one_process_wave(tmp_path, solver):
    """MPI (ADR 0085 step 3): the Starter splits the bar into two domains and the Engine
    runs under the image's OpenMPI; the wave is the same."""
    from puzzle import settings

    settings.current().set("solvers.processes", 2)  # reset after each test (conftest)
    from puzzle.jobs.runtime import process_count

    if process_count() < 2:
        pytest.skip("fewer than two cores per run here")
    c = math.sqrt(E / RHO)
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(-1.0, "m/s")}),
    ]
    p, study = _bar(solver, features, {"end_time": 0.75 * L / c, "frames": 6})
    run = _run(tmp_path, p, study)
    script = (tmp_path / "w" / "run.sh").read_text(encoding="utf-8")
    assert "-np 2" in script and "mpiexec -np 2 engine_ompi" in script
    last = run.results.steps[-1]
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    behind = stress.values[x > L - c * last.value + 0.02, 0]
    print(f"two processes: sigma_xx behind {behind.mean() / 1e6:.5g} MPa")
    assert behind.mean() == pytest.approx(-RHO * c, rel=0.01)


def test_a_native_run_from_the_release_gives_the_wave(tmp_path, monkeypatch):
    """ADR 0085 step 3: no container; the release unpacked (here PUZZLE_OPENCOURANT_PATH
    or a fetched one) run by the native driver in Puzzle's Python."""
    from puzzle.adapters.opencourant import native

    if native.root() is None:
        pytest.skip("no OpenCourant release unpacked here (PUZZLE_OPENCOURANT_PATH)")
    monkeypatch.setenv("PUZZLE_OPENCOURANT_RUNTIME", "native")
    c = math.sqrt(E / RHO)
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(-1.0, "m/s")}),
    ]
    p, study = _bar("opencourant", features, {"end_time": 0.75 * L / c, "frames": 6})
    run = _run(tmp_path, p, study)
    log = (tmp_path / "w" / "solver.log").read_text(encoding="utf-8", errors="replace")
    assert "NORMAL TERMINATION" in log
    last = run.results.steps[-1]
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    behind = stress.values[x > L - c * last.value + 0.02, 0]
    print(f"native: sigma_xx behind {behind.mean() / 1e6:.5g} MPa")
    assert behind.mean() == pytest.approx(-RHO * c, rel=0.01)


def _lumped(run) -> tuple[np.ndarray, np.ndarray]:
    """Each node's share of the mass (hex8: an eighth of each element's) and its id:
    the mass-weighted mean velocity is the centre of mass's."""
    blocks = [b for b in run.mesh.blocks if b.dim == 3]
    ids = run.mesh.node_ids
    share = np.zeros(len(ids))
    index = {int(n): i for i, n in enumerate(ids)}
    for b in blocks:
        for row in b.connectivity:
            for n in row[:8]:
                share[index[int(n)]] += 1.0
    return ids, share / share.sum()


@over("opencourant")
def test_free_bars_move_as_their_loads_and_amplitudes_say(tmp_path, solver):
    """A free bar (ADR 0090 OC-3: gravity, acceleration and amplitudes on loads had no
    benchmark): gravity along z, an acceleration along y (inertia: the body moves at -a),
    a force along x on its end and a pressure on its start (pushing into it, so +x), each
    ramped over T. By momentum, the centre of mass moves at vz = -g t² / 2T,
    vy = -a t² / 2T, vx = (F + p A) t² / (2 T m) within the ramp, each
    exact whatever the bar rings with. Central differences keep velocities half a time
    step behind (0.5 % at the first frame, 0.1 % by the last), so the frames from 0.4 T
    on are checked."""
    from puzzle.model.schema import Amplitude

    g, a, force, pressure = 9.81, 4.0, 50.0, 3e5
    ramp = 1e-3
    features = [
        Feature(type="gravity", selection="S/Body", values={"gz": q(-g, "m/s^2")}),
        Feature(type="acceleration", selection="S/Body", values={"ay": q(a, "m/s^2")}),
        Feature(type="force", selection="S/End", values={"x": q(force, "N")}),
        Feature(type="pressure", selection="S/Start", values={"p": q(pressure, "Pa")}),
    ]
    p, study = _bar(solver, features, {"end_time": ramp, "frames": 5})
    amp = p.add(Amplitude(name="ramp", points=[(0.0, 0.0), (ramp, 1.0)]))
    for f in p.objects[study.component].physics[0].features:
        f.amplitude = amp.id
    run = _run(tmp_path, p, study)
    ids, weight = _lumped(run)
    mass = RHO * L * W * W
    checked = 0
    for s in run.results.steps[2:]:
        t = s.value
        v = weight @ s.fields["velocity"].at(ids)  # the centre of mass's velocity
        push = force + pressure * W * W
        expected = np.array([push, -a * mass, -g * mass]) * t**2 / (2 * ramp * mass)
        print(f"t {t:.4g} s: v {v} ({expected})")
        assert v == pytest.approx(expected, rel=3e-3)
        checked += 1
    assert checked >= 3


@over("opencourant")
@pytest.mark.parametrize("name", ["ramp", "#1 ramp"])
def test_a_ramped_end_displacement_follows_its_amplitude(tmp_path, solver, name):
    """A prescribed displacement following an amplitude (ADR 0090 OC-3): the end of a
    bar held at its start is moved u_max · f(t), f ramping 0 to 1 over 25 wave transits;
    the end's displacement is that at each frame, and, the pull being slow, the bar's
    stress at the end is the static E u / L. Named `#1 ramp`, the /FUNCT's title had
    been read as a comment and its first point as the title: the ramp became a step
    (ADR 0090 OC-1)."""
    from puzzle.model.schema import Amplitude

    c = math.sqrt(E / RHO)
    ramp, u_max = 25 * L / c, 0.1e-3
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="displacement", selection="S/End", values={"x": q(u_max, "m")}),
    ]
    end = 1.5 * ramp
    p, study = _bar(solver, features, {"end_time": end, "frames": 6})
    amp = p.add(Amplitude(name=name, points=[(0.0, 0.0), (ramp, 1.0), (end, 1.0)]))
    p.objects[study.component].physics[0].features[1].amplitude = amp.id
    run = _run(tmp_path, p, study)
    tip = run.mesh.groups["S/End"].node_ids
    for s in run.results.steps[1:]:
        got = float(np.mean(s.fields["displacement"].at(tip)[:, 0]))
        print(f"t {s.value:.4g} s: end at {got:.6g} m ({u_max * amp.at(s.value):.6g})")
        assert got == pytest.approx(u_max * amp.at(s.value), rel=1e-3, abs=1e-9)
    last = run.results.steps[-1].fields
    x = run.mesh.node_coords(last["stress"].ids)[:, 0]
    middle = last["stress"].values[(x > 0.2 * L) & (x < 0.8 * L), 0]
    print(f"sigma_xx in the middle {middle.mean() / 1e6:.5g} MPa (E u / L "
          f"{E * u_max / L / 1e6:.5g})")  # fmt: skip
    assert middle.mean() == pytest.approx(E * u_max / L, rel=0.03)


@over("opencourant")
def test_a_block_sliding_on_a_rough_rigid_wall_slows_at_mu_g(tmp_path, solver):
    """A rigid wall's friction (ADR 0090 OC-3, ADR-64: claimed, never solved): a steel
    block 20 × 10 × 10 mm resting on a wall at z = 0 with μ = 0.3, thrown along x at
    1 m/s under 1000 m/s² of gravity (g × 100, so the slowing shows within 2 ms). By
    momentum its centre of mass slows at μ g: v = v0 − μ g t, whatever it rings with.
    Measured 2026-11-17: within 5e-4 m/s at every frame (0.1 % of v0)."""
    g, mu, v0, end = 1000.0, 0.3, 1.0, 2e-3
    features = [
        Feature(type="initial_velocity", selection="S/Body", values={"vx": q(v0, "m/s")}),
        Feature(type="gravity", selection="S/Body", values={"gz": q(-g, "m/s^2")}),
        Feature(type="rigid_wall", selection="S/Body", values={
            "z0": q(-1e-6, "mm"), "nz": q(1.0, "dimensionless"), "mu": q(mu, "dimensionless")}),
    ]  # fmt: skip
    p, study = _bar(solver, features, {"end_time": end, "frames": 10}, length=0.02, nu=0.3)
    run = _run(tmp_path, p, study)
    ids, weight = _lumped(run)
    for s in run.results.steps[1:]:
        v = weight @ s.fields["velocity"].at(ids)
        print(f"t {s.value:.4g} s: vx {v[0]:.6g} m/s (v0 - mu g t {v0 - mu * g * s.value:.6g})")
        assert v[0] == pytest.approx(v0 - mu * g * s.value, abs=2e-3 * v0)
    assert run.results.steps[-1].value == pytest.approx(end, rel=0.01)


@over("opencourant")
def test_mass_scaling_holds_its_time_step_and_slows_the_wave(tmp_path, solver):
    """`mass_scaling_time_step` (ADR 0090 OC-3, ADR-64: claimed, never solved): the bar
    of the wave test, its target twice its stable step. The Engine runs at the target,
    and the mass it adds makes the bar's impedance √(E ρ M / M0), M the mass with what
    was added, M0 the bar's own: the stress behind the front driven at v0 is
    v0 √(E ρ M / M0) (√(E ρ) v0 without scaling). Measured 2026-11-17: the step is the
    target to seven digits; added 3.42 times the bar's mass; the stress −0.14 %."""
    from puzzle import explicit
    from puzzle.mesh import generate_mesh

    v0 = 1.0
    c = math.sqrt(E / RHO)
    end = 1.5 * L / c  # the slowed front about three quarters of the way
    features = [
        Feature(type="fixed", selection="S/Start"),
        Feature(type="velocity", selection="S/End", values={"x": q(-v0, "m/s")}),
    ]
    p, study = _bar(solver, features, {"end_time": end, "frames": 6})
    save_project(p, tmp_path / "m")
    mesh = generate_mesh(p, study.component, tmp_path / "m", tmp_path / "mesh").mesh
    est = explicit.estimate(p, p.objects[study.component], mesh, end)
    target = 2 * est.time_step * est.scale
    study.steps[0].settings["mass_scaling_time_step"] = target
    run = _run(tmp_path, p, study)
    steps = [s.fields["time_step"].values[0, 0] for s in run.results.steps[1:]]
    last = run.results.steps[-1]
    whole, added = (last.fields[k].values[0, 0] for k in ("mass", "added_mass"))
    own = RHO * L * W * W
    assert whole - added == pytest.approx(own, rel=1e-6)
    stress = last.fields["stress"]
    x = run.mesh.node_coords(stress.ids)[:, 0]
    slowed = c * math.sqrt(own / whole)
    behind = stress.values[x > L - slowed * last.value + 0.02, 0]
    expected = -v0 * math.sqrt(E * RHO * whole / own)
    print(f"time step {np.median(steps):.7g} s (target {target:.7g}); added {added / own:.3g} "
          f"times the mass; sigma_xx behind {behind.mean() / 1e6:.5g} MPa "
          f"({expected / 1e6:.5g})")  # fmt: skip
    assert np.median(steps) == pytest.approx(target, rel=1e-5)
    assert added > 0.5 * own
    assert behind.mean() == pytest.approx(expected, rel=0.02)
