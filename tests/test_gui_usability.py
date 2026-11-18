"""GUI usability (ADR 0090 package 11): each test names the finding of
`docs/adr/0090/gui-usability.md` (UX-n) it keeps fixed."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Study  # noqa: E402

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _copy(tmp_path, name="cantilever"):
    root = tmp_path / name
    skip = shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    shutil.copytree(EXAMPLES / name, root, ignore=skip)
    return root


@pytest.fixture(autouse=True)
def _tidy(app):
    yield
    from PySide6.QtCore import QCoreApplication, QEvent

    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(_copy(tmp_path))
    yield w
    while w.tasks:
        app.processEvents()
    w.close()


def _settle(app, n=10):
    for _ in range(n):
        app.processEvents()


def _study(window, name):
    return next(s for s in window.session.project.of_type(Study) if s.name == name)


# --- UX-1: no tab holds the window wider than a laptop's screen -------------------------


def test_no_tab_holds_the_window_wider_than_a_laptop(app, window):
    """The Geometry ribbon's folded groups were each as wide as its widest tool, so ten
    of them held the window at 1597 px and crushed the side panels to 191 px. Now the
    ribbon folds into More, and a side panel goes no narrower than 260 px."""
    from puzzle.gui.docking import SIDE_MINIMUM

    window.show()
    _settle(app)
    window.resize(1366, 728)
    _settle(app)
    sides = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
    for i in range(window.left_tabs.count()):
        window.left_tabs.setCurrentIndex(i)
        _settle(app)
        assert window.minimumSizeHint().width() <= 1366, window.left_tabs.tabText(i)
        assert window.width() <= 1366
        for dock in window.findChildren(QDockWidget):
            if dock.isVisible() and not dock.isFloating() and window.dockWidgetArea(dock) in sides:
                assert dock.width() >= SIDE_MINIMUM, dock.windowTitle()
    ribbon = window.workbench.ribbon
    assert ribbon.minimumSizeHint().width() < 200


def test_a_narrow_ribbon_puts_the_last_groups_in_more(app):
    from puzzle.gui.ribbon import Ribbon

    ribbon = Ribbon()
    for g in range(8):
        ribbon.group(f"Group {g}", [Ribbon.button(f"Tool {g}.{k}") for k in range(4)])
    ribbon.resize(3000, 60)
    ribbon.show()
    _settle(app)
    assert not ribbon.more_holder.isVisible() and not ribbon.overflow
    ribbon.resize(200, 60)
    _settle(app)
    assert ribbon.more_holder.isVisible() and ribbon.overflow
    menu = ribbon.more.menu()
    ribbon._fill_more(menu)
    subs = [a.text() for a in menu.actions()]
    assert subs == [f"Group {n}" for n in ribbon.overflow]
    tools = [a.text() for a in menu.actions()[-1].menu().actions()]
    assert tools == [f"Tool 7.{k}" for k in range(4)]
    ribbon.close()


# --- UX-4: an Edit menu with Redo ---------------------------------------------------------


def _materials(window):
    from puzzle.model.schema import Material

    return {m.name for m in window.session.project.of_type(Material)}


def test_edit_menu_undoes_twice_and_redoes(window):
    """Undo pressed twice used to redo the first undo (an undo was the latest entry in
    effect). Now Undo steps back, Redo forward, and each names what it acts on."""
    from puzzle.commands.model import AddLibraryMaterial
    from puzzle.gui.main_menu import paths

    found = paths(window)
    for item in ("Edit → Undo", "Edit → Redo", "Edit → Delete", "Edit → Rename",
                 "Edit → Duplicate…", "Edit → New group…"):  # fmt: skip
        assert item in found, item
    assert "Project → Undo" not in found
    a = window.actions_by_name
    keys = {k.toString() for k in a["redo"].shortcuts()}
    assert {"Ctrl+Y", "Ctrl+Shift+Z"} <= keys
    first = _materials(window)
    window.submit(AddLibraryMaterial(key="copper"))
    window.submit(AddLibraryMaterial(key="titanium_ti6al4v"))
    assert "Titanium" in a["undo"].toolTip() and not a["redo"].isEnabled()
    a["undo"].trigger()
    a["undo"].trigger()
    assert _materials(window) == first
    assert a["redo"].isEnabled() and "Copper" in a["redo"].toolTip()
    a["redo"].trigger()
    assert _materials(window) == first | {"Copper"}
    a["redo"].trigger()
    assert _materials(window) == first | {"Copper", "Titanium Ti-6Al-4V"}
    assert not a["redo"].isEnabled()


def test_duplicate_takes_the_selected_study(window):
    from puzzle.gui import main_menu
    from puzzle.gui.tree import Node

    study = _study(window, "Tip load")
    window.tree.select(Node("object", str(study.id)))
    window.ask_text = lambda *a, **k: "Tip load copy"
    main_menu.duplicate(window)
    assert "Tip load copy" in {s.name for s in window.session.project.of_type(Study)}
    window.tree.select(Node("section", "", "", "Materials"))
    main_menu.duplicate(window)  # nothing to duplicate: said, nothing made
    assert window.toasts.shown[-1].text.startswith("Select a component or a study")


# --- UX-6: the Advisor follows the study on screen ----------------------------------------


def test_the_advisor_follows_the_selected_run_and_lists_the_others(window, monkeypatch):
    """With Tip load's results selected the Advisor spoke of Conduction (the first
    study). Now it follows the run's study, offers what to do with its results and
    lists the other studies."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from puzzle.gui.tree import Node

    tip = _study(window, "Tip load")
    record = SimpleNamespace(run_id="r1", study_id=str(tip.id), status="completed", run_key="k",
                             started_at=datetime(2026, 11, 17, tzinfo=UTC))  # fmt: skip
    panel = window.advisor
    monkeypatch.setattr(panel, "_runs", lambda: [record])
    window.tree.select(Node("section", "", "", "Materials"))  # nothing of a study's
    comp, study = panel.scope_objects()
    assert study.name == "Tip load"  # the study run last
    monkeypatch.setattr(window.tree, "current", lambda: Node("run", "r1", "", ""))
    comp, study, on_run = panel._scope()
    assert study.name == "Tip load" and comp.name == "Cantilever" and on_run
    panel.refresh()
    assert panel.heading.text() == "Results of Tip load"
    assert {"results:probe", "results:reaction", "results:export"} <= set(panel.buttons)
    others = {k for k in panel.buttons if k.startswith("study:")}
    assert others == {f"study:{_study(window, n).id}" for n in ("Conduction", "Modes")}
    monkeypatch.setattr(window.tree, "current", lambda: Node("object", str(tip.id)))
    assert panel.scope_objects()[1].name == "Tip load"


