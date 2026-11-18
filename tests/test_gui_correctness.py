"""GUI correctness (ADR 0090 package 10): one quantity entry, untouched values kept,
errors in words, keys scoped to their panel, tools and selections that follow the
project. Each test names the finding of `docs/adr/0090/gui-core-bugs.md` (GUI-n) or
`gui-panels-bugs.md` (PANEL-n) it keeps fixed."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.commands.model import Create, Edit, EditOp  # noqa: E402
from puzzle.gui.tree import Node  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Geometry, Study  # noqa: E402
from puzzle.model.units import parse_number, parse_quantity  # noqa: E402

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
    """Every window and dialog a test made, deleted before the next: a half-dead one
    crashed the next window's style sheet."""
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


@pytest.fixture
def hooked(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: caught.append(a))
    return caught


def _study(window, name):
    return next(s for s in window.session.project.of_type(Study) if s.name == name)


def _box_part(window, name="Block", dx=0.05):
    data = {"name": name, "history": [{"kind": "box", "name": "Box",
                                       "args": {"dx": dx, "dy": 0.03, "dz": 0.01}}],
            "selections": [{"name": "Body", "entity": "body", "members": ["Body"]}]}  # fmt: skip
    window.submit(Create(kind="geometry", data=data))
    return next(g for g in window.session.project.of_type(Geometry) if g.name == name)


# --- one quantity entry (GUI-10, PANEL-5, PANEL-11, PANEL-17, PANEL-21) ---------------


@pytest.mark.parametrize("text, unit, kind, expected", [
    ("5mm", "m", None, (5.0, "mm")),
    ("2,5 mm", "m", None, (2.5, "mm")),
    ("3000", "mm", None, (3000.0, "mm")),  # a bare number is in the unit shown
    ("-1.5e-3 in", "m", "length", (-0.0015, "in")),
    ("20 degC", "K", None, (20.0, "degC")),
    ("6 mm", "dimensionless", "any", (6.0, "mm")),
])  # fmt: skip
def test_quantities_are_read_as_people_type_them(text, unit, kind, expected):
    assert parse_quantity(text, unit, kind) == expected


@pytest.mark.parametrize("text, unit, words", [
    ("5 foo", "m", "'foo' is not a unit"),
    ("6 kN", "mm", "kN is not a length"),  # PANEL-11: a force for a length
    ("30 mm mm", "mm", "is not a length"),
    ("1e400", "m", "finite"),
    ("abc", "m", "type a number"),
    ("1,000.5", "m", "one decimal point"),
])  # fmt: skip
def test_a_wrong_quantity_is_refused_in_words(text, unit, words):
    with pytest.raises(ValueError, match=words):
        parse_quantity(text, unit)


def test_a_plain_number_is_refused_in_words():
    assert parse_number("2,5") == 2.5
    with pytest.raises(ValueError, match="cycles: 'x' is not a number"):
        parse_number("x", "cycles")


def test_properties_parse_and_refuse_typed_values():
    """GUI-10: "5mm" and a decimal comma read; a wrong unit and "," refused in words;
    an object named like a row marker renamed like any other."""
    from puzzle.gui.properties import STEPS, parse_steps, parse_value

    old = {"value": 2.0, "unit": "mm"}
    assert parse_value("5mm", old) == {"value": 5.0, "unit": "mm"}
    assert parse_value("2,5 mm", old) == {"value": 2.5, "unit": "mm"}
    with pytest.raises(ValueError, match="not a unit"):
        parse_value("5 foo", old)
    with pytest.raises(ValueError, match="not a length"):
        parse_value("5 kN", old)
    assert parse_steps(",") == []
    assert parse_value("Other", "steps") == "Other"  # a name, not the steps marker
    assert STEPS != "steps"


def test_live_forms_check_the_dimension():
    """PANEL-11: a force typed for a weld leg, a length for a clamp length twice."""
    from puzzle.gui.live_form import quantity

    with pytest.raises(ValueError, match="not a length"):
        quantity("6 kN", "mm")
    assert quantity("6", "mm") == {"value": 6.0, "unit": "mm"}


