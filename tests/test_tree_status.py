"""ADR 0033: every object's state, from one function, shown in the tree and outline.

A study whose stored run has the current run key is up to date; changing the load
makes it out of date; a study never run is "not run yet". A material assigned nowhere
is not used. A problem marks its object as missing input, with the problem as the
reason; sections show their worst child. The GUI's tree, Problems list and results bar
follow (run for real with CalculiX).
"""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from solvers import available  # noqa: E402

from puzzle.commands import Session, outline  # noqa: E402
from puzzle.materials import add_library_material  # noqa: E402
from puzzle.model.schema import Component, Material, Quantity, Study  # noqa: E402
from puzzle.project import load_project  # noqa: E402
from puzzle.project.graph import run_hashes  # noqa: E402
from puzzle.project.status import status, worst  # noqa: E402
from puzzle.results.store import RunRecord  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def copy(tmp_path) -> Path:
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    return root


def record(study, key) -> RunRecord:
    now = datetime(2026, 10, 23)
    return RunRecord(
        "r1", key, str(study.id), study.name, study.solver, "native", "m", "completed",
        now, 1.0, 1, now,
    )  # fmt: skip


def tip(p) -> Study:
    return next(s for s in p.of_type(Study) if s.name == "Tip load")


def test_states_follow_the_model(tmp_path):
    p = load_project(copy(tmp_path))
    study = tip(p)
    key = run_hashes(p)[study.id][study.solver][0]
    runs = [record(study, key)]
    st = status(p, runs)
    assert st[str(study.id)].state == "up_to_date"
    others = [s for s in p.of_type(Study) if s.id != study.id]
    assert all(st[str(s.id)].state == "not_run" for s in others)
    assert st[str(p.of_type(Material)[0].id)].state == "complete"

    comp = p.of_type(Component)[0]
    comp.physics[0].features[1].values["y"] = Quantity(value=-300, unit="N")
    st = status(p, runs)
    assert st[str(study.id)].words == "Out of date: the model changed since the last run"
    assert status(p, runs, running={str(study.id)})[str(study.id)].state == "running"
    assert status(p, runs, failed={str(study.id)})[str(study.id)].state == "failed"

    spare = add_library_material(p, "copper")
    assert status(p, runs)[str(spare.id)].words == "Not used: not assigned to any part"
    comp.materials[0].selection = "Bar/Nowhere"  # a problem: an unknown selection
    st = status(p, runs)
    assert st[str(comp.id)].state == "missing"
    assert "unknown selection 'Bar/Nowhere'" in st[str(comp.id)].reason
    assert worst(st.values()) == "missing"


def test_outline_says_each_study_state(tmp_path):
    root = copy(tmp_path)
    p = Session(root).project
    study = tip(p)
    key = run_hashes(p)[study.id][study.solver][0]
    text = outline(p, runs=[record(study, key)])
    assert "Study Tip load: calculix on Cantilever  [up to date]" in text
    assert "Study Modes: calculix on Cantilever  [not run yet: run it to get results]" in text
    assert "[" not in outline(p).split("Study Tip load")[1].splitlines()[0]  # no runs given


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_the_gui_tree_problems_and_results_bar(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.tree import ROLE, STATE
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])
    root = copy(tmp_path)
    p = load_project(root)
    tip(p).solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    try:

        def item(kind, label):
            return next(
                i
                for i in w.tree._items()
                if i.data(0, ROLE).kind == kind and i.data(0, ROLE).label == label
            )

        assert item("object", "Tip load").data(0, STATE) == "not_run"
        w.tree.setCurrentItem(item("object", "Tip load"))
        w.run_selected()
        assert item("object", "Tip load").data(0, STATE) == "running"
        while w.tasks:
            app.processEvents()
            time.sleep(0.02)
        assert item("object", "Tip load").data(0, STATE) == "up_to_date"
        assert not w.results_bar.stale.isVisibleTo(w)

        comp = w.session.project.of_type(Component)[0]
        force = {"value": -300, "unit": "N"}
        op = EditOp(op="set", path="physics.0.features.1.values.y", value=force)
        w.submit(Edit(object=str(comp.id), ops=[op]))
        assert item("object", "Tip load").data(0, STATE) == "out_of_date"
        run = next(i for i in w.tree._items() if i.data(0, ROLE).kind == "run")
        w.tree.setCurrentItem(run)
        assert w.results_bar.stale.text() == "Out of date: the model changed since this run"
        assert w.results_bar.rerun.isVisibleTo(w)

        op = EditOp(op="set", path="materials.0.selection", value="Bar/Nowhere")
        w.submit(Edit(object=str(comp.id), ops=[op]))
        assert item("object", "Cantilever").data(0, STATE) == "missing"
        assert item("section", "Components").data(0, STATE) == "missing"
        assert "unknown selection" in item("object", "Cantilever").toolTip(0)
        problem = w.problems.item(0)
        w.problems.itemDoubleClicked.emit(problem)
        assert w.tree.current().label == "Cantilever"
        assert problem.isSelected()  # the selected object's problems are marked
    finally:
        w.close()
