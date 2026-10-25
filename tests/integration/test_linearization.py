"""Stress linearization across a cantilever's depth (CalculiX), against beam theory.

Steel bar 200 x 10 x 10 mm fixed at x = 0, 100 N down at the tip. Across the depth at
mid-span the bending moment is 10 N m, so the linearized bending stress at the faces is
M (h/2) / I = 60 MPa (compression below, tension above), the normal membrane stress is
zero, and the membrane shear is the mean shear V / A = 1 MPa.
"""

import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import available

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import ResultProbe
from puzzle.post.probes import evaluate
from puzzle.project.layout import RESULTS_DIR
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

L, B, H = BAR
FORCE = 100.0


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_bending_across_the_depth_matches_beam_theory(tmp_path):
    step = write_bar_step(tmp_path / "bar.step")
    fixed = Feature(type="fixed", selection="Bar/Root")
    load = Feature(
        type="force", selection="Bar/Tip", values={"y": Quantity(value=-FORCE, unit="N")}
    )
    project, comp = bar_project(
        step, [Physics(type="solid_mechanics", features=[fixed, load])], mesh_size_mm=1.25
    )
    study = project.add(
        Study(name="s", component=comp.id, solver="calculix",
              steps=[SolveStep(type="stationary")])
    )  # fmt: skip
    store = ResultStore(tmp_path / RESULTS_DIR)
    run_study(project, study.id, tmp_path, tmp_path / "run", store=store, timeout_s=300)
    x = L / 2
    probe = project.add(
        ResultProbe(
            name="Across the depth",
            source=study.id,
            type="linearized",
            point=(x, 0.0, B / 2),
            end=(x, H, B / 2),
            samples=47,
        )
    )
    found = evaluate(project, store, probe)
    bending = FORCE * (L - x) * (H / 2) / (B * H**3 / 12)
    start, end = found["parts"]["start"], found["parts"]["end"]
    assert start["bending"]["components"]["xx"] == pytest.approx(-bending, rel=0.02)
    assert end["bending"]["components"]["xx"] == pytest.approx(bending, rel=0.02)
    assert abs(start["membrane"]["components"]["xx"]) < 0.01 * bending
    assert abs(start["membrane"]["components"]["xy"]) == pytest.approx(FORCE / (B * H), rel=0.1)
    assert found["value"] == pytest.approx(bending, rel=0.03)
    assert found["thickness"] == pytest.approx(H)
