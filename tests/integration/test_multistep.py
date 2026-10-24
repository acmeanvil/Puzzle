"""A study with two solve steps (static, then modal) on every solver.

CalculiX writes both steps in one deck; the other adapters take one step per input, so
run_study runs the steps one after the other on the same mesh. Either way the results
must equal those of the two single-step studies, with step numbers 1 and 2.
"""

import pytest
from conftest_geometry import bar_project, write_bar_step
from solvers import over

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study


@over("calculix", "elmer", "code_aster", "fenicsx")
def test_static_then_modal(tmp_path, solver):
    step = write_bar_step(tmp_path / "bar.step")
    solid = Physics(
        type="solid_mechanics",
        features=[
            Feature(type="fixed", selection="Bar/Root"),
            Feature(
                type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")}
            ),
        ],
    )
    project, comp = bar_project(step, [solid], mesh_size_mm=5)
    static, modal = SolveStep(type="stationary"), SolveStep(type="modal", settings={"modes": 2})
    both = project.add(Study(name="Both", component=comp.id, solver=solver, steps=[static, modal]))
    alone = [
        project.add(Study(name=n, component=comp.id, solver=solver, steps=[s]))
        for n, s in (("Static", static), ("Modal", modal))
    ]

    run = run_study(project, both.id, tmp_path, tmp_path / "both", timeout_s=900)
    singles = [run_study(project, s.id, tmp_path, tmp_path / s.name, timeout_s=900) for s in alone]

    first = [s for s in run.results.steps if s.index == 1]
    second = [s for s in run.results.steps if s.index == 2]
    assert [s.kind for s in first] == ["stationary"] and {s.kind for s in second} == {"modal"}
    deflection = first[0].fields["displacement"].values[:, 1].min()
    assert deflection == pytest.approx(
        singles[0].results.last("displacement").values[:, 1].min(), rel=1e-9
    )
    frequencies = [s.value for s in second]
    assert frequencies == pytest.approx([m.value for m in singles[1].results.modes()], rel=1e-9)


@over("calculix", "elmer", "code_aster", "fenicsx")
def test_load_in_one_step_only(tmp_path, solver):
    """A feature in some steps only (Feature.steps, ADR 0037): the tip force is in step 2
    alone, the pressure in step 1 alone. Each step must match a one-step study with just
    its own load (exactly, on the same mesh), and step 1 must not see the force."""
    import numpy as np

    step = write_bar_step(tmp_path / "bar.step")
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(
        type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")}, steps=[2]
    )
    pressure = Feature(
        type="pressure",
        selection="Bar/Tip",
        values={"p": Quantity(value=1e5, unit="Pa")},
        steps=[1],
    )
    solid = Physics(type="solid_mechanics", features=[fixed, force, pressure])
    project, comp = bar_project(step, [solid], mesh_size_mm=5)
    two = project.add(
        Study(
            name="Two",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="stationary"), SolveStep(type="stationary")],
        )
    )
    run = run_study(project, two.id, tmp_path, tmp_path / "two", timeout_s=900)

    def tip(results, index):
        u = [s for s in results.steps if s.index == index][-1].fields["displacement"]
        return u.values.min(axis=0), u.values.max(axis=0)

    for index, keep in ((1, pressure), (2, force)):
        solid.features = [fixed, keep.model_copy(update={"steps": []})]
        one = project.add(
            Study(name=f"Only {index}", component=comp.id, solver=solver,
                  steps=[SolveStep(type="stationary")])
        )  # fmt: skip
        alone = run_study(project, one.id, tmp_path, tmp_path / f"one{index}", timeout_s=900)
        got, want = tip(run.results, index), tip(alone.results, 1)
        print(f"{solver} step {index}: {got[0]} .. {got[1]}")
        assert np.allclose(got, want, rtol=1e-6, atol=1e-12)
    low, high = tip(run.results, 1)
    assert abs(low[1]) < 1e-3 * abs(tip(run.results, 2)[0][1])  # step 1: no sideways force
    solid.features = [fixed, force, pressure]
