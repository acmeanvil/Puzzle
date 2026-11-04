"""The optimisers (ADR 0063 phase 2) on closed-form stand-in studies, so every answer is
known by hand.

The beam (from `test_optimise`): the lightest section w t with a tip deflection
δ = 4 F L³ / (E w t³) ≤ 1 mm, so w t³ ≥ 2e-9 m⁴, with w from 10 to 30 mm and t from 4 to
8 mm. The area C / t² falls as t grows along the limit, but w = C / t³ stays below 10 mm,
so w sits at its lower bound and t = (2e-9 / 0.01)^(1/3) = 5.848 mm: 58.48 mm².

Rosenbrock's valley, (1 − x)² + 100 (y − x²)², an Expression over two parameters (the
stand-in study only stores a run), has its minimum 0 at (1, 1).
"""

from __future__ import annotations

import threading

import pytest
from test_optimise import Store, deflection, project, stand_in

from puzzle import optimise
from puzzle.model.schema import (
    DesignOutput,
    DesignVariable,
    Operator,
    ParameterSet,
    Quantity,
)

T_BEST = (2e-9 / 0.01) ** (1 / 3)  # 5.848 mm


def mm(v):
    return Quantity(value=v, unit="mm")


def run(p, opt, tmp_path, store=None, **kw):
    store = store or Store()
    kw.setdefault("solve", stand_in(store))
    return optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, **kw), store


@pytest.mark.parametrize("method", ["cobyla", "nelder_mead"])
def test_local_methods_find_the_lightest_beam(tmp_path, method):
    p, opt = project(method, max_solves=120)
    pts, _ = run(p, opt, tmp_path)
    top = optimise.best(opt, pts)
    assert top.labels["w"] == "10 mm"
    assert top.values["t"] == pytest.approx(T_BEST, rel=2e-3)
    assert top.objective == pytest.approx(10 * T_BEST * 1e3, rel=2e-3)
    assert deflection(*top.values.values()) <= 1e-3  # the best is feasible
    # each point's values are on the variables' grids (0.01 mm and 0.001 mm here), so a
    # point is found again by its run
    assert all(abs(v / 1e-6 - round(v / 1e-6)) < 1e-6 for pt in pts for v in pt.values.values())
    assert pts.ended == "converged" and len(pts) < 120
    assert len({pt.title for pt in pts}) == len(pts)  # each point once


def rosenbrock(method, **extra):
    from uuid import uuid4

    from puzzle.model.schema import Optimisation, Project, Study

    p = Project("r")
    p.add(ParameterSet(name="Valley", values={"x": Quantity(value=-1.2),
                                              "y": Quantity(value=1.0)}))  # fmt: skip
    study = p.add(Study(name="S", component=uuid4(), solver="calculix", steps=[]))
    f = p.add(Operator(name="Rosenbrock", type="expression",
                       expression="(1 - x)**2 + 100 * (y - x**2)**2"))  # fmt: skip
    opt = p.add(Optimisation(
        name="Valley", study=study.id, method=method, objective=DesignOutput(source=f.id),
        variables=[DesignVariable(name=n, low=Quantity(value=-2), high=Quantity(value=2))
                   for n in ("x", "y")], **extra))  # fmt: skip
    return p, opt


def test_nelder_mead_and_differential_evolution_on_rosenbrock(tmp_path):
    p, opt = rosenbrock("nelder_mead", max_solves=400, tolerance=1e-4)
    store = Store()
    pts, _ = run(p, opt, tmp_path, store)
    top = optimise.best(opt, pts)
    assert top.values["x"] == pytest.approx(1, abs=0.02)
    assert top.values["y"] == pytest.approx(1, abs=0.04) and top.objective < 1e-3
    p, opt = rosenbrock("differential_evolution", population=20, generations=40, seed=1)
    store = Store()
    pts, _ = run(p, opt, tmp_path, store)
    top = optimise.best(opt, pts)
    assert top.objective < 0.05 and top.values["x"] == pytest.approx(1, abs=0.2)
    assert len(pts) <= optimise.limit(opt) == 20 * 41