def test_vdi_form_refuses_impossible_values():
    """PANEL-12: a zero modulus or tightening factor, a negative friction, a fractional
    interface count are said, not raised or computed."""
    from puzzle.gui.vdi2230_form import arguments

    good = {"size": "M12", "grade": "10.9", "clamp_length": "30 mm", "e_bolt": "205 GPa",
            "e_plate": "205 GPa", "mu_thread": "0.12", "mu_head": "0.12", "n": "0.5",
            "rz": "16 um", "interfaces": "2"}  # fmt: skip
    assert arguments(good)["interfaces"] == 2
    for key, value, words in (("e_bolt", "0", "more than zero"),
                              ("mu_thread", "-1", "at least 0"),
                              ("interfaces", "1.7", "whole number"),
                              ("clamp_length", "inf", "'inf'"),
                              ("tightening_factor", "0", "more than 0")):  # fmt: skip
        with pytest.raises(ValueError, match=words):
            arguments({**good, key: value})


# --- values a form didn't touch are kept (GUI-29, PANEL-8, PANEL-9, PANEL-27) ---------


def test_an_untouched_step_form_gives_its_arguments_back(app, engineering_units):
    """GUI-29: every step kind's form, opened on 12-digit arguments, returns them."""
    from puzzle.geometry.steps import STEPS
    from puzzle.gui.step_dialog import StepForm

    for kind in STEPS:  # GUI-4: every kind's form builds
        StepForm(kind, {}, kind, features=["Box"], parts=[("Other", "id")])
    args = {"dx": 0.0123456789012, "dy": 1 / 3, "dz": 0.01, "x0": 0.0, "y0": 0.0,
            "z0": 0.0}  # fmt: skip
    back = StepForm("box", args, "Box").values()
    assert {k: back[k] for k in args} == args


def test_an_untouched_material_keeps_every_digit_and_its_units(app, engineering_units):
    """PANEL-3: a property stored in mPa·s shown and saved as such; PANEL-8: twelve
    digits survive a save; PANEL-7: editing one hardening curve keeps the others."""
    from puzzle.gui.material_builder import MaterialEditor
    from puzzle.model.schema import Material

    m = Material(name="Fluid", properties={
        "youngs_modulus": {"value": 206842718795.3, "unit": "Pa"},
        "dynamic_viscosity": {"value": 1.0, "unit": "mPa*s"},
        "electrical_conductivity": {"value": 58.0, "unit": "MS/m"},
    }, models={"plasticity": {"curves": [
        {"temperature": 293.15, "points": [(0.0, 2.5e8), (0.1, 3e8)]},
        {"temperature": 573.15, "points": [(0.0, 2e8), (0.1, 2.4e8)]}]}})  # fmt: skip
    editor = MaterialEditor(lambda: None)
    editor.load(m)
    assert editor.values["dynamic_viscosity"].text() == "0.001"  # shown in Pa·s
    out = editor.material()
    for key in m.properties:
        assert out.properties[key].to_si() == pytest.approx(m.properties[key].to_si(), rel=1e-15)
    assert out.properties["youngs_modulus"].value == 206842718795.3
    editor.ask_table = lambda title, headers, rows: rows  # accepted as shown
    editor._curve_to_edit = lambda: 1
    editor.edit_curve()
    curves = editor.material().models.plasticity.curves
    assert [c.temperature for c in curves] == [293.15, 573.15]
    assert curves[1].points == m.models.plasticity.curves[1].points


def test_a_sweep_reopened_and_accepted_is_unchanged():
    """PANEL-9: values with every digit, read back to the same floats."""
    from puzzle.gui.sweep_form import _text, _values
    from puzzle.model.schema import Quantity

    values = [Quantity(value=0.0012345678, unit="m"), Quantity(value=1 / 3, unit="m")]
    back = _values(_text(values), "m")
    assert [v["value"] for v in back] == [0.0012345678, 1 / 3]


def test_an_edited_plane_keeps_what_was_not_edited(window, app):
    """PANEL-27: a plane's offset and direction come back exactly after Edit…, Apply."""
    from PySide6.QtWidgets import QLineEdit

    from puzzle.gui.construction import read_kept, show_kept

    line = QLineEdit()
    show_kept(line, f"{0.0123456789012 * 1000:.6g}", 12.3456789012)
    assert read_kept(line, float) == 12.3456789012
    line.setText("12.5")
    assert read_kept(line, float) == 12.5


