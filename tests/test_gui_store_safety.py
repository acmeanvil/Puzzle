"""The GUI's side of ADR 0090 package 2: New project never replaces a project
(GUI-19), and a group can't go inside a group it holds, while a project whose groups
already hold each other still opens (GUI-21)."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui import organise  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow, _holds_project  # noqa: E402
from puzzle.model.schema import Project, TreeGroup  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.project.store import load_project  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _copy(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    return dest


def _items(window, kind):
    return [i for i in window.tree._items() if i.data(0, ROLE).kind == kind]


def test_new_into_a_project_folder_is_refused(app, tmp_path):
    root = _copy(tmp_path)
    files = sorted(p.read_bytes() for p in root.glob("*/*.toml"))
    assert "already holds the project 'Cantilever'" in _holds_project(root)
    assert _holds_project(tmp_path / "empty") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    w = MainWindow()  # the start page: no project open
    try:
        w.new_project(root, "Fresh")
        assert sorted(p.read_bytes() for p in root.glob("*/*.toml")) == files
        assert "already holds" in w.statusBar().currentMessage()
        w.new_project(empty, "Fresh")  # an empty folder is fine
        assert load_project(empty).name == "Fresh"
    finally:
        w.close()


def test_moving_a_group_into_one_it_holds_is_refused(app, tmp_path):
    root = tmp_path / "p"
    save_project(Project(name="p"), root)
    w = MainWindow(root)
    try:
        organise.new_group(w, "Amplitudes", [], name="P")
        p = next(g for g in w.session.project.of_type(TreeGroup) if g.name == "P")
        organise.new_group(w, "Amplitudes", [str(p.id)], name="C")
        c = next(g for g in w.session.project.of_type(TreeGroup) if g.name == "C")
        assert organise.move(w, [str(c.id)], str(p.id), "Amplitudes") is None
        assert "can't go inside" in w.statusBar().currentMessage()
        groups = {g.name: g for g in load_project(root).of_type(TreeGroup)}
        assert groups["P"].members == [] and groups["C"].members == [str(p.id)]
    finally:
        w.close()


def test_a_project_with_a_group_loop_opens(app, tmp_path):
    root = tmp_path / "p"
    from puzzle.model.schema import Amplitude

    project = Project(name="p")
    ramp = project.add(Amplitude(name="Ramp", points=[[0, 0], [1, 1]]))
    a, b = uuid4(), uuid4()
    project.add(TreeGroup(id=a, name="A", section="Amplitudes", members=[str(b), str(ramp.id)]))
    project.add(TreeGroup(id=b, name="B", section="Amplitudes", members=[str(a)]))
    save_project(project, root)
    w = MainWindow(root)  # hung in tree._group before the fix
    try:
        names = sorted(i.data(0, ROLE).label for i in _items(w, "group"))
        assert names == ["A", "B"]
    finally:
        w.close()
