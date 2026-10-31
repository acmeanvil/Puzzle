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
