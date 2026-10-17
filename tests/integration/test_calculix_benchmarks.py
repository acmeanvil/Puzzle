"""CalculiX through the whole pipeline (geometry, mesh, deck, solve, results).

Results are checked against closed-form answers.

Steel bar 200 x 10 x 10 mm, fixed at x = 0. Skipped when CalculiX isn't available.
"""

import math

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step

from puzzle.jobs import runtime_for
from puzzle.materials import library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytestmark = pytest.mark.skipif(
    not runtime_for("calculix").available("ccx"),
    reason="CalculiX not available (install ccx, or build its image and set PUZZLE_RUNTIME)",
)

STEEL = library_material("general_carbon_steel").properties
E, NU, RHO = (STEEL[k].to_si() for k in ("youngs_modulus", "poissons_ratio", "density"))
L, B, H = BAR
I = B * H**3 / 12  # noqa: E741
A = B * H
FORCE = 100.0  # N, downwards at the tip


def _run(tmp_path, physics, steps):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, physics)
    study = project.add(Study(name="s", component=comp.id, solver="calculix", steps=steps))
    return run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=300)


def _tip_deflection(run):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return run.results.last("displacement").at(tip)[:, 1].mean()


def _expected_deflection():
    shear = FORCE * L / ((5 / 6) * (E / (2 * (1 + NU))) * A)
    return -(FORCE * L**3 / (3 * E * I) + shear)


def test_cantilever_tip_force(tmp_path):
    load = Feature(
        type="force", selection="Bar/Tip", values={"y": Quantity(value=-FORCE, unit="N")}
    )
    physics = [
        Physics(
            type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"), load]
        )
    ]
    run = _run(tmp_path, physics, [SolveStep(type="stationary")])
    assert _tip_deflection(run) == pytest.approx(_expected_deflection(), rel=0.02)
    root_stress = run.results.last("stress")
    assert root_stress.values.shape[1] == 6


def test_cantilever_tip_traction_as_pressure_on_a_side_face(tmp_path):
    # The same resultant applied as pressure on the top face near the tip would differ;
    # instead push the tip face along x and check the axial stretch F L / (E A).
    push = Feature(
        type="pressure", selection="Bar/Tip", values={"p": Quantity(value=-FORCE / A, unit="Pa")}
    )
    physics = [
        Physics(
            type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"), push]
        )
    ]
    run = _run(tmp_path, physics, [SolveStep(type="stationary")])
    tip = run.mesh.groups["Bar/Tip"].node_ids
    ux = run.results.last("displacement").at(tip)[:, 0].mean()
    assert ux == pytest.approx(FORCE * L / (E * A), rel=0.02)  # negative pressure pulls outward


def test_cantilever_first_bending_frequency(tmp_path):
    physics = [
        Physics(type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root")])
    ]
    run = _run(tmp_path, physics, [SolveStep(type="modal", settings={"modes": 4})])
    f1 = 1.875104**2 / (2 * math.pi * L**2) * math.sqrt(E * I / (RHO * A))
    modes = run.results.modes()
    assert len(modes) == 4
    assert all(m.kind == "modal" for m in modes)
    # A square section gives two equal first bending modes (y and z).
    assert modes[0].value == pytest.approx(f1, rel=0.03)
    assert modes[1].value == pytest.approx(f1, rel=0.03)


def test_steady_conduction_is_linear(tmp_path):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    cold = Feature(
        type="temperature", selection="Bar/Tip", values={"T": Quantity(value=20, unit="degC")}
    )
    run = _run(
        tmp_path,
        [Physics(type="heat_transfer", features=[hot, cold])],
        [SolveStep(type="stationary")],
    )

    temperature = run.results.last("temperature")
    x = run.mesh.node_coords(temperature.ids)[:, 0]
    expected = 373.15 - 80.0 * x / L
    assert np.allclose(temperature.values[:, 0], expected, atol=1e-6)


def test_unchanged_study_reuses_the_stored_run(tmp_path):
    from puzzle.results import ResultStore

    step = write_bar_step(tmp_path / "bar.step")
    load = Feature(
        type="force", selection="Bar/Tip", values={"y": Quantity(value=-FORCE, unit="N")}
    )
    physics = [
        Physics(
            type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"), load]
        )
    ]
    project, comp = bar_project(step, physics)
    study = project.add(
        Study(name="s", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")])
    )
    store = ResultStore(tmp_path / "results")

    fresh = run_study(project, study.id, tmp_path, tmp_path / "run1", store=store)
    again = run_study(project, study.id, tmp_path, tmp_path / "run2", store=store)
    assert not fresh.cached and again.cached
    assert again.run_id == fresh.run_id
    assert _tip_deflection(again) == pytest.approx(_tip_deflection(fresh), rel=1e-12)

    load.values["y"] = Quantity(value=-2 * FORCE, unit="N")  # a changed input is a new run
    changed = run_study(project, study.id, tmp_path, tmp_path / "run3", store=store)
    assert not changed.cached
    assert _tip_deflection(changed) == pytest.approx(2 * _tip_deflection(fresh), rel=1e-6)
    assert len(store.runs()) == 2
