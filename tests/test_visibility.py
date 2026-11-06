"""Show and hide (D2) and body colours (D4) in the model view.

On two bolted plates: hiding a part, a body or a load's symbol leaves it out of the view;
a coordinate system or construction plane shown always is drawn with the mesh; that
view state lives in the user folder, not the project; a colour is one command, undone
at once, drawn per cell, and never changes a content hash.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from test_hide_suppress import _two_plates  # noqa: E402

from puzzle.model.schema import (  # noqa: E402
    Component,
    ConstructionGeometry,
    CoordinateSystem,
    Geometry,
)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def plates(app, tmp_path):
    """A window on the two plates with a system and a plane, the component meshed and
    shown."""
    from puzzle.gui.tree import Node
    from puzzle.gui.window import MainWindow
    from puzzle.mesh import generate_mesh
    from puzzle.project import save_project

    p = _two_plates()
    p.add(CoordinateSystem(name="Bolt axes", origin=(0.02, 0.02, 0.0)))
    p.add(ConstructionGeometry(name="Mid", type="plane", origin=(0.02, 0.02, 0.01),
                               normal=(0.0, 0.0, 1.0)))  # fmt: skip
    root = tmp_path / "two"
    save_project(p, root)
    window = MainWindow(root)
    comp = window.session.project.of_type(Component)[0]
    meshed = generate_mesh(window.session.project, comp.id, root, tmp_path / "m")
    cid = str(comp.id)
    window.meshes[cid] = meshed.mesh
    window.mesh_names[cid] = meshed.names
    window.mesh_boundaries[cid] = meshed.boundaries
    window.mesh_vertices[cid] = meshed.vertices
    window.tree.select(Node("object", cid, "", comp.name))
    app.processEvents()
    yield window, root
    window.close()


def _show(window, app):
    from puzzle.gui.tree import Node

    comp = window.session.project.of_type(Component)[0]
    window.tree.select(Node("object", str(comp.id), "", comp.name))
    window._selected()
    app.processEvents()
    return window.viewport.shown


def _geo(window, name):
    return next(g for g in window.session.project.of_type(Geometry) if g.name == name)


def test_a_part_hidden_and_shown_again(plates, app):
    from puzzle.gui import menus

    window, _ = plates
    both = _show(window, app)["cells"]
    menus.show_geometry(window, [_geo(window, "Upper plate")], False)
    shown = _show(window, app)
    assert shown["hidden_parts"] == ["Upper"] and 0 < shown["cells"] < both
    item = next(i for i in window.tree._items() if i.text(0) == "Upper plate")
    assert "hidden" in item.toolTip(0)
    menus.show_all(window)
    assert _show(window, app)["cells"] == both


def test_a_picked_body_hidden_then_shown(plates, app):
    from puzzle.gui import visibility

    window, _ = plates
    both = _show(window, app)["cells"]
    names = window.mesh_names[str(window.session.project.of_type(Component)[0].id)]
    upper = next(e for e, (part, _) in names.items() if e[0] == 3 and part == "Upper")
    window.viewport.picked = [upper]
    visibility.hide_picked_bodies(window)
    geo = _geo(window, "Upper plate")
    assert geo.hidden_bodies == [names[upper][1]] and not geo.hidden
    shown = _show(window, app)
    assert shown["hidden_bodies"] == [upper] and 0 < shown["cells"] < both
    visibility.show_all_bodies(window)
    assert _show(window, app)["cells"] == both


def test_a_loads_symbol_hidden(plates, app):
    from puzzle.gui import visibility

    window, _ = plates
    comp = window.session.project.of_type(Component)[0]
    ph = comp.physics[0]
    force = (ph, ph.features[1])
    visibility.hide_features(window, comp, [force])
    assert _show(window, app)["hidden_symbols"] == 1
    item = next(i for i in window.tree._items() if i.text(0).startswith("Force"))
    assert "hidden" in item.toolTip(0)
    visibility.hide_features(window, comp, [force], False)
    assert _show(window, app)["hidden_symbols"] == 0


def test_systems_and_planes_shown_always_and_kept_out_of_the_project(plates, app):
    from puzzle.gui import visibility
    from puzzle.gui.window import MainWindow

    window, root = plates
    before = {p: p.read_bytes() for p in root.rglob("*.toml")}
    assert _show(window, app).get("extras") == []
    project = window.session.project
    axes = project.of_type(CoordinateSystem)[0]
    plane = project.of_type(ConstructionGeometry)[0]
    visibility.show_objects(window, [axes, plane])
    assert sorted(_show(window, app)["extras"]) == [("Bolt axes", "triad"), ("Mid", "plane")]
    item = next(i for i in window.tree._items() if i.text(0) == "Mid")
    assert "drawn always" in item.toolTip(0)
    visibility.show_only(window, plane)
    assert _show(window, app)["extras"] == [("Mid", "plane")]
    # View state: the user folder's, the same next time, and no project file changed.
    again = MainWindow(root)
    try:
        assert again.visibility.shown == {str(plane.id)}
    finally:
        again.close()
    assert {p: p.read_bytes() for p in root.rglob("*.toml")} == before
    stored = json.loads(visibility.store_path().read_text(encoding="utf-8"))
    assert str(plane.id) in stored[str(root.resolve())]["shown"]


def test_a_colour_is_one_command_drawn_and_out_of_the_hashes(plates, app):
    from puzzle.gui import visibility
    from puzzle.project.graph import content_hashes

    window, _ = plates
    hashes = content_hashes(window.session.project)
    entries = len(window.session.history_entries())
    visibility.colour_geometry(window, [_geo(window, "Lower plate")], "#2a9d8f")
    assert _geo(window, "Lower plate").colour == "#2a9d8f"
    assert len(window.session.history_entries()) == entries + 1
    assert content_hashes(window.session.project) == hashes
    colours = _show(window, app)["colours"]
    assert colours and set(colours.values()) == {"#2a9d8f"}
    window.session.undo()
    assert _geo(window, "Lower plate").colour is None
    assert _show(window, app)["colours"] == {}


def test_a_picked_body_gets_its_own_colour(plates, app):
    from puzzle.gui import visibility

    window, _ = plates
    _show(window, app)  # the bodies are picked on the mesh shown
    names = window.mesh_names[str(window.session.project.of_type(Component)[0].id)]
    upper = next(e for e, (part, _) in names.items() if e[0] == 3 and part == "Upper")
    window.viewport.picked = [upper]
    visibility.colour_picked_bodies(window, "#e76f51")
    assert _geo(window, "Upper plate").body_colours == {names[upper][1]: "#e76f51"}
    colours = _show(window, app)["colours"]
    assert colours[upper] == "#e76f51"
    assert all(colours[f] == "#e76f51" for f in window.viewport.boundaries[upper])


def test_a_bad_colour_is_refused():
    with pytest.raises(ValueError):
        Geometry(name="g", colour="red")
    with pytest.raises(ValueError):
        Geometry(name="g", body_colours={"Body": "#12"})


def test_cells_take_their_bodys_colour():
    """The colours drawn follow `to_grid`'s cell order block by block."""
    import numpy as np

    from puzzle.gui.viewport import _cell_colours
    from puzzle.mesh.mesh import CellBlock, Mesh

    tet = np.array([[1, 2, 3, 4]])
    mesh = Mesh(
        node_ids=np.arange(1, 6),
        coords=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float),
        blocks=[CellBlock("tet4", 3, (3, 1), np.array([1]), tet),
                CellBlock("tet4", 3, (3, 2), np.array([2, 3]), np.array([[2, 3, 4, 5]] * 2))],
    )  # fmt: skip
    rgb = _cell_colours(mesh, {(3, 2): "#ff0000"})
    assert rgb.shape == (3, 3) and tuple(rgb[1]) == (255, 0, 0) and tuple(rgb[2]) == (255, 0, 0)
    assert tuple(rgb[0]) != (255, 0, 0)  # the first body keeps the theme's colour