def test_differential_evolution_with_a_catalogue_of_thicknesses(tmp_path):
    # t from a catalogue of whole millimetres: t 6 mm needs w ≥ 9.26 mm, so w = 10 mm
    # and 60 mm² (t 5 mm needs w 16 mm, 80 mm²; t 7 mm, 70 mm²).
    p, opt = project("differential_evolution", population=10, generations=15, seed=2)
    opt.variables[1] = DesignVariable(name="t", kind="choice",
                                      choices=[mm(v) for v in (4, 5, 6, 7, 8)])  # fmt: skip
    pts, _ = run(p, opt, tmp_path)
    top = optimise.best(opt, pts)
    assert top.labels["t"] == "6 mm"  # evolution ends near, not on, the bound
    assert top.values["w"] == pytest.approx(0.01, rel=0.01)
    assert top.objective == pytest.approx(60, rel=0.01)
    assert {pt.labels["t"] for pt in pts} <= {f"{v} mm" for v in (4, 5, 6, 7, 8)}


def test_an_optimiser_resumes_from_its_stored_points(tmp_path):
    p, opt = project("cobyla", max_solves=120)
    whole, _ = run(p, opt, tmp_path)
    store, calls = Store(), []
    solve = stand_in(store, calls=calls)
    first, _ = run(p, opt, tmp_path, store, solve=solve, budget=6)
    assert len(first) == 6 and len(calls) == 6 and first.ended == "past this run's budget"
    table = optimise.points(p, store, opt)  # no solving: replayed up to the first not run
    assert [pt.title for pt in table] == [pt.title for pt in first]
    assert table.ended == "not run yet"
    second, _ = run(p, opt, tmp_path, store, solve=solve)
    assert [pt.title for pt in second] == [pt.title for pt in whole]  # the same path
    assert len(calls) == len(whole)  # every point solved once, over both runs
    assert [pt.objective for pt in optimise.points(p, store, opt)] == pytest.approx(
        [pt.objective for pt in whole])  # fmt: skip


def test_stop_and_failed_points_in_an_optimiser(tmp_path):
    p, opt = project("nelder_mead")
    stop = threading.Event()
    stop.set()
    pts, store = run(p, opt, tmp_path, cancel=stop)
    assert not store.records and not pts and pts.ended == "stopped before it ran"
    # a mesh refused above 22 mm wide (the first simplex reaches 25 mm): those points are
    # infeasible with the reason, and the rest go on
    pts, _ = run(p, opt, tmp_path, solve=stand_in(Store(), fail=lambda w, t: w > 0.022))
    failed = [pt for pt in pts if not pt.run]
    assert failed and all(pt.reason == "mesh_failed: the mesh was refused" for pt in failed)
    assert optimise.best(opt, pts).labels["w"] == "10 mm"


def test_a_failed_point_steers_the_optimiser_away(tmp_path):
    # the start, w 20 mm, fails (a failed point says nothing, so a region of them would
    # leave nothing to follow); COBYLA still finds the lightest feasible beam
    p, opt = project("cobyla", max_solves=150)
    store = Store()
    pts, _ = run(p, opt, tmp_path, store,
                 solve=stand_in(store, fail=lambda w, t: abs(w - 0.02) < 1e-9))  # fmt: skip
    assert pts[0].reason == "mesh_failed: the mesh was refused" and not pts[0].feasible
    top = optimise.best(opt, pts)
    assert top.labels["w"] == "10 mm" and top.values["t"] == pytest.approx(T_BEST, rel=5e-3)


def test_choices_are_refused_by_the_local_methods():
    from puzzle.errors import PuzzleError

    p, opt = project("cobyla")
    opt.variables[1] = DesignVariable(name="t", kind="choice", choices=[mm(4), mm(6)])
    with pytest.raises(PuzzleError, match="t is a list of choices"):
        optimise.check(p, opt)


def test_resolution_and_snap():
    v = DesignVariable(name="t", low=mm(4), high=mm(10))
    assert optimise.resolution(v) == pytest.approx(1e-6)  # 0.001 mm: a round thousandth
    assert optimise.snap(v, 0.0058483) == 0.005848
    assert optimise.snap(v, 0.02) == 0.01  # inside the bounds
    v = DesignVariable(name="t", low=mm(4), high=mm(10), resolution=mm(0.5))
    assert optimise.snap(v, 0.00574) == 0.0055
    n = DesignVariable(name="n", kind="integer", low=Quantity(value=1.5),
                       high=Quantity(value=6))  # fmt: skip
    assert optimise.snap(n, 1.2) == 2 and optimise.snap(n, 4.6) == 5


