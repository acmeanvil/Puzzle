"""Help that opens its tools, and F1 everywhere ADR 0083 decision 2 says (ADR 0090
package 16, ADR-E-1, ADR-E-2): every menu location a page names gets an "Open" link in
the Help window that runs that menu item; every `puzzle:` link in a page resolves; F1
on an open menu's item opens the guide that names it; F1 with one node selected on the
schematic opens that node's entry, not the panel's page."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle import help as manual  # noqa: E402
from puzzle.help import pictures  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
LINK = re.compile(r"\]\(puzzle:([^)\s]+)\)")


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from conftest import delete_windows

    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path_factory.mktemp("links") / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    w = MainWindow(root)
    yield w
    end = time.monotonic() + 30
    while w.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    delete_windows(app)


def test_every_named_menu_location_links_to_its_item(window):
    """Each menu location the pages name (the pictures' index) is a link in the Help
    window, and each link finds its action on the menu bar."""
    from puzzle.gui import main_menu
    from puzzle.gui.help_viewer import rendered

    page = manual.pages()["guides/studies-and-solvers"]
    markdown, _images = rendered(page.body)
    named = {m for _, m in pictures.menu_mentions(page.body)} & set(pictures.index()["menus"])
    assert named, "the guide names menu locations"
    for menu in named:
        assert f"]({pictures.link(menu)})" in markdown, menu
    bar = set(main_menu.paths(window))
    links = {m: pictures.link(m).removeprefix("puzzle:") for m in pictures.index()["menus"]}
    missing = [m for m, target in links.items()
               if m in bar and main_menu.action_for(window, target) is None]  # fmt: skip
    assert missing == []


def test_puzzle_links_written_in_pages_resolve(window):
    from puzzle.gui import main_menu

    bad = []
    for page in manual.pages().values():
        for target in LINK.findall(page.body):
            if main_menu.action_for(window, target) is None:
                bad.append(f"{page.name}: puzzle:{target}")
    assert bad == []


def test_following_a_link_runs_the_menu_item(window):
    from puzzle.gui.help_viewer import open_tool

    window.advisor_dock.hide()
    assert open_tool(window, "menu/window-advisor")
    assert not window.advisor_dock.isHidden()
    assert not open_tool(window, "menu/no-such-place")


def test_f1_on_an_open_menu_opens_the_guide_that_names_it(window):
    from puzzle.gui import main_menu

    chain = main_menu.paths(window)["Model → Studies → New analysis…"]
    menu, action = chain[-1]
    menu.setActiveAction(action)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier)
    assert window._menu_help.eventFilter(menu, press)
    viewer = window._help_window
    name, _heading = viewer.current
    place = manual.menu_place("Model → Studies → New analysis…")
    assert place is not None and name == place[0].name
    assert "Model → Studies → New analysis…" in place[0].body.replace("\n", " ")


def test_f1_on_a_schematic_node_opens_its_entry(window):
    from puzzle.gui.help_viewer import help_key

    panel = window.schematic
    panel.refresh() if hasattr(panel, "refresh") else None
    app = QApplication.instance()
    app.processEvents()
    items = [i for i in panel.scene.items() if hasattr(i, "node")]
    study = next(i for i in items if i.node.kind == "study")
    for i in items:
        i.setSelected(False)
    study.setSelected(True)
    assert help_key(window, panel.canvas) == "kind:study"
    study.setSelected(False)
    assert help_key(window, panel.canvas) == "panel:schematic"
