"""Construction geometry in the GUI (ADR 0046), on a solved bar (CalculiX).

New plane… and New path… open panels beside the live view (Pick, then double-click
the model; on a result the point snaps to a mesh node) and make objects under
"Construction geometry"; selecting one draws it over the model. Section force… across
the plane reads the beam's shear, Result on a plane… cuts the view at the plane, and
Linearized stress… runs along the path.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import construction  # noqa: E402
from puzzle.gui import result_tools as tools  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    ConstructionGeometry,
    Feature,
    Physics,
    Quantity,
    ResultProbe,
    SolveStep,
    Study,
)
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _texts(window) -> dict:
    panel, out = window.properties, {}
    for i in range(panel.topLevelItemCount()):
        item = panel.topLevelItem(i)
        out[item.text(0)] = item.text(1)
    return out


def _select(window, label):
    for item in window.tree._items():
        if item.data(0, ROLE).label == label:
            window.tree.setCurrentItem(item)
            _settle(window)
            return item
    raise AssertionError(f"no {label} in the tree")


def _settle(window):
    """Wait for background work (the model's preview) to finish and show."""
    import time

    app = QApplication.instance()
    while window.tasks:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_planes_and_paths_feed_result_tools(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    held = Feature(type="fixed", selection="Bar/Root")
    project, comp = bar_project(
        write_bar_step(root / "bar.step"),
        [Physics(type="solid_mechanics", features=[held, force])],
        2.5,
    )
    steps = [SolveStep(type="stationary")]
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    run_study(project, study.id, root, tmp_path / "w", store=ResultStore(root / "results"))
    w = MainWindow(root)
    try:
        plane = construction.new_plane(w)  # a panel beside the live view
        assert w.side.currentWidget() is plane
        plane.origin.text.setText("0.1, 0, 0")
        plane.facing.setCurrentIndex(plane.facing.findData("x"))
        plane.name.setText("Mid-span")
        plane.create()
        assert w.side.currentWidget() is w.properties

        made = {c.name: c for c in w.session.project.of_type(ConstructionGeometry)}
        assert made["Mid-span"].type == "plane"
        assert tuple(made["Mid-span"].normal) == (1.0, 0.0, 0.0)
        _select(w, "Mid-span")
        assert w.viewport.shown.get("construction") == "plane"

        for item in w.tree._items():  # show a result, as a user would first
            if item.data(0, ROLE).kind == "run":
                w.tree.setCurrentItem(item)
        app.processEvents()
        # A path picked on the result, snapping to mesh nodes: Pick, then double-click.
        path = construction.new_path(w)
        assert path.snap() == "node"  # a result is shown: its nodes
        path.start.pick.setChecked(True)
        assert w.viewport.capture is not None
        w.viewport._captured((0.1004, 0.0003, 0.0051))  # where the double-click landed
        start = path.start.value()
        mesh = w.viewport.node_mesh()
        assert min(np.linalg.norm(mesh.coords - start, axis=1)) < 1e-12  # on a node
        assert w.viewport.capture is None and not path.start.pick.isChecked()
        path.end.text.setText("0.1, 0.01, 0.005")
        path.name.setText("Depth")
        path.create()
        made = {c.name: c for c in w.session.project.of_type(ConstructionGeometry)}
        assert made["Depth"].type == "path"
        _select(w, "Depth")
        assert w.viewport.shown.get("construction") == "path"
        for item in w.tree._items():  # back to the result
            if item.data(0, ROLE).kind == "run":
                w.tree.setCurrentItem(item)
        app.processEvents()
        plane = str(made["Mid-span"].id)
        fill(w, plane=plane)
        tools.add_section_force(w)
        fill(w, plane=plane, quantity="stress:xx", reduction="max")
        tools.add_plane_result(w)
        fill(w, along=str(made["Depth"].id))
        tools.add_linearized(w)
        probes = {p.type: p for p in w.session.project.of_type(ResultProbe)}
        assert probes["section"].construction == made["Mid-span"].id
        assert probes["linearized"].construction == made["Depth"].id

        _select(w, probes["section"].name)
        app.processEvents()
        rows = _texts(w)
        assert rows["Across"] == "Mid-span"
        assert float(rows["Force Y"].split()[0]) == pytest.approx(-100, rel=0.05)
        assert w.viewport.cut is not None and w.viewport.cut.origin is not None

        _select(w, probes["plane"].name)
        app.processEvents()
        assert w.viewport.shown.get("cut") is not None
        _select(w, probes["linearized"].name)
        app.processEvents()
        rows = _texts(w)
        assert rows["Along"] == "Depth" and "Membrane + bending" in rows
        assert w.viewport.cut is None  # leaving a plane tool removes its cut
    finally:
        w.close()
