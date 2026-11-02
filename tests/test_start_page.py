"""The start page (shown before a project is open), recent projects, the app's art and
its macOS name."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui import start_page  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Project  # noqa: E402
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_recent():
    """The user folder is shared by the test session: other GUI tests open projects."""
    start_page._recent_path().unlink(missing_ok=True)


def _project(root, name="Bar"):
    save_project(Project(name=name), root)
    return root


def test_recent_projects_newest_first_without_repeats_or_missing_folders(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    start_page.remember(a)
    start_page.remember(b)
    start_page.remember(a)
    assert start_page.recent() == [a.resolve(), b.resolve()]
    b.rmdir()
    assert start_page.recent() == [a.resolve()]


def test_recent_list_keeps_the_latest_ten(tmp_path):
    for k in range(start_page.KEEP + 3):
        (tmp_path / str(k)).mkdir()
        start_page.remember(tmp_path / str(k))
    got = start_page.recent()
    assert len(got) == start_page.KEEP and got[0].name == str(start_page.KEEP + 2)


def test_window_without_a_project_shows_the_start_page(app):
    w = MainWindow(None)
    assert w.centre.currentWidget() is w.start_page
    assert not w.start_page.recent.isVisibleTo(w.start_page)  # nothing opened yet
    w.close()


def test_opening_a_project_leaves_the_start_page_and_lists_it(app, tmp_path):
    root = _project(tmp_path / "bar")
    w = MainWindow(None)
    w.open(root)
    assert w.centre.currentIndex() == 0  # the 3D view
    assert start_page.recent()[0] == root.resolve()
    assert w.start_page.recent.count() == 1
    assert "bar" in w.start_page.recent.item(0).text()
    w.close()


def test_a_recent_project_opens_from_the_list(app, tmp_path):
    root = _project(tmp_path / "bar", "Bar")
    start_page.remember(root)
    w = MainWindow(None)
    w.start_page._open_recent(w.start_page.recent.item(0))
    assert w.session is not None and w.session.project.name == "Bar"
    w.close()


def test_the_art_is_shipped_and_loads(app):
    for name in ("icon.svg", "icon.png", "start.svg"):
        assert (start_page.ART / name).exists(), name
    assert not start_page.app_icon().isNull()
    assert start_page.Picture().renderer.isValid()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS names apps from the bundle")
def test_the_macos_menu_bar_says_puzzle():
    from puzzle.gui.app import mac_app_name

    assert mac_app_name("Puzzle")
    objc = pytest.importorskip("Foundation")
    bundle = objc.NSBundle.mainBundle()
    info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
    assert info["CFBundleName"] == "Puzzle"
