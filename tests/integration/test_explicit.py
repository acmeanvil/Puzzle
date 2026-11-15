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
