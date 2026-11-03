"""The schematic in the window (ADR 0058): its tab and dock, selection with the tree,
highlighting, dragging, drawing a wire into a transfer, dropping a part, export."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Study, Transfer, TreeGroup  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.schematic import view_state  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, bracket):
    root = tmp_path / "bracket"
    save_project(bracket, root)
    w = MainWindow(root)
    w.left_tabs.setCurrentWidget(w.schematic_side)
    yield w
    w.close()


def _node(w, name, kind=None):
    return next(i for i in w.schematic.items.values()
                if i.node.label == name and (kind is None or i.node.kind == kind))  # fmt: skip


def _study(w, name):
    return next(s for s in w.session.project.of_type(Study) if s.name == name)


def test_the_tab_puts_the_schematic_in_the_centre(window):
    w = window
    assert w.centre.currentWidget() is w.schematic and w.context == "schematic"
    assert set(w.schematic.items) == set(w.schematic.schematic.nodes)
    assert len(w.schematic.wires) == len(w.schematic.schematic.wires)
    w.left_tabs.setCurrentIndex(0)
    assert w.centre.currentIndex() == 0 and w.context == "model"


def test_a_click_selects_the_object_in_the_tree(window):
    w = window
    item = _node(w, "Static")
    w.schematic.chosen(item.node)
    assert w.tree.current().object == str(_study(w, "Static").id)
    assert item.isSelected()
    assert "Built from:" in w.schematic_side.details.text()


def test_selection_lights_what_it_is_built_from_or_what_it_reaches(window):
    w = window
    w.schematic.chosen(_node(w, "Thermal").node)
    assert _node(w, "Tets").opacity() == 1  # upstream
    assert _node(w, "Static").opacity() == 1  # its own wire's other end
    assert _node(w, "Modal").opacity() < 1
    w.schematic.reach.setChecked(True)
    assert _node(w, "Static").opacity() == 1 and _node(w, "Modal").opacity() == 1
    assert _node(w, "Tets").opacity() < 1
    w.schematic.chosen(None)
    assert all(i.opacity() == 1 for i in w.schematic.items.values())


def test_search_and_the_state_filter_dim_the_rest(window):
    w = window
    w.schematic.search.setText("tets")
    assert _node(w, "Tets").opacity() == 1 and _node(w, "Thermal").opacity() < 1
    w.schematic.search.clear()
    w.schematic.state_filter.setCurrentIndex(
        w.schematic.state_filter.findData("not_run"))  # fmt: skip
    assert _node(w, "Thermal").opacity() == 1 and _node(w, "Tets").opacity() < 1


def test_use_wires_follow_the_setting(window):
    w = window
    grey = [x for x in w.schematic.wires if x.wire.kind == "use"]
    assert grey and all(x.isVisible() for x in grey)
    w.schematic.uses.setCurrentIndex(w.schematic.uses.findData("none"))
    assert not any(x.isVisible() for x in grey)
    assert all(x.isVisible() for x in w.schematic.wires if x.wire.kind == "data")
    w.schematic.uses.setCurrentIndex(w.schematic.uses.findData("selected"))
    w.schematic.chosen(_node(w, "Al 6061-T6").node)
    shown = {(x.a.node.label, x.b.node.label) for x in grey if x.isVisible()}
    assert ("Al 6061-T6", "Bracket") in shown and ("bracket.step", "Bracket") not in shown


def test_a_dragged_node_stays_for_this_user_until_tidied(window):
    w = window
    item = _node(w, "Globals")
    start = item.pos()
    item.setPos(start + QPointF(0, 400))
    w.schematic.dropped(item, start)
    saved = view_state.load(w.session.root).positions[item.node.id]
    w.refresh()
    assert _node(w, "Globals").pos() == QPointF(*saved) != start
    wire_ends = [x for x in w.schematic.wires if x.a is _node(w, "Globals")]
    assert all(x.path().elementAt(0).y == pytest.approx(x.a.anchor("", "out").y())
               for x in wire_ends)  # fmt: skip
    w.schematic.tidy_layout()
    assert view_state.load(w.session.root).positions == {}
    assert not (w.session.root / "schematic.json").exists()


def test_drawing_a_wire_between_studies_makes_a_transfer(window):
    w = window
    source, target = _node(w, "Thermal"), _node(w, "Modal")
    w.schematic.start_wire(source, "temperature")
    w.schematic.wire_to(target.sceneBoundingRect().center())
    fill(w, role="load")
    w.schematic.end_wire(target.sceneBoundingRect().center())
    offered = w.last_form
    assert offered.values()["source"] == "Thermal" and offered.values()["target"] == "Modal"
    assert offered.values()["field"] == "temperature"
    made = [t for t in w.session.project.of_type(Transfer) if t.target == _study(w, "Modal").id]
    assert any(t.field == "temperature" and t.role == "load" for t in made)
    assert w.schematic.drawing is None
    w.session.undo()  # an ordinary command: undoable
    assert len(w.session.project.of_type(Transfer)) == 2


def test_a_wire_dropped_off_a_study_makes_nothing(window):
    w = window
    w.schematic.start_wire(_node(w, "Thermal"), "temperature")
    w.schematic.end_wire(_node(w, "Tets").sceneBoundingRect().center())
    assert len(w.session.project.of_type(Transfer)) == 2


def test_dropping_a_geometry_on_a_component_adds_a_part(window):
    w = window
    geo, comp = _node(w, "Bracket", "geometry"), _node(w, "Bracket", "component")
    start = geo.pos()
    geo.setPos(comp.pos())
    fill(w, name="Second bracket")
    w.schematic.dropped(geo, start)
    parts = w.session.project.of_type(Component)[0].parts
    assert [p.name for p in parts] == ["Bracket", "Second bracket"]
    assert view_state.load(w.session.root).positions == {}  # it went back, not pinned


def test_groups_fold_into_one_node(window):
    w = window
    studies = [str(_study(w, n).id) for n in ("Static", "Modal")]
    w.session.project.add(TreeGroup(name="Structural", section="Studies", members=studies))
    w.schematic.refresh()
    assert len(w.schematic.frames) == 1
    group = w.schematic.frames[0].group.id
    w.schematic.toggle_group(group)
    assert f"group:{group}" in w.schematic.items and studies[0] not in w.schematic.items
    w.schematic.open_node(w.schematic.items[f"group:{group}"].node)  # a double-click
    assert studies[0] in w.schematic.items


def test_a_double_click_opens_a_geometry_in_the_geometry_tab(window):
    w = window
    w.schematic.open_node(_node(w, "Bracket", "geometry").node)
    assert w.left_tabs.currentWidget() is w.workbench
    assert w.centre.currentIndex() == 0


def test_export_svg_and_png(window, tmp_path):
    w = window
    svg = w.schematic.export_picture(str(tmp_path / "s.svg"))
    assert "Thermal" in (tmp_path / "s.svg").read_text() and svg
    w.schematic.export_picture(str(tmp_path / "s.png"))
    assert (tmp_path / "s.png").stat().st_size > 1000


def test_window_menu_shows_the_dock(window):
    w = window
    action = w.actions_by_name["schematic"]
    action.trigger()
    assert not w.schematic_dock.isHidden()
    w.refresh()  # off screen, a dock is never told it became visible
    assert set(w.schematic_panel.items) == set(w.schematic.schematic.nodes)


def test_new_transfer_from_the_menu(window):
    from puzzle.gui import menus

    w = window
    fill(w, source="Thermal", field="temperature", target="Modal", role="load")
    menus.new_transfer(w)
    assert len(w.session.project.of_type(Transfer)) == 3
    fill(w, source="Modal", target="Modal")
    assert menus.new_transfer(w) is None
    assert w.last_form.problem() == "A study can't feed itself"