# --- UX-13: Run and Mesh find what they mean; UX-8: a refusal names its action ----------


def test_run_and_mesh_find_what_they_mean(window):
    from puzzle.gui.tree import Node

    started = []
    window._start_run = lambda study, call, **k: started.append(study.name)
    window.start_meshing = lambda comp, then=None: started.append(comp.name)
    tip = _study(window, "Tip load")
    window.tree.select(Node("object", str(tip.id)))
    window.actions_by_name["run"].trigger()
    assert started == ["Tip load"]
    window.tree.select(Node("section", "", "", "Materials"))  # no study selected: asked
    fill(window, which=str(_study(window, "Modes").id))
    window.actions_by_name["run"].trigger()
    assert window.last_form.windowTitle() == "Run" and started[-1] == "Modes"
    window.actions_by_name["mesh"].trigger()  # the only component needs no selecting
    assert started[-1] == "Cantilever"


def test_a_run_refused_for_no_support_offers_one(window):
    from puzzle.commands.model import Edit, EditOp
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component

    comp = window.session.project.of_type(Component)[0]
    fixed = next(i for i, f in enumerate(comp.physics[0].features) if f.type == "fixed")
    window.submit(Edit(object=str(comp.id),
                       ops=[EditOp(op="remove", path=f"physics.0.features.{fixed}")]))  # fmt: skip
    tip = _study(window, "Tip load")
    (problem,) = [p for p in window.session.project.problems() if p.code == "unconstrained"
                  and p.object == "Tip load"]  # fmt: skip
    assert problem.message.endswith("Add a fixed support or a displacement")
    window._run_failed(PuzzleError(problem.message, code="inconsistent"), tip, str(tip.id))
    assert window.toasts.shown[-1].action == "Add fixed support…"
    assert window.output_tabs.currentWidget() is window.problems  # UX-37: opened on it
    tab = window.output_tabs.tabText(window.output_tabs.indexOf(window.problems))
    assert tab.startswith("Problems (") and tab != "Problems (0)"


# --- the maintainer (2026-11-17): opening another project while a solve runs ------------


@pytest.mark.parametrize("runner", [False, True])
def test_opening_another_project_asks_to_stop_a_run_only_without_the_runner(
    window, tmp_path, monkeypatch, runner
):
    import threading

    from puzzle.jobs import service

    monkeypatch.setattr(service, "enabled", lambda: runner)
    cancel = threading.Event()
    window.solves["s"] = ("Tip load", cancel)
    asked = []
    window.ask_yes = lambda title, text: (asked.append((title, text)), True)[1]
    other = _copy(tmp_path / "other", "bracket_assembly")
    window.open(other)
    if runner:  # the runner holds the solve: it goes on, nothing asked
        assert not asked and not cancel.is_set()
    else:
        assert [t for t, _ in asked] == ["Stop the run?"] and "Tip load" in asked[0][1]
        assert cancel.is_set()


