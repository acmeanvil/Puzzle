"""The sketch editor (G5): drawing by clicks, constraining, dimensioning, and the
step it feeds. Clicks are given in model coordinates, as the canvas reports them."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")
pytest.importorskip("py_slvs")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.sketcher import SketchEditor, constraint_text  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def answers(editor, *replies):
    queue = list(replies)
    editor.ask_value = lambda *args: queue.pop(0)


def select(editor, *names):
    editor.canvas.selected = list(names)
    editor.changed()


def mm(x):
    return x / 1000


def test_draw_constrain_and_dimension_a_plate(app):
    e = SketchEditor(params={"W": 0.06})
    e.set_tool("rect")
    e.click(mm(1), mm(-1))
    e.click(mm(58), mm(31))  # drawn roughly
    m = e.model
    assert len(m.spec["lines"]) == 4
    assert e.status.text() == "4 degrees of freedom left"  # 8 coordinates, 4 relations
    bottom = next(n for n, (a, b) in m.spec["lines"].items() if m.point(a)[1] < 0)
    left = next(
        n for n, (a, b) in m.spec["lines"].items() if max(m.point(a)[0], m.point(b)[0]) < mm(2)
    )
    corner = next(n for n, (u, v) in m.spec["points"].items() if u < mm(2) and v < 0)

    select(e, corner)
    assert e.relation_buttons["fix"].isEnabled()
    assert not e.relation_buttons["horizontal"].isEnabled()  # a point is not a line
    e.relate("fix")
    select(e, bottom)
    answers(e, "W")  # a parameter
    e.dimension()
    select(e, left)
    answers(e, "30")
    e.dimension()
    assert e.status.text() == "Fully constrained"
    assert constraint_text(m.spec["constraints"][-2]) == f"Length of {bottom} = W"
    width = max(u for u, _ in m.spec["points"].values()) - min(
        u for u, _ in m.spec["points"].values()
    )
    assert width == pytest.approx(0.06)

    # A bore: circle tool, then its radius and position from the fixed corner.
    e.set_tool("circle")
    e.click(mm(20), mm(14))
    e.click(mm(23), mm(14))
    (bore,) = m.spec["circles"]
    assert e.status.text() == "3 degrees of freedom left"
    e.set_tool("select")
    select(e, bore)
    answers(e, "4")
    e.dimension()
    centre = m.spec["circles"][bore][0]
    select(e, corner, centre)
    answers(e, "across", "20")
    e.dimension()
    select(e, corner, centre)
    answers(e, "up", "15")
    e.dimension()
    assert e.status.text() == "Fully constrained"
    cu, cv = m.point(corner)  # fixed where it was drawn, at (1, -1) mm
    assert m.point(centre) == pytest.approx((cu + mm(20), cv + mm(15)), abs=1e-9)

    # One length too many: refused in red, naming the constraint.
    top = next(n for n in m.spec["lines"] if n not in (bottom, left) and m.length(n) > mm(50))
    select(e, top)
    answers(e, "50")
    e.dimension()
    assert "conflict" in e.status.text()
    e.constraints.setCurrentRow(len(m.spec["constraints"]) - 1)
    e._remove_constraint()
    assert e.status.text() == "Fully constrained"

    # Edge names become face names: rename the bottom line.
    select(e, bottom)
    answers(e, "bottom")
    e._rename()
    assert "bottom" in m.spec["lines"]
    assert any(c[1] == "bottom" for c in m.spec["constraints"] if c[0] == "length")


def test_a_free_point_drags_and_a_fixed_one_does_not(app):
    e = SketchEditor()
    e.set_tool("line")
    for u, v in [(0, 0), (40, 0), (40, 20), (0, 0)]:  # a triangle, closed on its start
        e.click(mm(u), mm(v))
    m = e.model
    assert len(m.spec["lines"]) == 3 and len(m.spec["points"]) == 3
    start = next(n for n, (u, v) in m.spec["points"].items() if u == 0 and v == 0)
    tip = next(n for n, (u, v) in m.spec["points"].items() if v > 0)
    select(e, start)
    e.relate("fix")
    e.drag(tip, mm(45), mm(25))
    assert m.point(tip) == pytest.approx((mm(45), mm(25)))
    e.drag(start, mm(5), mm(5))  # held by its constraint
    assert m.point(start) == pytest.approx((0.0, 0.0))
    # A point held by a dimension can't be dragged off it either.
    base = next(
        n for n, (a, b) in m.spec["lines"].items() if {a, b} == {start, other(m, start, tip)}
    )
    select(e, base)
    answers(e, "40")
    e.dimension()
    end = other(m, start, tip)
    select(e, base)
    e.relate("horizontal")
    before = m.point(end)
    e.drag(end, mm(60), mm(0))
    assert m.point(end) == pytest.approx(before)


def other(m, *taken):
    return next(n for n in m.spec["points"] if n not in taken)


def test_the_step_dialog_extrudes_a_sketch(app):
    from puzzle.geometry.history import Op, body_signature, regenerate
    from puzzle.gui.step_dialog import StepDialog

    d = StepDialog("extrude", {}, "Plate", params={"W": 0.06})
    field = d.widgets["profile"]
    field.line.setText("0, 0; 60, 0; 60, 30; 0, 30")
    seen = {}

    def edit(spec):  # the editor opens on the typed polygon, as four lines
        seen["lines"] = len(spec["lines"])
        e = SketchEditor(spec, d.params)
        first = sorted(spec["points"])[0]
        select(e, first)
        e.relate("fix")
        for line in spec["lines"]:
            select(e, line)
            e.relate(
                "horizontal"
                if spec["points"][spec["lines"][line][0]][1]
                == spec["points"][spec["lines"][line][1]][1]
                else "vertical"
            )
        return e.sketch()

    d.edit_sketch = edit
    d.open_sketch(field)
    assert seen["lines"] == 4
    assert field.line.text() == "Sketch: 4 lines" and field.line.isReadOnly()
    d.widgets["distance"].setText("10")
    args = d.values()
    assert "sketch" in args["profile"] and args["mode"] == "add"
    shape = regenerate([Op("extrude", "Plate", args)])
    assert body_signature(shape.bodies["Body"][0])[0] == pytest.approx(0.06 * 0.03 * 0.01)
    assert {"Plate/line-1", "Plate/line-3"} <= set(shape.names)
    _ = time
