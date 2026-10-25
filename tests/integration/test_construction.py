"""Construction geometry (ADR 0046) on a CalculiX cantilever, against beam theory.

Steel bar 200 x 10 x 10 mm fixed at x = 0, 100 N down (-y) at the tip. A plane across
the bar at mid-span carries the shear and moment there: the tip side passes on 100 N
down and a 10 N m moment about z; its largest bending stress is M (h/2) / I = 60 MPa.
A construction path through the depth linearizes to the same bending stress.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import available

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import ConstructionGeometry, CoordinateSystem, ResultProbe
from puzzle.post.probes import evaluate
from puzzle.project.layout import RESULTS_DIR
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

L, B, H = BAR
FORCE = 100.0
X = L / 2
MOMENT = FORCE * (L - X)
BENDING = MOMENT * (H / 2) / (B * H**3 / 12)

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    root = tmp_path_factory.mktemp("construction")
    step = write_bar_step(root / "bar.step")
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
    store = ResultStore(root / RESULTS_DIR)
    run_study(project, study.id, root, root / "run", store=store, timeout_s=300)
    return project, study, store


def _probe(project, study, **data):
    return project.add(ResultProbe(name=data.pop("name", "t"), source=study.id, **data))


def test_section_force_across_a_plane(solved):
    project, study, store = solved
    plane = project.add(
        ConstructionGeometry(name="Mid", type="plane", origin=(X, 0, 0), normal=(1, 0, 0))
    )
    found = evaluate(project, store, _probe(project, study, type="section",
                                            construction=plane.id))  # fmt: skip
    f, m = found["components"], found["moment"]
    assert f["y"] == pytest.approx(-FORCE, rel=0.02)
    assert abs(f["x"]) < 0.01 * FORCE and abs(f["z"]) < 0.01 * FORCE
    assert abs(m["z"]) == pytest.approx(MOMENT, rel=0.02)
    assert found["area"] == pytest.approx(B * H, rel=1e-6)
    assert found["shear_force"] == pytest.approx(FORCE, rel=0.02)


def test_a_plane_from_a_coordinate_system(solved):
    project, study, store = solved
    axes = project.add(CoordinateSystem(name="At mid-span", origin=(X, 0, 0)))
    plane = project.add(
        ConstructionGeometry(name="Axes yz", type="plane", axes=axes.id, plane="yz")
    )
    origin, normal = plane.frame(project)
    assert np.allclose(origin, (X, 0, 0)) and np.allclose(normal, (1, 0, 0))
    found = evaluate(project, store, _probe(project, study, type="section",
                                            construction=plane.id))  # fmt: skip
    assert found["components"]["y"] == pytest.approx(-FORCE, rel=0.02)


def test_result_over_a_plane(solved):
    project, study, store = solved
    plane = project.add(
        ConstructionGeometry(name="Mid 2", type="plane", origin=(X, 0, 0), normal=(1, 0, 0))
    )
    top = evaluate(project, store, _probe(project, study, type="plane", construction=plane.id,
                                          quantity="stress:xx", reduction="max"))  # fmt: skip
    assert top["value"] == pytest.approx(BENDING, rel=0.03)
    assert top["point"][1] == pytest.approx(H, abs=1e-6)  # tension on top
    mean = evaluate(project, store, _probe(project, study, type="plane", construction=plane.id,
                                           quantity="stress:xx", reduction="mean"))  # fmt: skip
    assert abs(mean["value"]) < 0.01 * BENDING


def test_linearization_along_a_construction_path(solved):
    project, study, store = solved
    path = project.add(
        ConstructionGeometry(name="Depth", type="path", start=(X, 0, B / 2), end=(X, H, B / 2))
    )
    found = evaluate(project, store, _probe(project, study, type="linearized",
                                            construction=path.id, samples=47))  # fmt: skip
    assert found["parts"]["end"]["bending"]["components"]["xx"] == pytest.approx(BENDING, rel=0.02)
