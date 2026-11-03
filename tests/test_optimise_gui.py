"""Optimisations in the window (ADR 0063), on the beam benchmark (CalculiX): made in
one form, run in the background, the points in the Optimisation panel with the best
in bold, the Advisor suggesting it, the schematic node right of its study; Apply best
saves a version first and is one undo.
"""

from __future__ import annotations

import os
import time
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from forms import fill  # noqa: E402
from optimise_beam import beam_project  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import optimisation_panel as panel  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Optimisation, ParameterSet, Study  # noqa: E402
from puzzle.project import save_project  # noqa: E402

pytestmark = [
    pytest.mark.usefixtures("engineering_units"),
    pytest.mark.skipif(not available("calculix"), reason="calculix not available"),
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, w, timeout=600.0):
    end = time.monotonic() + timeout
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    app.processEvents()


def test_an_optimisation_from_the_form_to_apply_best(app, tmp_path):
    p, opt = beam_project()
    del p.objects[opt.id]  # made here in the form instead
    root = tmp_path / "beam"
    save_project(p, root)
    w = MainWindow(root)
    try:
        study = next(s for s in w.session.project.of_type(Study))
        fill(w, study=str(study.id), variables="t: 6 mm .. 9 mm",
             objective=next(str(o.id) for o in w.session.project.objects.values()
                            if o.name == "Thickness"),
             constraints="Tip deflection <= 2 mm", method="full_factorial",
             name="Thinnest")  # fmt: skip
        oid = panel.new_optimisation(w)
        opt = w.session.project.objects[UUID(oid)]
        assert isinstance(opt, Optimisation) and opt.levels == 3  # 6, 7.5, 9 mm
        assert opt.constraints[0].upper.value == 2 and not w.optimisation_dock.isHidden()
        panel.run(w, oid)
        wait(app, w)
        rows = w.optimisation.table.rowCount()
        assert rows == 3
        heads = [w.optimisation.table.horizontalHeaderItem(c).text()
                 for c in range(w.optimisation.table.columnCount())]  # fmt: skip
        assert heads[:4] == ["#", "t", "Thickness", "Tip deflection"]
        feasible = [w.optimisation.table.item(r, 4).text() for r in range(rows)]
        assert feasible[0].startswith("Tip deflection above 2 mm")  # 6 mm bends too far
        assert feasible[1:] == ["Yes", "Yes"]
        assert w.optimisation.table.item(1, 1).font().bold()  # 7.5 mm, the best
        assert "Best: t = 7.5 mm: Thickness 7.5 mm" in w.optimisation.summary.text()
        from puzzle.advisor import advise

        said = [a for a in advise(w.session.project, w._store().runs(), root=root)
                if a.step == "optimise"]  # fmt: skip
        assert said and said[0].action == f"apply_best:{oid}"
        w.schematic.refresh()
        node = w.schematic.items[oid].node
        assert node.badge == "3 of 3 points" and node.value.startswith("best 7.5 mm")
        assert any(x.wire.kind == "drives" and x.a.node.object == str(study.id)
                   for x in w.schematic.wires)  # fmt: skip
        versions = len(list((root / "versions").glob("*.json")))
        panel.apply_best(w, oid)
        t = next(ps for ps in w.session.project.of_type(ParameterSet)).values["t"]
        assert (t.value, t.unit) == (7.5, "mm")
        assert len(list((root / "versions").glob("*.json"))) > versions
        assert "The model is at the best point" in w.optimisation.summary.text()
        w.session.undo()
        t = next(ps for ps in w.session.project.of_type(ParameterSet)).values["t"]
        assert t.value == 6
    finally:
        wait(app, w)
        w.close()


def test_the_form_refuses_what_cant_run(app, tmp_path):
    p, opt = beam_project()
    root = tmp_path / "beam"
    save_project(p, root)
    w = MainWindow(root)
    try:
        fill(w, variables="nope: 1 .. 2")
        assert panel.new_optimisation(w) is None
        assert w.last_form.problem().startswith("no parameter 'nope'")
        fill(w, variables="t: 4 mm .. 8 mm", constraints="Tip <= 2 mm")
        assert panel.new_optimisation(w) is None
        assert w.last_form.problem() == "no operator or result tool 'Tip'"
    finally:
        wait(app, w)
        w.close()
