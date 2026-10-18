"""Steady conduction with temperature-dependent conductivity (EN 1993-1-2 steel).

The steel bar's root is held at 100 °C and its tip at 700 °C; the sides are insulated.
Over that range the Eurocode conductivity falls from 50.7 to 30.7 W/(m K), so the
profile is far from linear. The Kirchhoff transform gives it exactly:
F(T) = integral of k dT is linear in x, so T(x) = F^-1(F(T0) + (F(T1) - F(T0)) x / L),
with k interpolated linearly between the table points as every solver does.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.materials import library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

L = BAR[0]
T_ROOT, T_TIP = 373.15, 973.15  # K


def _kirchhoff(T: np.ndarray) -> np.ndarray:
    """F(T) = integral of k from T_ROOT, for the table interpolated linearly."""
    curve = library_material("structural_steel").tables["thermal_conductivity"].curve()
    temps = np.linspace(T_ROOT, T_TIP, 6001)
    k = np.interp(temps, *zip(*curve, strict=True))
    F = np.concatenate([[0.0], np.cumsum(0.5 * (k[1:] + k[:-1]) * np.diff(temps))])
    return np.interp(T, temps, F)


def _exact(x: np.ndarray) -> np.ndarray:
    temps = np.linspace(T_ROOT, T_TIP, 6001)
    F = _kirchhoff(temps)
    return np.interp(F[-1] * x / L, F, temps)


def _project(tmp_path, solver):
    step = write_bar_step(tmp_path / "bar.step")
    heat = Physics(
        type="heat_transfer",
        features=[
            Feature(
                type="temperature",
                selection="Bar/Root",
                values={"T": Quantity(value=100, unit="degC")},
            ),
            Feature(
                type="temperature",
                selection="Bar/Tip",
                values={"T": Quantity(value=700, unit="degC")},
            ),
        ],
    )
    project, comp = bar_project(step, [heat])
    study = project.add(
        Study(
            name="Hot bar", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")]
        )
    )
    return project, study


@over("calculix", "elmer", "code_aster", "fenicsx")
def test_conductivity_table(tmp_path, solver):
    project, study = _project(tmp_path, solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    T = run.results.last("temperature")
    x = run.mesh.node_coords(T.ids)[:, 0]
    error = np.abs(T.values[:, 0] - _exact(x))
    linear = T_ROOT + (T_TIP - T_ROOT) * x / L  # what a constant k would give
    print(
        f"{solver}: max error {error.max():.3f} K (vs {np.abs(linear - _exact(x)).max():.1f} K "
        "for a linear profile)"
    )
    assert np.abs(linear - _exact(x)).max() > 20  # the table matters
    assert error.max() < 0.05


def test_transient_tables_agree_across_solvers(tmp_path):
    """Heating from 20 °C with the root at 700 °C: conductivity and specific heat both
    follow the Eurocode tables. No closed form, so the solvers check each other."""
    from solvers import available

    solvers = [s for s in ("calculix", "elmer", "code_aster", "fenicsx") if available(s)]
    if len(solvers) < 2:
        pytest.skip("needs two solvers")
    probes = np.array([0.01, 0.03, 0.06])  # m from the root
    traces = {}
    for solver in solvers:
        (tmp_path / solver).mkdir()
        step = write_bar_step(tmp_path / solver / "bar.step")
        heat = Physics(
            type="heat_transfer",
            features=[
                Feature(
                    type="temperature",
                    selection="Bar/Root",
                    values={"T": Quantity(value=700, unit="degC")},
                )
            ],
            settings={"initial_temperature": Quantity(value=20, unit="degC")},
        )
        project, comp = bar_project(step, [heat], mesh_size_mm=5)
        transient = SolveStep(
            type="transient",
            settings={
                "time_step": Quantity(value=5, unit="s"),
                "end_time": Quantity(value=120, unit="s"),
            },
        )
        study = project.add(
            Study(name="Heating", component=comp.id, solver=solver, steps=[transient])
        )
        run = run_study(
            project, study.id, tmp_path / solver, tmp_path / solver / "run", timeout_s=900
        )
        last = run.results.steps[-1].fields["temperature"]
        x = run.mesh.node_coords(last.ids)[:, 0]
        traces[solver] = np.array([last.values[np.abs(x - p) < 1e-9, 0].mean() for p in probes])
        print(solver, np.round(traces[solver] - 273.15, 2), "°C")
    reference = np.mean(list(traces.values()), axis=0)
    assert reference[0] > 400  # well into the nonlinear range near the root
    for solver, trace in traces.items():
        assert np.abs(trace - reference).max() < 2.0, solver  # K
