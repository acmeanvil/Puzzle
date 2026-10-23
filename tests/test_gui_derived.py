"""Derived results in the GUI and for agents (ADR 0025), on the cantilever's Tip load.

The cantilever is linear, so 2 × Tip load − 0.5 × Tip load is 1.5 × Tip load exactly:
deflection and von Mises stress both scale by 1.5. An equation `von_mises / 1e6` is the
von Mises stress in MPa; one over the combination reads the combination's results.
"""

import asyncio
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import ResultDefinition, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

pytestmark = [
    pytest.mark.usefixtures("engineering_units"),
    pytest.mark.skipif(not available("calculix"), reason="calculix not available"),
]

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    next(s for s in p.of_type(Study) if s.name == "Tip load").solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    for item in w.tree._items():
        node = item.data(0, ROLE)
        if node.kind == "object" and node.label == "Tip load":
            w.tree.setCurrentItem(item)
    w.run_selected()
    end = time.monotonic() + 600
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    yield w
    w.close()


def tip(window) -> Study:
    return next(s for s in window.session.project.of_type(Study) if s.name == "Tip load")


def derived_named(window, name):
    return next(d for d in window.session.project.of_type(ResultDefinition) if d.name == name)


def test_combination_and_equation_in_the_gui(window):
    study = str(tip(window).id)

    def combination(dialog):
        dialog.name.setText("1.5 x tip")
        for r, coef in ((0, "2"), (1, "-0.5")):
            dialog.table.cellWidget(r, 3).setText(coef)
        assert dialog.table.cellWidget(0, 0).currentText() == "Tip load"
        assert dialog.table.cellWidget(0, 2).itemText(1).startswith("1: t = ")
        assert dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()
        return True

    window.run_dialog = combination
    window.new_derived("linear", study)
    combo = derived_named(window, "1.5 x tip")
    assert [c.coefficient for c in combo.cases] == [2.0, -0.5]
    assert window.session.history(limit=1)[0]["command"]["command"] == "create"
    node = window.tree.current()
    assert node.kind == "derived" and node.label == "1.5 x tip"
    combined = window.viewport.shown
    assert combined["scalar"] == "Total deformation" and combined["unit"] == "mm"

    # The tip load's own deflection, from its run, is 1/1.5 of the combination's.
    run = next(i for i in window.tree._items() if i.data(0, ROLE).kind == "run")
    window.tree.setCurrentItem(run)
    assert combined["max"] == pytest.approx(1.5 * window.viewport.shown["max"], rel=1e-9)
    bar = window.results_bar
    bar.field.setCurrentIndex(bar.field.findData("stress:von_mises"))
    vm = window.viewport.shown["max"]  # MPa

    def equation(dialog):
        dialog.name.setText("Von Mises in MPa")
        dialog.source.setCurrentIndex(dialog.source.findData(str(combo.id)))
        shown = [dialog.names.item(i).text() for i in range(dialog.names.count())]
        assert any(t.startswith("von_mises  —  Equivalent (von Mises)") for t in shown)
        dialog.expression.setText("von_mise / 1e6")
        assert "did you mean von_mises" in dialog.status.text()
        dialog.expression.setText("von_mises / 1e6")
        assert dialog.status.text().startswith("From ")
        return True

    window.run_dialog = equation
    window.new_derived("equation")
    eq = derived_named(window, "Von Mises in MPa")
    assert window.viewport.shown["max"] == pytest.approx(1.5 * vm, rel=1e-6)
    assert window.results_bar.field.currentText() == "Von Mises in MPa"

    # Edit the combination: a third case is refused nothing, and one undo takes it back.
    def edit(dialog):
        dialog.add_case(study, None, -1, 1.0)
        return True

    window.run_dialog = edit
    window.new_derived("linear", definition=combo)
    assert len(derived_named(window, "1.5 x tip").cases) == 3
    window.undo()
    assert len(derived_named(window, "1.5 x tip").cases) == 2
    assert eq.source == combo.id


def test_agents_query_a_derived_result_like_a_study(window):
    from puzzle.agent.mcp import build_server
    from puzzle.commands.model import Create

    study = tip(window)
    window.submit(
        Create(
            kind="result",
            data={
                "name": "Envelope",
                "method": "envelope",
                "quantity": "stress:von_mises",
                "cases": [
                    {"study": str(study.id), "coefficient": 1.0},
                    {"study": str(study.id), "coefficient": -3.0},
                ],
            },
        )
    )
    server = build_server(window.session.root, "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    info = call("derived_result", name="Envelope")
    assert [r["field"] for r in info["results"]] == ["envelope", "envelope_case"]
    env = call("result_stats", study="Envelope", field="envelope")
    plain = call("result_stats", study="Tip load", field="stress")
    assert env["max"] == pytest.approx(3 * plain["max"], rel=1e-9)  # the −3 × case wins
    case = call("result_stats", study="Envelope", field="envelope_case")
    assert case["max"] == 2.0
    names = call("equation_names", study="Tip load")["results"]
    assert names["von_mises"] == "Equivalent (von Mises) stress" and "deformation_y" in names
    assert np.isfinite(env["mean"])
