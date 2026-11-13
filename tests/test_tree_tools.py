"""The tree's tools on an assembly (the maintainer's findings, 2026-11-09).

On two bolted plates: several items deleted together are one batch and one undo; a
body selection hides, shows, hides the others and takes a colour from the tree; groups
sit inside a geometry, a component and a physics, and hiding one hides what it holds;
bolts hide one by one, by group and all (view state, never the project); a body
selection's Assign material… gives the component that body's material.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from test_hide_suppress import _two_plates  # noqa: E402

from puzzle.gui.tree import ROLE, Node  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    CoordinateSystem,
    Geometry,
    Material,
    Study,
    TreeGroup,
)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def plates(app, tmp_path):
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    p = _two_plates()
    p.add(CoordinateSystem(name="Bolt axes", origin=(0.02, 0.02, 0.0)))
    root = tmp_path / "two"
    save_project(p, root)
    window = MainWindow(root)
    yield window
    window.close()


def _comp(w) -> Component:
    return w.session.project.of_type(Component)[0]


def _geo(w, name) -> Geometry:
    return next(g for g in w.session.project.of_type(Geometry) if g.name == name)


def _item(w, kind, label=None, obj=None):
    for item in w.tree._items():
        node = item.data(0, ROLE)
        if node.kind == kind and (label is None or node.label == label) and (
                obj is None or node.object == str(obj.id)):  # fmt: skip
            return item
    raise AssertionError(f"no {kind} {label}")


def _select(w, *items):
    w.tree.clearSelection()
    for i in items:
        i.setSelected(True)
    w.tree.setCurrentItem(items[-1], 0, w.tree.selectionModel().SelectionFlag.NoUpdate)


def test_several_items_deleted_as_one_batch_and_one_undo(plates):
    w = plates
    asked = []
    w.ask_yes = lambda title, text: asked.append(text) or True
    entries = len(w.session.history_entries())
    _select(w, _item(w, "feature", "Upper/Top"), _item(w, "feature", "Lower/Bottom"),
            _item(w, "contact", "Seat"), _item(w, "object", "Bolt axes"),
            _item(w, "object", "S"))  # fmt: skip
    w.delete_selected()
    assert asked and "5 items" in asked[0] and "Seat" in asked[0]
    project = w.session.project
    assert not project.of_type(Study) and not project.of_type(CoordinateSystem)
    assert _comp(w).physics[0].features == [] and _comp(w).contacts == []
    assert len(w.session.history_entries()) == entries + 1
    w.undo()
    comp = _comp(w)
    assert [f.type for f in comp.physics[0].features] == ["fixed", "force"]
    assert comp.contacts and w.session.project.of_type(CoordinateSystem)
    # A component and the study on it: the study goes first, so the session takes it.
    _select(w, _item(w, "object", "Joint"), _item(w, "object", "S"))
    w.delete_selected()
    assert not w.session.project.of_type(Component) and not w.session.project.of_type(Study)


def test_items_of_one_list_removed_highest_first(plates):
    """Two loads of one physics: both go, whatever the order they were picked in."""
    from puzzle.gui import deleting

    w = plates
    comp = _comp(w)
    nodes = [Node("feature", str(comp.id), "physics.0.features.0", "Lower/Bottom"),
             Node("feature", str(comp.id), "physics.0.features.1", "Upper/Top"),
             Node("physics", str(comp.id), "physics.0.features", "x")]  # fmt: skip
    plan = deleting.plan(w.session.project, nodes[:2])
    (edit,) = plan.commands
    assert [op.path for op in edit.ops] == ["physics.0.features.1", "physics.0.features.0"]
    w.submit_batch(plan.commands)
    assert _comp(w).physics[0].features == []


def test_a_body_selection_hidden_shown_and_coloured_from_the_tree(plates):
    from puzzle.gui import visibility

    w = plates
    upper = _geo(w, "Upper plate")
    body = _item(w, "selection", "Body", upper).data(0, ROLE)
    things = visibility.node_things(w, body)
    assert things == [visibility.Thing("body", str(upper.id), "Body")]
    visibility.set_hidden(w, things)
    assert _geo(w, "Upper plate").hidden_bodies == ["Body"]
    assert "hidden" in _item(w, "selection", "Body", upper).toolTip(0)
    visibility.show_all_like(w, body)
    assert _geo(w, "Upper plate").hidden_bodies == []
    visibility.hide_others(w, body)  # every other geometry and body
    assert _geo(w, "Lower plate").hidden and not _geo(w, "Upper plate").hidden
    w.undo()
    assert not _geo(w, "Lower plate").hidden
    visibility.colour_bodies(w, _geo(w, "Upper plate"), ["Body"], "#e76f51")
    assert _geo(w, "Upper plate").body_colours == {"Body": "#e76f51"}
    visibility.colour_bodies(w, _geo(w, "Upper plate"), ["Body"], None)
    assert _geo(w, "Upper plate").body_colours == {}


def test_a_body_selections_right_click_offers_hide_colour_and_material(plates):
    from puzzle.gui.menus import build_menu

    w = plates
    item = _item(w, "selection", "Body", _geo(w, "Upper plate"))
    w.tree.setCurrentItem(item)
    texts = [a.text() for a in build_menu(w, item.data(0, ROLE)).actions()]
    for wanted in ("Hide", "Hide all others", "Show all", "Colour…", "Assign material…"):
        assert wanted in texts, texts


def test_assign_material_from_a_body_selection(plates):
    from forms import fill

    from puzzle.gui import menus

    w = plates
    steel = w.session.project.of_type(Material)[0]
    comp = _comp(w)
    w.session.submit_batch([{"command": "edit", "object": str(comp.id), "ops": [
        {"op": "remove", "path": "materials.1"}]}])  # fmt: skip
    assert [a.selection for a in _comp(w).materials] == ["Lower/Body"]
    # One component uses the geometry: the form asks only for the material.
    fill(w, material=steel.name)
    menus.assign_material_to_bodies(w, _geo(w, "Upper plate"), "Body")
    assert set(w.last_form.widgets) == {"material"}
    assert [a.selection for a in _comp(w).materials] == ["Lower/Body", "Upper/Body"]
    fill(w, material=steel.name)  # again: the assignment is replaced, not doubled
    menus.assign_material_to_bodies(w, _geo(w, "Upper plate"), "Body")
    assert [a.selection for a in _comp(w).materials].count("Upper/Body") == 1


def test_groups_inside_a_component_and_a_physics(plates):
    from puzzle.gui import organise, visibility

    w = plates
    comp = _comp(w)
    scope = f"{comp.id}/solid_mechanics"
    keys = ["feature:fixed:Lower/Bottom", "feature:force:Upper/Top"]
    organise.new_group(w, scope, keys, name="Loads and supports")
    folder = _item(w, "group", "Loads and supports")
    # In the physics' default folder (the maintainer, 2026-11-11).
    assert folder.parent().data(0, ROLE).label == "Loads and boundary conditions"
    assert folder.parent().parent().data(0, ROLE).kind == "physics"
    assert {folder.child(i).data(0, ROLE).label for i in range(folder.childCount())} == {
        "Lower/Bottom", "Upper/Top"}  # fmt: skip
    assert w.tree.scope_of(folder.child(0)) == scope
    # Hiding the group hides its symbols (view state).
    node = folder.data(0, ROLE)
    things = visibility.node_things(w, node)
    assert {t.kind for t in things} == {"feature"} and len(things) == 2
    visibility.set_hidden(w, things)
    assert len(w.visibility.hidden_features) == 2
    # Properties say where it sits by name, never by id.
    w.tree.setCurrentItem(_item(w, "group", "Loads and supports"))
    rows = w._group_rows(w.session.project.of_type(TreeGroup)[0])
    assert rows["Under"] == "Joint: Solid mechanics"
    # A component's own items: its bolt and contact in a group, out again.
    organise.new_group(w, str(comp.id), ["connector:Bolt", "contact:Seat"], name="Joints")
    # A group of a bolt and a contact shows in both folders, each with its own.
    shown = [i for i in w.tree._items() if i.data(0, ROLE).kind == "group"
             and i.data(0, ROLE).label == "Joints"]  # fmt: skip
    assert sorted(i.parent().data(0, ROLE).label for i in shown) == ["Contacts", "Fasteners"]
    bolt = _item(w, "connector", "Bolt")
    assert w.tree.groupable(bolt) and bolt.parent().data(0, ROLE).label == "Joints"
    organise.move(w, ["connector:Bolt"], None, str(comp.id))
    assert _item(w, "connector", "Bolt").parent().data(0, ROLE).label == "Fasteners"
    # A geometry's selections.
    upper = _geo(w, "Upper plate")
    organise.new_group(w, str(upper.id), ["selection:Top", "selection:Bottom"], name="Faces")
    faces = _item(w, "group", "Faces")
    assert faces.parent().data(0, ROLE).label == "Selections"
    assert faces.parent().parent().data(0, ROLE).label == "Upper plate"


def test_bolts_hidden_one_by_group_and_all_kept_out_of_the_project(plates):
    from puzzle.gui import visibility

    w = plates
    comp = _comp(w)
    before = {p: p.read_bytes() for p in w.session.root.rglob("*.toml")}
    visibility.hide_bolts(w, comp, comp.connectors)
    assert w.visibility.hidden_bolts == {visibility.bolt_key(comp, comp.connectors[0])}
    assert w._hidden_bolt_names(comp) == {"Bolt"}
    assert "hidden" in _item(w, "connector", "Bolt").toolTip(0)
    node = _item(w, "connector", "Bolt").data(0, ROLE)
    w.tree.setCurrentItem(_item(w, "connector", "Bolt"))
    visibility.toggle_selected(w)  # the menu bar's Show or hide the selected item
    assert w.visibility.hidden_bolts == set()
    visibility.hide_others(w, node)  # the only bolt: nothing else to hide
    assert w.visibility.hidden_bolts == set()
    assert {p: p.read_bytes() for p in w.session.root.rglob("*.toml")} == before


def test_hidden_bolts_are_not_drawn():
    from puzzle.post.symbols import _fastener_symbols

    class Bolt:
        name = "Bolt"

    class Comp:
        connectors = [Bolt()]

    assert _fastener_symbols(Comp(), None, None, {"Bolt"}) == []


def test_default_folders_sort_what_is_added(plates):
    """A component's Geometry, Material assignments and Fasteners, a physics' Contacts and
    Loads and boundary conditions, a geometry's Bodies and Selections; Bolt types before
    Studies; a group made in a folder stays there (the maintainer, 2026-11-11)."""
    from puzzle.gui import organise

    w = plates

    def under(kind, label):
        return [i.data(0, ROLE).label for p in [_item(w, kind, label).parent()]
                for i in [p, p.parent()]]  # fmt: skip

    comp = _comp(w)
    assert under("part", "Upper")[0] == "Geometry"
    assert under("connector", "Bolt") == ["Fasteners", "Joint"]
    assert under("contact", "Seat")[0] == "Contacts"
    assert under("feature", "Lower/Bottom")[0] == "Loads and boundary conditions"
    assert under("selection", "Top")[0] == "Selections"
    folders = [i.data(0, ROLE) for i in w.tree._items() if i.data(0, ROLE).kind == "folder"]
    held = {n.label for n in folders if n.object == str(comp.id)}
    assert {"Geometry", "Material assignments", "Fasteners", "Contacts",
            "Loads and boundary conditions"} <= held  # fmt: skip
    titles = [w.tree.topLevelItem(0).child(i).data(0, ROLE).label
              for i in range(w.tree.topLevelItem(0).childCount())]  # fmt: skip
    if "Bolt types" in titles:
        assert titles.index("Bolt types") < titles.index("Studies")
    # An empty group made on the Fasteners folder stays in it.
    organise.new_group(w, str(comp.id), [], name="Flange bolts", folder="fasteners")
    assert _item(w, "group", "Flange bolts").parent().data(0, ROLE).label == "Fasteners"
    # A selected folder is never its component (Delete there deletes nothing).
    item = next(i for i in w.tree._items() if i.data(0, ROLE).label == "Fasteners")
    assert w._object(item.data(0, ROLE)) is None
    # Selecting a folder again after a refresh finds that folder, not its neighbour.
    w.tree.setCurrentItem(item)
    w.refresh()
    assert w.tree.current().label == "Fasteners"
