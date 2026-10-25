"""Find contacts between parts (ADR 0039).

A 20 mm cube sits on a 100 × 100 × 10 mm plate: their touching faces are found, with
the cube's base area as the overlap. A cube lifted 0.2 mm is still found (within 0.5 %
of the model's size), with that gap; one lifted 5 mm, or standing beside the plate,
isn't. A turned part is found where its placement puts it. Accepting makes face
selections and the contacts in one batch, which one undo removes.
"""

import pytest

from puzzle.model.schema import (
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    PartInstance,
    Project,
    Quantity,
)

pytest.importorskip("OCP")

from puzzle.geometry.contacts import contact_commands, find_contacts, proposals  # noqa: E402


def _assembly(cube_at=(0.04, 0.04, 0.01), rotation=(0.0, 0.0, 0.0)):
    p = Project(name="assembly")
    plate = p.add(
        Geometry(
            name="Plate",
            history=[HistoryStep(kind="box", name="P", args={"dx": 0.1, "dy": 0.1, "dz": 0.01})],
        )
    )
    cube = p.add(
        Geometry(
            name="Cube",
            history=[HistoryStep(kind="box", name="C", args={"dx": 0.02, "dy": 0.02, "dz": 0.02})],
        )
    )
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(
        Component(
            name="Stack",
            mesh=mesh.id,
            parts=[
                PartInstance(name="Plate", geometry=plate.id),
                PartInstance(name="Cube", geometry=cube.id, translation=cube_at, rotation=rotation),
            ],
        )
    )
    return p, comp


def test_a_cube_on_a_plate(tmp_path):
    p, comp = _assembly()
    (pair,) = find_contacts(p, comp, tmp_path)
    assert (pair.source_part, pair.source_face) == ("Plate", "P/+z")
    assert (pair.target_part, pair.target_face) == ("Cube", "C/-z")
    assert pair.distance == pytest.approx(0.0, abs=1e-9)
    assert pair.area == pytest.approx(0.02 * 0.02, rel=1e-6)


def test_gaps_and_neighbours(tmp_path):
    p, comp = _assembly(cube_at=(0.04, 0.04, 0.0102))
    (pair,) = find_contacts(p, comp, tmp_path)
    assert pair.distance == pytest.approx(2e-4, rel=1e-6)
    p, comp = _assembly(cube_at=(0.04, 0.04, 0.015))
    assert find_contacts(p, comp, tmp_path) == []
    # Beside the plate, touching its edge face: the cube's side faces the plate's side
    # only over the plate's 10 mm height, and that is found; standing off it, nothing.
    p, comp = _assembly(cube_at=(0.1, 0.04, 0.0))
    (pair,) = find_contacts(p, comp, tmp_path)
    assert {pair.source_face, pair.target_face} == {"P/+x", "C/-x"}
    assert pair.area == pytest.approx(0.02 * 0.01, rel=1e-6)


def test_a_turned_part_is_placed(tmp_path):
    # Turned 90° about x ((x, y, z) -> (x, -z, y)), the cube's -y face is its base, at
    # z = 0 before the move, and it spans y = -20..0 mm: moved by 60 mm it is on the plate.
    p, comp = _assembly(cube_at=(0.04, 0.06, 0.01), rotation=(90.0, 0.0, 0.0))
    (pair,) = find_contacts(p, comp, tmp_path)
    assert pair.source_face == "P/+z" and pair.target_face == "C/-y"


def test_accepting_makes_selections_and_contacts_in_one_batch(tmp_path):
    from puzzle.commands.session import Session
    from puzzle.project import save_project

    p, comp = _assembly()
    root = tmp_path / "assembly"
    save_project(p, root)
    session = Session(root)
    project = session.project
    comp = project.of_type(Component)[0]
    (prop,) = proposals(find_contacts(project, comp, root))
    assert prop.name == "Plate to Cube" and prop.type == "bonded"
    session.submit_batch(contact_commands(project, comp, [prop]))
    comp = session.project.of_type(Component)[0]
    (contact,) = comp.contacts
    assert (contact.source, contact.target) == ("Plate/Touches Cube", "Cube/Touches Plate")
    plate = next(g for g in session.project.of_type(Geometry) if g.name == "Plate")
    assert [s.members for s in plate.selections if s.name == "Touches Cube"] == [["P/+z"]]
    session.undo()
    assert session.project.of_type(Component)[0].contacts == []


def test_agents_find_the_same_and_submit_the_batch(tmp_path):
    """MCP `contacts_find`: the proposals and a ready batch; submitting it makes them."""
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.commands.session import Session
    from puzzle.project import save_project

    p, comp = _assembly()
    root = tmp_path / "assembly"
    save_project(p, root)
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    found = call("contacts_find", component="Stack")
    (prop,) = found["proposals"]
    assert prop["name"] == "Plate to Cube" and prop["source_faces"] == ["P/+z"]
    assert prop["overlap_m2"] == pytest.approx(4e-4, rel=1e-6)
    out = call("submit", command=found["make_bonded"], rationale="bond the stack")
    assert out["status"] == "applied"
    (contact,) = Session(root).project.of_type(Component)[0].contacts
    assert contact.type == "bonded"


def test_the_dialog_reviews_and_makes(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.contact_search import find_contacts_dialog
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])
    p, comp = _assembly()
    root = tmp_path / "assembly"
    save_project(p, root)
    window = MainWindow(root)
    shown = []
    window.show_faces = lambda comp, faces: shown.append(faces)

    def review(dialog):
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "Plate to Cube"
        dialog.table.setCurrentCell(0, 1)  # shows both sides' faces
        types = dialog.table.cellWidget(0, 5)
        types.setCurrentIndex(types.findData("frictional"))
        dialog.table.cellWidget(0, 6).setValue(0.3)
        return True

    window.run_dialog = review
    find_contacts_dialog(window, window.session.project.of_type(Component)[0])
    assert shown == [[("Plate", "P/+z"), ("Cube", "C/-z")]]
    (contact,) = window.session.project.of_type(Component)[0].contacts
    assert contact.type == "frictional" and contact.friction == pytest.approx(0.3)
    assert window.session.history(limit=1)[0]["command"]["command"] == "batch"
    window.close()
    assert app is not None
