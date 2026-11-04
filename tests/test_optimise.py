"""Design of experiments (ADR 0063, phase 1) on a stand-in study whose "solve" is the
closed form: a cantilever's tip deflection δ = 4 F L³ / (E w t³), F = 100 N,
L = 100 mm, E = 200 GPa, so every point's answer is known by hand. The objective is
the cross-section (w t, a mass per length), the constraint δ ≤ 1 mm.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from puzzle import optimise
from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.model.schema import (
    DesignOutput,
    DesignVariable,
    Operator,
    OperatorInput,
    Optimisation,
    ParameterSet,
    Project,
    Quantity,
    Study,
)
from puzzle.operators import parameter_values
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord

F, L, E = 100.0, 0.1, 200e9


def deflection(w, t):
    return 4 * F * L**3 / (E * w * t**3)


class Store:
    root = "memory"

    def __init__(self):
        self.records, self.results = [], {}

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.results[run_id]

    def read_mesh(self, mesh_id):
        return Mesh(np.array([1, 2]), np.array([[0.0, 0, 0], [L, 0, 0]]))


def stand_in(store, fail=lambda w, t: False, calls=None):
    """A solve: the closed form at the point's parameters, stored with its labels."""

    def solve(project, study_id, root, workdir, *, store=store, solver, _point, **_):
        values = parameter_values(project)
        w, t = values.get("w", 0.02), values.get("t", 0.005)  # a beam, or any study
        if calls is not None:
            calls.append((solver, w, t))
        if fail(w, t):
            raise PuzzleError("the mesh was refused", code="mesh_failed")
        run_id = f"r{len(store.records)}"
        u = np.array([[0.0, 0, 0], [0, -deflection(w, t), 0]])
        field = Field("displacement", "node", ["x", "y", "z"], np.array([1, 2]), u)
        store.results[run_id] = Results(
            solver, [StepResult(1, "stationary", 1.0, fields={"displacement": field})]
        )
        when = datetime(2026, 11, 3, 12, 0, len(store.records) % 60)
        store.records.append(RunRecord(run_id, _point[0], str(study_id), "Beam", solver,
                                       "native", "m", "completed", when, 2.0, 1, when,
                                       json.dumps(_point[1])))  # fmt: skip
        return SimpleNamespace(run_id=run_id, mesh_result=None)

    return solve


def project(method="full_factorial", **extra):
    p = Project("p")
    p.add(ParameterSet(name="Section", values={"w": Quantity(value=20, unit="mm"),
                                               "t": Quantity(value=5, unit="mm")}))  # fmt: skip
    study = p.add(Study(name="Beam", component=uuid4(), solver="calculix", steps=[]))
    tip = p.add(
        Operator(
            name="Tip",
            type="reduce",
            quantity="displacement:total",
            inputs={"result": OperatorInput(source=study.id, port="result")},
        )
    )
    area = p.add(Operator(name="Area", type="expression", expression="w * t / mm**2"))
    opt = p.add(Optimisation(
        name="Lightest", study=study.id, method=method,
        variables=[DesignVariable(name="w", low=Quantity(value=10, unit="mm"),
                                  high=Quantity(value=30, unit="mm")),
                   DesignVariable(name="t", low=Quantity(value=4, unit="mm"),
                                  high=Quantity(value=8, unit="mm"))],
        objective=DesignOutput(source=area.id),
        constraints=[DesignOutput(source=tip.id, upper=Quantity(value=1, unit="mm"))],
        **extra))  # fmt: skip
    return p, opt


def test_full_factorial_finds_the_lightest_section_by_hand(tmp_path):
    p, opt = project(levels=3)
    store = Store()
    pts = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=stand_in(store))
    assert len(pts) == 9 and all(pt.run for pt in pts)
    for pt in pts:
        w, t = pt.values["w"], pt.values["t"]
        assert pt.objective == pytest.approx(w * t * 1e6)
        assert pt.constraints[0] == pytest.approx(deflection(w, t))
        assert pt.feasible == (deflection(w, t) <= 1e-3)
    top = optimise.best(opt, pts)
    # By hand: δ ≤ 1 mm needs w t³ ≥ 2e-12 m⁴; of the grid, w 10 mm t 6 mm (2.16e-12)
    # is the smallest area (60 mm²) that holds.
    assert top.labels == {"w": "10 mm", "t": "6 mm"} and top.objective == pytest.approx(60)
    bad = next(pt for pt in pts if not pt.feasible)
    assert bad.reason.startswith("Tip above 1 mm")
    commands = optimise.apply_commands(p, opt, top)
    assert commands[0]["ops"][0] == {"op": "set", "path": "values.w",
                                     "value": {"value": 10.0, "unit": "mm"}}  # fmt: skip
    said = optimise.suggestion(p, opt, pts)
    assert said == "w = 10 mm, t = 6 mm: Area 60, every constraint holds"


