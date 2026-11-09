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


def test_duplicate_a_component_with_its_studies_and_a_study(window):
    """Duplicate in the tree and the menu bar (the maintainer, 2026-11-08)."""
    from forms import fill

    from puzzle.model.schema import Component, Study

    project = window.session.project
    comp = project.of_type(Component)[0]
    before = len(project.of_type(Study))
    fill(window, name="Heavy", studies="yes")
    menus.copy_component(window, comp)
    project = window.session.project
    heavy = next(c for c in project.of_type(Component) if c.name == "Heavy")
    assert [s for s in project.of_type(Study) if s.component == heavy.id]
    study = project.of_type(Study)[0]
    window.ask_text = lambda *a, **k: "Variant"
    menus.copy_study(window, study)
    names = {s.name for s in window.session.project.of_type(Study)}
    assert "Variant" in names and len(names) == 2 * before + 1
    assert "Duplicate study…" in texts(window) and "Duplicate component…" in texts(window)


def _leaves(menu: QMenu) -> list[str]:
    """A menu's entries at any depth (New operator > a group > its operators)."""
    out = []
    for a in menu.actions():
        out += _leaves(a.menu()) if a.menu() else [a.text()]
    return out


def test_operator_menus_are_grouped_and_alphabetical(window):
    """Every New operator menu (the tree's and the schematic's New, the Results menu)
    has one flyout per group, each alphabetical (the maintainer, 2026-11-08)."""
    from puzzle.operators import GROUPS

    menu = QMenu()
    menus._operator_menu(window, menu)
    top = menu.actions()[0].menu()
    titles = [a.text() for a in top.actions()]
    assert titles == [g for g in GROUPS if g in titles]  # groups, in the order work flows
    for a in top.actions():
        labels = [b.text() for b in a.menu().actions()]
        assert labels and labels == sorted(labels, key=str.casefold), a.text()


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
        items = _leaves(menus.build_menu(window, Node("section", label=section)))
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


def test_a_refresh_shows_the_selection_once_and_menus_outlive_it(window):
    """The tree is rebuilt after every change. It used to show the selection three times
    a refresh (nothing, the item, the item), and a right-click menu held items that a
    rebuild deleted (Rename... raised "already deleted")."""
    from puzzle.gui import organise
    from puzzle.gui.tree import Node
    from puzzle.model.schema import Component

    w = window
    comp = w.session.project.of_type(Component)[0]
    node = Node("object", str(comp.id))
    w.tree.select(node)
    shown = []
    w._show_selected = lambda: shown.append(w.tree.current())
    w.refresh()
    assert len(shown) == 1 and shown[0].object == str(comp.id)

    menu = QMenu()
    organise.add_to_menu(w, menu, w.tree.current())
    w.refresh()  # an edit lands while the menu is open
    (rename,) = [a for a in menu.actions() if a.text().startswith("Rename")]
    rename.trigger()
    assert w.tree.current().object == str(comp.id)