# --- UX-2: a load on picked faces, its values in the same form ----------------------------


def _pick(window, comp, faces):
    """Faces picked on the component's view, as a double-click would leave them."""
    window.mesh_component = str(comp.id)
    window.mesh_names[str(comp.id)] = {(2, 10 + n): ("Bar", f) for n, f in enumerate(faces)}
    window.viewport.pick_kind = "face"
    window.viewport.picked = [(2, 10 + n) for n in range(len(faces))]


def _cantilever(window):
    from puzzle.model.schema import Component

    return window.session.project.of_type(Component)[0]


def test_a_force_goes_on_the_picked_faces_with_its_value(window):
    from puzzle.gui import menus
    from puzzle.model.catalog import spec
    from puzzle.model.schema import Geometry

    comp = _cantilever(window)
    _pick(window, comp, ["Tip"])
    fill(window, selection="\x00picked", **{"force:y": "-250 N"})
    seq = len(window.session.history(limit=1000))
    menus.add_feature(window, comp, 0, spec("solid_mechanics", "force"))
    form = window.last_form
    assert form.windowTitle() == "Add force"
    shown = [
        form.widgets["selection"].itemText(i) for i in range(form.widgets["selection"].count())
    ]
    assert shown[0] == "The 1 face picked on Bar (a new selection)" and "Bar/Tip" in shown
    comp = _cantilever(window)
    force = comp.physics[0].features[-1]
    assert force.type == "force" and force.selection == "Bar/Force 1"
    assert (force.values["y"].value, force.values["y"].unit) == (-250.0, "N")
    geo = window.session.project.of_type(Geometry)[0]
    assert next(s for s in geo.selections if s.name == "Force 1").members == ["Tip"]
    assert len(window.session.history(limit=1000)) == seq + 1  # one batch, one undo
    assert not window.viewport.picked
    window.undo()
    assert len(_cantilever(window).physics[0].features) == 2
    geo = window.session.project.of_type(Geometry)[0]
    assert "Force 1" not in {s.name for s in geo.selections}


def test_the_pick_bar_adds_a_support_on_what_is_picked(window):
    comp = _cantilever(window)
    _pick(window, comp, ["Root"])
    fill(window, type="fixed", selection="\x00picked")
    window.add_load_on_picks()
    assert window.last_form.windowTitle() == "Add load or support"
    fixed = _cantilever(window).physics[0].features[-1]
    assert fixed.type == "fixed" and fixed.selection == "Bar/Fixed support 1"


def test_a_wrong_value_is_said_in_the_form(window):
    from puzzle.gui import menus
    from puzzle.model.catalog import spec

    comp = _cantilever(window)
    problems = []

    def run(dialog):
        dialog.set("selection", "Bar/Tip")
        dialog.set("pressure:p", "6 mm")
        problems.append(dialog.problem())
        return False

    window.run_dialog = run
    assert menus.add_feature(window, comp, 0, spec("solid_mechanics", "pressure")) is None
    assert problems[0].startswith("Pressure:") and "not a pressure" in problems[0]


def test_with_nothing_to_put_it_on_picking_starts(window, monkeypatch):
    from puzzle.gui import load_form
    from puzzle.model.catalog import spec

    comp = _cantilever(window)
    monkeypatch.setattr("puzzle.gui.menus.selections_of", lambda *a, **k: [])
    window.viewport.picked = []
    assert load_form.add_load(window, comp, 0, spec("solid_mechanics", "pressure")) is None
    assert window.viewport.picking
    assert window.toasts.shown[-1].text.startswith("Pick the faces in the view")


# --- UX-7: Properties shows the task, the rest folded under Advanced ----------------------


def _rows(window):
    root = window.properties.invisibleRootItem()
    return {root.child(i).text(0): root.child(i) for i in range(root.childCount())}


def test_properties_fold_the_advanced_and_hide_what_is_unset(window):
    from puzzle.gui.tree import Node

    tip = _study(window, "Tip load")
    window.tree.select(Node("object", str(tip.id)))
    rows = _rows(window)
    for gone in ("Shootout", "Convergence", "Adapt", "Morph", "Remote", "Seismic",
                 "Weak springs", "Tags"):  # fmt: skip
        assert gone not in rows, gone
    advanced = rows["Advanced"]
    assert not advanced.isExpanded()
    inside = {advanced.child(i).text(0) for i in range(advanced.childCount())}
    assert {"Weak springs", "Weak spring factor", "Preload step", "Tags"} <= inside
    assert list(rows)[-1] == "Advanced"


