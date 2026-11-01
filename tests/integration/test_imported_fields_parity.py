"""Scalar face values from imported fields on every FE solver (ADR 0044), on the
200 x 10 x 10 mm steel bar along x, its root (x = 0) held and its tip (x = L) loaded:

- **a pressure:** an imported uniform 10 MPa on the tip gives the uniform feature's
  answer, u = -p L / E along the bar (the stage 1 test, on all four solvers);
- **a linear heat flux** q(y) = 2 q̄ y / w on the tip, the root held at T0. The sides are
  insulated, so each cross-section's mean temperature is T0 + q̄ x / k whatever the
  variation across (the energy balance: all the heat in, q̄ A, leaves through the root),
  and the tip's spread follows the cosine series of the y-varying part:
  T(L, y) - T̄(L) = Σ_odd a_n w / (nπ k) tanh(nπ L / w) cos(nπ y / w), a_n = -4 q' w / (nπ)²;
- **a film coefficient and ambient temperature:** imported uniform h and T_inf give the
  uniform feature's answer, the 1D one T_tip = T_inf + (T0 - T_inf) R_h / (R_k + R_h);
  an imported linear T_inf with a uniform h keeps the mean's 1D answer (the y-varying
  part has zero mean and decouples); an imported linear h has no closed form here, so
  each solver is held to CalculiX's answer within the solvers' agreement on the uniform
  case.

CalculiX takes each face's mean of the nodal values; Code_Aster a FORMULE interpolating
them on the face, Elmer nodal variables read from a restart file, FEniCSx a Lagrange
function: the last three are the field's own finite-element interpolant.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import library_material
from puzzle.model import Feature, FieldUse, ImportedField, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")

SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx")
L, W, H = BAR
STEEL = library_material("structural_steel").properties
E = STEEL["youngs_modulus"].to_si()
K = STEEL["thermal_conductivity"].to_si()
CONSTANT = {"temperature_dependent_materials": False}
T0 = 400.0  # K, the root
MESH_MM = 2.5


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _tip_grid():
    """Points on the tip plane, past its edges, so the linear mapping spans the face."""
    g = np.linspace(-0.002, 0.012, 8)
    yy, zz = np.meshgrid(g, g)
    return np.column_stack([np.full(yy.size, L), yy.ravel(), zz.ravel()])


def _csv(path, points, values, header):
    rows = [header] + [
        ",".join(f"{c:.12g}" for c in (*p, v)) for p, v in zip(points, values, strict=True)
    ]
    path.write_text("\n".join(rows) + "\n")
    return path


def _run(where, solver, physics, fields, kind=("heat_transfer",)):
    """The bar with `physics`; `fields` maps (feature index, value name) to (values on
    the tip grid, their unit): each becomes an imported field that feature's value uses."""
    where.mkdir(parents=True, exist_ok=True)
    project, comp = bar_project(write_bar_step(where / "bar.step"), [physics], MESH_MM)
    points = _tip_grid()
    for (index, name), (values, unit) in fields.items():
        path = _csv(where / f"{name}{index}.csv", points, values, f"x,y,z,{name} [{unit}]")
        field = project.add(ImportedField(**new_field_data(where, path, name=f"{name}{index}")))
        physics.features[index].fields[name] = FieldUse(field=field.id, column=name)
    steps = [SolveStep(type="stationary", physics=list(kind))]
    study = project.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    return run_study(project, study.id, where, where / "run", timeout_s=900)


def _tip_nodes(run):
    ids = run.mesh.groups["Bar/Tip"].node_ids
    return ids, run.mesh.node_coords(ids)


def _tip_mean(run, name="temperature", component=0):
    ids, _ = _tip_nodes(run)
    return float(run.results.last(name).at(ids)[:, component].mean())


def _edges(run):
    """The tip's mean temperature along its y = 0 and its y = w edges."""
    ids, xyz = _tip_nodes(run)
    t = run.results.last("temperature").at(ids)[:, 0]
    return float(t[xyz[:, 1] < 1e-6].mean()), float(t[xyz[:, 1] > W - 1e-6].mean())


def _heat(feature):
    root = Feature(type="temperature", selection="Bar/Root", values={"T": qty(T0, "K")})
    return Physics(type="heat_transfer", settings=CONSTANT, features=[root, feature])


# --- pressure ------------------------------------------------------------------------