# --- errors in words, panels on their own (GUI-5, GUI-24, GUI-25, GUI-30) -------------


def test_errors_are_said_in_words(window):
    """GUI-5: no internal code in the status bar, and the reason from the details."""
    from puzzle.errors import PuzzleError
    from puzzle.gui.errors import plain

    errors = [{"path": "dx", "message": "Value error, too big"}]
    exc = PuzzleError("not a valid command", details={"errors": errors})
    assert plain(exc) == "not a valid command: dx: too big"
    window.report(PuzzleError("invalid mesh: size: must be positive", code="invalid"))
    message = window.statusBar().currentMessage()
    assert message == "invalid mesh: size: must be positive" and "[" not in message
    assert "[invalid]" in window.log.toPlainText()  # the code kept for the log


def test_one_panel_failing_leaves_the_rest_following(window, caplog):
    """GUI-24: a mesh study shown in the Convergence panel, then gone: refresh goes on."""
    window.convergence_dock.show()
    window.convergence.study_id = str(_study(window, "Tip load").id)
    window.convergence.check_name = "A mesh study no longer there"
    window.refresh()
    assert window.convergence.title.text() == "No convergence check shown"
    window.convergence.refresh = lambda: 1 / 0  # any panel's failure, kept to itself
    study = _study(window, "Modes")
    window.tree.select(Node("object", str(study.id)))
    window.refresh()
    assert window.tree.current().object == str(study.id)
    assert "the convergence failed" in caplog.text


def test_the_tree_lists_result_tools_beside_swept_runs(window):
    """GUI-25: `title` meant two functions in one loop."""
    from types import SimpleNamespace

    from puzzle.model.schema import ResultProbe

    study = _study(window, "Tip load")
    window.submit(Create(kind="probe", data={"name": "Tip", "source": str(study.id),
                                              "point": [0.1, 0.005, 0.005]}))  # fmt: skip
    assert window.session.project.of_type(ResultProbe)
    run = SimpleNamespace(run_id="r1", study_id=str(study.id), study_name=study.name,
                          solver="calculix", started_at=__import__("datetime").datetime(2026, 1, 1),
                          point_labels=lambda: {"Bar/Body": "Copper"}, status="completed",
                          study_hash="", run_key="", recorded_at=None)  # fmt: skip
    import puzzle.project.status as status

    original = status.run_state
    status.run_state = lambda r, p: None
    try:
        window.tree.populate(window.session.project, [run], {}, {})
    finally:
        status.run_state = original
    labels = [i.text(0) for i in window.tree._items()]
    assert any(t.startswith("Bar/Body: Copper") or "Copper" in t for t in labels)


def test_custom_pde_physics_asks_its_equation(window):
    """GUI-30: Add physics → Custom PDE opened its equation form, not an empty physics."""
    from forms import fill

    from puzzle.gui import menus

    comp = window.session.project.of_type(Component)[0]
    fill(window, name="T", weak_form="inner(grad(T), grad(v_T)) * dx")
    assert menus.add_physics(window, comp, "custom_pde") is not None
    comp = window.session.project.get(comp.id, Component)
    assert comp.physics[-1].equation.unknowns[0].name == "T"


# --- Delete and Undo where they belong (GUI-1, GUI-26) --------------------------------


def test_delete_in_another_tab_leaves_the_tree_alone(window, app):
    """GUI-1: a study selected in the tree, then Delete in the Geometry tab's list."""
    study = _study(window, "Tip load")
    window.tree.select(Node("object", str(study.id)))
    window.left_tabs.setCurrentWidget(window.workbench)
    window.show()
    window.workbench.steps.setFocus()
    QTest.keyClick(window.workbench.steps, Qt.Key.Key_Delete)
    app.processEvents()
    assert study.id in window.session.project.objects
    window.left_tabs.setCurrentWidget(window.tree)
    window.tree.setFocus()
    window.tree.select(Node("object", str(study.id)))
    window.delete_selected()
    assert study.id not in window.session.project.objects
    assert "Deleted Tip load" in window.statusBar().currentMessage()


