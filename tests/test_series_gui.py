"""Curves in the window (ADR 0071 decision 3): a Plot of a curve, a limit line and a note
in the Plots panel; the curve's small plot on its schematic node; settings in readable
words. Needs no solve: the curve is a typed table of stress along a wall."""

from __future__ import annotations

import os
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Material, Operator, Project, Quantity  # noqa: E402
from puzzle.project import save_project  # noqa: E402

pytestmark = pytest.mark.usefixtures("engineering_units")


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    p = Project(name="p")
    steel = p.add(Material(name="Steel", properties={
        "yield_strength": Quantity(value=250, unit="MPa")}))  # fmt: skip
    p.add(Operator(name="Wall", type="table",
                   text="d [mm],stress [MPa]\n0,0\n10,100\n20,200\n"))  # fmt: skip
    p.add(Operator(name="Sy", type="model_value", target=steel.id,
                   property="properties.yield_strength"))  # fmt: skip
    save_project(p, tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    yield w
    w.close()
    app.processEvents()


def _id(w, name) -> str:
    return str(next(o.id for o in w.session.project.of_type(Operator) if o.name == name))


def test_a_curve_with_a_limit_line_and_a_note_in_the_plots_panel(window):
    from puzzle.gui import operators

    w = window
    curve = operators.new_operator(w, "curve_from_table", _id(w, "Wall"), "out")
    note = operators.new_operator(w, "annotate", curve, "out")
    limit = operators.new_operator(w, "limit_line", _id(w, "Sy"), "out")
    plot = operators.new_operator(w, "plot", note, "out")
    w.session.submit(
        {
            "command": "edit",
            "object": plot,
            "ops": [{"op": "set", "path": "inputs.b", "value": {"source": limit, "port": "out"}}],
        }
    )
    w.plots.show_operator(w.session.project.objects[UUID(plot)])
    shown = w.plots.curve
    assert len(shown.series) == 2 and shown.dashed == {1}
    assert shown.series[0][1] == [(0.0, 0.0), (10.0, 100.0), (20.0, 200.0)]  # mm, MPa
    assert shown.series[1][1] == [(0.0, 250.0), (20.0, 250.0)]  # yield, across
    assert shown.notes == [(20.0, 200.0, "max 200 MPa")]
    assert "Distance" not in shown.x_label and "(mm)" in shown.x_label


def test_a_curve_node_has_a_small_plot_and_settings_in_words(window):
    from puzzle import schematic as sch
    from puzzle.gui import operators
    from puzzle.gui.choices import choices

    w = window
    curve = operators.new_operator(w, "curve_from_table", _id(w, "Wall"), "out")
    built = sch.build(w.session.project, [], store=None)
    node = built.nodes[curve]
    assert node.sketch and node.sketch[0][0] == (0.0, 0.0) and node.sketch[0][-1] == (1.0, 1.0)
    assert node.port("out").type == "series"
    cmp = operators.new_operator(w, "compare_steps", curve, "out")
    op = w.session.project.objects[UUID(cmp)]
    assert choices(w.session.project, op, "comparison") == [
        ("Difference", "difference"),
        ("Ratio", "ratio"),
    ]