def test_the_pareto_front_of_a_design_of_experiments(tmp_path):
    # Area against deflection over a 3 x 3 grid: a lighter beam bends more. With a loose
    # limit (every point feasible), the front is the points no other is both lighter
    # and stiffer than.
    p, opt = project(levels=3)
    opt.constraints[0].upper = mm(100)
    pts, _ = run(p, opt, tmp_path)
    front = optimise.pareto(opt, pts)
    by_hand = []
    for a in pts:
        da = deflection(a.values["w"], a.values["t"])
        if not any(b.objective <= a.objective and deflection(b.values["w"], b.values["t"]) <= da
                   and b is not a and (b.objective, deflection(b.values["w"], b.values["t"]))
                   != (a.objective, da) for b in pts):  # fmt: skip
            by_hand.append(a.title)
    assert sorted(pt.title for pt in front) == sorted(by_hand)
    assert [pt.objective for pt in front] == sorted(pt.objective for pt in front)
    # the thinnest-and-narrowest (lightest) and the widest-and-thickest (stiffest) ends
    assert front[0].labels == {"w": "10 mm", "t": "4 mm"}
    assert front[-1].labels == {"w": "30 mm", "t": "8 mm"}
    assert optimise.better(opt, "objective") == 1 and optimise.better(opt, "constraint 1") == 1
    opt.constraints.append(DesignOutput(source=opt.constraints[0].source, lower=mm(0.1)))
    assert optimise.better(opt, "constraint 2") == -1


def test_the_outline_says_an_optimisers_budget():
    from puzzle.commands.outline import outline

    p, opt = project("cobyla")
    assert "COBYLA of Beam (up to 50 points) over w 10..30 mm" in outline(p)
    opt.method, opt.population, opt.generations = "differential_evolution", 10, 4
    assert "Differential evolution of Beam (up to 50 points)" in outline(p)  # 10 x (4 + 1)
    opt.method, opt.levels = "full_factorial", 2
    assert "Full factorial of Beam (4 points)" in outline(p)


# --- phase 3: Bayesian optimisation and response surfaces -------------------------


@pytest.mark.parametrize("method", ["bayesian", "response_surface"])
def test_modelled_methods_find_the_lightest_beam_in_few_solves(tmp_path, method):
    # from 8 Latin hypercube points, a few more by the model: within 0.5 % of 58.48 mm²
    p, opt = project(method, samples=8)
    pts, store = run(p, opt, tmp_path)
    top = optimise.best(opt, pts)
    assert top.labels["w"] == "10 mm"
    assert top.objective == pytest.approx(10 * T_BEST * 1e3, rel=5e-3)
    assert len(pts) <= 14 and pts.ended.startswith("converged")
    assert [pt.origin for pt in pts[:8]] == ["initial plan"] * 8
    assert {pt.origin for pt in pts[8:]} == {"expected improvement" if method == "bayesian"
                                            else "surface optimum"}  # fmt: skip
    # the start is the Latin hypercube design of the same seed and size: shared runs
    lhs = optimise.plan(opt.model_copy(update={"method": "latin_hypercube"}))
    assert [pt.values for pt in pts[:8]] == lhs
    # replayed from the store, the same points (and the table is cached until it changes)
    again = optimise.points(p, store, opt)
    assert [pt.title for pt in again] == [pt.title for pt in pts]
    assert optimise.points(p, store, opt) is again


def test_a_bayesian_run_resumes_on_its_path(tmp_path):
    p, opt = project("bayesian", samples=6)
    whole, _ = run(p, opt, tmp_path)
    store, calls = Store(), []
    solve = stand_in(store, calls=calls)
    run(p, opt, tmp_path, store, solve=solve, budget=8)
    second, _ = run(p, opt, tmp_path, store, solve=solve)
    assert [pt.title for pt in second] == [pt.title for pt in whole]
    assert len(calls) == len(whole)


def test_surface_fit_is_shown(tmp_path):
    # the area w t is a quadratic in (w, t), so a quadratic surface fits it exactly;
    # the deflection, 1/(w t³), it does not
    p, opt = project("response_surface", samples=8, surface="quadratic")
    pts, _ = run(p, opt, tmp_path)
    fit = optimise.fit_quality(opt, pts)
    assert fit["objective"] < 1e-9 and 0.01 < fit["constraint 1"] < 0.2
    p, opt = project("response_surface", samples=4, surface="quadratic")
    from puzzle.errors import PuzzleError

    with pytest.raises(PuzzleError, match="has 6 coefficients"):
        optimise.check(p, opt)