def test_points_are_reused_and_rebuilt_from_the_store(tmp_path):
    p, opt = project(levels=2)
    store, calls = Store(), []
    solve = stand_in(store, calls=calls)
    first = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=solve)
    table = optimise.points(p, store, opt)  # no solving: from the stored runs
    assert [pt.objective for pt in table] == pytest.approx([pt.objective for pt in first])
    assert all(pt.seconds == 2.0 for pt in table)
    opt.levels = 3  # a bigger grid: the four corners were run already
    reused = {pt.title for pt in table}
    assert {pt.title for pt in optimise.points(p, store, opt) if pt.run} == reused


def test_a_failed_point_is_infeasible_and_the_rest_run(tmp_path):
    p, opt = project(levels=2)
    store = Store()
    solve = stand_in(store, fail=lambda w, t: w > 0.02)
    pts = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=solve)
    failed = [pt for pt in pts if not pt.run]
    assert len(failed) == 2 and all(pt.reason == "mesh_failed: the mesh was refused"
                                    for pt in failed)  # fmt: skip
    assert len([pt for pt in pts if pt.run]) == 2


def test_stop_and_budget(tmp_path):
    p, opt = project(levels=3, max_solves=4)
    store = Store()
    assert len(optimise.plan(opt)) == 4
    stop = threading.Event()
    stop.set()
    pts = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=stand_in(store),
                       cancel=stop)  # fmt: skip
    assert not store.records and all(pt.reason == "stopped before it ran" for pt in pts)


def test_sampled_plans_are_seeded_and_in_bounds():
    _, opt = project("latin_hypercube", samples=12, seed=3)
    a, b = optimise.plan(opt), optimise.plan(opt)
    assert a == b and len(a) == 12
    w = sorted(pt["w"] for pt in a)  # one per twelfth of the range: a Latin hypercube
    assert all(0.01 + i * 0.02 / 12 <= x <= 0.01 + (i + 1) * 0.02 / 12 for i, x in enumerate(w))
    opt.method, opt.samples = "sobol", 8
    s = optimise.plan(opt)
    assert len(s) == 8 and all(0.004 <= pt["t"] <= 0.008 for pt in s)
    opt.variables[1] = DesignVariable(name="t", kind="choice", choices=[
        Quantity(value=v, unit="mm") for v in (4, 6, 8)])  # fmt: skip
    assert {pt["t"] for pt in optimise.plan(opt)} <= {0.004, 0.006, 0.008}
    opt.variables[1] = DesignVariable(name="t", kind="integer", low=Quantity(value=1),
                                      high=Quantity(value=5))  # fmt: skip
    assert all(float(pt["t"]).is_integer() for pt in optimise.plan(opt))


def test_sensitivities_and_checks(tmp_path):
    p, opt = project(levels=3)
    store = Store()
    pts = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=stand_in(store))
    sens = optimise.sensitivities(opt, pts)
    # Wider and thicker both add area; thicker cuts the deflection (as 1/t³, so its
    # straight-line correlation is weaker than its effect).
    assert sens["w"]["objective"] > 0.5 and sens["t"]["objective"] > 0.5
    assert sens["t"]["constraint 1"] < -0.3 and sens["w"]["constraint 1"] < 0
    opt.variables[0] = DesignVariable(name="nope", low=Quantity(value=1), high=Quantity(value=2))
    with pytest.raises(PuzzleError, match="no parameter 'nope'"):
        optimise.check(p, opt)
    with pytest.raises(ValueError, match="low must be below high"):
        DesignVariable(name="w", low=Quantity(value=3), high=Quantity(value=2))


def test_a_shoot_out_runs_every_solver_per_point(tmp_path):
    from puzzle.model.schema import Shootout

    p, opt = project(levels=2)
    study = p.get(opt.study, Study)
    study.shootout = Shootout(solvers=["code_aster"])
    store, calls = Store(), []
    pts = optimise.run(p, opt, tmp_path, tmp_path / "w", store=store,
                       solve=stand_in(store, calls=calls))  # fmt: skip
    assert [c[0] for c in calls[:2]] == ["calculix", "code_aster"]
    assert set(pts[0].runs) == {"calculix", "code_aster"}
    assert set(pts[0].by_solver) == {"calculix", "code_aster"}
    opt.reads = "worst"
    again = optimise.points(p, store, opt)
    assert again[0].feasible == pts[0].feasible
