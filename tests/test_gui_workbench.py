"""The Geometry tab (ADR 0017): a part made from a sketch, tools inline with a live
preview, edges and faces picked in the view, sketches edited in the main view.

The window's questions (`ask_text`, `choose`) and the sketch's dimension prompts are
answered by the test; everything else goes through the widgets' own methods.
"""

import math
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")
pytest.importorskip("py_slvs")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.geometry.history import body_signature, ops_of, regenerate  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.gui.workbench import ToolPanel  # noqa: E402
from puzzle.model.schema import Geometry  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow()
    w.new_project(tmp_path / "parts", "Parts")
    w.left_tabs.setCurrentWidget(w.workbench)
    yield w
    while w.tasks:
        app.processEvents()
    w.close()


def wait(app, window, timeout=120.0):
    end = time.monotonic() + timeout
    while window.tasks:
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.02)
    app.processEvents()


def answer(window, *replies):
    queue = list(replies)
    window.ask_text = lambda *a, **k: queue.pop(0)
    window.choose = lambda *a, **k: queue.pop(0)


def dims(workspace, *replies):
    queue = list(replies)
    workspace.ask_value = lambda *a: queue.pop(0)


def part(window, name="Plate"):
    return next(g for g in window.session.project.of_type(Geometry) if g.name == name)


def volume(window, name="Plate"):
    return body_signature(regenerate(ops_of(part(window, name))).bodies["Body"][0])[0]


def draw_plate(ws, w_mm=60, h_mm=30):
    """A rectangle from the origin, fixed there and dimensioned."""
    ws.set_tool("rect")
    ws.click(0.0, 0.0)
    ws.click(w_mm / 1000 * 0.9, h_mm / 1000 * 1.1)
    m = ws.model
    corner = next(n for n, (u, v) in m.spec["points"].items() if u == 0 and v == 0)
    bottom = next(n for n, (a, b) in m.spec["lines"].items() if m.point(a)[1] == m.point(b)[1] == 0)
    left = next(n for n, (a, b) in m.spec["lines"].items() if m.point(a)[0] == m.point(b)[0] == 0)
    ws.set_tool("select")
    ws.canvas.selected = [corner]
    ws.relate("fix")
    for line, value in ((bottom, w_mm), (left, h_mm)):
        ws.canvas.selected = [line]
        dims(ws, str(value))
        ws.dimension()
    assert ws.status.text() == "Fully constrained"


def test_a_new_part_is_sketched_then_extruded(app, window):
    answer(window, "Plate", "xy")
    window.workbench.new_part()
    ws = window.workbench.workspace
    assert window.centre.currentWidget() is ws  # sketching in the main view
    assert "Sketching a new sketch on the XY plane" in window.workbench.sketch_title.text()
    draw_plate(ws)
    window.workbench.finish_sketch()
    # The part exists with its sketch, and the extrusion opens on that sketch.
    assert [s.kind for s in part(window).history] == ["sketch"]
    assert window.centre.currentIndex() == 0  # back to 3D
    tool = window.side.currentWidget()
    assert isinstance(tool, ToolPanel) and tool.kind == "extrude"
    profile = tool.form.widgets["profile"]
    assert profile.source.currentText() == "Sketch 1 (sketch)"
    tool.form.widgets["distance"].setText("10")
    tool.preview()
    wait(app, window)
    assert window.viewport.shown["kind"] == "preview"  # the pending part, before Apply
    assert len(part(window).history) == 1  # nothing written yet
    tool.apply()
    assert [s.kind for s in part(window).history] == ["sketch", "extrude"]
    assert volume(window) == pytest.approx(0.06 * 0.03 * 0.01)
    assert window.side.currentWidget() is window.properties
    window.undo()
    assert [s.kind for s in part(window).history] == ["sketch"]  # one command


def make_plate(app, window):
    answer(window, "Plate", "xy")
    window.workbench.new_part()
    draw_plate(window.workbench.workspace)
    window.workbench.finish_sketch()
    tool = window.side.currentWidget()
    tool.form.widgets["distance"].setText("10")
    tool.apply()
    window.workbench.refresh()
    window.show_workbench_part()
    wait(app, window)


def test_fillet_edges_picked_in_the_view(app, window):
    make_plate(app, window)
    tool = window.workbench.start_tool("fillet")
    tool.form.widgets["r"].setText("3")
    edges = tool.form.widgets["rule"]
    edges.choice.setCurrentIndex(edges.choice.findData("picked"))
    tool.pick(edges)  # the view shows the part before the fillet, picking edges
    wait(app, window)
    v = window.viewport
    assert v.pick_kind == "edge" and window.viewport.picking
    for x, y in ((0.0, 0.0), (0.06, 0.03)):  # two vertical corner edges
        hit = v.entity_at([x, y, 0.005])
        v.toggle_pick(hit)
    tool.pick(edges)  # take them
    assert len(edges.picked) == 2 and edges.count.text() == "2 edges picked"
    assert all("|" in n for n in edges.picked)  # named by their faces (G3)
    tool.apply()
    wait(app, window)
    corners = 2 * (0.003**2 - math.pi * 0.003**2 / 4) * 0.01
    assert volume(window) == pytest.approx(0.06 * 0.03 * 0.01 - corners, rel=1e-9)


def test_a_sketch_on_a_picked_face_cuts_holes(app, window):
    make_plate(app, window)
    window.toggle_picking(True)
    v = window.viewport
    v.set_pick_kind("face")
    v.toggle_pick(v.entity_at([0.03, 0.015, 0.01]))  # the top face
    choices = {}

    def choose(title, label, items):
        choices["items"] = items
        return items[0][1]  # the picked face, offered first

    window.choose = choose
    window.workbench.new_sketch()
    assert choices["items"][0][0].startswith("The picked face")
    ws = window.workbench.workspace
    ws.set_tool("circle")
    ws.click(0.02, 0.015)
    ws.click(0.024, 0.015)
    ws.set_tool("select")
    (circle,) = ws.model.spec["circles"]
    ws.canvas.selected = [ws.model.spec["circles"][circle][0]]
    ws.relate("fix")
    ws.canvas.selected = [circle]
    dims(ws, "4")
    ws.dimension()
    window.workbench.finish_sketch()
    sketch = part(window).history[-1]
    assert sketch.kind == "sketch" and sketch.args["face"] == "Extrude 1/end"
    tool = window.workbench.start_tool("extrude")
    tool.form.widgets["distance"].setText("-10")
    mode = tool.form.widgets["mode"]
    mode.setCurrentIndex(mode.findData("cut"))
    assert tool.form.widgets["profile"].source.currentText() == f"{sketch.name} (sketch)"
    tool.apply()
    expected = (0.06 * 0.03 - math.pi * 0.004**2) * 0.01
    assert volume(window) == pytest.approx(expected, rel=1e-9)


def test_history_ticks_and_double_click_edits(app, window):
    make_plate(app, window)
    wb = window.workbench
    assert [wb.steps.item(i).text() for i in range(wb.steps.count())] == [
        "1. Sketch 1",
        "2. Extrude 1",
    ]
    from PySide6.QtCore import Qt

    wb.steps.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert part(window).history[1].suppressed
    wb.edit(0)  # a sketch opens in the main view
    assert window.centre.currentWidget() is wb.workspace
    assert "Sketching Sketch 1" in wb.sketch_title.text()
    wb.cancel_sketch()
    assert window.centre.currentIndex() == 0
