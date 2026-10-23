"""ADR 0034: picking by click and modifier keys, undo while picking, extend across
smooth joins, pick alike, a whole part.

The part: an 80 × 40 × 10 mm plate, two holes of radius 3 mm and one of 4 mm, its four
vertical corners rounded. The plate's sides and the corner fillets join smoothly, so
extending from one side picks the whole outer wall (4 sides + 4 fillets) and none of
the top, bottom or holes (at 90°). "Same as" on one 3 mm hole picks the other, not the
4 mm one. The top outer edges chain the same way round the plate.
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    p = Project(name="plate")
    geo = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(kind="box", name="Plate", args={"dx": 0.08, "dy": 0.04, "dz": 0.01}),
                HistoryStep(kind="hole", name="H1", args={"x": 0.02, "y": 0.02, "r": 0.003}),
                HistoryStep(kind="hole", name="H2", args={"x": 0.04, "y": 0.02, "r": 0.003}),
                HistoryStep(kind="hole", name="H3", args={"x": 0.06, "y": 0.02, "r": 0.004}),
                HistoryStep(
                    kind="fillet",
                    name="Round",
                    args={"rule": {"parallel": "z", "outer": True}, "r": 0.005},
                ),
            ],
            selections=[NamedSelection(name="Body", entity="body", members=["body-1"])],
        )
    )
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=4, unit="mm")))
    p.add(Component(name="Plate", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id))
    root = tmp_path / "plate"
    save_project(p, root)
    w = MainWindow(root)
    for item in w.tree._items():
        node = item.data(0, ROLE)
        if node.kind == "object" and node.label == "Plate" and "Component" in item.parent().text(0):
            w.tree.setCurrentItem(item)
    w.mesh_selected()
    end = time.monotonic() + 300
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    w.toggle_picking(True)
    yield w
    w.close()


def entity(w, name, dim=2):
    names = w.mesh_names[w.mesh_component]
    return next(e for e, (_, n) in names.items() if n == name and e[0] == dim)


def picked(w):
    return sorted(n for _, n in w.picked_names())


def test_modes_undo_extend_alike_and_part(window):
    w, v = window, window.viewport
    side, top = entity(w, "Plate/-x"), entity(w, "Plate/+z")
    v.pick([side])
    v.pick([top], "add")  # Shift-click
    assert picked(w) == ["Plate/+z", "Plate/-x"]
    v.pick([top], "remove")  # Ctrl- or ⌘-click
    assert picked(w) == ["Plate/-x"]
    w.undo()  # while picking: the picks, not the project
    assert picked(w) == ["Plate/+z", "Plate/-x"]
    v.pick([side])

    v.extend_picks()
    walls = picked(w)
    assert len(walls) == 8, walls  # four sides and four corner fillets
    assert not any(n.startswith(("H", "Plate/+z", "Plate/-z")) for n in walls)
    assert sum(n.startswith("Round") for n in walls) == 4

    v.pick([entity(w, "H1/wall")])
    w.pick_alike()
    assert picked(w) == ["H1/wall", "H2/wall"]  # not the 4 mm hole

    v.pick([top])
    w.pick_whole_part()
    assert len(picked(w)) == len([e for e in w.mesh_names[w.mesh_component] if e[0] == 2])


def test_edge_chains_follow_the_outline(window):
    w, v = window, window.viewport
    v.set_pick_kind("edge")
    edge = next(
        e for e, (_, n) in w.mesh_names[w.mesh_component].items()
        if e[0] == 1 and set(n.split("|")) == {"Plate/+z", "Plate/-x"}
    )  # fmt: skip
    v.pick([edge])
    v.extend_picks()
    chain = picked(w)
    assert len(chain) == 8, chain  # four straight top edges and four fillet arcs
    assert all("Plate/+z" in n.split("|") for n in chain)