def test_a_load_shows_its_values_open_under_its_selection(window):
    from puzzle.gui.tree import Node

    comp = _cantilever(window)
    window.tree.select(Node("feature", str(comp.id), "physics.0.features.1", "force"))
    rows = _rows(window)
    names = list(rows)
    assert names[:3] == ["Type", "Selection", "Values"] and rows["Values"].isExpanded()
    assert "Amplitude" not in rows and "Applies in steps" not in rows
    advanced = rows["Advanced"]
    inside = {advanced.child(i).text(0) for i in range(advanced.childCount())}
    assert "Applies in steps" in inside


def test_mesh_order_reads_as_words(window):
    from puzzle.gui import choices
    from puzzle.model.schema import MeshRecipe

    recipe = window.session.project.of_type(MeshRecipe)[0]
    assert choices.choices(window.session.project, recipe, "element_order") == [
        ("Linear", 1),
        ("Quadratic", 2),
    ]


def test_a_bolt_shows_its_ground_only_when_grounded():
    from puzzle.gui.properties import applies

    bolt = {"ground": False, "ground_direction": [0, 0, 1], "ground_length": None,
            "formulation": "beam"}  # fmt: skip
    assert not applies(bolt, "ground_direction") and not applies(bolt, "ground_length")
    bolt["ground"] = True
    assert applies(bolt, "ground_direction") and applies(bolt, "ground_length")


# --- UX-10: the start page teaches, a dropped CAD file starts a project -------------------


def test_the_start_page_opens_an_example_and_getting_started(app, monkeypatch):
    from puzzle.gui import main_menu

    w = MainWindow()
    try:
        page = w.start_page
        assert set(page.examples) == {"cantilever", "bracket_assembly"}
        from PySide6.QtWidgets import QLabel

        assert any("PyNite" in label.text() for label in page.findChildren(QLabel))
        page.examples["cantilever"].click()
        assert w.session is not None and w.session.project.name
        assert w.session.root.parent.name == "examples"
        shown = []
        monkeypatch.setattr(main_menu, "_getting_started", lambda window: shown.append(1))
        page.guide.click()
        assert shown == [1]
    finally:
        while w.tasks:
            app.processEvents()
        w.close()


def test_a_dropped_step_file_starts_a_project_and_offers_an_analysis(app, tmp_path):
    w = MainWindow()
    try:
        step = tmp_path / "bar.step"
        shutil.copy(EXAMPLES / "cantilever" / "bar.step", step)
        fill(w, folder=str(tmp_path / "new"), name="Drop")
        w.drop_cad([step])
        assert w.last_form.windowTitle() == "New project"
        assert w.session is not None and w.session.project.name == "Drop"
        from puzzle.model.schema import Geometry

        assert [g.name for g in w.session.project.of_type(Geometry)] == ["bar"]
        assert w.toasts.shown[-1].action == "New analysis…"
        w.drop_cad([step])  # a project open: imported into it
        assert len(w.session.project.of_type(Geometry)) == 2
    finally:
        while w.tasks:
            app.processEvents()
        w.close()


# --- UX-3: New analysis in the GUI -------------------------------------------------------


def test_new_analysis_makes_everything_in_one_form(window):
    from puzzle.gui import menus
    from puzzle.model.schema import Component

    fill(window, geometry="Bar", analysis="modal", material="Structural steel", mesh="coarse")
    out = menus.new_analysis(window)
    assert window.last_form.windowTitle() == "New analysis"
    assert {o["kind"] for o in out.created} == {"mesh", "component", "study"}
    made = next(c for c in window.session.project.of_type(Component) if c.name == "Bar")
    assert window.tree.current().object == str(made.id)  # selected: the Advisor's next
    assert "Bar modal" in {s.name for s in window.session.project.of_type(Study)}
    assert window.actions_by_name["new_analysis"] in window.toolbar.actions()


# --- UX-23: Settings, one name per group and no design-record numbers -------------------


def test_settings_have_one_agent_group_and_speak_plainly(app):
    import re

    from PySide6.QtWidgets import QLabel

    from puzzle.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        groups = [dialog.groups.item(i).text() for i in range(dialog.groups.count())]
        assert len(groups) == len(set(groups)), groups
        assert groups.index("Solvers") < groups.index("3D mouse") < groups.index("Agent")
        said = [w.text() for w in dialog.findChildren(QLabel)] + [
            b.toolTip() for b in dialog.boxes.values()]  # fmt: skip
        assert not [t for t in said if re.search(r"ADR \d{4}", t)]
    finally:
        dialog.close()


