"""Tidying the model tree: renaming anything under a heading, and groups (folders).

A study, a result tool and a stored run are renamed (a run's name lives in the
project's TreeLabels, the run itself being a cache); a name another study has is
refused. Groups hold studies and runs, nest, empty out and go, and every change undoes.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui import organise  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Feature,
    Physics,
    Quantity,
    SolveStep,
    Study,
    TreeGroup,
    TreeLabels,
)
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _item(window, kind, label=None):
    for item in window.tree._items():
        node = item.data(0, ROLE)
        if node.kind == kind and (label is None or node.label == label):
            return item
    return None


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_rename_and_group(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    load = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    held = Feature(type="fixed", selection="Bar/Root")
    physics = [Physics(type="solid_mechanics", features=[held, load])]
    project, comp = bar_project(write_bar_step(root / "bar.step"), physics, 10.0)
    steps = [SolveStep(type="stationary")]
    bend = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    project.add(Study(name="Bend 2", component=comp.id, solver="calculix", steps=steps))
    project.add(Study(name="Modes", component=comp.id, solver="calculix",
                      steps=[SolveStep(type="modal")]))  # fmt: skip
    save_project(project, root)
    run_study(project, bend.id, root, tmp_path / "w", store=ResultStore(root / "results"))
    w = MainWindow(root)
    try:
        # A study, by its name (the delegate edits the name, not the item's text).
        node = _item(w, "object", "Bend").data(0, ROLE)
        organise.rename(w, node, "Tip load")
        assert w.session.project.objects[bend.id].name == "Tip load"
        assert _item(w, "object", "Tip load") is not None
        assert _item(w, "run").text(0).startswith("Tip load · ")  # the study's name now
        organise.rename(w, _item(w, "object", "Tip load").data(0, ROLE), "Bend 2")
        assert w.session.project.objects[bend.id].name == "Tip load"  # taken: refused
        assert "already called" in w.statusBar().currentMessage()
        # A stored run: a name kept in the project.
        run_id = _item(w, "run").data(0, ROLE).object  # the tree is rebuilt after each edit
        organise.rename(w, _item(w, "run").data(0, ROLE), "First try")
        (named,) = w.session.project.of_type(TreeLabels)
        assert named.runs == {run_id: "First try"}
        assert _item(w, "run").text(0) == "First try"

        # Groups: a new one with the selected studies, under Studies.
        studies = [_item(w, "object", n) for n in ("Tip load", "Bend 2")]
        for i in studies:
            i.setSelected(True)
        assert w.tree.section_of(studies[0]) == "Studies"
        keys = organise.selected_keys(w, "Studies")
        assert len(keys) == 2
        fill(w, name="Static")
        organise.new_group(w, "Studies", keys)
        (static,) = w.session.project.of_type(TreeGroup)
        folder = _item(w, "group", "Static")
        assert folder.parent().data(0, ROLE).label == "Studies"
        assert {folder.child(i).text(0) for i in range(folder.childCount())} == {
            "Tip load",
            "Bend 2",
        }
        # A group inside a group, and the run grouped under Results.
        organise.new_group(w, "Studies", [str(static.id)], name="All")
        outer = _item(w, "group", "All")
        assert outer.child(0).data(0, ROLE).label == "Static"
        organise.new_group(w, "Results", [f"run:{run_id}"], name="Early")
        assert _item(w, "run").parent().data(0, ROLE).label == "Early"
        # Out of a group, then ungroup: what it held goes back where it was.
        tip = organise.member_key(_item(w, "object", "Tip load").data(0, ROLE))
        organise.move(w, [tip], None, "Studies")
        assert _item(w, "object", "Tip load").parent().data(0, ROLE).kind == "section"
        organise.ungroup(w, str(static.id))
        assert _item(w, "group", "Static") is None
        assert _item(w, "object", "Bend 2").parent().data(0, ROLE).label == "All"
        # Undo brings the group back.
        w.undo()
        assert _item(w, "group", "Static") is not None
        # Properties of a group: names, never ids.
        w.tree.setCurrentItem(_item(w, "group", "Static"))
        app.processEvents()
        texts = {w.properties.topLevelItem(i).text(0): w.properties.topLevelItem(i).text(1)
                 for i in range(w.properties.topLevelItemCount())}  # fmt: skip
        assert texts["Holds"] == "Bend 2" and texts["Under"] == "Studies"
    finally:
        w.close()


def test_drop_moves_into_a_group(app, tmp_path):
    from puzzle.model.schema import Project

    root = tmp_path / "p"
    project = Project(name="p")
    save_project(project, root)
    w = MainWindow(root)
    try:
        from puzzle.commands.model import Create

        w.submit(Create(kind="amplitude", data={"name": "Ramp", "points": [[0, 0], [1, 1]]}))
        w.submit(Create(kind="amplitude", data={"name": "Pulse", "points": [[0, 0], [1, 1]]}))
        organise.new_group(w, "Amplitudes", [], name="Loads")
        got = []
        w.tree.moved.connect(lambda keys, group, section: got.append((keys, group, section)))
        ramp = _item(w, "object", "Ramp")
        assert w.tree.groupable(ramp)
        ramp.setSelected(True)
        # What dropping Ramp on the folder does (Qt's drag itself needs a real mouse).
        folder = _item(w, "group", "Loads")
        organise.move(w, [organise.member_key(ramp.data(0, ROLE))],
                      folder.data(0, ROLE).object, "Amplitudes")  # fmt: skip
        assert _item(w, "object", "Ramp").parent().data(0, ROLE).label == "Loads"
        assert _item(w, "object", "Pulse").parent().data(0, ROLE).kind == "section"
    finally:
        w.close()
