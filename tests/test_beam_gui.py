"""Beams in the model and the GUI (ADR 0027); the solver benchmarks are in
tests/integration/test_beams.py.

A frame drawn as a polyline gets a tube section through Add beam section…: the tree and
the outline name it, the properties panel shows only the sizes a tube takes, in readable
words, Elmer reports that it does not write beams, and the polyline is a ribbon tool.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.adapters.calculix import CalculixAdapter  # noqa: E402
from puzzle.adapters.elmer import ElmerAdapter  # noqa: E402
from puzzle.commands.outline import outline  # noqa: E402
from puzzle.gui.beam_dialog import add_beam_section  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _frame(tmp_path):
    from puzzle.materials import add_library_material

    p = Project(name="frame")
    points = [[0, 0, 0], [0, 0, 1], [1, 0, 1]]
    geo = p.add(
        Geometry(
            name="Frame", history=[HistoryStep(kind="polyline", name="L", args={"points": points})]
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=100, unit="mm")))
    comp = p.add(
        Component(
            name="Frame",
            parts=[PartInstance(name="F", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="F")],
        )
    )
    p.add(Study(name="S", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")]))
    root = tmp_path / "frame"
    save_project(p, root)
    return root


def test_add_a_beam_section(app, tmp_path):
    from test_gui_names import bad, texts

    window = MainWindow(_frame(tmp_path))

    def tube(dialog):
        dialog.shape.setCurrentIndex(dialog.shape.findData("tube"))
        assert dialog.form.isRowVisible(dialog.sizes["thickness"])
        assert not dialog.form.isRowVisible(dialog.sizes["width"])
        dialog.sizes["diameter"].setText("0.06")
        dialog.sizes["thickness"].setText("0.004")
        dialog.axis.setCurrentIndex(dialog.axis.findText("Global Z"))
        return True

    window.run_dialog = tube
    try:
        add_beam_section(window, window.session.project.of_type(Component)[0])
        project = window.session.project
        (section,) = project.of_type(Component)[0].beam_sections
        assert section.selection == "F" and section.shape == "tube"
        assert section.diameter.to_si() == pytest.approx(0.06)
        assert section.thickness.to_si() == pytest.approx(0.004)
        assert section.width is None and tuple(section.orientation) == (0.0, 0.0, 1.0)
        assert "beam section on F: tube 0.06 × 0.004 m" in outline(project)
        (item,) = [i for i in window.tree._items() if i.data(0, ROLE).kind == "beam_section"]
        assert item.text(0) == "Beam section on F: tube 0.06 × 0.004 m"
        window.tree.setCurrentItem(item)
        app.processEvents()
        shown = texts(window)
        assert "Thickness" in shown and "Diameter" in shown
        assert "Width" not in shown and "Height" not in shown
        assert not [t for t in shown if bad(t)], shown
        study = project.of_type(Study)[0]
        assert any("does not write beams" in p for p in ElmerAdapter().check(project, study))
        assert not [p for p in CalculixAdapter().check(project, study) if "beams" in p]
    finally:
        window.close()


def test_polyline_is_a_ribbon_tool():
    from puzzle.gui.workbench import GROUPS

    assert "polyline" in dict(GROUPS)["Create"]


def test_add_a_shell_thickness(app, tmp_path):
    """A flat sheet gets a thickness from one form; the tree and outline name it, its
    menus offer it, and Elmer reports that it does not write shells."""
    from forms import fill

    from puzzle.gui.beam_dialog import add_shell_thickness

    p = Project(name="plate")
    square = [[0, 0, 0], [0.1, 0, 0], [0.1, 0.1, 0], [0, 0.1, 0]]
    geo = p.add(
        Geometry(
            name="Plate", history=[HistoryStep(kind="sheet", name="P", args={"points": square})]
        )
    )
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(
        Component(name="Plate", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id)
    )
    p.add(Study(name="S", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")]))
    root = tmp_path / "plate"
    save_project(p, root)
    window = MainWindow(root)
    try:
        fill(window, selection="P", thickness="0.004", offset=0.5)
        add_shell_thickness(window, window.session.project.of_type(Component)[0])
        project = window.session.project
        (shell,) = project.of_type(Component)[0].shell_sections
        assert shell.thickness.to_si() == pytest.approx(0.004) and shell.offset == 0.5
        assert "shell on P: 0.004 m thick, offset +0.5 t" in outline(project)
        assert any(i.text(0).startswith("Shell on P: 0.004 m thick") for i in window.tree._items())
        study = project.of_type(Study)[0]
        assert any("does not write shells" in m for m in ElmerAdapter().check(project, study))
        fill(window, thickness="-1")
        assert add_shell_thickness(window, project.of_type(Component)[0]) is None
        assert window.last_form.problem() == "Type a thickness greater than zero"
    finally:
        window.close()


def test_sheets_are_ribbon_tools():
    from puzzle.gui.workbench import GROUPS

    assert {"sheet", "curved_sheet"} <= set(dict(GROUPS)["Create"])


def test_a_table_section_oriented_toward_a_point(app, tmp_path):
    """ADR 0055: a W12X26 from the AISC table, its first axis toward a point selection;
    the dialog shows the section's sizes, Properties only the fields that apply."""
    from test_gui_names import bad, texts

    from puzzle.commands.model import Edit, EditOp
    from puzzle.project import load_project

    root = _frame(tmp_path)
    p = load_project(root)
    geo = p.of_type(Geometry)[0]
    from puzzle.model.schema import NamedSelection

    geo.selections.append(NamedSelection(name="Corner", entity="vertex",
                                         rule={"plane": {"axis": "x", "at": 1.0}}))  # fmt: skip
    save_project(p, root)
    window = MainWindow(root)

    def table(dialog):
        dialog.shape.setCurrentIndex(dialog.shape.findData("library"))
        assert dialog.form.isRowVisible(dialog.designation)
        assert not dialog.form.isRowVisible(dialog.sizes["width"])
        dialog.standard.setCurrentIndex(dialog.standard.findData("aisc"))
        dialog.family.setCurrentIndex(dialog.family.findData("W"))
        dialog.designation.setCurrentIndex(dialog.designation.findData("W12X26"))
        assert dialog.about.text().startswith("W12X26: 310 × 165 mm")
        dialog.orient.setCurrentIndex(dialog.orient.findData("toward"))
        assert dialog.form.isRowVisible(dialog.point) and not dialog.form.isRowVisible(dialog.axis)
        dialog.point.setCurrentIndex(dialog.point.findData("F/Corner"))
        dialog.roll.setText("90")
        return True

    window.run_dialog = table
    try:
        add_beam_section(window, window.session.project.of_type(Component)[0])
        project = window.session.project
        (section,) = project.of_type(Component)[0].beam_sections
        assert (section.shape, section.standard, section.designation) == (
            "library",
            "aisc",
            "W12X26",
        )
        assert (section.orient, section.toward, section.roll) == ("toward", "F/Corner", 90.0)
        assert "beam section on F: W12X26 (AISC)" in outline(project)
        (item,) = [i for i in window.tree._items() if i.data(0, ROLE).kind == "beam_section"]
        assert item.text(0) == "Beam section on F: W12X26 (AISC)"
        window.tree.setCurrentItem(item)
        app.processEvents()
        shown = texts(window)
        assert "Designation" in shown and "Toward" in shown and "Roll" in shown
        assert "Width" not in shown and "Orientation" not in shown and "Reference" not in shown
        assert not [t for t in shown if bad(t)], shown
        comp = project.of_type(Component)[0]
        window.submit(Edit(object=str(comp.id), ops=[EditOp(op="set",
                           path="beam_sections.0.designation", value="IPE 300")]))  # fmt: skip
        assert window.session.project.of_type(Component)[0].beam_sections[0].designation == (
            "W12X26"
        )  # refused: IPE 300 isn't an AISC section
    finally:
        window.close()


