"""Design of experiments on a real solve (ADR 0063): a steel cantilever, L = 200 mm,
w = 20 mm, thickness t from 4 to 10 mm, a 100 N tip load. The objective is the
thinnest t (an Expression over the parameter), the constraint a tip deflection of at
most 2 mm. By hand, Euler-Bernoulli, δ = 4 F L³ / (E w t³), so δ ≤ 2 mm needs
t ≥ (4 F L³ / (E w · 2 mm))^(1/3) = 7.37 mm: of the 1 mm grid, 8 mm is the best point.
Every point's deflection is within 3 % of the closed form (shear and the mesh), and a
second run reuses every stored point.
"""

from __future__ import annotations

import pytest
from optimise_beam import LIMIT, F, L, W, beam_project
from solvers import available

from puzzle import optimise
from puzzle.project import save_project
from puzzle.project.layout import RESULTS_DIR
from puzzle.results.store import ResultStore

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")


def test_thinnest_cantilever_by_design_of_experiments(tmp_path):
    pytest.importorskip("OCP")
    p, opt = beam_project()
    root = tmp_path / "beam"
    save_project(p, root)
    store = ResultStore(root / RESULTS_DIR)
    steel = next(m for m in p.objects.values() if m.kind == "material")
    E = steel.properties["youngs_modulus"].to_si()
    pts = optimise.run(p, opt, root, tmp_path / "w", store=store)
    assert len(pts) == 7 and all(pt.run for pt in pts)
    for pt in pts:
        t = pt.values["t"]
        by_hand = 4 * F * L**3 / (E * W * t**3)
        assert pt.constraints[0] == pytest.approx(by_hand, rel=0.03), pt.title
        assert pt.feasible == (by_hand <= LIMIT)
    top = optimise.best(opt, pts)
    assert top.labels == {"t": "8 mm"} and top.objective == pytest.approx(0.008)
    assert optimise.suggestion(p, opt, pts).startswith("t = 8 mm: Thickness 0.008 m")  # SI shown
    runs = len(store.runs())
    again = optimise.run(p, opt, root, tmp_path / "w2", store=store)  # resume: all stored
    assert len(store.runs()) == runs and [a.runs for a in again] == [b.runs for b in pts]


@pytest.mark.parametrize("method", ["bayesian", "response_surface"])
def test_thinnest_cantilever_by_a_model_of_the_outputs(tmp_path, method):
    # ADR 0063 phase 3: from four Latin hypercube points, the model picks the rest; the
    # best t sits on the 2 mm limit, within 4 % of 7.37 mm by hand (the tet mesh is a
    # few per cent stiffer than Euler-Bernoulli), in at most a dozen solves
    pytest.importorskip("OCP")
    p, opt = beam_project()
    opt.method, opt.samples = method, 4
    opt.variables[0].resolution = opt.variables[0].low.model_copy(update={"value": 0.01})
    root = tmp_path / "beam"
    save_project(p, root)
    store = ResultStore(root / RESULTS_DIR)
    pts = optimise.run(p, opt, root, tmp_path / "w", store=store)
    top = optimise.best(opt, pts)
    assert top is not None and len(pts) <= 12, [pt.title for pt in pts]
    assert top.values["t"] == pytest.approx(0.00737, rel=0.04)
    assert 0.98 * LIMIT <= top.constraints[0] <= LIMIT


def test_thinnest_cantilever_under_a_thickness_tolerance(tmp_path):
    # ADR 0063 phase 4: t made to a normal tolerance, standard deviation 0.1 mm, and the
    # 2 mm deflection held at 99 %. The deflection falls as t grows, so 99 % of parts
    # hold when t's 1st percentile does: by hand, the deterministic thinnest t on this
    # mesh (7.24 to 7.26 mm, the Bayesian and COBYLA answers) plus 2.326 × 0.1 mm.
    pytest.importorskip("OCP")
    from puzzle.model.schema import Quantity, Scatter

    p, opt = beam_project()
    opt.method, opt.samples = "robust", 6
    opt.variables[0].low = Quantity(value=6, unit="mm")
    opt.variables[0].high = Quantity(value=9, unit="mm")
    opt.variables[0].resolution = Quantity(value=0.01, unit="mm")
    opt.variables[0].scatter = Scatter(spread=Quantity(value=0.1, unit="mm"))
    opt.constraints[0].probability = 0.99
    root = tmp_path / "beam"
    save_project(p, root)
    store = ResultStore(root / RESULTS_DIR)
    pts = optimise.run(p, opt, root, tmp_path / "w", store=store)
    summary = optimise.robust_summary(p, opt, pts)
    t = summary["design"]["t"]
    assert t == pytest.approx(0.00725 + 2.326e-4, abs=4e-5), [pt.title for pt in pts]
    assert summary["outputs"]["constraint 1"]["holds"] == pytest.approx(0.99, abs=0.005)
    assert len(pts) <= 26