def test_undo_while_sketching_leaves_the_project(window):
    """GUI-26: Ctrl+Z in a sketch must not undo the last project command."""
    _box_part(window)
    before = len(window.session.history(limit=100))
    window.workbench.sketching = {"geo": None, "plane": "xy"}
    try:
        window.undo()
    finally:
        window.workbench.sketching = None
    assert len(window.session.history(limit=100)) == before
    assert "sketch" in window.statusBar().currentMessage()


# --- tools, sketches and selections that follow the project (GUI-3, 9, 23, 27, 28) ----


def test_a_tool_with_nothing_to_choose_opens_and_says_why(window, hooked):
    """GUI-3: Pattern on the imported bar, which has no step to repeat; the preview
    stays sound."""
    geo = window.session.project.of_type(Geometry)[0]
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench.geo_id = geo.id
    window.workbench.refresh()
    panel = window.workbench.start_tool("pattern")
    assert panel is not None and "no step to repeat" in panel.error.text()
    window._selected()
    window.workbench.close_tool()
    assert window.pending is None and not hooked


def test_a_tool_applies_to_a_file_part_as_a_history(window, engineering_units):
    """GUI-5: Move on the imported bar makes its history: the import, then the move."""
    geo = window.session.project.of_type(Geometry)[0]
    assert geo.source is not None
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench.geo_id = geo.id
    window.workbench.refresh()
    panel = window.workbench.start_tool("move")
    panel.form.widgets["dx"].setText("10")
    panel.apply()
    geo = window.session.project.get(geo.id, Geometry)
    assert geo.source is None and [s.kind for s in geo.history] == ["import", "move"]
    assert geo.history[1].args["dx"] == pytest.approx(0.01)


def test_a_tool_whose_step_was_undone_closes(window, hooked):
    """GUI-9: editing a step, its creation undone, then Apply; and another project."""
    geo = _box_part(window)
    menus_apply = __import__("puzzle.gui.menus", fromlist=["apply_step"]).apply_step
    menus_apply(window, geo, "move", "M1", {"dx": 0.01, "dy": 0.0, "dz": 0.0})
    geo = window.session.project.get(geo.id, Geometry)
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench.geo_id = geo.id
    panel = window.workbench.start_tool("move", 1)
    window.undo()  # the step's creation
    assert window.workbench.tool is None  # closed by the refresh, said why
    assert "no longer there" in window.statusBar().currentMessage()
    panel.apply()  # a panel still held: refused in words
    assert not hooked


def test_the_tree_keeps_its_selection_when_a_label_changes(window):
    """GUI-23: rename a study through Properties; change a load's selection."""
    study = _study(window, "Tip load")
    window.tree.select(Node("object", str(study.id)))
    window._edit(str(study.id), "name", "Renamed")
    assert window.tree.current().object == str(study.id)
    comp = window.session.project.of_type(Component)[0]
    for item in window.tree._items():
        node = item.data(0, 256)
        if node.kind == "feature":
            window.tree.setCurrentItem(item)
            break
    node = window.tree.current()
    window._edit(str(comp.id), f"{node.path}.selection", "Bar/Tip")
    assert window.tree.current() is not None and window.tree.current().path == node.path


def test_a_pending_step_stays_in_the_geometry_tab(window):
    """GUI-28: the Model tab's preview key never has a tool's unapplied step."""
    geo = _box_part(window)
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench.geo_id = geo.id
    window.workbench.start_tool("move")
    assert window._pending() is not None
    window.left_tabs.setCurrentWidget(window.tree)
    assert window._pending() is None


def test_another_project_ends_the_sketch_and_the_tool(window, tmp_path):
    """GUI-27, GUI-9 C: nothing of the old project's sketch or tool stays."""
    geo = _box_part(window)
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench.geo_id = geo.id
    window.workbench.start_tool("move")
    window.open(_copy(tmp_path, "bracket_assembly"))
    assert window.workbench.tool is None and window.pending is None
    assert window.workbench.sketching is None


# --- reloads, runs and other projects (GUI-2, GUI-7, GUI-8) ---------------------------


