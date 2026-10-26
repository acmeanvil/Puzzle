"""Result tools in the GUI (ADR 0042), on the cantilever example (CalculiX).

With a result shown, the Results ribbon lists the tools. Reaction… on the support makes
a tool that sums to the 100 N tip load; it sits in the tree under Results and its
Properties list the force and moment. A result on a selection, a path and a linearized
stress are made the same way, the paths plotting against the distance in the Plots tab.
Construction paths and planes feed the line and plane tools. The support's own menu
offers Reaction force….
"""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import menus  # noqa: E402
from puzzle.gui import result_tools as tools  # noqa: E402
from puzzle.gui.tree import ROLE, Node  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, ResultProbe, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _texts(window) -> dict:
    panel, out = window.properties, {}
    for i in range(panel.topLevelItemCount()):
        item = panel.topLevelItem(i)
        out[item.text(0)] = item.text(1)
    return out


def _select(window, kind, label=None):
    for item in window.tree._items():
        node = item.data(0, ROLE)
        if node.kind == kind and (label is None or node.label == label):
            window.tree.setCurrentItem(item)
            return item
    raise AssertionError(f"no {kind} {label} in the tree")


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_result_tools_from_the_ribbon(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    study.solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    try:
        _select(w, "object", "Tip load")
        w.run_selected()
        while w.tasks:
            app.processEvents()
            time.sleep(0.02)
        assert w.context == "results" and w.results_ribbon.isVisibleTo(w)
        assert set(w.results_ribbon.buttons) >= {"probe", "reaction", "selection", "path"}

        fill(w, selection="Bar/Root")
        tools.add_reaction(w)
        (reaction,) = w.session.project.of_type(ResultProbe)
        assert reaction.type == "reaction" and reaction.name == "Reaction on Bar/Root"
        # The support's selection is offered first, named by its feature.
        assert w.last_form.widgets["selection"].itemText(0) == "Bar/Root: fixed support"
        item = _select(w, "probe", "Reaction on Bar/Root")
        assert item.text(0) == "Reaction on Bar/Root"
        app.processEvents()
        rows = _texts(w)
        assert rows["Force Y"] == "100 N" and rows["Force (total)"] == "100 N"
        assert rows["Moment Z"].startswith("20 N·m")

        fill(w, quantity="displacement:y", selection="Bar/Tip", reduction="mean")
        tools.add_selection_result(w)
        fill(w, quantity="displacement:y")
        tools.add_path(w)
        path = next(p for p in w.session.project.of_type(ResultProbe) if p.type == "path")
        assert path.point[0] == pytest.approx(0.0) and path.end[0] == pytest.approx(0.2)
        _select(w, "probe", path.name)
        app.processEvents()
        assert w.output_tabs.currentWidget() is w.plots
        ((name, pts),) = w.plots.series
        assert name == path.name and len(pts) == 50
        assert w.plots.curve.x_label.startswith("Distance along the path")

        # Linearized stress: across the thinnest side at mid-span, the beam's bending.
        fill(w, equivalent="tresca")
        tools.add_linearized(w)
        lin = next(p for p in w.session.project.of_type(ResultProbe) if p.type == "linearized")
        assert lin.equivalent == "tresca" and lin.point[0] == pytest.approx(0.1)
        assert lin.samples == 47
        _select(w, "probe", lin.name)
        app.processEvents()
        panel = w.linearization_panel  # the ASME breakdown, its table in Messages
        assert w.side.currentWidget() is panel
        assert panel.equivalent.currentData() == "tresca"
        assert w.output_tabs.currentWidget() is panel.report
        assert "Bending, start" in panel.text() and "\tXX\t" in panel.text()
        assert [n.split(": ")[1] for n, _ in w.plots.series] == [
            "total",
            "membrane",
            "membrane + bending",
        ]

        comp = w.session.project.of_type(Component)[0]
        fixed = next(j for j, f in enumerate(comp.physics[0].features) if f.selection == "Bar/Root")
        node = Node("feature", str(comp.id), f"physics.0.features.{fixed}")
        texts = [a.text() for a in menus.build_menu(w, node).actions()]
        assert "Reaction force…" in texts
    finally:
        w.close()
