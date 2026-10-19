"""Custom PDEs on FEniCSx: user-written UFL residuals, solved by Newton.

1. Nonlinear conduction -div((1 + u^2) grad u) = 0 along the bar, u = 1 at the root and
   2 at the tip: u + u^3/3 is linear in x (Kirchhoff), which gives u(x) exactly.
2. Linear elasticity written by hand must equal FEniCSx's built-in elasticity.
3. Transient diffusion written by hand must equal the built-in transient heat solver.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import available

from puzzle.materials import library_material
from puzzle.model import (
    CustomEquation,
    Feature,
    Physics,
    Quantity,
    SolveStep,
    Study,
    Unknown,
)
from puzzle.solve import run_study

pytestmark = pytest.mark.skipif(not available("fenicsx"), reason="fenicsx not available")
L, B, H = BAR


def _value(v, unit="dimensionless"):
    return Quantity(value=v, unit=unit)


def _custom(tmp_path, equation, features, step, **settings):
    tmp_path.mkdir(parents=True, exist_ok=True)
    physics = Physics(type="custom_pde", equation=equation, features=features, settings=settings)
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    study = project.add(Study(name="pde", component=comp.id, solver="fenicsx", steps=[step]))
    return run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)


def _builtin(tmp_path, physics, step):
    tmp_path.mkdir(parents=True, exist_ok=True)
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    study = project.add(Study(name="built-in", component=comp.id, solver="fenicsx", steps=[step]))
    return run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)


def test_nonlinear_conduction_matches_kirchhoff(tmp_path):
    equation = CustomEquation(
        unknowns=[Unknown(name="u", degree=2)],
        weak_form="(1 + u**2) * inner(grad(u), grad(v_u)) * dx",
    )
    ends = [
        Feature(type="dirichlet", selection="Bar/Root", values={"u": _value(1.0)}),
        Feature(type="dirichlet", selection="Bar/Tip", values={"u": _value(2.0)}),
    ]
    run = _custom(tmp_path, equation, ends, SolveStep(type="stationary"))
    u = run.results.last("u")
    x = run.mesh.node_coords(u.ids)[:, 0]

    def kirchhoff(s):
        return s + s**3 / 3

    grid = np.linspace(1.0, 2.0, 20001)
    target = kirchhoff(1.0) + (kirchhoff(2.0) - kirchhoff(1.0)) * x / L
    exact = np.interp(target, kirchhoff(grid), grid)
    error = np.abs(u.values[:, 0] - exact).max()
    print(f"nonlinear conduction: max error {error:.2e}")
    assert error < 1e-5


def test_hand_written_elasticity_equals_built_in(tmp_path):
    steel = library_material("structural_steel").properties
    e, nu = steel["youngs_modulus"].to_si(), steel["poissons_ratio"].to_si()
    lam, mu = e * nu / ((1 + nu) * (1 - 2 * nu)), e / (2 * (1 + nu))
    force = -100.0
    equation = CustomEquation(
        unknowns=[Unknown(name="u", degree=2, shape="vector")],
        weak_form=(
            "inner(lam * tr(sym(grad(u))) * Identity(3) + 2 * mu * sym(grad(u)), sym(grad(v_u)))"
            " * dx - inner(as_vector((0.0, ty, 0.0)), v_u) * ds('Bar/Tip')"
        ),
        coefficients={
            "lam": _value(lam, "Pa"),
            "mu": _value(mu, "Pa"),
            "ty": _value(force / (B * H), "Pa"),
        },
    )
    clamp = Feature(
        type="dirichlet", selection="Bar/Root", values={a: _value(0.0, "m") for a in "xyz"}
    )
    custom = _custom(tmp_path / "custom", equation, [clamp], SolveStep(type="stationary"))
    builtin = _builtin(
        tmp_path / "builtin",
        Physics(
            type="solid_mechanics",
            features=[
                Feature(type="fixed", selection="Bar/Root"),
                Feature(type="force", selection="Bar/Tip", values={"y": _value(force, "N")}),
            ],
        ),
        SolveStep(type="stationary"),
    )
    tip_custom = custom.results.last("u").values[:, 1].min()
    tip_builtin = builtin.results.last("displacement").values[:, 1].min()
    print(f"elasticity: custom {tip_custom:.9e}, built-in {tip_builtin:.9e}")
    assert tip_custom == pytest.approx(tip_builtin, rel=1e-9)


def test_hand_written_diffusion_equals_built_in_heat(tmp_path):
    steel = library_material("structural_steel").properties
    k = steel["thermal_conductivity"].to_si()
    rho_cp = steel["density"].to_si() * steel["specific_heat"].to_si()
    settings = {"time_step": _value(20, "s"), "end_time": _value(200, "s")}
    equation = CustomEquation(
        unknowns=[Unknown(name="T", degree=2)],
        weak_form="rho_cp * (T - T_n) / dt * v_T * dx + k * inner(grad(T), grad(v_T)) * dx",
        coefficients={"rho_cp": _value(rho_cp, "J/(m^3*K)"), "k": _value(k, "W/(m*K)")},
    )
    hot = Feature(type="dirichlet", selection="Bar/Root", values={"T": _value(373.15)})
    custom = _custom(
        tmp_path / "custom",
        equation,
        [hot],
        SolveStep(type="transient", settings=settings),
        initial_value=293.15,
    )
    builtin = _builtin(
        tmp_path / "builtin",
        Physics(
            type="heat_transfer",
            features=[
                Feature(type="temperature", selection="Bar/Root", values={"T": _value(100, "degC")})
            ],
            settings={
                "initial_temperature": _value(20, "degC"),
                "temperature_dependent_materials": False,
            },
        ),
        SolveStep(type="transient", settings=settings),
    )
    a, b = custom.results.steps[-1].fields["T"], builtin.results.steps[-1].fields["temperature"]
    assert len(custom.results.steps) == len(builtin.results.steps) == 10
    assert np.abs(a.values - b.values).max() < 1e-9 * 373.15


def test_a_mistake_in_the_weak_form_is_reported(tmp_path):
    from puzzle.solve import StudyError

    equation = CustomEquation(unknowns=[Unknown(name="u")], weak_form="kappa * u * v_u * dx")
    with pytest.raises(StudyError) as err:
        _custom(tmp_path, equation, [], SolveStep(type="stationary"))
    assert err.value.code == "solver_failed"
    assert any("weak form: NameError" in line for line in err.value.details["log_tail"])


def test_the_weak_form_cannot_reach_python(tmp_path):
    from puzzle.solve import StudyError

    equation = CustomEquation(
        unknowns=[Unknown(name="u")], weak_form="__import__('os').system('true')"
    )
    with pytest.raises(StudyError) as err:
        _custom(tmp_path, equation, [], SolveStep(type="stationary"))
    assert any("weak form: NameError" in line for line in err.value.details["log_tail"])
