"""Coordinate systems and construction geometry edited after they are made (D1, D3 of
the first Windows tests), and paths and planes placed on the model's edges and faces.

A 40 x 20 x 10 mm box with an edge selection along z at (40, 20) mm and a face
selection on its top: the path along the edge runs between that edge's ends, the plane
on the top face sits at its centre facing z; Edit… reopens either panel filled in, and
Apply changes the object in one command that undo takes back.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.axes_panel import edit_axes  # noqa: E402
from puzzle.gui.construction import (  # noqa: E402
    PathPanel,
    PlanePanel,
    edge_ends,
    edit_construction,
)
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.mesh.generate import generate_mesh  # noqa: E402
from puzzle.mesh.mesh import CellBlock, Mesh  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    ConstructionGeometry,
    CoordinateSystem,
    Geometry,
    HistoryStep,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.project import save_project  # noqa: E402

BOX = [HistoryStep(kind="box", name="B", args={"dx": 0.04, "dy": 0.02, "dz": 0.01})]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "box"
    root.mkdir()
    p = Project(name="box")
    geo = p.add(Geometry(name="G", history=BOX, selections=[
        NamedSelection(name="Edge", entity="edge", members=["B/+x|B/+y"]),
        NamedSelection(name="Top", entity="face", members=["B/+z"]),
    ]))  # fmt: skip
    recipe = p.add(MeshRecipe(name="M", global_size=Quantity(value=4, unit="mm"),
                              element_order=1))  # fmt: skip
    comp = p.add(Component(name="C", parts=[PartInstance(name="K", geometry=geo.id)],
                           mesh=recipe.id))  # fmt: skip
    save_project(p, root)
    meshed = generate_mesh(p, comp.id, root, tmp_path / "m")
    w = MainWindow(root)
    w.meshes[str(comp.id)] = meshed.mesh
    yield w
    w.close()


def _line_mesh(points) -> Mesh:
    ids = np.arange(1, len(points) + 1)
    pairs = np.column_stack([ids[:-1], ids[1:]])
    block = CellBlock(element="line2", dim=1, entity=(1, 7), ids=np.arange(len(pairs)),
                      connectivity=pairs)  # fmt: skip
    return Mesh(node_ids=ids, coords=np.asarray(points, float), blocks=[block])


def test_edge_ends_take_a_straight_edge_and_refuse_a_curved_or_closed_one():
    a, b = edge_ends(_line_mesh([[0, 0, 0], [0.5, 0, 0], [1, 0, 0]]), [(1, 7)])
    assert sorted([a[0], b[0]]) == [0.0, 1.0]
    t = np.linspace(0, np.pi / 2, 9)
    arc = np.column_stack([np.cos(t), np.sin(t), 0 * t])
    with pytest.raises(ValueError, match="curved edge"):
        edge_ends(_line_mesh(arc), [(1, 7)])
    t = np.linspace(0, 2 * np.pi, 9)[:-1]
    ring = np.column_stack([np.cos(t), np.sin(t), 0 * t])
    loop = _line_mesh(np.vstack([ring, ring[:1]]))
    loop.blocks[0].connectivity[-1, 1] = 1  # back to the first node
    loop.node_ids, loop.coords = loop.node_ids[:-1], loop.coords[:-1]
    with pytest.raises(ValueError, match="closed edge"):
        edge_ends(loop, [(1, 7)])


def _made(window, name):
    return next(c for c in window.session.project.of_type(ConstructionGeometry) if c.name == name)


def test_a_path_along_an_edge_selection_and_a_plane_on_a_face(window):
    path = PathPanel(window)
    path.how.setCurrentIndex(path.how.findData("edge"))
    edge = next(i for i in range(path.edge.count()) if "Edge (K)" in path.edge.itemText(i))
    path.edge.setCurrentIndex(edge)
    path.name.setText("Along")
    assert path.problem() is None
    path.create()
    along = _made(window, "Along")
    ends = sorted([along.start, along.end], key=lambda p: p[2])
    assert ends[0] == pytest.approx((0.04, 0.02, 0.0), abs=1e-9)
    assert ends[1] == pytest.approx((0.04, 0.02, 0.01), abs=1e-9)
    assert along.made_from == "edge Edge (K)"

    plane = PlanePanel(window)
    plane.how.setCurrentIndex(plane.how.findData("face"))
    top = next(i for i in range(plane.face.count()) if "Top (K)" in plane.face.itemText(i))
    plane.face.setCurrentIndex(top)
    plane.offset.setText("0.002")  # SI by default: metres
    plane.name.setText("Over the top")
    assert plane.problem() is None
    plane.create()
    over = _made(window, "Over the top")
    assert abs(over.normal[2]) == pytest.approx(1.0)
    assert over.origin[:2] == pytest.approx((0.02, 0.01), abs=1e-9)
    assert over.origin[2] == pytest.approx(0.01 + 0.002 * np.sign(over.normal[2]), abs=1e-9)
    assert over.made_from == "face Top (K)"


def test_a_picked_face_is_asked_for_when_none_is_picked(window):
    plane = PlanePanel(window)
    plane.how.setCurrentIndex(plane.how.findData("face"))  # the picked face: none yet
    assert "Pick a face" in plane.problem()


def test_editing_a_plane_and_a_path_is_one_undoable_command(window):
    from puzzle.commands.model import Create

    window.submit(Create(kind="construction", data={
        "name": "Cut", "type": "plane", "origin": [0.01, 0, 0], "normal": [1, 0, 0],
        "made_from": "face Old (K)"}))  # fmt: skip
    window.submit(Create(kind="construction", data={
        "name": "Line", "type": "path", "start": [0, 0, 0], "end": [0.04, 0, 0]}))  # fmt: skip
    entries = len(window.session.history_entries())

    panel = edit_construction(window, _made(window, "Cut"))
    assert panel.create_button.text() == "Apply"
    assert panel.facing.currentData() == "x" and panel.name.text() == "Cut"
    panel.facing.setCurrentIndex(panel.facing.findData("typed"))
    panel.direction.setText("0, 1, 1")
    panel.origin.text.setText("0.02, 0.01, 0.005")
    panel.name.setText("Tilted")
    panel.create()
    tilted = _made(window, "Tilted")
    assert tilted.normal == pytest.approx((0, 1, 1)) and tilted.origin[0] == pytest.approx(0.02)
    assert tilted.made_from is None  # typed now, not on a face
    assert len(window.session.history_entries()) == entries + 1
    window.session.undo()  # (a second undo would redo it)
    assert _made(window, "Cut").normal == pytest.approx((1, 0, 0))
    assert _made(window, "Cut").made_from == "face Old (K)"

    path = edit_construction(window, _made(window, "Line"))
    path.end.text.setText("0.04, 0.02, 0")
    path.create()
    assert _made(window, "Line").end == pytest.approx((0.04, 0.02, 0.0))


def test_a_coordinate_system_is_redefined_in_one_command(window):
    from puzzle.commands.model import Create

    local = {"name": "Local", "type": "cartesian", "origin": [0, 0, 0], "angles": [0, 0, 30]}
    window.submit(Create(kind="axes", data=local))
    cs = next(c for c in window.session.project.of_type(CoordinateSystem) if c.name == "Local")
    entries = len(window.session.history_entries())

    panel = edit_axes(window, cs)
    assert panel.create_button.text() == "Apply"
    assert panel.how.currentData() == "angles" and panel.angles.text() == "0, 0, 30"
    assert panel.relative.findData(str(cs.id)) == -1  # not relative to itself
    panel.type.setCurrentIndex(panel.type.findData("cylindrical"))
    panel.how.setCurrentIndex(panel.how.findData("directions"))
    panel.axis.setText("1, 0, 0")
    panel.origin.text.setText("0.02, 0.01, 0.005")
    assert panel.problem() is None
    panel.create()
    after = window.session.project.objects[cs.id]
    assert after.type == "cylindrical" and after.angles is None  # the old way cleared
    assert after.axis == pytest.approx((1, 0, 0))
    assert after.origin == pytest.approx((0.02, 0.01, 0.005))
    assert len(window.session.history_entries()) == entries + 1

    window.session.undo()
    back = window.session.project.objects[cs.id]
    assert back.type == "cartesian" and back.angles == pytest.approx((0, 0, 30))
