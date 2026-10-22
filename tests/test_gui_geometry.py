"""The geometry history in the GUI (G4): steps in the tree, added, edited, turned off,
moved and deleted through commands; the preview follows; parameters drive steps.

`window.edit_step` stands in for the step dialog (it returns name and arguments, as
the dialog does); the dialog's own parsing is tested at the end.
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

from puzzle.gui import menus  # noqa: E402
from puzzle.gui.tree import ROLE, Node  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Geometry, ParameterSet  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow()
    w.new_project(tmp_path / "parts", "Parts")
    yield w
    w.close()


def wait(app, window, timeout=120.0):
    end = time.monotonic() + timeout
    while window.tasks:
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.02)
    app.processEvents()


def labels(window):
    return [i.text(0) for i in window.tree._items()]


def geometry(window, name="Part 1"):
    return next(g for g in window.session.project.of_type(Geometry) if g.name == name)


def answer(window, *replies):
    queue = list(replies)
    window.edit_step = lambda *args: queue.pop(0)
    window.ask_text = lambda *args, **kw: queue.pop(0)


def show(app, window, name="Part 1"):
    """Select the geometry and wait for its preview."""
    geo = geometry(window, name)
    window.tree.select(Node("object", str(geo.id)))
    wait(app, window)
    return window.viewport.shown


def test_build_a_part_from_steps(app, window):
    answer(window, "Part 1")
    menus.new_part(window)
    assert "1. box Base" in labels(window)
    # A part with no component is shown on its own.
    assert show(app, window)["kind"] == "preview"
    six = window.viewport.surface.n_cells and len(window.viewport.entities)
    assert six == 6

    answer(window, ("Bore", {"r": 0.004, "x": 0.025, "y": 0.015, "z": 0.0, "axis": "z"}))
    menus.add_step_to(window, geometry(window), "hole")
    assert "2. hole Bore" in labels(window)
    assert show(app, window)["kind"] == "preview" and len(window.viewport.entities) == 7

    answer(window, ("Round", {"r": 0.002, "rule": {"parallel": "z", "outer": True}}))
    menus.add_step_to(window, geometry(window), "fillet")
    show(app, window)
    assert len(window.viewport.entities) == 11  # four rounds

    # Edit the hole: a bigger bore (one command, undoable).
    answer(window, ("Bore", {"r": 0.006, "x": 0.025, "y": 0.015, "z": 0.0, "axis": "z"}))
    menus.edit_step(window, geometry(window), 1)
    assert geometry(window).history[1].args["r"] == 0.006
    window.undo()
    assert geometry(window).history[1].args["r"] == 0.004

    # Off, on, moved, deleted.
    menus.toggle_step(window, geometry(window), 2)
    assert "3. fillet Round  (off)" in labels(window)
    show(app, window)
    assert len(window.viewport.entities) == 7  # the rounds are gone while off
    menus.toggle_step(window, geometry(window), 2)
    menus.move_step(window, geometry(window), 2, -1)
    assert [s.name for s in geometry(window).history] == ["Base", "Round", "Bore"]
    menus.remove(window, geometry(window), "history.1")
    assert [s.name for s in geometry(window).history] == ["Base", "Bore"]


def test_a_failing_step_is_reported_by_name(app, window):
    answer(window, "Part 1")
    menus.new_part(window)
    answer(window, ("Huge", {"r": 0.5, "rule": {"parallel": "z", "outer": True}}))
    menus.add_step_to(window, geometry(window), "fillet")
    show(app, window)
    assert "Huge" in window.log.toPlainText()  # which step, not just "OCCT failed"
    assert window.viewport.shown == {}


def test_parameters_drive_a_step(app, window):
    menus.new_parameter_set(window)
    params = window.session.project.of_type(ParameterSet)[0]
    answer(window, "t", "12 mm")
    menus.add_parameter(window, params)
    params = window.session.project.of_type(ParameterSet)[0]
    assert params.values["t"].value == 12 and params.values["t"].unit == "mm"

    answer(window, "Part 1")
    menus.new_part(window)
    geo = geometry(window)
    answer(
        window,
        ("Base", {"dx": 0.05, "dy": 0.03, "dz": "t", "x0": 0, "y0": 0, "z0": 0, "mode": "new"}),
    )
    menus.edit_step(window, geo, 0)
    show(app, window)
    z = window.viewport.mesh.coords[:, 2]
    assert z.max() == pytest.approx(0.012)
    # Change the parameter, as the properties panel does: the part follows.
    from puzzle.commands.model import Edit, EditOp

    window.submit(
        Edit(
            object="Parameters 1",
            ops=[EditOp(op="set", path="values.t", value={"value": 20, "unit": "mm"})],
        )
    )
    show(app, window)
    assert window.viewport.mesh.coords[:, 2].max() == pytest.approx(0.020)


def test_measure_two_faces(app, window):
    answer(window, "Part 1")
    menus.new_part(window)  # a 50 x 30 x 10 mm box
    show(app, window)
    window.toggle_picking(True)
    v = window.viewport
    lo, hi = v.mesh.coords.min(axis=0), v.mesh.coords.max(axis=0)
    mid = (lo + hi) / 2
    v.toggle_pick(v.entity_at([mid[0], mid[1], lo[2]]))
    v.toggle_pick(v.entity_at([mid[0], mid[1], hi[2]]))
    size = v.measure(v.picked)
    assert size["area"] == pytest.approx(2 * 0.05 * 0.03, rel=1e-6)
    assert size["distance"] == pytest.approx(0.01, rel=1e-6)
    assert "centres 10 mm apart" in window.pick_bar.label.text()


def test_an_imported_part_becomes_a_history(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    w = MainWindow(root)
    try:
        bar = geometry(w, "Bar")
        root_members = next(s for s in bar.selections if s.name == "Root").members
        menus.to_history(w, bar)
        bar = geometry(w, "Bar")
        assert bar.source is None and [s.kind for s in bar.history] == ["import"]
        root_sel = next(s for s in bar.selections if s.name == "Root")
        assert root_sel.members == [f"CAD/{m}" for m in root_members]
        assert w.session.project.problems() == []
        # Shown, with the Root selection still on the x = 0 end.
        w.tree.select(Node("object", str(bar.id)))
        wait(app, w)
        for item in w.tree._items():
            n = item.data(0, ROLE)
            if n.kind == "selection" and n.label == "Root":
                w.tree.setCurrentItem(item)
        (face,) = w.viewport.shown["highlighted"]
        size = w.viewport.measure([face])
        assert size["area"] == pytest.approx(0.01 * 0.01, rel=1e-3)
        # The check runs in the background and lands in Problems.
        menus.check_geometry_of(w, geometry(w, "Bar"))
        wait(app, w)
        assert w.geometry_findings == {"Bar": []}
    finally:
        w.close()


def test_step_dialog_units_and_parameters(app):
    from puzzle.gui.step_dialog import StepDialog

    d = StepDialog("extrude", {}, "L")
    d.widgets["profile"].setText("0, 0; 60, 0; 60, t; t, t; t, 40; 0, 40")
    d.widgets["distance"].setText("30")
    args = d.values()
    assert args["distance"] == pytest.approx(0.03)  # typed in mm, stored in m
    assert args["profile"]["points"][2] == [0.06, "t"]  # a parameter kept by name
    d.widgets["distance"].setText("depth")
    assert d.values()["distance"] == "depth"
    d.widgets["distance"].setText("3 apples")
    with pytest.raises(ValueError, match="neither a number nor a parameter"):
        d.values()
    filleted = StepDialog("fillet", {"r": 0.004, "rule": {"face": "L/end"}})
    assert filleted.widgets["r"].text() == "4"  # shown in mm
    assert filleted.values()["rule"] == {"face": "L/end"}


def test_selecting_a_step_highlights_its_faces(app, window):
    answer(
        window,
        "Part 1",
        ("Bore", {"r": 0.004, "x": 0.015, "y": 0.015, "z": 0.0, "axis": "z"}),
        ("Bores", {"feature": "Bore", "count": 2, "dx": 0.02, "dy": 0.0, "dz": 0.0}),
    )
    menus.new_part(window)
    menus.add_step_to(window, geometry(window), "hole")
    menus.add_step_to(window, geometry(window), "pattern")
    show(app, window)
    window.tree.select(Node("operation", str(geometry(window).id), "history.1", "Bore"))
    assert len(window.viewport.shown["highlighted"]) == 2  # the bore and its copy
