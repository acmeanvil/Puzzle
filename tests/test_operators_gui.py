"""Operators in the window (ADR 0062), on the cantilever's Tip load run (CalculiX).

The palette (Tab in the schematic) makes a Reduce wired from the selected results
node; the node shows its value. A Check gets its limit typed in Properties and turns
green or red. A wire drawn from the results onto an operator's input wires it; one
that doesn't fit is refused. A Plot draws in the Plots panel, an Export writes its file.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import operators  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Operator, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

pytestmark = [
    pytest.mark.usefixtures("engineering_units"),
    pytest.mark.skipif(not available("calculix"), reason="calculix not available"),
]
EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def solved(app, tmp_path_factory):
    """The cantilever with its Tip load run, solved once for the module."""
    root = tmp_path_factory.mktemp("ops") / "cantilever"
    skip = shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    shutil.copytree(EXAMPLE, root, ignore=skip)
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
    w.close()
    return root


@pytest.fixture
def window(app, solved, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(solved, root, ignore=shutil.ignore_patterns(".puzzle"))
    w = MainWindow(root)
    w.left_tabs.setCurrentWidget(w.schematic_side)
    yield w
    while w.tasks:  # previews still drawing
        app.processEvents()
        time.sleep(0.02)
    w.close()


def _study(w):
    return next(s for s in w.session.project.of_type(Study) if s.name == "Tip load")


def _op(w, name):
    return next(o for o in w.session.project.of_type(Operator) if o.name == name)


def _node(w, name):
    return next(i for i in w.schematic.items.values() if i.node.label == name)


def _rows(w) -> dict:
    out, stack = {}, [w.properties.invisibleRootItem()]
    while stack:
        item = stack.pop()
        for i in range(item.childCount()):
            child = item.child(i)
            out.setdefault(child.text(0), child)
            stack.append(child)
    return out


def test_the_palette_makes_a_reduce_wired_from_the_selected_results(window):
    w = window
    runs = w.schematic.items[f"runs:{_study(w).id}"]
    w.schematic.chosen(runs.node)
    palette = w.schematic.open_palette()
    palette.search.setText("redu")  # Reduce by name (and Group and aggregate's help)
    assert palette.list.currentItem().data(Qt.ItemDataRole.UserRole) == "reduce"
    palette._make()
    op = _op(w, "Result summary 1")
    assert op.inputs["result"].source == _study(w).id and op.inputs["result"].port == "result"
    node = w.schematic.items[str(op.id)].node
    assert node.value.endswith(" mm") and node.state == "up_to_date"  # the tip deflection
    assert w.tree.current().object == str(op.id)  # selected, so Properties shows it
    rows = _rows(w)
    assert "Inputs" in rows and rows["Shows"].text(1) == node.value
    assert "Against" not in rows and "Reduction" in rows  # its own settings only
    box = w.properties.dropdown("inputs.result")
    assert box.currentText() == "Tip load: Result"


def test_a_check_with_a_typed_limit_turns_red_then_green(window):
    w = window
    reduce_id = operators.new_operator(w, "reduce", str(_study(w).id), "result")
    check_id = operators.new_operator(w, "check", reduce_id, "out")
    check = w.session.project.objects[UUID(check_id)]
    assert check.inputs["value"].source == _op(w, "Result summary 1").id
    assert "limit input is not wired" in w.schematic.items[check_id].node.reason
    w.select_in_tree(("object", check_id))
    row = _rows(w)["Typed value"]
    row.setText(1, "0.001 mm")  # far less than the tip moves
    node = w.schematic.items[check_id].node
    assert node.verdict == "fail" and node.value.startswith("Fail, margin -")
    w.select_in_tree(("object", check_id))
    _rows(w)["Typed value"].setText(1, "100 mm")
    assert w.schematic.items[check_id].node.verdict == "pass"
    _rows(w)["Typed value"].setText(1, "12 wibbles")  # not a unit: put back, nothing changed
    assert _op(w, "Check against limit 1").inputs["limit"].value.unit == "mm"


def test_a_wire_drawn_onto_an_operator_wires_it(window):
    w = window
    op_id = operators.new_operator(w, "reduce")
    runs = w.schematic.items[f"runs:{_study(w).id}"]
    w.schematic.start_wire(runs, "stress")
    target = w.schematic.items[op_id]
    w.schematic.end_wire(target.sceneBoundingRect().center())
    op = _op(w, "Result summary 1")
    assert op.inputs["result"].port == "stress"
    assert w.schematic.items[op_id].node.value.endswith(" MPa")  # von Mises, its first
    wires = [x for x in w.schematic.wires if x.b is w.schematic.items[op_id]]
    assert len(wires) == 1 and wires[0].wire.target_port == "result"
    check_id = operators.new_operator(w, "check")
    w.schematic.start_wire(w.schematic.items[f"runs:{_study(w).id}"], "result")
    w.schematic.end_wire(w.schematic.items[check_id].sceneBoundingRect().center())
    assert not _op(w, "Check against limit 1").inputs  # a result fits no input of a check
    assert "takes no result" in w.statusBar().currentMessage()


def test_plot_shows_in_the_plots_panel_and_export_writes(window):
    w = window
    reduce_id = operators.new_operator(w, "reduce", str(_study(w).id), "result")
    plot_id = operators.new_operator(w, "plot", reduce_id, "out")
    plot = w.session.project.objects[UUID(plot_id)]
    operators.show_plot(w, plot)
    assert w.output_tabs.currentWidget() is w.plots
    assert len(w.plots.series) == 1 and len(w.plots.series[0][1]) == 1  # one static step
    export_id = operators.new_operator(w, "export", reduce_id, "out")
    operators.export_now(w, w.session.project.objects[UUID(export_id)])
    written = w.session.root / "exports" / "Export_1.csv"
    assert written.read_text(encoding="utf-8").startswith("name,value [m]")
    w.session.undo()  # an export changes no model: the last command was its creation
    assert not any(o.type == "export" for o in w.session.project.of_type(Operator))


def test_tab_opens_the_palette(window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    w = window
    w.schematic.canvas.event(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                                       Qt.KeyboardModifier.NoModifier))  # fmt: skip
    palette = w.last_palette
    assert palette.isVisible() and palette.at is not None
    palette.choose("panel")
    op = _op(w, "Show value 1")
    assert w.schematic.view.positions[str(op.id)] == pytest.approx(palette.at, abs=0.1)


def test_the_toolbar_lists_every_operator_by_group(window):
    from puzzle.operators import TYPES

    w = window
    menu = w.schematic.operators.menu()
    menu.aboutToShow.emit()
    actions = menu.actions()
    assert actions[0].text().startswith("Find an operator")
    groups = {a.text(): a.menu() for a in actions if a.menu() is not None
              and a.text() not in ("Fits the selection", "Recently used", "Recipes",
                                   "My clusters", "Share blocks")}  # fmt: skip
    from puzzle.operators import GROUPS

    assert list(groups) == [g for g in GROUPS if any(t.group == g for t in TYPES.values())]
    listed = {a.text() for m in groups.values() for a in m.actions()}
    assert listed == {t.label for t in TYPES.values()}
    runs = w.schematic.items[f"runs:{_study(w).id}"]
    w.schematic.chosen(runs.node)  # wired from the selected run, as Tab's palette is
    reduce = next(a for a in groups["Results"].actions() if a.text() == TYPES["reduce"].label)
    assert reduce.toolTip() == TYPES["reduce"].help
    reduce.trigger()
    op = next(o for o in w.session.project.objects.values()
              if getattr(o, "type", None) == "reduce"
              and o.name.startswith("Result summary"))  # fmt: skip
    assert op.inputs["result"].source == _study(w).id
    assert str(op.id) in w.schematic.view.positions
    w.schematic.chosen(runs.node)
    menu.aboutToShow.emit()  # with the run selected: what fits it comes first
    fits = next(a.menu() for a in menu.actions() if a.text() == "Fits the selection")
    shown = {a.text() for a in fits.actions()}
    assert {"Result summary", "Result table", "Keep result where"} <= shown


def test_the_palette_finds_by_other_names_and_offers_what_fits(window):
    w = window
    runs = w.schematic.items[f"runs:{_study(w).id}"]
    w.schematic.chosen(runs.node)
    palette = w.schematic.open_palette()
    heads = [palette.list.item(i).text() for i in range(palette.list.count())
             if palette.list.item(i).data(Qt.ItemDataRole.UserRole) is None]  # fmt: skip
    assert heads[0] == "Fits here" and heads[-1] == "Every operator"
    palette.search.setText("cull pattern")  # Grasshopper's name
    item = palette.list.currentItem()
    assert item.data(Qt.ItemDataRole.UserRole) == "filter_rows"
    assert item.text().endswith("(as cull pattern)")
    palette.search.setText("vlookup")  # Excel's
    assert palette.list.currentItem().data(Qt.ItemDataRole.UserRole) == "lookup"
    palette.search.setText("")
    palette.choose("sort")
    again = w.schematic.open_palette()
    heads = [again.list.item(i).text() for i in range(again.list.count())
             if again.list.item(i).data(Qt.ItemDataRole.UserRole) is None]  # fmt: skip
    assert "Recently used" in heads
    again.reject()


def test_a_result_table_in_the_table_viewer(window):
    w = window
    from puzzle.gui.operators import new_operator

    study = _study(w)
    tid = new_operator(w, "result_table", str(study.id), "result")
    table = w.session.project.objects[UUID(tid)]
    assert table.inputs["result"].source == study.id
    rows = _rows(w)
    assert rows["Results"].text(1) == ""  # an empty list still has its row, typed in
    w.properties.edited.emit(tid, "quantities", ["displacement:total"])
    top = new_operator(w, "top_n", tid, "out")
    w.select_in_tree(("object", top))
    viewer = w.table_view
    assert viewer.op.id == UUID(top) and viewer.table is not None
    assert viewer.grid.rowCount() == 10  # ten by default
    heads = [viewer.grid.horizontalHeaderItem(c).text() for c in range(viewer.grid.columnCount())]
    assert heads[0] == "node" and heads[1].startswith("x [") and heads[-1].startswith(
        "deformation")  # fmt: skip
    box = w.properties.dropdown("column")  # the table's own columns, by name
    names = [box.itemText(i) for i in range(box.count())]
    assert "node" in names and "deformation" in names
    srt = new_operator(w, "sort", tid, "out")
    w.select_in_tree(("object", srt))
    viewer._header(0)  # a Sort's header click sets its order: one edit
    op = w.session.project.objects[UUID(srt)]
    assert op.by == ["node"] and op.descending is False
    viewer._header(0)
    assert w.session.project.objects[UUID(srt)].descending is True


def test_a_preview_pins_the_worst_nodes_and_a_bar_chart(window):
    w = window
    from puzzle.gui.operators import new_operator, preview

    study = _study(w)
    tid = new_operator(w, "result_table", str(study.id), "result")
    top = new_operator(w, "top_n", tid, "out")
    pv = new_operator(w, "preview", top, "out")
    op = w.session.project.objects[UUID(pv)]
    assert op.inputs["table"].source == UUID(top)
    assert preview(w, op) == 10
    pins = w.viewport.shown["pins"]
    assert len(pins) == 10 and all(p.endswith("mm") for p in pins)  # deformation, shown
    grp = new_operator(w, "group", tid, "out")
    w.properties.edited.emit(grp, "by", ["part"])
    w.properties.edited.emit(grp, "aggregations", ["max(deformation) as peak"])
    plot = new_operator(w, "plot", grp, "out")
    w.properties.edited.emit(plot, "chart", "bar")
    w.properties.edited.emit(plot, "column", "peak")
    w.plots.show_operator(w.session.project.objects[UUID(plot)])
    assert w.plots.curve.bars == {0} and len(w.plots.curve.series[0][1]) == 1


def test_phase_three_in_the_window(window):
    w = window
    from puzzle.gui.operators import new_operator, use_as_field

    tbl = new_operator(w, "table")
    w.properties.edited.emit(tbl, "text", "x [mm], y [mm], z [mm], T [degC]\n0, 0, 0, 20")
    fid = use_as_field(w, w.session.project.objects[UUID(tbl)])
    f = w.session.project.objects[UUID(fid)]
    assert f.format == "table" and f.source == UUID(tbl)
    ch = new_operator(w, "choice")
    w.select_in_tree(("object", ch))
    rows = _rows(w)
    assert "Options" in rows and "Names" in rows
    rows["Options"].setText(1, "8 mm, 10 mm, 12 mm")  # typed in, as a list of quantities
    op = w.session.project.objects[UUID(ch)]
    assert [q.value for q in op.options] == [8, 10, 12] and op.options[0].unit == "mm"
    sw = new_operator(w, "toggle")
    from puzzle.gui.choices import choices
    from puzzle.model.schema import Component

    comp = next(iter(w.session.project.of_type(Component)))

    got = choices(w.session.project, comp, "physics.0.features.0.when")
    assert got[0] == ("Always", None)
    assert (w.session.project.objects[UUID(sw)].name,) * 2 in got


def test_recipes_from_the_toolbar_and_a_cluster_saved(window):
    w = window
    from puzzle.gui.operators import insert_template, save_cluster
    from puzzle.model.schema import TreeGroup

    study = _study(w)
    runs = w.schematic.items[f"runs:{study.id}"]
    w.schematic.chosen(runs.node)
    menu = w.schematic.operators.menu()
    menu.aboutToShow.emit()
    recipes = next(a.menu() for a in menu.actions() if a.text() == "Recipes")
    assert "Worst nodes" in [a.text() for a in recipes.actions()]
    source, port = w.schematic.palette_source()
    insert_template(w, "peak_per_part", source, port)
    table = next(o for o in w.session.project.objects.values()
                 if getattr(o, "name", "") == "Peak per part: Result table")  # fmt: skip
    assert table.inputs["result"].source == study.id  # wired from the selected run
    group = next(g for g in w.session.project.of_type(TreeGroup) if g.name == "Peak per part")
    from forms import fill

    fill(w, name="My peaks")
    assert save_cluster(w, str(group.id)).endswith("my_peaks.json")
    menu.aboutToShow.emit()
    mine = next(a.menu() for a in menu.actions() if a.text() == "My clusters")
    assert "My peaks" in [a.text() for a in mine.actions()]  # (others saved too)


@pytest.mark.skipif(not os.environ.get("PUZZLE_RENDER_TESTS"), reason="renders off screen")
def test_a_snapshot_export_writes_a_png(window):
    w = window
    from puzzle.gui.operators import new_operator
    from puzzle.operators import evaluate as ev

    study = _study(w)
    snap = new_operator(w, "export", str(study.id), "result")
    w.properties.edited.emit(snap, "file", "report/tip.png")
    w.properties.edited.emit(snap, "format", "png")
    op = w.session.project.objects[UUID(snap)]
    ctx = ev.Context(w.session.project, w._store(), root=w.session.root)
    (path,) = ev.export(ctx, op, w.session.root)
    assert path.name == "tip.png" and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_morph_handle_from_the_study_menu(window):
    w = window
    from forms import fill

    from puzzle.gui.menus import add_morph_handle

    study = _study(w)
    comp = w.session.project.objects[study.component]
    face = next(f"{p.name}/{s.name}" for p in comp.parts
                for s in w.session.project.objects[p.geometry].selections
                if s.entity == "face")  # fmt: skip
    fill(w, selection=face, distance="2 mm", direction="z", fixed=None)
    add_morph_handle(w, study)
    morph = w.session.project.objects[study.id].morph
    assert morph.handles[0].selection == face
    assert morph.handles[0].distance == pytest.approx(0.002) and morph.handles[0].direction == (
        0.0, 0.0, 1.0)  # fmt: skip
    w.session.undo()
    assert w.session.project.objects[study.id].morph is None


def test_selecting_a_filter_previews_its_field(window):
    """ADR 0062: a Filter's kept values drawn on the mesh, the rest muted."""
    from puzzle.commands.model import Create
    from puzzle.gui import operators as gui_ops

    w = window
    study = _study(w)
    data = {"name": "High stress", "type": "filter", "keep": "above",
            "quantity": "stress:von_mises",
            "inputs": {"result": {"source": str(study.id), "port": "result"},
                       "limit": {"value": {"value": 1, "unit": "MPa"}}}}  # fmt: skip
    w.submit(Create(kind="operator", data=data))
    assert gui_ops.preview_field(w, _op(w, "High stress"))
    shown = w.viewport.shown["field_values"]
    assert shown["title"].startswith("High stress") and shown["nodes"] > 0
    assert 0 < shown["extrapolated"] < shown["nodes"]  # some values left out, muted