def test_surrogates_on_known_functions():
    import numpy as np

    from puzzle import surrogates

    rng = np.random.default_rng(0)
    x = rng.random((12, 2))
    y = np.sin(3 * x[:, 0]) + x[:, 1] ** 2
    gp = surrogates.fit_gaussian(x, y)
    mu, sd = gp.predict(x)
    assert np.allclose(mu, y, atol=1e-3) and np.all(sd < 0.05)  # through its data
    far = gp.predict(np.array([[3.0, 3.0]]))[1][0]
    assert far > 5 * sd.max()  # unsure away from it
    q = surrogates.Surface("quadratic", x, 1 + 2 * x[:, 0] - x[:, 1] + 3 * x[:, 0] * x[:, 1])
    assert q(np.array([[0.5, 0.5]]))[0] == pytest.approx(1 + 1 - 0.5 + 0.75)
    assert surrogates.expected_improvement(np.array([0.0]), np.array([1e-9]), 1.0)[0] == (
        pytest.approx(1.0))  # fmt: skip
    assert surrogates.feasibility(np.array([0.0]), np.array([1.0]))[0] == pytest.approx(0.5)


# --- phase 4: robust design ------------------------------------------------------------


def robust_beam(design=True, **extra):
    """The beam with w 10 mm and the tip load F scattering normally, 100 N ± 10 N (one
    standard deviation)."""
    from puzzle.model.schema import Scatter

    p, opt = project("robust", samples=12, **extra)
    ps = next(o for o in p.objects.values() if isinstance(o, ParameterSet))
    ps.values.update({"F": Quantity(value=100, unit="N"), "w": Quantity(value=10, unit="mm"),
                      "t": Quantity(value=6, unit="mm")})  # fmt: skip
    load = DesignVariable(name="F", kind="random",
                          scatter=Scatter(spread=Quantity(value=10, unit="N")))  # fmt: skip
    opt.variables = ([opt.variables[1]] if design else []) + [load]
    return p, opt


def test_uncertainty_at_the_current_design_by_hand(tmp_path):
    # δ ∝ F: at t 6 mm the deflection is normal, mean δ(100 N) = 0.9259 mm, standard
    # deviation 10 % of it; P(δ ≤ 1.1 × mean) = Φ(1) = 0.8413
    p, opt = robust_beam(design=False)
    nominal = deflection(0.01, 0.006)
    opt.objective = None
    opt.constraints[0].upper = Quantity(value=1.1 * nominal * 1e3, unit="mm")
    pts, store = run(p, opt, tmp_path)
    got = optimise.robust_summary(p, opt, pts)["outputs"]["constraint 1"]
    assert got["mean"] == pytest.approx(nominal, rel=2e-3)
    assert got["sd"] == pytest.approx(0.1 * nominal, rel=0.03)
    assert got["holds"] == pytest.approx(0.8413, abs=0.01)
    assert optimise.best(opt, pts) is None  # nothing chosen, nothing to apply
    assert [pt.title for pt in optimise.points(p, store, opt)] == [pt.title for pt in pts]


def test_robust_design_holds_its_limit_at_a_probability(tmp_path):
    # δ ≤ 1 mm at 99 %: F's 99th percentile is 100 + 2.326 × 10 = 123.26 N, so by hand
    # t = (4 × 123.26 N × L³ / (E w × 1 mm))^(1/3) = 6.272 mm (5.848 mm at the mean load)
    p, opt = robust_beam()
    opt.constraints[0].probability = 0.99
    pts, _ = run(p, opt, tmp_path)
    summary = optimise.robust_summary(p, opt, pts)
    t = summary["design"]["t"]
    assert t == pytest.approx(0.006272, rel=2e-3) and pts.ended.startswith("converged")
    assert summary["outputs"]["constraint 1"]["holds"] == pytest.approx(0.99, abs=0.003)
    assert {pt.origin for pt in pts} == {"initial plan", "robust design", "probability check"}
    top = optimise.best(opt, pts)
    assert top.values["t"] == t
    # applying it sets t only: the load stays as it is, random
    edits = optimise.apply_commands(p, opt, top)
    assert [op["path"] for c in edits for op in c["ops"]] == ["values.t"]
    assert "F =" not in optimise.suggestion(p, opt, pts)


def test_robust_objectives_and_refusals():
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Scatter

    p, opt = robust_beam()
    opt.variables = opt.variables[:1]  # nothing scatters
    with pytest.raises(PuzzleError, match="needs something that scatters"):
        optimise.check(p, opt)
    p, opt = project("cobyla")
    opt.variables.append(DesignVariable(name="w", kind="random",
                                        scatter=Scatter(cv=0.05)))  # fmt: skip
    with pytest.raises(PuzzleError, match="only robust design reads"):
        optimise.check(p, opt)
    with pytest.raises(ValueError, match="its spread or its cv"):
        Scatter()
    with pytest.raises(ValueError, match="needs its scatter"):
        DesignVariable(name="F", kind="random")


