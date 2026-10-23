"""A study's material table and Compare in the GUI (ADR 0018).

The cantilever's Tip load gets steel and aluminium through the study's Materials…
dialog (one command, undoable); running it makes one run per material, listed under
one heading, and Compare shows them side by side on one colour scale, as a ratio to
the steel run (deflection scales as 1/E with the same Poisson's ratio) and in a table.
"""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import sweep  # noqa: E402
from puzzle.gui import menus  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Material, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

pytestmark = pytest.mark.usefixtures("engineering_units")

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    next(s for s in p.of_type(Study) if s.name == "Tip load").solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    yield w
    w.close()


def wait(app, condition, timeout=600.0):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("the GUI did not finish in time")
        time.sleep(0.02)


def tip_load(window) -> Study:
    return next(s for s in window.session.project.of_type(Study) if s.name == "Tip load")


def nodes(window, kind):
    return [i for i in window.tree._items() if i.data(0, ROLE).kind == kind]


def tick(names):
    """A run_dialog stand-in: tick these materials at every location, then OK."""

    def run(dialog):
        for lst in dialog.lists.values():
            for i in range(lst.count()):
                item = lst.item(i)
                on = any(item.text().startswith(n) for n in names)
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        run.count = dialog.count.text()
        return True

    return run


def test_the_material_table_writes_the_sweep_in_one_command(window):
    window.add_material("aluminium_6061_t6")
    project = window.session.project
    steel, al = (m for m in project.of_type(Material))
    location = project.of_type(Component)[0].materials[0].selection
    window.run_dialog = tick([steel.name, al.name])
    menus.study_materials(window, tip_load(window))
    assert window.run_dialog.count == "The study makes 2 runs."
    study = tip_load(window)
    assert sweep.material_table(study) == {location: [str(steel.id), str(al.id)]}
    assert any(i.text(0) == "Runs 2 materials" for i in nodes(window, "step"))
    assert window.session.history(limit=1)[0]["command"]["command"] == "edit"
    # Back to the assigned material only: the sweep step goes away.
    window.run_dialog = tick([steel.name])
    menus.study_materials(window, tip_load(window))
    assert tip_load(window).sweeps() == []
    window.undo()
    assert len(tip_load(window).sweeps()) == 1


def test_with_material_table_keeps_parameter_sweeps():
    """Plain parameter sweeps stay where they are; the choices go in front."""
    root = EXAMPLE
    p = load_project(root)
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    comp = p.get(study.component, Component)
    location, steel = comp.materials[0].selection, str(comp.materials[0].material)
    other = "00000000-0000-0000-0000-000000000001"
    steps = sweep.with_material_table(p, study, {location: [steel, other]})
    assert steps[0]["parameters"][0]["materials"] == [steel, other]
    assert sweep.with_material_table(p, study, {location: [steel]}) == [
        s.model_dump(mode="json") for s in study.steps
    ]


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_run_each_material_then_compare(app, window):
    window.add_material("aluminium_6061_t6")
    project = window.session.project
    steel, al = (m for m in project.of_type(Material))
    # The same Poisson's ratio, so deflection goes exactly as 1/E.
    from puzzle.commands.model import Edit, EditOp

    nu = steel.properties["poissons_ratio"].model_dump(mode="json")
    window.submit(
        Edit(object=str(al.id), ops=[EditOp(op="set", path="properties.poissons_ratio", value=nu)])
    )
    window.run_dialog = tick([steel.name, al.name])
    menus.study_materials(window, tip_load(window))
    for item in window.tree._items():
        if item.data(0, ROLE).kind == "object" and item.data(0, ROLE).label == "Tip load":
            window.tree.setCurrentItem(item)
    window.run_selected()
    wait(app, lambda: not window.tasks)

    (head,) = nodes(window, "sweep")
    children = [head.child(i).text(0) for i in range(head.childCount())]
    assert children[0] == "Compare…"
    assert any(c.startswith("Structural steel · CalculiX") for c in children[1:]), children
    assert any(c.startswith(al.name) for c in children[1:]), children

    compare = window.open_compare(head.data(0, ROLE).object)
    # In the study's own order (its sweep's), whatever order the runs finished in.
    (ids,) = sweep.material_table(tip_load(window)).values()
    names = {str(m.id): m.name for m in window.session.project.of_type(Material)}
    assert [c.label for c in compare.cases] == [names[i] for i in ids]
    i_steel = [c.label for c in compare.cases].index(steel.name)
    i_al = 1 - i_steel
    compare.result.setCurrentIndex(compare.result.findData("displacement:total"))
    a, b = (v.shown for v in compare.views)
    assert a["clim"] == b["clim"] and a["clim"][1] == pytest.approx(max(a["max"], b["max"]))

    # Aluminium's deflection as a ratio to steel's: E_steel / E_al where it moves.
    compare.baseline.setCurrentIndex(i_steel)
    compare.other.setCurrentIndex(i_al)
    compare.mode.setCurrentIndex(1)
    ratio = steel.properties["youngs_modulus"].to_si() / al.properties["youngs_modulus"].to_si()
    assert compare.diff_view.shown["max"] == pytest.approx(ratio, rel=1e-3)

    table = compare.table
    assert table.rowCount() == 2 and table.horizontalHeaderItem(2).text() == "Maximum (mm)"
    top = [float(table.item(r, 2).text()) for r in range(2)]
    assert top[i_al] / top[i_steel] == pytest.approx(ratio, rel=1e-3)
    assert table.item(0, 3).text().endswith(" mm")
    compare.close()