@over(*SOLVERS)
def test_an_imported_pressure_is_the_uniform_one(tmp_path, solver):
    p = 10e6

    def case(use_field):
        values = {"p": qty(0 if use_field else p / 1e6, "MPa")}
        pressure = Feature(type="pressure", selection="Bar/Tip", values=values)
        held = Feature(type="fixed", selection="Bar/Root")
        physics = Physics(type="solid_mechanics", settings=CONSTANT, features=[held, pressure])
        fields = {(1, "p"): (np.full(64, p / 1e6), "MPa")} if use_field else {}
        where = tmp_path / ("field" if use_field else "plain")
        return _tip_mean(_run(where, solver, physics, fields, ("solid_mechanics",)), "displacement")

    u, uniform = case(True), case(False)
    print(f"{solver}: tip {u:.6g} m, uniform {uniform:.6g} m, -pL/E {-p * L / E:.6g} m")
    assert u == pytest.approx(-p * L / E, rel=0.01)
    assert u == pytest.approx(uniform, rel=1e-6)


# --- heat flux -----------------------------------------------------------------------


def _series(q_slope, terms=401):
    """T(L, y) - T̄(L) at y = 0 and y = w for the flux's y-varying part q' (y - w/2)."""
    n = np.arange(1, terms + 1, 2)
    a = -4 * q_slope * W / (n * np.pi) ** 2
    amp = a * W / (n * np.pi * K) * np.tanh(n * np.pi * L / W)
    return float(amp.sum()), float((amp * np.cos(n * np.pi)).sum())


@over(*SOLVERS)
def test_an_imported_linear_heat_flux_balances_and_spreads(tmp_path, solver):
    q_mean = 1e5  # W/m², into the body: q(y) = 2 q̄ y / w, from 0 to 2 q̄
    flux = Feature(type="heat_flux", selection="Bar/Tip", values={"q": qty(0, "W/m^2")})
    q = 2 * q_mean * _tip_grid()[:, 1] / W
    run = _run(tmp_path, solver, _heat(flux), {(1, "q"): (q, "W/m^2")})
    mean, (low, high) = _tip_mean(run), _edges(run)
    exact_mean = T0 + q_mean * L / K
    exact_low, exact_high = (exact_mean + d for d in _series(2 * q_mean / W))
    print(
        f"{solver}: tip mean {mean:.6g} K (energy balance {exact_mean:.6g}), "
        f"y = 0 edge {low:.6g} ({exact_low:.6g}), y = w edge {high:.6g} ({exact_high:.6g})"
    )
    assert mean == pytest.approx(exact_mean, abs=0.002 * (exact_mean - T0))
    # The others integrate the field's interpolant (FEniCSx: 10.175 K of 10.177);
    # CalculiX's one value per face smears the spread to 9.71 K, 4.6 % short.
    spread, exact_spread = high - low, exact_high - exact_low
    assert spread == pytest.approx(exact_spread, rel=0.06 if solver == "calculix" else 0.01)


@over(*SOLVERS)
def test_an_imported_heat_flux_follows_its_amplitude(tmp_path, solver):
    """The insulated bar heated through its tip by the linear flux times a pulse (0 → 1
    over 10 s, held to 20 s, back to 0 at 30 s). Backward Euler at 1 s sums the
    amplitude at each step's end: by 15 s 10.5 s of full flux, by 40 s 20 s, so the mean
    temperature rise is q̄ A t_eff / (ρ c V) exactly."""
    from puzzle.model.schema import Amplitude
    from puzzle.results.model import Results
    from puzzle.results.query import integrate

    where = tmp_path
    q_mean, start = 1e5, 293.15
    flux = Feature(type="heat_flux", selection="Bar/Tip", values={"q": qty(0, "W/m^2")})
    physics = Physics(type="heat_transfer", features=[flux], settings=CONSTANT | {
        "initial_temperature": qty(start, "K").model_dump(mode="json")})  # fmt: skip
    project, comp = bar_project(write_bar_step(where / "bar.step"), [physics], 5)
    pulse = [(0.0, 0.0), (10.0, 1.0), (20.0, 1.0), (30.0, 0.0)]
    flux.amplitude = project.add(Amplitude(name="Pulse", points=pulse)).id
    path = _csv(where / "q.csv", _tip_grid(), 2 * q_mean * _tip_grid()[:, 1] / W, "x,y,z,q [W/m^2]")
    field = project.add(ImportedField(**new_field_data(where, path, name="q")))
    flux.fields["q"] = FieldUse(field=field.id, column="q")
    settings = {"time_step": {"value": 1, "unit": "s"}, "end_time": {"value": 40, "unit": "s"}}
    steps = [SolveStep(type="transient", physics=["heat_transfer"], settings=settings)]
    study = project.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    run = run_study(project, study.id, where, where / "run", timeout_s=900)
    rho, c = STEEL["density"].to_si(), STEEL["specific_heat"].to_si()
    for time, full in ((15.0, 10.5), (40.0, 20.0)):
        at = Results(
            run.results.solver, [s for s in run.results.steps if abs(s.value - time) < 1e-6]
        )
        mean = integrate(at, run.mesh, "temperature", "Bar/Body")["mean"]
        exact = start + q_mean * W * H * full / (rho * c * L * W * H)
        print(f"{solver}: mean {mean:.6g} K at {time:g} s (energy balance {exact:.6g} K)")
        assert mean == pytest.approx(exact, abs=1e-3 * (exact - start))