def test_the_random_operator():
    import numpy as np

    from puzzle.model.schema import OperatorInput
    from puzzle.operators import evaluate as ev

    p = project()[0]
    q = {"mean": Quantity(value=100, unit="N"), "spread": Quantity(value=10, unit="N"),
         "count": Quantity(value=4000)}  # fmt: skip
    for dist, sd in (("normal", 10), ("uniform", 10 / 3**0.5), ("lognormal", 10)):
        op = p.add(Operator(name=f"Load {dist}", type="random", distribution=dist, seed=3,
                            inputs={k: OperatorInput(value=v) for k, v in q.items()}))  # fmt: skip
        got = ev.source(ev.Context(p, None), op.id, "out")
        assert got.kind == "force" and len(got.values) == 4000
        assert np.mean(got.values) == pytest.approx(100, rel=0.01)
        assert np.std(got.values) == pytest.approx(sd, rel=0.05)
        again = ev.source(ev.Context(p, None), op.id, "out")
        assert np.array_equal(again.values, got.values)  # seeded


def test_what_the_panel_plots(tmp_path):
    import numpy as np

    p, opt = robust_beam()
    opt.constraints[0].probability = 0.99
    pts, _ = run(p, opt, tmp_path)
    hist = optimise.distribution(p, opt, pts, "constraint 1")
    xs, ys = np.array(hist).T
    assert np.sum(ys) * (xs[1] - xs[0]) == pytest.approx(1, rel=0.02)  # a density
    peak = xs[np.argmax(ys)]  # δ ∝ F, so its mode is the mean load's deflection
    assert peak == pytest.approx(deflection(0.01, optimise.best(opt, pts).values["t"]), rel=0.05)
    line, dots = optimise.surface_slice(p, opt, pts, "constraint 1", "t")
    assert len(line) == 60 and len(dots) == len([pt for pt in pts if pt.run])
    t, d = np.array(line).T  # the surface along t at the mean load: 1/t³ by hand, close
    k = int(np.argmin(abs(t - optimise.best(opt, pts).values["t"])))  # where it was checked
    assert d[k] == pytest.approx(deflection(0.01, t[k]), rel=0.01)
    p, opt = project("response_surface", samples=8, surface="quadratic")
    pts, _ = run(p, opt, tmp_path)
    line, _ = optimise.surface_slice(p, opt, pts, "objective", "t")
    w = optimise.best(opt, pts).values["w"]  # area w t, a quadratic fits it exactly:
    assert line[0][1] == pytest.approx(w * line[0][0] * 1e6, rel=1e-6)  # a line in t
    assert line[-1][1] == pytest.approx(w * line[-1][0] * 1e6, rel=1e-6)


def test_the_forms_robust_syntax():
    pytest.importorskip("PySide6")
    from puzzle.gui.optimisation_panel import parse_constraints, parse_variables

    got = parse_variables("t: 4 mm .. 8 mm uniform ± 0.1 mm\nF: random ± 10 N\n"
                          "E: random lognormal 5 %\nn: 2 .. 6 integer")  # fmt: skip
    assert got[0] == {
        "name": "t",
        "kind": "continuous",
        "low": {"value": 4, "unit": "mm"},
        "high": {"value": 8, "unit": "mm"},
        "scatter": {"distribution": "uniform", "spread": {"value": 0.1, "unit": "mm"}},
    }
    assert got[1] == {
        "name": "F",
        "kind": "random",
        "scatter": {"distribution": "normal", "spread": {"value": 10, "unit": "N"}},
    }
    assert got[2]["scatter"] == {"distribution": "lognormal", "cv": 0.05}
    assert got[3]["kind"] == "integer" and "scatter" not in got[3]
    for v in got:
        DesignVariable(**v)  # each one the schema takes
    with pytest.raises(ValueError, match="says how it scatters"):
        parse_variables("F: random")
    c = parse_constraints("Tip <= 1 mm at 99.9 %\nTip <= 2 mm", {"Tip": "id"})
    assert c[0]["probability"] == pytest.approx(0.999) and c[0]["upper"]["value"] == 1
    assert "probability" not in c[1]