# --- ADR 0055: members drawn with their outline, and orienting by a pick ------------------


def _frame_and_roof(tmp_path, sections=()):
    """The L frame (a column up z, a beam along x) and a box beside it whose +y face
    (at y = 0.4) a pick can orient the frame by."""
    from puzzle.project import load_project

    root = _frame(tmp_path)
    p = load_project(root)
    roof = p.add(Geometry(name="Roof", history=[HistoryStep(kind="box", name="B", args={
        "dx": 0.6, "dy": 0.1, "dz": 0.6, "x0": 0.2, "y0": 0.3, "z0": 0.2})]))  # fmt: skip
    comp = p.of_type(Component)[0]
    comp.parts.append(PartInstance(name="R", geometry=roof.id))
    comp.beam_sections.extend(sections)
    save_project(p, root)
    return root


def _meshed(app, root):
    import time

    from puzzle.gui.tree import Node

    window = MainWindow(root)
    comp = window.session.project.of_type(Component)[0]
    window.tree.select(Node("object", str(comp.id), "", comp.name))
    window.mesh_selected()
    end = time.monotonic() + 300
    while window.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    window.tree.select(Node("object", str(comp.id), "", comp.name))
    window._selected()
    return window


def test_members_are_drawn_with_their_section(app, tmp_path):
    """A W12X26 on the frame: both members drawn as the section swept along them, on
    the axes the solver gets; the view toolbar turns them back into lines."""
    from puzzle.model.schema import BeamSection

    w12 = BeamSection(selection="F", shape="library", standard="aisc", designation="W12X26",
                      orientation=(0.0, 1.0, 0.0))  # fmt: skip
    window = _meshed(app, _frame_and_roof(tmp_path, [w12]))
    try:
        assert window.viewport.shown["beams"] == [("F", "W12X26"), ("F", "W12X26")]
        comp = window.session.project.of_type(Component)[0]
        members = window._beam_members(window.meshes[str(comp.id)])
        for m in members:
            assert abs(m.first @ (0, 1, 0)) == pytest.approx(1.0)
        bar = window.viewport.view_bar.actions_by_name["beams"]
        bar.trigger()
        assert window.viewport.shown["beams"] == []
        bar.trigger()
        assert len(window.viewport.shown["beams"]) == 2
        # Over a result too, deformed and coloured by the value.
        import numpy as np

        from puzzle.results.model import Field, Results, StepResult

        mesh = window.meshes[str(comp.id)]
        u = Field("displacement", "node", ["x", "y", "z"], mesh.node_ids,
                  np.outer(mesh.coords[:, 2], [1e-3, 0, 0]))  # fmt: skip
        results = Results("code_aster", [StepResult(1, "stationary", 1.0,
                                                    fields={"displacement": u})])  # fmt: skip
        window._result_component = str(comp.id)
        shown = window.viewport.show_result(mesh, results, "displacement")
        assert shown["beams"] == [("F", "W12X26"), ("F", "W12X26")]
    finally:
        window.close()