# --- convection ----------------------------------------------------------------------

H_FILM, T_INF = 250.0, 300.0


def _film_answer(h=H_FILM, t_inf=T_INF):
    """The 1D answer: conduction through the bar in series with the film on its tip."""
    r_k, r_h = L / (K * W * H), 1 / (h * W * H)
    return t_inf + (T0 - t_inf) * r_h / (r_k + r_h)


def _film(tmp_path, solver, fields):
    values = {"h": qty(H_FILM, "W/(m^2*K)"), "T_inf": qty(T_INF, "K")}
    film = Feature(type="convection", selection="Bar/Tip", values=values)
    run = _run(tmp_path, solver, _heat(film), {(1, k): v for k, v in fields.items()})
    return _tip_mean(run)


@over(*SOLVERS)
def test_an_imported_uniform_film_is_the_uniform_one(tmp_path, solver):
    n = len(_tip_grid())
    plain = _film(tmp_path / "plain", solver, {})
    both = _film(tmp_path / "field", solver, {
        "h": (np.full(n, H_FILM), "W/(m^2*K)"), "T_inf": (np.full(n, T_INF), "K")})  # fmt: skip
    print(f"{solver}: tip {both:.8g} K, uniform {plain:.8g} K, 1D {_film_answer():.8g} K")
    assert plain == pytest.approx(_film_answer(), abs=0.05)
    assert both == pytest.approx(plain, abs=1e-6)


@over(*SOLVERS)
def test_imported_film_values_with_temperature_tables(tmp_path, solver):
    """Steel's temperature tables make the solve nonlinear (Code_Aster: THER_NL and
    THER_NON_LINE): imported uniform h and T_inf still give the uniform feature's answer."""
    n = len(_tip_grid())

    def case(where, fields):
        values = {"h": qty(H_FILM, "W/(m^2*K)"), "T_inf": qty(T_INF, "K")}
        film = Feature(type="convection", selection="Bar/Tip", values=values)
        physics = _heat(film)
        physics.settings = {}  # the material's tables
        return _tip_mean(_run(where, solver, physics, {(1, k): v for k, v in fields.items()}))

    plain = case(tmp_path / "plain", {})
    both = case(tmp_path / "field", {
        "h": (np.full(n, H_FILM), "W/(m^2*K)"), "T_inf": (np.full(n, T_INF), "K")})  # fmt: skip
    print(f"{solver}: tip {both:.8g} K, uniform {plain:.8g} K (temperature tables)")
    assert both == pytest.approx(plain, abs=1e-5)


@over(*SOLVERS)
def test_an_imported_linear_ambient_keeps_the_mean(tmp_path, solver):
    y = _tip_grid()[:, 1]
    t_inf = T_INF + 100.0 * (y - W / 2) / W  # 250 K to 350 K across the tip, mean T_INF
    mean = _film(tmp_path, solver, {"T_inf": (t_inf, "K")})
    print(f"{solver}: tip mean {mean:.8g} K, 1D with the mean ambient {_film_answer():.8g} K")
    assert mean == pytest.approx(_film_answer(), abs=0.05)


# The linear film coefficient's tip mean on CalculiX (2.5 mm tets): the reference. Its
# face means of the nodal h put it 0.004 K below the interpolating solvers, 5 % of the
# 0.075 K the variation moves the tip; each solver is held to 0.01 K of it.
LINEAR_H_CALCULIX = 351.68533


@over(*SOLVERS)
def test_an_imported_linear_film_coefficient_matches_calculix(tmp_path, solver):
    y = _tip_grid()[:, 1]
    h = H_FILM * (0.2 + 1.6 * y / W)  # 50 to 450 W/(m² K) across the tip, mean H_FILM
    mean = _film(tmp_path, solver, {"h": (h, "W/(m^2*K)")})
    print(f"{solver}: tip mean {mean:.8g} K with a linear h (uniform h: {_film_answer():.8g})")
    assert mean == pytest.approx(LINEAR_H_CALCULIX, abs=0.01)
    assert mean - _film_answer() > 0.05  # the variation shows: it is not the uniform answer
