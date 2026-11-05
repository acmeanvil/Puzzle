"""2D dynamics (ADR 0048 with ADR 0041): modes, a sudden load and a harmonic response
of planar and axisymmetric models, against closed forms, on every solver.

Generic structural steel, constant properties, c = √(E/ρ):

- **axial modes of a bar**, plane stress: a 200 x 10 mm strip, its left end held along
  x and its bottom a line of symmetry (y held), so only the symmetric (axial) modes
  are found: f_n = (2n - 1) c / 4L, lowered by lateral inertia (Rayleigh–Love) by
  ν² k² r² / 2, k = (2n - 1) π / 2L and r² = (2H)² / 12 (0.25 % in the third mode); in
  plane strain the same with c = √(E / ρ (1 - ν²)) and ν / (1 - ν) for ν;
- **a cantilever's first bending mode**, plane stress: 200 x 10 mm clamped at the left,
  f₁ = 1.8751² / 2π √(E I / ρ A L⁴) with I / A = H² / 12 (Euler–Bernoulli; Timoshenko's
  shear and rotary inertia lower it by about 0.3 % at L/H = 20);
- **a ring's breathing mode**, axisymmetric: a 4 x 4 mm section at R = 100 mm, its
  bottom a plane of symmetry: f = c / 2πR (Love; the section's size changes it by
  (a/R)² ~ 0.1 %);
- **a suddenly applied end load** on the plane-stress bar: the end moves as a triangle
  wave, peaking at 2 F L / E A at t = 2L / c;
- **a harmonic end load** on the plane-stress bar and on an axisymmetric rod (a solid
  cylinder of radius 5 mm, held axially at its root): u(L) = F tan(kL) / (E A k),
  k = ω / c.
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
E, NU, RHO = (STEEL[k].to_si() for k in ("youngs_modulus", "poissons_ratio", "density"))
C = math.sqrt(E / RHO)
L, H, T = 0.2, 0.01, 0.002  # the strip: length, height, thickness
CONSTANT = {"temperature_dependent_materials": False}
FOUR = ("calculix", "code_aster", "fenicsx", "elmer")
# Sides of a rectangle face: side-1 bottom (y = y0), side-2 right, side-3 top, side-4 left.
SIDES = {"Bottom": ["S/side-1"], "Right": ["S/side-2"], "Top": ["S/side-3"], "Left": ["S/side-4"]}


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def mm(v):
    return qty(v, "mm")


def _project(x0, y0, w, h, dimension, features, size, **comp):
    rect = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]]
    steps = [HistoryStep(kind="face", name="S", args={"profile": {"points": rect}})]
    p = Project(name="2d dynamics")
    selections = [NamedSelection(name="Region", entity="face", members=["S/face"])]
    selections += [NamedSelection(name=n, entity="edge", members=m) for n, m in SIDES.items()]
    geo = p.add(Geometry(name="Part", history=steps, selections=selections))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=mm(size)))
    physics = [Physics(type="solid_mechanics", features=features, settings=CONSTANT)]
    c = p.add(Component(name="Part", dimension=dimension, mesh=mesh.id,
                        parts=[PartInstance(name="Part", geometry=geo.id)],
                        materials=[MaterialAssignment(material=steel.id, selection="Part/Region")],
                        physics=physics, **comp))  # fmt: skip
    return p, c


def _strip(features, size=2.5, model="plane_stress"):
    sections = [ShellSection(selection="Part", thickness=mm(T * 1e3))]
    return _project(0, 0, L, H, "2d_planar", features, size, planar_model=model,
                    shell_sections=sections if model == "plane_stress" else [])  # fmt: skip


def _solve(tmp_path, p, c, solver, step):
    study = p.add(Study(name="s", component=c.id, solver=solver, steps=[step]))
    return run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900)


def _frequencies(run):
    return sorted(s.value for s in run.results.steps if s.kind == "modal")


SYMMETRIC_BAR = [
    Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
    Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
]


@over(*FOUR)
@pytest.mark.parametrize("model", ["plane_stress", "plane_strain"])
def test_axial_modes_of_a_bar(tmp_path, solver, model):
    p, c = _strip(SYMMETRIC_BAR, model=model)
    run = _solve(tmp_path, p, c, solver, SolveStep(type="modal", settings={"modes": 3}))
    stress = model == "plane_stress"
    wave, nu = (C, NU) if stress else (C / math.sqrt(1 - NU**2), NU / (1 - NU))
    r2 = (2 * H) ** 2 / 12  # the whole strip's (its bottom is a line of symmetry)
    want = [(2 * n - 1) * wave / (4 * L) * (1 - nu**2 * ((2 * n - 1) * math.pi / (2 * L)) ** 2
            * r2 / 2) for n in (1, 2, 3)]  # fmt: skip
    got = _frequencies(run)[:3]
    print(f"{solver} {model}: {[f'{f:.1f}' for f in got]} Hz (theory "
          f"{[f'{f:.1f}' for f in want]})")  # fmt: skip
    assert got == pytest.approx(want, rel=2e-3)


@over(*FOUR)
def test_cantilever_first_bending_mode(tmp_path, solver):
    p, c = _strip([Feature(type="fixed", selection="Part/Left")], size=2.5)
    run = _solve(tmp_path, p, c, solver, SolveStep(type="modal", settings={"modes": 2}))
    want = 1.875104069**2 / (2 * math.pi) * C * H / math.sqrt(12) / L**2
    got = _frequencies(run)[0]
    print(f"{solver}: first bending {got:.2f} Hz (Euler–Bernoulli {want:.2f})")
    assert got == pytest.approx(want * 0.997, rel=6e-3)  # Timoshenko: about 0.3 % lower


@over(*FOUR)
def test_ring_breathing_mode(tmp_path, solver):
    R, a = 0.1, 0.004
    held = [Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)})]
    p, c = _project(R - a / 2, 0, a, a / 2, "2d_axisymmetric", held, size=0.5)
    run = _solve(tmp_path, p, c, solver, SolveStep(type="modal", settings={"modes": 2}))
    want = C / (2 * math.pi * R)
    got = _frequencies(run)[0]
    print(f"{solver}: breathing {got:.1f} Hz (thin ring {want:.1f})")
    assert got == pytest.approx(want, rel=3e-3)


@over(*FOUR)
def test_sudden_end_load(tmp_path, solver):
    F = 1000.0
    force = Feature(type="force", selection="Part/Right", values={"x": qty(F, "N")})
    p, c = _strip([*SYMMETRIC_BAR, force], size=2.5)
    period = 4 * L / C
    step = SolveStep(type="transient", physics=["solid_mechanics"],
                     settings={"time_step": period / 200, "end_time": 0.75 * period})  # fmt: skip
    run = _solve(tmp_path, p, c, solver, step)
    tip = run.mesh.groups["Part/Right"].node_ids
    t, u = np.array([
        (s.value, s.fields["displacement"].at(tip)[:, 0].mean())
        for s in run.results.steps if "displacement" in s.fields and s.value > 0
    ]).T  # fmt: skip
    peak = 2 * F * L / (E * H * T)
    print(f"{solver}: peak {u.max():.4g} m at {t[u.argmax()] / period:.3f} T "
          f"(theory {peak:.4g} m at 0.5 T)")  # fmt: skip
    assert u.max() == pytest.approx(peak, rel=2e-2)
    assert t[u.argmax()] / period == pytest.approx(0.5, abs=0.02)
    # The rise is linear: the end moves at F / (ρ c A) until the wave comes back.
    early = (t > 0.1 * period) & (t < 0.4 * period)
    slope = np.polyfit(t[early], u[early], 1)[0]
    assert slope == pytest.approx(F / (RHO * C * H * T), rel=3e-2)


def _harmonic_end(run, group, axis):
    ids = run.mesh.groups[group].node_ids
    return [(s.value, s.fields["displacement_amplitude"].at(ids)[:, axis].mean())
            for s in run.results.steps if s.kind == "frequency_domain"]  # fmt: skip


def _tan_response(f, F, area, length):
    k = 2 * math.pi * f / C
    return abs(F * math.tan(k * length) / (E * area * k))


@over(*FOUR)
def test_harmonic_end_load_plane_stress(tmp_path, solver):
    F = 1000.0
    force = Feature(type="force", selection="Part/Right", values={"x": qty(F, "N")})
    p, c = _strip([*SYMMETRIC_BAR, force], size=2.5)
    f1 = C / (4 * L)
    hz = [0.5 * f1, 1.5 * f1]
    settings = {"frequencies": ", ".join(f"{f:.3f}" for f in hz)}
    # CalculiX superposes modes, and an end load's share falls off as 1/n² over the
    # axial ones: 20 modes (10 axial) are 1.5 % short, 200 within 1.2 %.
    tolerance = 1.5e-2 if solver == "calculix" else 1e-2
    if solver == "calculix":
        settings["modes"] = 200
    step = SolveStep(type="frequency_domain", physics=["solid_mechanics"], settings=settings)
    run = _solve(tmp_path, p, c, solver, step)
    for f, amp in _harmonic_end(run, "Part/Right", 0):
        want = _tan_response(f, F, H * T, L)
        print(f"{solver} {f:.1f} Hz: {amp:.5g} m (theory {want:.5g})")
        assert amp == pytest.approx(want, rel=tolerance)


@over(*FOUR)
def test_harmonic_end_load_axisymmetric_rod(tmp_path, solver):
    """A solid rod along the axis Y: y held at its root (y = 0), 1 kN axially at its end."""
    r, length, F = 0.005, 0.2, 1000.0
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="force", selection="Part/Top", values={"y": qty(F, "N")}),
    ]
    p, c = _project(0, 0, r, length, "2d_axisymmetric", features, size=2.5)
    f1 = C / (4 * length)
    step = SolveStep(type="frequency_domain", physics=["solid_mechanics"],
                     settings={"frequencies": f"{0.5 * f1:.3f}"})  # fmt: skip
    run = _solve(tmp_path, p, c, solver, step)
    for f, amp in _harmonic_end(run, "Part/Top", 1):
        want = _tan_response(f, F, math.pi * r * r, length)
        print(f"{solver} {f:.1f} Hz: {amp:.5g} m (theory {want:.5g})")
        assert amp == pytest.approx(want, rel=1e-2)


@over("calculix")
def test_calculix_refuses_an_oblique_edge_force_over_modes(tmp_path, solver):
    """Over modes CalculiX keeps edge pressures only: a force square to the edge becomes
    one (above), an oblique one is refused by name."""
    oblique = Feature(type="force", selection="Part/Right",
                      values={"x": qty(1000, "N"), "y": qty(100, "N")})  # fmt: skip
    p, c = _strip([*SYMMETRIC_BAR, oblique])
    step = SolveStep(type="frequency_domain", physics=["solid_mechanics"],
                     settings={"frequencies": "1000"})  # fmt: skip
    with pytest.raises(Exception, match="loses point loads on 2D elements"):
        _solve(tmp_path, p, c, solver, step)


# --- body loads in 2D (ADR 0037 with ADR 0048) ---------------------------------------


@over(*FOUR)
def test_rotating_thin_disc_axisymmetric(tmp_path, solver):
    """A solid disc b = 100 mm, 2 mm thick (half of it, its mid-plane held axially),
    spun at 10 000 rpm about the axis Y: plane stress, σr = σθ = (3 + ν) / 8 ρ ω² b² at
    the centre, σθ = (1 - ν) / 4 ρ ω² b² at the rim (Timoshenko; the thickness's
    effect is of order (h / b)²)."""
    b, h, rpm = 0.1, 0.002, 10000.0
    omega = rpm * 2 * math.pi / 60
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="rotation", selection="Part/Region",
                values={"omega": qty(rpm, "rpm"), "ny": qty(1, "dimensionless")}),
    ]  # fmt: skip
    p, c = _project(0, 0, b, h / 2, "2d_axisymmetric", features, size=1.0)
    run = _solve(tmp_path, p, c, solver, SolveStep(type="stationary"))
    s = run.results.last("stress")
    xyz = run.mesh.node_coords(s.ids)
    centre = xyz[:, 0] < 0.02 * b
    rim = xyz[:, 0] > b * (1 - 1e-6)
    q = RHO * omega**2 * b**2
    hub, hoop = s.values[centre][:, 0].mean(), s.values[rim][:, 2].mean()
    print(f"{solver}: centre σr {hub / 1e6:.3f} MPa (theory {(3 + NU) / 8 * q / 1e6:.3f}), "
          f"rim σθ {hoop / 1e6:.3f} MPa (theory {(1 - NU) / 4 * q / 1e6:.3f})")  # fmt: skip
    assert hub == pytest.approx((3 + NU) / 8 * q, rel=1e-2)  # nodes within 2 % of b
    assert hoop == pytest.approx((1 - NU) / 4 * q, rel=1e-2)


@over(*FOUR)
def test_hanging_strip_under_gravity(tmp_path, solver):
    """A plane-stress strip 200 mm long hanging from its top (y = L) under 1000 g: its
    bottom moves ρ g L² / 2E and σy at the top is ρ g L (Bottom free, Top held in y,
    the left edge a line of symmetry)."""
    g = 9810.0
    features = [
        Feature(type="displacement", selection="Part/Top", values={"y": mm(0)}),
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="gravity", selection="Part/Region",
                values={"gx": qty(0, "m/s^2"), "gy": qty(-g, "m/s^2"), "gz": qty(0, "m/s^2")}),
    ]  # fmt: skip
    sections = [ShellSection(selection="Part", thickness=mm(T * 1e3))]
    p, c = _project(0, 0, H, L, "2d_planar", features, size=2.5, planar_model="plane_stress",
                    shell_sections=sections)  # fmt: skip
    run = _solve(tmp_path, p, c, solver, SolveStep(type="stationary"))
    u = run.results.last("displacement").at(run.mesh.groups["Part/Bottom"].node_ids)[:, 1]
    want = -RHO * g * L**2 / (2 * E)
    print(f"{solver}: bottom {u.mean():.5g} m (theory {want:.5g})")
    assert u.mean() == pytest.approx(want, rel=5e-3)


# --- a temperature load in 2D (ADR 0009 with ADR 0048) ---------------------------------


def _heated(tmp_path, p, c, solver, edges):
    """The component at a uniform 120 °C (the edges held there) from a 20 °C reference:
    a heat study, its temperature carried to the static study by a transfer."""
    from puzzle.model import Transfer

    hot = {"T": qty(120, "degC")}
    heat = [Feature(type="temperature", selection=e, values=hot) for e in edges]
    c.physics[0].settings = {**CONSTANT, "reference_temperature": qty(20, "degC")}
    c.physics.append(Physics(type="heat_transfer", settings=CONSTANT, features=heat))
    steps = {k: [SolveStep(type="stationary", physics=[k])]
             for k in ("heat_transfer", "solid_mechanics")}  # fmt: skip
    thermal = p.add(Study(name="Thermal", component=c.id, solver=solver,
                          steps=steps["heat_transfer"]))  # fmt: skip
    static = p.add(Study(name="Static", component=c.id, solver=solver,
                         steps=steps["solid_mechanics"]))  # fmt: skip
    p.add(Transfer(name="T", source=thermal.id, field="temperature", target=static.id,
                   role="load"))  # fmt: skip
    return run_study(p, static.id, tmp_path, tmp_path / "run", timeout_s=900)


@over(*FOUR)
@pytest.mark.parametrize("model", ["plane_stress", "plane_strain"])
def test_heated_strip_held_lengthwise(tmp_path, solver, model):
    """The strip at a uniform 120 °C (both ends held there) from a 20 °C reference, held
    along x at both ends, its bottom a line of symmetry: σx = -E α ΔT and the top moves
    (1 + ν) α ΔT H in plane stress; σx = σz = -E α ΔT / (1 - ν) and (1 + ν) / (1 - ν)
    α ΔT H in plane strain. The heat study's temperature reaches the structure by a transfer."""
    alpha, dT = STEEL["thermal_expansion"].to_si(), 100.0
    held = [
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="displacement", selection="Part/Right", values={"x": mm(0)}),
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
    ]
    p, c = _strip(held, model=model)
    run = _heated(tmp_path, p, c, solver, ("Part/Left", "Part/Right"))
    stress = model == "plane_stress"
    sigma = -E * alpha * dT / (1 if stress else 1 - NU)
    lift = (1 + NU) / (1 if stress else 1 - NU) * alpha * dT * H
    s = run.results.last("stress").values
    sx, sz = np.median(s[:, 0]), np.median(s[:, 2])
    uy = run.results.last("displacement").at(run.mesh.groups["Part/Top"].node_ids)[:, 1].mean()
    print(f"{solver} {model}: σx {sx / 1e6:.2f} MPa (theory {sigma / 1e6:.2f}), "
          f"top {uy:.5g} m (theory {lift:.5g})")  # fmt: skip
    assert sx == pytest.approx(sigma, rel=5e-3)
    assert sz == pytest.approx(0.0 if stress else sigma, abs=5e-3 * abs(sigma))  # εz = 0
    assert uy == pytest.approx(lift, rel=5e-3)


