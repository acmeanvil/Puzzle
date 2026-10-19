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
