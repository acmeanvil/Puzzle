"""The menu bar reaches everything the ribbons and the tree's menus do.

Project, Materials, Geometry, Mesh, Model, Results, Window and Help, in that order.
Every ribbon button (Geometry, Mesh, Results) and every item of the tree's section
menus has a menu bar action of the same name; shortcuts don't clash. Actions that need
a part or study take the selected one or ask in one form: a geometry tool from the menu
opens the Geometry tab with the tool's options.
"""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from puzzle.gui import main_menu, menus  # noqa: E402
from puzzle.gui.tree import Node  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
# Tree items whose menu bar twin reads a little differently.
RENAMED = {"Add from library…": "Add material…", "New Cartesian system…": "Cartesian…",
           "New cylindrical system…": "Cylindrical…", "New spherical system…": "Spherical…",
           "Reaction…": "Reaction…"}  # fmt: skip


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "c"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    yield w
    w.close()


def texts(window) -> set[str]:
    out = set()

    def walk(menu: QMenu):
        for a in menu.actions():
            if a.menu() is not None:
                a.menu().aboutToShow.emit()  # dynamic submenus fill when opened
                walk(a.menu())
            elif a.text():
                out.add(a.text())

    for a in window.menuBar().actions():
        a.menu().aboutToShow.emit()
        walk(a.menu())
    return out


def test_menu_titles(window):
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()]
    assert titles == [
        "Project", "Materials", "Geometry", "Mesh", "Model", "Results", "Window", "Help"
    ]  # fmt: skip


def test_every_ribbon_button_and_section_item_is_in_the_menus(window):
    have = texts(window)
    ribbons = (window.workbench.ribbon, window.mesh_bench.ribbon, window.results_ribbon)
    # The ribbons' own tools: not the drop-downs a narrow view folds groups into.
    buttons = {b.text() for r in ribbons for g in r.groups for b in g[2]}
    missing = {b for b in buttons if b not in have and f"{b}…" not in have}
    assert not missing, missing
    for section in ("Geometry", "Parameters", "Materials", "Meshes", "Components",
                    "Studies", "Amplitudes", "Coordinate systems", "Results"):  # fmt: skip
        menu = menus.build_menu(window, Node("section", label=section))
        items = []
        for a in menu.actions():
            items += [s.text() for s in a.menu().actions()] if a.menu() else [a.text()]
        lost = {t for t in items if t and RENAMED.get(t, t) not in have}
        assert not lost, (section, lost)
    rows = main_menu.shortcut_rows(window)
    keys = [k for _, k in rows]
    assert len(keys) == len(set(keys)), rows  # no clashes
    assert any(p.endswith("Run study") for p, _ in rows)


def test_a_geometry_tool_from_the_menu_opens_its_tab(app, window):
    from forms import fill

    from puzzle.gui.workbench import ToolPanel
    from puzzle.model.schema import Geometry

    fill(window, which=str(window.session.project.of_type(Geometry)[0].id))
    geometry = window.menus["Geometry"]
    create = next(a.menu() for a in geometry.actions() if a.text() == "Create")
    next(a for a in create.actions() if a.text() == "Box").trigger()
    app.processEvents()
    assert window.left_tabs.currentWidget() is window.workbench
    assert isinstance(window.side.currentWidget(), ToolPanel)


def test_run_asks_which_study_when_none_is_selected(app, window):
    asked = []

    def run(dialog):
        asked.append(
            [dialog.widgets["which"].itemText(i) for i in range(dialog.widgets["which"].count())]
        )
        return False  # cancel

    window.run_dialog = run
    window.actions_by_name["run"].trigger()
    assert asked and "Tip load" in asked[0]
