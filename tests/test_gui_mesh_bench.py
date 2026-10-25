"""The Mesh tab (ADR 0019): a component's recipe and its controls.

A local size is made from the ribbon (the selection a dropdown of the component's own
selections, the size in the display unit), applied as one command, listed, edited and
deleted from the list's menu. Meshing fills the quality box and colours the view by
element shape; double-clicking a worst element pins it in the view. The mesher toggles
between Gmsh and Netgen, and Netgen as Gmsh's optimiser is offered only when Settings
allows it.
"""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.gui.choices import choices  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import MeshRecipe  # noqa: E402

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
    w = MainWindow(root)
    yield w
    w.close()


def _wait(app, done, timeout=60):
    end = time.time() + timeout
    while not done() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert done()


def _recipe(window) -> MeshRecipe:
    return window.session.project.get(window.mesh_bench.component_obj().mesh, MeshRecipe)


def test_controls_are_made_listed_edited_and_deleted(app, window):
    bench = window.mesh_bench
    window.left_tabs.setCurrentWidget(bench)
    assert window.context == "mesh" and bench.ribbon.isVisibleTo(window)
    before = len(_recipe(window).controls)
    panel = bench.start_tool("size")
    what, box = panel.widgets["selection"]
    names = [box.itemText(i) for i in range(box.count())]
    assert "Bar/Tip" in names and all("/" in n for n in names)
    box.setCurrentIndex(box.findData("Bar/Tip"))
    panel.widgets["size"][1].setText("0.001")  # in the display unit (m by default)
    panel.apply()
    recipe = _recipe(window)
    assert len(recipe.controls) == before + 1
    assert recipe.controls[-1].describe() == "size 0.001 m on Bar/Tip"
    assert bench.controls.item(before).text().endswith("size 0.001 m on Bar/Tip")
    panel = bench.start_tool("size", before)  # editing keeps its place
    panel.widgets["size"][1].setText("0.002")
    panel.apply()
    assert _recipe(window).controls[before].size.value == pytest.approx(0.002)
    window.undo()
    assert _recipe(window).controls[before].size.value == pytest.approx(0.001)


def test_mesh_quality_and_worst_elements(app, window):
    bench = window.mesh_bench
    window.left_tabs.setCurrentWidget(bench)
    bench.mesh()
    _wait(app, lambda: not window.tasks)
    assert "elements. Shape: worst" in bench.quality.text()
    assert bench.worst.count() == 10
    assert window.viewport.shown.get("field") == "shape_quality" or window.viewport.result_state
    bench._show_worst(bench.worst.item(0))
    assert window.viewport.shown["pins"] == ["worst element"]


def test_mesher_and_optimiser_choices(app, window):
    bench = window.mesh_bench
    window.left_tabs.setCurrentWidget(bench)
    bench.set_mesher("netgen")
    assert _recipe(window).mesher == "netgen" and bench.mesher["netgen"].isChecked()
    recipe = _recipe(window)
    offered = [v for _, v in choices(window.session.project, recipe, "optimiser")]
    assert "netgen" in offered
    s = settings.current()
    s.set("meshing.netgen_optimiser", False)
    s.save()
    offered = [v for _, v in choices(window.session.project, recipe, "optimiser")]
    assert "netgen" not in offered
