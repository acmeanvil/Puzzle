"""The Contact tool (ADR 0056) in the GUI: Setup lists each contact with what the
solver is given and edits several rows as one undo; Initial state checks the mesh and
colours the source faces; its warnings join the Problems list."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_contact_check import _bar  # noqa: E402

from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model import Contact, Study  # noqa: E402
from puzzle.model.schema import Component, SolveStep  # noqa: E402
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, condition, timeout=120.0):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("the GUI did not finish in time")
        time.sleep(0.02)


def test_the_contact_tool(app, tmp_path):
    from puzzle.gui.contact_tool import COLUMNS

    p, comp, mesh = _bar(tmp_path, offset=(0, 3e-3, 0))
    comp.contacts.append(Contact(name="Side tie", type="bonded", meshing="nonconformal",
                                 source="Right/Side", target="Left/Side"))  # fmt: skip
    p.add(Study(name="Load", component=comp.id, solver="calculix",
                steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, tmp_path)
    window = MainWindow(tmp_path)
    window.open_contacts()
    setup = window.contacts.setup
    assert setup.table.rowCount() == 2 and setup.solver.currentData() == "calculix"
    col = {c.title: i for i, c in enumerate(COLUMNS)}
    rows = {setup.table.item(r, 0).text(): r for r in range(2)}
    given = setup.table.item(rows["Joint"], col["Solver is given"]).text()
    assert given.startswith("*CONTACT PAIR") and "(h from the mesh)" in given
    assert setup.table.item(rows["Side tie"], col["Solver is given"]).text().startswith("*TIE")
    assert setup.table.item(rows["Joint"], col["Type"]).text() == "Frictionless"

    # Both rows made frictional at once: each gets a coefficient, one command.
    setup.table.selectAll()
    setup.edit(rows["Joint"], COLUMNS[col["Type"]], "frictional")
    comp = window.session.project.of_type(Component)[0]
    assert [(c.type, c.friction, c.meshing) for c in comp.contacts] == [
        ("frictional", 0.2, None), ("frictional", 0.2, "nonconformal")]  # fmt: skip
    assert window.session.history(limit=1)[0]["command"]["command"] == "edit"
    # An offset interface gets an offset to edit; a press fit's interference.
    setup.table.clearSelection()
    setup.edit(rows["Joint"], COLUMNS[col["Interface"]], "offset")
    setup.edit(rows["Joint"], COLUMNS[col["Offset"]], "-0.02 mm")
    joint = window.session.project.of_type(Component)[0].contacts[0]
    assert (joint.interface, joint.offset.value, joint.offset.unit) == ("offset", -0.02, "mm")
    assert setup.table.item(rows["Joint"], col["Interface"]).text() == (
        "Offset (clearance or interference)")  # fmt: skip
    assert "into the target (interference)" in setup.table.item(
        rows["Joint"], col["Solver is given"]).text()  # fmt: skip
    setup.solver.setCurrentIndex(setup.solver.findData("code_aster"))
    assert "Coulomb μ = 0.2" in setup.table.item(0, col["Solver is given"]).text()

    # Initial state on the mesh: the overhang is found, drawn, and listed as a problem.
    window.meshes[str(comp.id)] = mesh
    window.open_contacts("initial")
    initial = window.contacts.initial
    initial.check()
    wait(app, lambda: str(comp.id) in initial.found and not window.tasks)
    statuses = {initial.table.item(r, 0).text(): initial.table.item(r, 2).text()
                for r in range(initial.table.rowCount())}  # fmt: skip
    assert statuses["Joint"] == "Overhanging"
    assert window.viewport.shown["contact_state"]["Joint"] == "overhanging"
    texts = [window.problems.item(i).text() for i in range(window.problems.count())]
    assert any("contact warning" in t and "swap the sides" in t for t in texts)
