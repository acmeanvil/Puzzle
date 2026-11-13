"""One part per body (ADR 0039): the plate and two cubes of test_contacts_in_a_part, one
STEP part, split into three parts. Every reference follows its body: a material on the
whole part becomes one per part, a support on the plate's base moves to the plate, a
sliding contact between the plate and cube A moves with its faces; bonded contacts join
the pairs that touched with no contact (plate–B, A–B), as Share faces had; one undo
takes it all back, and the split project meshes with every selection found.
"""

import numpy as np
import pytest

from puzzle.model.schema import (
    Component,
    Contact,
    Feature,
    Geometry,
    MaterialAssignment,
    NamedSelection,
    Physics,
)

pytest.importorskip("OCP")

from test_contacts_in_a_part import _project  # noqa: E402

from puzzle.materials import add_library_material  # noqa: E402


def _setup(root):
    from puzzle.commands.session import Session
    from puzzle.geometry.contacts import _face_owners
    from puzzle.geometry.naming import named_geometry
    from puzzle.project import save_project

    p, comp = _project(root)
    geo = p.of_type(Geometry)[0]
    owners = _face_owners(named_geometry(p, geo, root))
    plate = [f for f, b in owners.items() if b == "body-1"]
    cube_a = [f for f, b in owners.items() if b == "body-2"]
    # The plate's base (z = 0) is its first face in file order for a box: find it.
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    faces = named_geometry(p, geo, root).of["face"]

    def z(name):
        props = GProp_GProps()
        _static(BRepGProp, "SurfaceProperties")(faces[name][0], props)
        return props.CentreOfMass().Z()

    base = next(f for f in plate if abs(z(f)) < 1e-9)
    top = next(f for f in plate if abs(z(f) - 0.01) < 1e-9)
    under = next(f for f in cube_a if abs(z(f) - 0.01) < 1e-9)
    geo.selections += [
        NamedSelection(name="Base", entity="face", members=[base]),
        NamedSelection(name="Plate top", entity="face", members=[top]),
        NamedSelection(name="Under A", entity="face", members=[under]),
    ]
    steel = add_library_material(p, "structural_steel")
    comp.materials = [MaterialAssignment(material=steel.id, selection="Stack")]
    comp.physics = [Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Stack/Base")])]  # fmt: skip
    comp.contacts = [Contact(name="Slide", type="frictional", friction=0.2,
                             source="Stack/Under A", target="Stack/Plate top")]  # fmt: skip
    save_project(p, root)
    return Session(root)


def test_splitting_moves_every_reference_and_bonds_what_touched(tmp_path):
    from puzzle.split_parts import plan

    session = _setup(tmp_path)
    comp = session.project.of_type(Component)[0]
    made = plan(session.project, comp, "Stack", tmp_path)
    assert [n for n, _ in made.parts] == ["Plate", "Cube A", "Cube B"]
    assert sorted(made.bonds) == ["Cube A to Cube B", "Plate to Cube B"]
    session.submit_batch(made.commands)
    p = session.project
    comp = p.of_type(Component)[0]
    assert [x.name for x in comp.parts] == ["Plate", "Cube A", "Cube B"]
    assert [m.selection for m in comp.materials] == ["Plate", "Cube A", "Cube B"]
    assert [f.selection for f in comp.physics[0].features] == ["Plate/Base"]
    slide = next(c for c in comp.contacts if c.name == "Slide")
    assert (slide.source, slide.target) == ("Cube A/Under A", "Plate/Plate top")
    assert {c.name for c in comp.contacts} == {"Slide", "Cube A to Cube B", "Plate to Cube B"}
    names = sorted(g.name for g in p.of_type(Geometry))
    assert names == ["Cube A", "Cube B", "Plate"]  # the old geometry went, unused
    cube = next(g for g in p.of_type(Geometry) if g.name == "Cube A")
    assert [s.kind for s in cube.history] == ["import", "keep"]
    assert {s.name for s in cube.selections} >= {"Cube A", "Under A"}
    session.undo()
    comp = session.project.of_type(Component)[0]
    assert [x.name for x in comp.parts] == ["Stack"] and len(comp.contacts) == 1


def test_the_split_project_meshes_with_every_selection_found(tmp_path):
    from puzzle.mesh.generate import generate_mesh
    from puzzle.split_parts import plan

    session = _setup(tmp_path)
    comp = session.project.of_type(Component)[0]
    session.submit_batch(plan(session.project, comp, "Stack", tmp_path).commands)
    p = session.project
    comp = p.of_type(Component)[0]
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    mesh = result.mesh
    wanted = {m.selection for m in comp.materials} | {"Plate/Base"}
    wanted |= {s for c in comp.contacts for s in (c.source, c.target)}
    assert wanted <= set(mesh.groups)
    assert not [w for w in result.warnings if "not found" in w]
    for c in comp.contacts:
        a, b = (set(np.asarray(mesh.groups[s].node_ids).tolist()) for s in (c.source, c.target))
        small, large = sorted((a, b), key=len)
        if c.type == "bonded":
            assert small and small <= large  # joined
    slide = next(c for c in comp.contacts if c.name == "Slide")
    a, b = (set(np.asarray(mesh.groups[s].node_ids).tolist()) for s in (slide.source, slide.target))
    assert len(a & b) <= 0.2 * len(a)  # apart but for cube B's line (4 of 20 nodes)


def test_one_body_is_refused(tmp_path):
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import HistoryStep, MeshRecipe, PartInstance, Project, Quantity
    from puzzle.split_parts import plan

    p = Project(name="one")
    step = HistoryStep(kind="box", name="B", args={"dx": 1, "dy": 1, "dz": 1})
    g = p.add(Geometry(name="Box", history=[step]))
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=0.5, unit="m")))
    box = PartInstance(name="Box", geometry=g.id)
    comp = p.add(Component(name="C", mesh=mesh.id, parts=[box]))
    with pytest.raises(PuzzleError, match="one body"):
        plan(p, comp, "Box", tmp_path)


def test_the_menu_and_the_agent_split_too(tmp_path):
    import asyncio
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("mcp")
    from PySide6.QtWidgets import QApplication

    from puzzle.agent.mcp import build_server
    from puzzle.commands.session import Session
    from puzzle.gui.split_dialog import split_part
    from puzzle.gui.window import MainWindow

    session = _setup(tmp_path)
    Session(tmp_path).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path, "test")
    result = asyncio.run(server.call_tool("parts_split", {"component": "Stack", "part": "Stack"}))
    out = result.structured_content.get("result", result.structured_content)
    assert [x["name"] for x in out["parts"]] == ["Plate", "Cube A", "Cube B"]
    assert out["batch"] and len(out["bonded_contacts"]) == 2

    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.ask_form = lambda *a, **k: {"bond": False}
    split_part(window, window.session.project.of_type(Component)[0], "Stack")
    comp = window.session.project.of_type(Component)[0]
    assert [x.name for x in comp.parts] == ["Plate", "Cube A", "Cube B"]
    assert [c.name for c in comp.contacts] == ["Slide"]  # not bonded: asked not to
    window.close()
    assert app is not None and session is not None