@over(*FOUR)
def test_heated_rod_expands_freely_axisymmetric(tmp_path, solver):
    """A solid rod r = 5 mm along Y at a uniform 120 °C from 20 °C, held axially at its
    root only: it grows α ΔT r outward and α ΔT L along, stress-free (the hoop strain's
    thermal part checked)."""
    alpha, dT, r, length = STEEL["thermal_expansion"].to_si(), 100.0, 0.005, 0.05
    held = [Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)})]
    p, c = _project(0, 0, r, length, "2d_axisymmetric", held, size=1.0)
    run = _heated(tmp_path, p, c, solver, ("Part/Bottom", "Part/Top"))
    u = run.results.last("displacement")
    ur = u.at(run.mesh.groups["Part/Right"].node_ids)[:, 0].mean()
    uy = u.at(run.mesh.groups["Part/Top"].node_ids)[:, 1].mean()
    worst = np.abs(run.results.last("stress").values).max()
    print(f"{solver}: radial {ur:.5g} m (theory {alpha * dT * r:.5g}), axial {uy:.5g} m "
          f"(theory {alpha * dT * length:.5g}), largest stress {worst / 1e6:.3g} MPa")  # fmt: skip
    assert ur == pytest.approx(alpha * dT * r, rel=5e-3)
    assert uy == pytest.approx(alpha * dT * length, rel=5e-3)
    assert worst < 1e-3 * E * alpha * dT
