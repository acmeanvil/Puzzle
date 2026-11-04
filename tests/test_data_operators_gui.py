"""Data operators in the window (ADR 0062, phase 2): a Slider wired into a plate's
width by drawing a wire onto its geometry; dragging the knob previews the plate at the
new width and writes nothing until let go, then one undoable command; a Range dropped
on a study sweeps it.
"""

from __future__ import annotations

import os
import time
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui import operators  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    Operator,
    PartInstance,
    Project,
    Quantity,
    Study,
)
from puzzle.project import save_project  # noqa: E402

pytestmark = pytest.mark.usefixtures("engineering_units")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, w, timeout=120.0):
    end = time.monotonic() + timeout
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    p = Project("Plates")
    geo = p.add(Geometry(name="Plate", history=[HistoryStep(
        kind="box", name="B", args={"dx": 0.03, "dy": 0.02, "dz": 0.005})]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Coarse"))
    comp = p.add(Component(name="Plate", parts=[PartInstance(name="Plate", geometry=geo.id)],
                           mesh=mesh.id))  # fmt: skip
    p.add(Study(name="Static", component=comp.id, solver="calculix", steps=[]))
    root = tmp_path / "plates"
    save_project(p, root)
    w = MainWindow(root)
    w.left_tabs.setCurrentWidget(w.schematic_side)
    yield w
    wait(app, w)
    w.close()


def _op(w, name):
    return next(o for o in w.session.project.of_type(Operator) if o.name == name)


def _geo(w):
    return next(g for g in w.session.project.of_type(Geometry))


def test_a_slider_drives_the_width_and_previews_while_dragged(app, window):
    w = window
    sid = operators.new_operator(w, "slider")
    w.submit_batch([{"command": "edit", "object": sid, "ops": [
        {"op": "set", "path": "low", "value": {"value": 10, "unit": "mm"}},
        {"op": "set", "path": "high", "value": {"value": 50, "unit": "mm"}},
        {"op": "set", "path": "increment", "value": {"value": 1, "unit": "mm"}},
        {"op": "set", "path": "inputs.value", "value": {"value": {"value": 30, "unit": "mm"}}},
    ]}])  # fmt: skip
    slider = w.schematic.items[sid]
    assert slider.node.column == 1 and slider.node.slider == pytest.approx((0.01, 0.05, 0.03))
    fill(w, argument="0:dx")
    w.schematic.start_wire(slider, "out")
    w.schematic.end_wire(w.schematic.items[str(_geo(w).id)].sceneBoundingRect().center())
    assert _geo(w).history[0].args["dx"] == "Slider 1"
    geo_node = w.schematic.items[str(_geo(w).id)].node
    assert geo_node.port("arg:0:dx").label == "B: dx"
    assert any(x.wire.target_port == "arg:0:dx" for x in w.schematic.wires)

    seq = len(w.session.history(limit=1000))
    knob = w.schematic.items[sid]
    w.schematic.slide(knob.node, 0.0423)  # dragging: snapped to 1 mm, nothing written
    assert w.slider_overrides == {UUID(sid): pytest.approx(0.042)}
    assert knob.node.value == "42 mm"
    assert len(w.session.history(limit=1000)) == seq
    wait(app, w)
    comp = next(c for c in w.session.project.of_type(Component))
    shown = w.previews[str(comp.id)][1].mesh.coords
    assert shown[:, 0].max() - shown[:, 0].min() == pytest.approx(0.042, abs=1e-6)
    w.schematic.slide_done(knob.node)
    assert w.slider_overrides == {}
    value = _op(w, "Slider 1").inputs["value"].value
    assert value == Quantity(value=42, unit="mm")  # in the unit shown
    w.session.undo()
    assert _op(w, "Slider 1").inputs["value"].value == Quantity(value=30, unit="mm")


def test_a_range_dropped_on_a_study_sweeps_it(window):
    w = window
    t = operators.new_operator(w, "number")
    w.submit(
        {
            "command": "edit",
            "object": t,
            "ops": [
                {
                    "op": "set",
                    "path": "inputs.value",
                    "value": {"value": {"value": 5, "unit": "mm"}},
                }
            ],
        }
    )
    rid = operators.new_operator(w, "range")
    w.submit({"command": "edit", "object": rid, "ops": [
        {"op": "set", "path": "inputs", "value": {
            "start": {"value": {"value": 2, "unit": "mm"}},
            "end": {"value": {"value": 8, "unit": "mm"}},
            "count": {"value": 4.0}}}]})  # fmt: skip
    study = next(s for s in w.session.project.of_type(Study))
    fill(w, parameter="Number 1")
    w.schematic.start_wire(w.schematic.items[rid], "out")
    w.schematic.end_wire(w.schematic.items[str(study.id)].sceneBoundingRect().center())
    study = w.session.project.get(study.id, Study)
    (prm,) = study.sweeps()[0].parameters
    assert prm.name == "Number 1" and prm.source == UUID(rid)
    node = w.schematic.items[str(study.id)].node
    assert node.badge.startswith("× 4 points")
    assert node.port("sweep:0:0").label == "Sweep: Number 1"
    wrong = operators.new_operator(w, "number")
    w.schematic.start_wire(w.schematic.items[wrong], "out")
    w.schematic.end_wire(w.schematic.items[str(study.id)].sceneBoundingRect().center())
    assert "dropped on a study sweeps it" in w.statusBar().currentMessage()


def test_the_properties_slider_and_a_table_from_a_csv(app, window, tmp_path):
    w = window
    sid = operators.new_operator(w, "slider")
    w.submit({"command": "edit", "object": sid, "ops": [
        {"op": "set", "path": "high", "value": {"value": 20, "unit": "mm"}}]})  # fmt: skip
    w.select_in_tree(("object", sid))
    bar = w.properties.slider
    bar.setValue(500)  # half way: 10 mm, previewed
    assert w.slider_overrides[UUID(sid)] == pytest.approx(0.010)
    bar.sliderReleased.emit()
    assert _op(w, "Slider 1").inputs["value"].value == Quantity(value=10, unit="mm")
    csv = tmp_path / "plates.csv"
    csv.write_text("t [mm], load [kN]\n2, 1\n4, 3\n")
    tid = operators.new_operator(w, "table")
    operators.read_csv(w, w.session.project.objects[UUID(tid)], str(csv))
    assert _op(w, "Table 1").text.startswith("t [mm]")
    assert w.schematic.items[tid].node.value == "2 rows × 2: t, load"