def test_own_edits_reload_nothing_and_foreign_ones_do(window, app):
    """GUI-7: the watcher's reload skips this window's own writes."""
    calls = []
    real = window.reload
    window.reload = lambda retry=True: calls.append(1) or real(retry)
    study = _study(window, "Tip load")
    window._edit(str(study.id), "name", "Again")
    window._reload_if_changed()
    assert calls == []
    other = __import__("puzzle.commands", fromlist=["Session"]).Session(window.session.root)
    other.submit(Edit(object=str(study.id), ops=[EditOp(op="set", path="name", value="Far")]))
    window._reload_if_changed()
    assert calls == [1] and _study(window, "Far")


def test_an_unreadable_file_on_reload_is_said(window):
    """GUI-8: a half-written file: said, the project kept, no raise."""
    study = _study(window, "Tip load")
    path = window.session.root / "study" / f"{study.id}.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) // 2] + "\n[[[", encoding="utf-8")
    assert window.reload(retry=False) is False
    assert window.statusBar().currentMessage()
    assert window.session.project.objects.get(study.id) is not None
    path.write_text(text, encoding="utf-8")
    assert window.reload(retry=False) is True


def test_a_run_ending_after_another_project_opened(window, app, tmp_path, hooked):
    """GUI-2: the old project's run is let go; the new project's store isn't read."""
    import threading

    gate = threading.Event()
    study = _study(window, "Tip load")

    def call(emit, event, cancel):
        gate.wait(10)
        return __import__("types").SimpleNamespace(run=__import__("types").SimpleNamespace(
            run_id="nowhere", cached=False))  # fmt: skip

    window._start_run(study, call)
    window.open(_copy(tmp_path, "bracket_assembly"))
    assert window.running == set()
    gate.set()
    end = time.monotonic() + 20
    while window.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not hooked and window.running == set()
    assert "no longer open here" in window.log.toPlainText()


# --- forms that start from what is stored (PANEL-6, 15, 22, 24, 25; QuickForm follow) --


def test_choices_follow_the_fields_they_depend_on(app):
    from puzzle.gui.quick_form import Ask, QuickForm

    form = QuickForm("T", [
        Ask("which", "Which", choices=[("A", "a"), ("B", "b")]),
        Ask("hand", "Hand", choices=[("Right", "right"), ("Left", "left")],
            follow=lambda v: "left" if v.get("which") == "b" else "right"),
    ])  # fmt: skip
    assert form.values()["hand"] == "right"
    form.set("which", "b")
    assert form.values()["hand"] == "left"
    form.set("hand", "right")  # chosen by a person: no longer follows
    form.set("which", "a")
    form.set("which", "b")
    assert form.values()["hand"] == "right"


def test_a_stored_value_the_dropdown_lacks_is_offered():
    """PANEL-25: 10 passes stayed 10, not the first choice."""
    from puzzle.gui.convergence_panel import PASSES
    from puzzle.gui.quick_form import with_stored

    assert with_stored(PASSES, 10, "Up to 10 passes")[-1] == ("Up to 10 passes", 10)
    assert with_stored(PASSES, 3) == PASSES


def test_cyclic_symmetry_keeps_an_axis_it_cannot_show(window):
    """PANEL-15: a skew origin, only the sectors changed."""
    from forms import fill

    from puzzle.gui.symmetry_dialog import set_cyclic

    geo = window.session.project.of_type(Geometry)[0]
    names = [s.name for s in geo.selections if s.entity == "face"][:2]
    comp = window.session.project.of_type(Component)[0]
    value = {"source": f"Bar/{names[0]}", "target": f"Bar/{names[1]}", "sectors": 6,
             "origin": (0.1, 0.0, 0.0), "axis": (0.0, 0.0, -1.0)}  # fmt: skip
    window.submit(Edit(object=str(comp.id), ops=[EditOp(op="set", path="cyclic", value=value)]))
    fill(window, sectors="8")
    set_cyclic(window, window.session.project.get(comp.id, Component))
    cyclic = window.session.project.get(comp.id, Component).cyclic
    assert cyclic.sectors == 8 and tuple(cyclic.origin) == (0.1, 0.0, 0.0)
    assert tuple(cyclic.axis) == (0.0, 0.0, -1.0)
    fill(window, target=f"Bar/{names[0]}")
    assert set_cyclic(window, window.session.project.get(comp.id, Component)) is None
    assert "must differ" in window.last_form.problem()


# --- panels (PANEL-10, 14, 16, 18, 23) ------------------------------------------------


def test_welds_and_members_follow_the_project(window):
    """PANEL-10: shown, they refresh with the window."""
    counts = {"welds": 0, "members": 0}
    for name in counts:
        getattr(window, f"{name}_dock").show()
        getattr(window, name).refresh = lambda n=name: counts.__setitem__(n, counts[n] + 1)
    window.refresh()
    assert counts == {"welds": 1, "members": 1}


def test_runs_are_listed_by_name_and_local_time():
    """PANEL-18."""
    import datetime
    from types import SimpleNamespace

    from puzzle.gui.choices import run_label

    when = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)
    text = run_label(SimpleNamespace(study_name="S", solver="code_aster", recorded_at=when))
    assert "code_aster" not in text and when.astimezone().strftime("%H:%M") in text


def test_bolt_cells_sort_by_value(app):
    """PANEL-14: "8 mm" before "12 mm"."""
    from puzzle.gui.bolt_table import NumericItem

    assert NumericItem("8 mm") < NumericItem("12 mm")
    assert NumericItem("Bolt 10") < NumericItem("Bolt 2")  # words stay words


def test_paraview_folders_have_no_colon():
    """PANEL-23: a derived result's run id made a folder Windows refused."""
    from puzzle.post.paraview_link import folder_for

    assert ":" not in folder_for(Path("p"), "derived:1234").name


# --- the view (GUI-12, GUI-13, GUI-15, GUI-17) ----------------------------------------


def test_a_probe_on_a_mirrored_copy_reads_its_own_place():
    """GUI-13: a tet mirrored across x = 0; the copy of node 2 reads node 2's value."""
    pytest.importorskip("pyvista")
    from puzzle.gui.viewport import _on_copies
    from puzzle.mesh.mesh import CellBlock, Mesh
    from puzzle.post.quantities import catalog
    from puzzle.results.model import Field, StepResult

    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float) + [0.5, 0.0, 0.0]
    mesh = Mesh(node_ids=np.arange(1, 5), coords=pts,
                blocks=[CellBlock("tet4", 3, (3, 1), np.array([1]),
                                  np.array([[1, 2, 3, 4]]))])  # fmt: skip
    mesh.copies = [(np.eye(3), np.zeros(3), 1.0), (np.diag([-1.0, 1, 1]), np.zeros(3), 1.0)]
    step = StepResult(index=1, kind="static", value=1.0, fields={
        "temperature": Field("temperature", "node", ["value"], np.arange(1, 5),
                             np.array([[10.0], [20.0], [30.0], [40.0]])),
        "displacement": Field("displacement", "node", ["x", "y", "z"], np.arange(1, 5),
                              np.tile([1.0, 0.0, 0.0], (4, 1)))})  # fmt: skip
    from puzzle.settings import current

    current().set("results.symmetric_view", "whole")
    items = catalog([("temperature", "node", ["value"]), ("displacement", "node", ["x", "y", "z"])])
    temperature = next(i for i in items if i.field == "temperature")
    ux = next(i for i in items if i.field == "displacement" and i.what == "x")
    state = {"mesh": mesh, "step": step, "item": temperature}
    assert _on_copies(state, [1.5, 0.0, 0.0])["value"] == pytest.approx(20.0)
    assert _on_copies(state, [-1.5, 0.0, 0.0])["value"] == pytest.approx(20.0)
    state["item"] = ux
    assert _on_copies(state, [-1.5, 0.0, 0.0])["value"] == pytest.approx(-1.0)


def test_orthographic_zoom_and_pan_move_the_view():
    """GUI-15: a dolly changes nothing seen in a parallel projection."""
    vtk = pytest.importorskip("vtk")
    from types import SimpleNamespace

    from puzzle.gui.viewport import SceneView

    camera = vtk.vtkCamera()
    camera.ParallelProjectionOn()
    camera.SetParallelScale(2.0)
    fake = SimpleNamespace(plotter=SimpleNamespace(camera=camera))
    SceneView._zoom(fake, 2.0)
    assert camera.GetParallelScale() == pytest.approx(1.0)


def test_picks_with_a_part_hidden_keep_the_mesh_shown(window, app):
    """GUI-12, GUI-17: the filtered mesh is kept, by identity, across picks."""
    view = window.viewport
    comp = window.session.project.of_type(Component)[0]
    window.tree.select(Node("object", str(comp.id)))
    end = time.monotonic() + 60
    while window.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    window._selected()
    if view.mesh is None:
        pytest.skip("no preview mesh offscreen")
    source = view._unfiltered()
    some = sorted({b for b in source.groups})[:1]
    view.hidden_parts = lambda mesh: {"Nothing"} if some else set()
    view.show_mesh(source)
    shown = view.mesh
    view._picks_changed()
    assert view.mesh is shown


# --- dialogs that check before they close (PANEL-4, 21, 22, 29, 31, 33) --------------


def test_the_field_wizard_says_a_missing_time_and_stays_open(app, tmp_path):
    """PANEL-4: two files and no times; a non-number in Move."""
    from puzzle.gui.field_wizard import FieldWizard
    from puzzle.model.schema import ImportedField, Project
    from puzzle.project import save_project

    for t in (0, 10):
        (tmp_path / f"t{t}.csv").write_text("x,y,z,T [K]\n0,0,0,300\n1,0,0,400\n0,1,0,300\n",
                                            encoding="utf-8")  # fmt: skip
    save_project(Project(name="fields"), tmp_path / "proj")
    window = MainWindow(tmp_path / "proj")
    try:
        wizard = FieldWizard(window)
        wizard.file.load(tmp_path / "t0.csv")
        wizard.file.set_more([tmp_path / "t10.csv"])
        wizard.file.timing.setCurrentIndex(wizard.file.timing.findData("files"))
        assert wizard.file.validatePage() is False
        assert "2 files need 2 times" in wizard.file.note.text()
        wizard.file.times.setText("0, 5s")
        assert wizard.file.validatePage() is False and "'5s'" in wizard.file.note.text()
        wizard.file.times.setText("0, 10")
        wizard.align.move[0].setText("abc")
        assert wizard.align.validatePage() is False
        wizard.accept()  # refused: still open, nothing made
        assert not window.session.project.of_type(ImportedField)
        assert "Not made" in wizard.naming.subTitle()
        wizard.align.move[0].setText("0")
        wizard.accept()
        assert len(window.session.project.of_type(ImportedField)) == 1
    finally:
        window.close()


def _two_contacts(root):
    from test_thread_setup import _project

    from puzzle.model.schema import Contact, Thread
    from puzzle.project import save_project

    p, comp = _project()
    threaded = Contact(name="Threaded", type="bonded", source="Boss/Shank", target="Boss/Bore",
                       meshing="nonconformal",
                       thread=Thread(form="iso", size="M12", hand="left"))  # fmt: skip
    p.objects[comp.id] = comp.model_copy(update={"contacts": [*comp.contacts, threaded]})
    save_project(p, root)
    return comp


def test_the_thread_form_starts_from_the_contact_chosen(app, tmp_path, engineering_units):
    """PANEL-22: from the menu bar, the second contact's thread is what it shows;
    PANEL-21: a bare major diameter is in the unit shown (mm here)."""
    from forms import fill

    from puzzle.gui.thread_dialog import thread_contact

    comp = _two_contacts(tmp_path / "p")
    window = MainWindow(tmp_path / "p")
    try:
        comp = window.session.project.get(comp.id, Component)
        fill(window, contact="Threaded", type="bonded")
        thread_contact(window, comp)  # nothing but the contact chosen: kept as it was
        kept = window.session.project.get(comp.id, Component).contacts[1].thread
        assert (kept.size, kept.hand) == ("M12", "left")
        fill(window, contact="Thread", form="custom", major="20", pitch="2.5",
             angles="30, 30", type="bonded")  # fmt: skip
        thread_contact(window, window.session.project.get(comp.id, Component))
        made = window.session.project.get(comp.id, Component).contacts[0].thread
        assert made.major.to_si() == pytest.approx(0.020)
    finally:
        window.close()


def test_add_fastener_says_which_number_is_wrong(window, app, engineering_units):
    """PANEL-29: checked before it closes, the field that is wrong named."""
    from puzzle.gui.fastener_dialog import AddFastener

    comp = window.session.project.of_type(Component)[0]
    dialog = AddFastener(window.session.project, comp, window)
    dialog.diameter.setText("10")
    dialog.pretension.setText("5 mm")
    dialog._accept()
    assert "Pretension" in dialog.error.text() and not dialog.result()
    dialog.pretension.setText("5 kN")
    assert dialog.value()["diameter"] == {"value": 10.0, "unit": "mm"}


def test_the_log_window_adds_lines_and_keeps_the_cursor(app, tmp_path):
    """PANEL-31: a growing file is appended to, not set again."""
    from PySide6.QtGui import QTextCursor

    from puzzle.gui.text_viewer import TextViewer

    log = tmp_path / "solver.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")
    viewer = TextViewer(title="Log", files=[log])
    viewer.timer.stop()
    cursor = viewer.text.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
    viewer.text.setTextCursor(cursor)
    viewer.text.verticalScrollBar().setValue(0)
    with log.open("a", encoding="utf-8") as f:
        f.write("four\n" * 200)
    viewer.fill()
    assert viewer.text.textCursor().selectedText() == "ne"
    assert viewer.text.toPlainText().count("four") == 200