# --- UX-32: the contact table leads with the model's words -------------------------------


def test_the_contact_table_hides_what_the_solver_gets_until_asked(app, tmp_path):
    from puzzle.gui.contact_tool import COLUMNS, GIVEN

    w = MainWindow(_copy(tmp_path, "bracket_assembly"))
    try:
        setup = w.contacts.setup
        setup.refresh()
        titles = [c.title for c in COLUMNS]
        assert "Source" in titles and not [t for t in titles if "slave" in t or "master" in t]
        assert setup.table.isColumnHidden(GIVEN)
        setup.show_given.setChecked(True)
        assert not setup.table.isColumnHidden(GIVEN)
    finally:
        while w.tasks:
            app.processEvents()
        w.close()


# --- UX-9, UX-36, UX-24, UX-34, UX-38: the smaller ones ----------------------------------


def test_the_model_tab_has_its_ribbon(window):
    window.left_tabs.setCurrentWidget(window.tree)
    window._tab_changed()
    window.set_context("model")
    assert window.model_ribbon.isVisibleTo(window)
    texts = {b.text() for _box, _h, bs in window.model_ribbon.groups for b in bs}
    assert {"New analysis…", "Add load or support", "Find contacts…", "Run study"} <= texts
    window.set_context("results")
    assert not window.model_ribbon.isVisibleTo(window)


def test_an_empty_mesh_recipe_offers_a_local_size(window):
    bench = window.mesh_bench
    window.left_tabs.setCurrentWidget(bench)
    bench.refresh()
    assert bench.recipe() is not None and not bench.recipe().controls
    assert bench.no_controls.isVisibleTo(bench) and not bench.controls.isVisibleTo(bench)
    bench.no_controls.click()
    assert bench.tool is not None and bench.tool.kind == "size"  # the Local size tool


def test_small_wording(window):
    from puzzle.gui.choices import label
    from puzzle.gui.feedback import StatusProgress
    from puzzle.gui.main_menu import paths

    assert label("custom_pde") == "Custom PDE"
    assert StatusProgress().cancel_button.text() == "Stop"
    helps = [p for p in paths(window) if p.startswith("Help → ")]
    assert helps.index("Help → Log") > helps.index("Help → Element library")


# --- UX-8: a geometry step that can't be built says so in words --------------------------


def test_a_step_opencascade_refuses_is_said_in_words(monkeypatch):
    pytest.importorskip("OCP")
    from puzzle.geometry import history

    def broken(state, op, a):
        raise TypeError("__init__(): incompatible constructor arguments. Invoked with: None")

    monkeypatch.setitem(history.MODIFIERS, "fillet", broken)
    ops = [history.Op("box", "Box", {"dx": 0.01, "dy": 0.01, "dz": 0.01}),
           history.Op("fillet", "Fillet 1", {})]  # fmt: skip
    with pytest.raises(history.GeometryError) as err:
        history.regenerate(ops)
    text = str(err.value)
    assert text.startswith("step 'Fillet 1' (fillet) failed: it could not be built")
    assert "Invoked with" not in text and "__init__" not in text


def test_a_selected_rows_state_icon_is_drawn_to_show_on_the_highlight(app):
    """UX-29: the state colour vanished on the blue selection."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    from puzzle.gui.icons import state_icon
    from puzzle.gui.theme import current

    icon = state_icon("up_to_date")
    normal = icon.pixmap(QSize(16, 16), QIcon.Mode.Normal).toImage()
    selected = icon.pixmap(QSize(16, 16), QIcon.Mode.Selected).toImage()
    assert normal != selected
    colours = {selected.pixelColor(x, y).name() for x in range(16) for y in range(16)
               if selected.pixelColor(x, y).alpha() > 200}  # fmt: skip
    assert current().accent_text.lower() in colours


# --- UX-30: New study asks the analysis first, the solver automatic ----------------------


def test_new_study_asks_the_analysis_first_and_picks_a_solver(window):
    from puzzle.gui import menus

    fill(window, analysis="modal", component="Cantilever", name="Ringing")
    menus.new_study(window)
    form = window.last_form
    assert list(form.widgets)[:3] == ["analysis", "component", "solver"]
    box = form.widgets["solver"]
    assert box.itemText(0).startswith("Automatic (") and box.itemData(0) == ""
    made = _study(window, "Ringing")
    assert made.solver == menus._auto_solver("modal", _cantilever(window))
