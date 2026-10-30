"""Editing a selection from picks (the tree's and the Geometry tab's right-click):
Replace with picked, Add picked, Remove picked, and Edit in the view (its members
picked, to change by double-clicks). A click on a selection in the Geometry tab's list
lights it in the view. Picks of another kind are refused, and so is emptying it."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Feature, Geometry, Physics  # noqa: E402
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(window):
    app = QApplication.instance()
    end = time.monotonic() + 60
    while window.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def test_replace_add_and_remove_from_picks(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    held = Feature(type="fixed", selection="Bar/Root")
    physics = [Physics(type="solid_mechanics", features=[held])]
    project, comp = bar_project(write_bar_step(root / "bar.step"), physics, 10.0)
    save_project(project, root)
    w = MainWindow(root)
    try:
        for item in w.tree._items():
            if (
                item.data(0, ROLE).kind == "object"
                and item.data(0, ROLE).label == "Bar"
                and (item.parent().data(0, ROLE).label == "Components")
            ):
                w.tree.setCurrentItem(item)
        _settle(w)
        geo = next(g for g in w.session.project.of_type(Geometry))
        names = w.mesh_names[str(comp.id)]
        by_name = {n: e for e, (_p, n) in names.items() if e[0] == 2}
        root_i = next(i for i, s in enumerate(geo.selections) if s.name == "Root")
        tip_face = geo.selections[[s.name for s in geo.selections].index("Tip")].members[0]
        root_face = geo.selections[root_i].members[0]

        w.viewport.set_pick_kind("face")
        w.viewport.pick([by_name[tip_face]], "replace")
        w.edit_selection(geo, root_i, "add")
        sel = lambda: w.session.project.objects[geo.id].selections[root_i]  # noqa: E731
        assert sel().members == [root_face, tip_face]
        w.viewport.pick([by_name[root_face]], "replace")
        w.edit_selection(geo, root_i, "remove")
        assert sel().members == [tip_face], w.statusBar().currentMessage()
        w.viewport.pick([by_name[root_face]], "replace")
        w.edit_selection(geo, root_i, "replace")
        assert sel().members == [root_face]
        # Emptying it, or picks of another kind, are refused.
        w.viewport.pick([by_name[root_face]], "replace")
        w.edit_selection(geo, root_i, "remove")
        assert sel().members == [root_face] and "empty" in w.statusBar().currentMessage()
        w.pick_bar.filter.setCurrentIndex(w.pick_bar.filter.findData("edge"))  # clears faces
        edge = next(e for e in names if e[0] == 1)
        w.viewport.pick([edge], "replace")
        w.edit_selection(geo, root_i, "add")
        assert "Faces" in w.statusBar().currentMessage()
        # Edit in the view: its members picked, its kind's filter on.
        w.edit_selection_in_view(geo, root_i)
        assert w.viewport.pick_kind == "face" and w.viewport.picked == [by_name[root_face]]
        assert w.viewport.picking
        # The tree's menu offers the three, enabled once there are picks.
        from puzzle.gui.menus import build_menu
        from puzzle.gui.tree import Node

        node = Node("selection", str(geo.id), f"selections.{root_i}", "Root")
        texts = {a.text(): a.isEnabled() for a in build_menu(w, node).actions()}
        assert texts["Replace with picked"] and texts["Add picked"] and "Edit in the view" in texts

        # The Geometry tab: the part's selections, a click lights one.
        w.left_tabs.setCurrentWidget(w.workbench)
        _settle(w)
        listed = w.workbench.selection_list
        assert listed.count() == len(geo.selections)
        listed.setCurrentRow(root_i)
        w.highlight_part_selection(root_i)
        _settle(w)
        w.highlight_part_selection(root_i)
        assert w.viewport.shown.get("highlight") == "Bar/Root"
    finally:
        w.close()
