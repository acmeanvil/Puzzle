"""Coordinate systems in the GUI (ADR 0049): the New coordinate system… panel (by
directions, rotation angles or three picked points), the triad drawn when one is
selected, Properties showing only how it is defined, and results read in one."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import menus  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    CoordinateSystem,
    Feature,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _select(window, label):
    for item in window.tree._items():
        if item.data(0, ROLE).label == label:
            window.tree.setCurrentItem(item)
            QApplication.instance().processEvents()
            return item
    raise AssertionError(label)


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_systems_made_shown_and_used_by_results(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    load = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    held = Feature(type="fixed", selection="Bar/Root")
    physics = [Physics(type="solid_mechanics", features=[held, load])]
    project, comp = bar_project(write_bar_step(root / "bar.step"), physics, 10.0)
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(project, root)
    run_study(project, study.id, root, tmp_path / "w", store=ResultStore(root / "results"))
    w = MainWindow(root)
    try:
        # By rotation angles: 90° about z.
        panel = menus.new_axes(w, "cartesian")
        assert w.side.currentWidget() is panel
        panel.how.setCurrentIndex(panel.how.findData("angles"))
        panel.angles.setText("0, 0, 90")
        panel.name.setText("Turned")
        panel.create()
        # Cylindrical about the bar's axis (x), by directions.
        panel = menus.new_axes(w, "cylindrical")
        panel.origin.text.setText("0, 0.005, 0.005")
        panel.axis.setText("1, 0, 0")
        panel.name.setText("Bar axis")
        panel.create()
        # Spherical by three points (the origin picked: Pick, then a double-click).
        panel = menus.new_axes(w, "spherical")
        panel.how.setCurrentIndex(panel.how.findData("points"))
        panel.origin.pick.setChecked(True)
        w.viewport._captured((0.0, 0.0, 0.0))
        panel.on_x.text.setText("0.01, 0, 0")
        panel.on_xy.text.setText("0, 0.01, 0")
        panel.name.setText("Ball")
        panel.create()
        made = {c.name: c for c in w.session.project.of_type(CoordinateSystem)}
        assert np.allclose(made["Turned"].orientation()[:, 0], (0, 1, 0))
        assert made["Bar axis"].type == "cylindrical"
        assert made["Ball"].type == "spherical" and made["Ball"].points[0] == (0.0, 0.0, 0.0)

        _select(w, "Turned")
        assert w.viewport.shown.get("triad") == "cartesian"
        keys = [w.properties.topLevelItem(i).text(0)
                for i in range(w.properties.topLevelItemCount())]  # fmt: skip
        assert "Angles" in keys and "X axis" not in keys and "Points" not in keys

        # Results: global at first; in the cylindrical system the components are named
        # for it, and the values are the rotated ones.
        for item in w.tree._items():
            if item.data(0, ROLE).kind == "run":
                w.tree.setCurrentItem(item)
        app.processEvents()
        bar = w.results_bar
        assert bar.axes.currentData() is None and bar.axes.isVisibleTo(w)
        bar.field.setCurrentIndex(bar.field.findData("stress:yy"))
        bar.axes.setCurrentIndex(bar.axes.findData(str(made["Bar axis"].id)))
        app.processEvents()
        assert bar.field.currentText() == "Normal stress θ (hoop)"
        assert w.viewport.shown.get("scalar") == "Normal stress θ (hoop)"
        # Another run shown afresh starts in global axes again.
        for item in w.tree._items():
            if item.data(0, ROLE).kind == "run":
                w.tree.setCurrentItem(item.parent())
                w.tree.setCurrentItem(item)
        app.processEvents()
        assert bar.axes.currentData() is None
    finally:
        w.close()