def test_the_material_builder_asks_before_dropping_edits(app):
    """PANEL-33."""
    from puzzle.gui.material_builder import MaterialBuilder

    builder = MaterialBuilder()
    asked = []
    builder.ask_yes = lambda title, text: asked.append(text) or False
    builder.new()
    builder.editor.name.setText("Mine")
    builder.editor.on_change()
    builder.new()  # declined: the edits stay
    assert asked and builder.editor.name.text() == "Mine"
    builder.ask_yes = lambda title, text: True
    builder.new()
    assert builder.editor.name.text() == "New material" and not builder.dirty


def test_member_lengths_are_in_the_unit_shown(engineering_units):
    """PANEL-5: 3000 with millimetres shown is 3 m; a mass for a length is refused."""
    from puzzle.gui.member_design import _check_form, _quantity

    assert _quantity("3000").to_si() == pytest.approx(3.0)
    assert "not a length" in _check_form({"unbraced_length": "10 kg"})
    assert "not a unit" in _check_form({"unbraced_length": "10 foos"})


def test_a_form_reads_a_run_once(window, monkeypatch):
    """PANEL-13: choices rebuilt per keystroke read the stored run once."""
    from types import SimpleNamespace

    from puzzle.gui import result_tools
    from puzzle.post import derived
    from puzzle.results.store import ResultStore

    reads = []
    monkeypatch.setattr(derived, "run_for",
                        lambda *a: SimpleNamespace(run_id="r", mesh_id="m"))  # fmt: skip
    monkeypatch.setattr(ResultStore, "read_mesh", lambda self, m: reads.append(m) or "mesh")
    monkeypatch.setattr(ResultStore, "read_results", lambda self, r: "results")
    study = _study(window, "Tip load")
    for _ in range(5):
        assert result_tools._run_of(window, str(study.id)) == ("mesh", "results")
    assert reads == ["m"]


def test_the_sketch_canvas_follows_its_tab(window):
    """GUI-27: the Model tab shows the 3D view, the Geometry tab the sketch again."""
    pytest.importorskip("py_slvs")
    window.left_tabs.setCurrentWidget(window.workbench)
    window.workbench._start_sketch({"new_part": "Plate", "plane": "xy"})
    sketch = window.workbench.workspace
    assert window.centre.currentWidget() is sketch
    window.left_tabs.setCurrentWidget(window.tree)
    assert window.centre.currentWidget() is window.centre.widget(0)
    window.left_tabs.setCurrentWidget(window.workbench)
    assert window.centre.currentWidget() is sketch and window.context == "sketch"
    window.workbench.cancel_sketch()
    assert window.workbench.sketching is None
    assert window.centre.currentWidget() is window.centre.widget(0)


def test_escape_and_another_project_cancel_a_point_pick(window, tmp_path):
    """GUI-18: a tool waiting for a double-clicked point is told None and let go."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    got = []
    window.viewport.capture_point(got.append)
    window.viewport.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    assert got == [None] and window.viewport.capture is None
    window.viewport.capture_point(got.append)
    window.open(_copy(tmp_path, "bracket_assembly"))
    assert got == [None, None] and window.viewport.capture is None
