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