def test_orient_by_a_face_picked_in_the_view(app, tmp_path):
    """Pick a face in the view from the form: it closes, the double-click on the roof's
    +y face comes back as a new selection chosen in the form, and OK makes the
    selection and the section in one command. From a section's menu a pick of the same
    face reuses that selection. The view then draws the members with first axis y."""
    from puzzle.gui.beam_dialog import orient_by_pick

    window = _meshed(app, _frame_and_roof(tmp_path))
    comp = window.session.project.of_type(Component)[0]
    seen = []

    def form(dialog):
        if not seen:  # the first time: a table section, then Pick a face
            dialog.shape.setCurrentIndex(dialog.shape.findData("library"))
            dialog.standard.setCurrentIndex(dialog.standard.findData("aisc"))
            dialog.family.setCurrentIndex(dialog.family.findData("W"))
            dialog.designation.setCurrentIndex(dialog.designation.findData("W12X26"))
            assert dialog.outline.regions  # the outline drawn in the form
            dialog.orient.setCurrentIndex(dialog.orient.findData("normal"))
            assert dialog.form.isRowVisible(dialog.pick_face)
            assert not dialog.form.isRowVisible(dialog.pick_edge)
            seen.append(dialog.face.currentData())
            dialog.pick_face.click()
            return False
        seen.append((dialog.face.currentData(), dialog.face.currentText()))
        assert dialog.orient.currentData() == "normal"
        return True

    window.run_dialog = form
    try:
        before = len(window.session.history(limit=100))
        assert add_beam_section(window, comp) is None  # waiting for the pick
        assert window.viewport.shown["capturing"]
        window.viewport._captured((0.5, 0.4, 0.5))  # a double-click on the +y face
        name = "R/Beam orientation face"
        assert seen[1] == (name, f"{name} (picked)")
        project = window.session.project
        roof = next(g for g in project.of_type(Geometry) if g.name == "Roof")
        (sel,) = roof.selections
        assert (sel.name, sel.entity, list(sel.members)) == ("Beam orientation face", "face",
                                                             ["B/+y"])  # fmt: skip
        (section,) = project.of_type(Component)[0].beam_sections
        assert (section.orient, section.reference, section.designation) == (
            "normal", name, "W12X26")  # fmt: skip
        assert len(window.session.history(limit=100)) == before + 1  # one command

        # From the section's menu, the same face: its selection is used again.
        orient_by_pick(window, project.of_type(Component)[0], 0, "face")
        window.viewport._captured((0.4, 0.4, 0.3))
        project = window.session.project
        assert len(next(g for g in project.of_type(Geometry) if g.name == "Roof").selections) == 1
        assert project.of_type(Component)[0].beam_sections[0].reference == name

        # Meshed again, the members follow the face's normal.
        window.mesh_selected()
        import time

        end = time.monotonic() + 300
        while window.tasks:
            app.processEvents()
            assert time.monotonic() < end
            time.sleep(0.02)
        mesh = window.meshes[str(comp.id)]
        members = window._beam_members(mesh)
        assert len(members) == 2
        for m in members:
            assert abs(m.first @ (0, 1, 0)) == pytest.approx(1.0)
    finally:
        window.close()
