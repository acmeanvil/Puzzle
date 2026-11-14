"""Load combinations on the finite-element solvers (ADR 0079 step 2, `puzzle.combine`).

A steel bar fixed at its root carries a dead-load tip force (case D, down) and a live
one (case L, sideways). The study's combinations 1.2D + 1.6L and 0.9D become a static
step each; the bar is linear, so each combination's tip deflection must be the factored
sum of the two cases solved alone, to the solver's own digits.
"""

import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step
from solvers import over

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import LoadCombination
from puzzle.solve import run_study


def _project(tmp_path, solver, combinations, cases=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "bar.step")
    physics = Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"),
        Feature(type="force", selection="Bar/Tip", case="D" if cases else None,
                values={"y": Quantity(value=-200.0, unit="N")}),
        Feature(type="force", selection="Bar/Tip", case="L" if cases else None,
                values={"z": Quantity(value=-100.0, unit="N")}),
    ])  # fmt: skip
    project, comp = bar_project(step, [physics], mesh_size_mm=5.0, element_order=2)
    study = project.add(Study(name="Combined", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")],
                              combinations=combinations))  # fmt: skip
    return project, study


def _tip(run, step):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return step.fields["displacement"].at(tip).mean(axis=0)


@over("calculix", "code_aster")
def test_combinations_are_solved_as_factored_steps(tmp_path, solver):
    combos = [LoadCombination(name="1.2D + 1.6L", factors={"D": 1.2, "L": 1.6}),
              LoadCombination(name="0.9D", factors={"D": 0.9})]  # fmt: skip
    project, study = _project(tmp_path / "c", solver, combos)
    run = run_study(project, study.id, tmp_path / "c", tmp_path / "c" / "w", timeout_s=1800)
    steps = [s for s in run.results.steps if s.kind == "combination"]
    assert [s.label() for s in steps] == ["1.2D + 1.6L", "0.9D"]

    # each case alone, as a reference
    alone = {}
    for case, factors in (("D", {"D": 1.0}), ("L", {"L": 1.0})):
        p, s = _project(tmp_path / case, solver, [LoadCombination(name=case, factors=factors)])
        r = run_study(p, s.id, tmp_path / case, tmp_path / case / "w", timeout_s=1800)
        alone[case] = _tip(r, [x for x in r.results.steps if x.kind == "combination"][0])
    first, second = (_tip(run, s) for s in steps)
    expected = 1.2 * alone["D"] + 1.6 * alone["L"]
    print(f"{solver}: 1.2D+1.6L tip {first} against {expected}")
    # to the digits the solvers print (CalculiX's .frd: six), against the deflection
    tol = 1e-6 * np.linalg.norm(expected)
    assert np.allclose(first, expected, rtol=0, atol=tol)
    assert np.allclose(second, 0.9 * alone["D"], rtol=0, atol=tol)
    assert abs(first[1]) > 0 and abs(first[2]) > 0  # both loads are there


def test_a_case_on_an_imposed_value_is_refused(tmp_path):
    from puzzle.combine import applied
    from puzzle.errors import PuzzleError

    project, study = _project(tmp_path, "calculix",
                              [LoadCombination(name="D", factors={"D": 1.0})])  # fmt: skip
    comp = project.objects[study.component]
    held = {"x": Quantity(value=0, unit="m")}
    comp.physics[0].features[0] = Feature(type="displacement", selection="Bar/Root", case="D",
                                          values=held)  # fmt: skip
    with pytest.raises(PuzzleError, match="only loads"):
        applied(project, study)
