"""Solve progress on FEniCSx and Elmer (ADR 0054, phase 2), on the transient bar of
test_benchmarks.py: a 200 mm steel bar at 20 °C whose end x = 0 is held at 100 °C.

Every time step arrives as an increment and a converged event, and a probe at the far
end followed while solving gives the series solution there (within 2 % of the 80 K
step, as the stored results) and equals the stored result at each output time."""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle import progress
from puzzle.materials import library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import ResultProbe
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

L = BAR[0]
DT, END = 20.0, 400.0  # s


def _series(x, t, alpha, t0=293.15, t1=373.15, terms=200):
    n = np.arange(terms)[:, None]
    lam = (2 * n + 1) * np.pi / (2 * L)
    s = 4 / ((2 * n + 1) * np.pi) * np.sin(lam * x) * np.exp(-(lam**2) * alpha * t)
    return t1 + (t0 - t1) * s.sum(axis=0)


def _project(tmp_path, solver, tables=False, transient=True):
    step = write_bar_step(tmp_path / "bar.step")
    hot = Feature(type="temperature", selection="Bar/Root",
                  values={"T": Quantity(value=100, unit="degC")})  # fmt: skip
    flux = Feature(type="heat_flux", selection="Bar/Tip",
                   values={"q": Quantity(value=-2e4, unit="W/m^2")})  # fmt: skip
    heat = Physics(type="heat_transfer", features=[hot] if transient else [hot, flux],
                   settings={"initial_temperature": Quantity(value=20, unit="degC"),
                             "temperature_dependent_materials": tables})  # fmt: skip
    project, comp = bar_project(step, [heat], mesh_size_mm=5)
    settings = {"time_step": Quantity(value=DT, unit="s"),
                "end_time": Quantity(value=END, unit="s")}  # fmt: skip
    solve = (
        SolveStep(type="transient", settings=settings)
        if transient
        else SolveStep(type="stationary")
    )
    study = project.add(Study(name="t", component=comp.id, solver=solver, steps=[solve]))
    project.add(
        ResultProbe(
            name="Far end",
            source=study.id,
            type="point",
            point=(L, 0.005, 0.005),
            quantity="temperature",
            track=True,
        )
    )
    return project, study


@over("fenicsx", "elmer")
def test_time_steps_and_a_tracked_temperature(solver, tmp_path):
    project, study = _project(tmp_path, solver)
    seen = []
    store = ResultStore(tmp_path / "results")
    run = run_study(project, study.id, tmp_path, tmp_path / "run", store=store,
                    on_event=seen.append, timeout_s=1200)  # fmt: skip
    steps = round(END / DT)
    increments = [e for e in seen if isinstance(e, progress.Increment)]
    assert [round(e.time, 6) for e in increments] == [round(DT * k, 6) for k in range(1, steps + 1)]
    assert len([e for e in seen if isinstance(e, progress.Converged)]) == steps
    s = progress.state(seen)
    assert s.fraction() == pytest.approx(1.0) and not s.failed and s.equations

    steel = library_material("structural_steel").properties
    k, rho, cp = (steel[p].to_si() for p in ("thermal_conductivity", "density", "specific_heat"))
    alpha = k / (rho * cp)
    far = [e for e in seen if isinstance(e, progress.Tracked) and e.name == "Far end"]
    assert len(far) == steps
    for e in far:
        assert e.value == pytest.approx(_series(np.array([L]), e.time, alpha)[0], abs=0.02 * 80)
    end = run.results.steps[-1].fields["temperature"]
    xyz = run.mesh.node_coords(end.ids)
    nearest = int(np.argmin(np.linalg.norm(xyz - np.array([L, 0.005, 0.005]), axis=1)))
    assert far[-1].value == pytest.approx(end.values[nearest, 0], abs=1e-6)
    assert store.read_progress(run.run_id)


@over("fenicsx", "elmer")
def test_nonlinear_iterations_are_shown(solver, tmp_path):
    """Temperature tables make steady conduction nonlinear: its iterations show, the
    last below the criterion."""
    project, study = _project(tmp_path, solver, tables=True, transient=False)
    seen = []
    run_study(project, study.id, tmp_path, tmp_path / "run", on_event=seen.append,
              timeout_s=600)  # fmt: skip
    its = [e for e in seen if isinstance(e, progress.Iteration)]
    assert len(its) >= 2
    name, value = next(iter(its[-1].residuals.items()))
    assert value <= its[-1].criteria[name]
    assert any(isinstance(e, progress.Tracked) for e in seen)
