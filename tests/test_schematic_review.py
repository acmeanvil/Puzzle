"""The schematic after the design review (ADR 0090 package 13).

- SCH-1: a refresh builds once and evaluates nothing on the GUI thread; the operators'
  values are worked out on a worker, once per state of the project (`_value_key`).
  Done when: a refresh at 55 nodes is under 100 ms (`schematic_project.build`: the
  cantilever, its three runs and forty operators).
- SCH-3, SCH-UX-8: no out-of-date verdict is drawn as current (`schematic.value_look`
  decides how every value is drawn, on the canvas and in the SVG); "Bring up to date"
  runs what the selection needs, on request.
- SCH-2, SCH-4 to SCH-10, SCH-UX-1 to 3, 10, 15, UX-35: data wires removed, drops that
  never replace a wire unasked, text cut short by its own font, search, Properties'
  type, the palette from the keyboard, labels that follow their type, joints counted,
  several selected, Delete, copy and paste, sockets lit while dragging, a search on a
  drop, editing on the node, a folded group's checks summed, the side panel's help.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import schematic_project as sp  # noqa: E402

from puzzle import schematic as sch  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Feature,
    Operator,
    Physics,
    SweepParameter,
    SweepStep,
)
from puzzle.results import ResultStore  # noqa: E402
from puzzle.schematic import links  # noqa: E402
from puzzle.schematic.layout import layout  # noqa: E402


def _named(s, label):
    return next(n for n in s.nodes.values() if n.label == label)


def _stale_project(tmp_path):
    """The 55-node project with the Tip load doubled after its run: its checks read a
    run that is no longer current."""
    root, s = sp.build(tmp_path)
    comp = s.project.of_type(Component)[0]
    s.submit({"command": "edit", "object": str(comp.id), "ops": [
        {"op": "set", "path": "physics.0.features.1.values.y",
         "value": {"value": -200.0, "unit": "N"}}]})  # fmt: skip
    return root, s


def _everything(root, project):
    store = ResultStore(root / "results")
    return sch.build(project, store.runs(), root=root, store=store)


# --- SCH-1: one build, no evaluation on the GUI thread ---------------------------------


def test_a_build_without_evaluating_works_nothing_out(tmp_path, monkeypatch):
    from puzzle.operators import evaluate as ev
    from puzzle.project.status import status

    root, s = sp.build(tmp_path)
    store = ResultStore(root / "results")
    runs = store.runs()
    states = status(s.project, runs)  # the window's, before the panel builds
    calls = []
    real = ev.outputs
    monkeypatch.setattr(ev, "outputs", lambda ctx, op: calls.append(op.name) or real(ctx, op))
    built = sch.build(s.project, runs, states, root=root, store=store, evaluate=False)
    assert calls == [] and len(built.nodes) == 55
    ops = [n for n in built.nodes.values() if n.kind == "operator"]
    assert len(ops) == 40 and all(n.value_state == "working" for n in ops)
    sch.fill(built, sch.values(s.project, store, root=root))  # the worker's half
    assert calls and all(n.value_state == "current" for n in ops)
    assert _named(built, "Check 0").verdict == "pass"  # 1 mm against a 1 mm tip: passes


def test_a_build_hashes_the_project_once(tmp_path, monkeypatch):
    from puzzle.project import graph

    root, s = sp.build(tmp_path)
    store = ResultStore(root / "results")
    runs = store.runs()
    count = []
    real = graph.run_hashes
    monkeypatch.setattr(graph, "run_hashes", lambda *a, **k: count.append(1) or real(*a, **k))
    sch.build(s.project, runs, root=root, store=store)  # states, runs' states, values
    assert len(count) == 1  # was seven: status, one per results node, the operators'


# --- SCH-3: no out-of-date verdict drawn as current ------------------------------------


def test_no_out_of_date_verdict_is_drawn_as_current(tmp_path):
    from puzzle.schematic.layout import layout
    from puzzle.schematic.svg import MUTED, VERDICT_COLOURS, to_svg

    root, s = _stale_project(tmp_path)
    built = _everything(root, s.project)
    checks = [n for n in built.nodes.values() if n.label.startswith("Check ")]
    assert len(checks) == 10
    for n in checks:
        assert n.state == "out_of_date" and n.verdict == "pass"  # the old run's verdict
        look, text = sch.value_look(n)
        assert look == "stale" and "stale" in text  # muted, said, never green
    for n in built.nodes.values():  # the rule, for every node of every kind
        if n.value and n.state not in ("up_to_date", "complete"):
            assert sch.value_look(n)[0] not in ("pass", "fail"), n.label
    picture = to_svg(built, layout(built))
    assert VERDICT_COLOURS["pass"] not in picture  # no green verdict anywhere
    assert f'fill="{MUTED}" font-style="italic">Pass (stale' in picture


def test_current_values_keep_their_verdict_colour(tmp_path):
    root, s = sp.build(tmp_path)
    built = _everything(root, s.project)
    check = _named(built, "Check 0")
    assert check.state == "up_to_date" and sch.value_look(check) == ("pass", check.value)
    failing = _named(built, "Peak 0")  # a number: plain text
    assert sch.value_look(failing)[0] == "text"


def test_value_look_for_errors_and_work_in_progress():
    node = sch.Node("n", "operator", 4, "N", value="Pass, margin +5 %", verdict="pass")
    node.value_state = "working"
    assert sch.value_look(node)[0] == "working"
    node.value_state, node.value = "error", "a text can't be used where a number is needed"
    assert sch.value_look(node)[0] == "error"
    node.state, node.value = "not_run", "study 'S' has no run yet"  # waiting, not wrong
    assert sch.value_look(node) == ("not_run", "study 'S' has no run yet")
    node.state = "complete"
    node.value_state, node.value, node.state = "current", "Pass, margin +5 %", "failed"
    assert sch.value_look(node) == ("stale", "Pass, margin +5 % (out of date)")


def test_a_folded_group_of_checks_says_how_many_fail(tmp_path):
    root, s = sp.build(tmp_path)
    ids = [str(o.id) for o in s.project.of_type(Operator) if o.name.startswith("Check ")]
    s.submit({"command": "edit", "object": ids[0], "ops": [  # 0.0001 mm: fails
        {"op": "set", "path": "inputs.limit", "value": {"value": {"value": 1e-4,
                                                                  "unit": "mm"}}}]})  # fmt: skip
    s.submit({"command": "create", "kind": "group", "data": {
        "name": "Checks", "section": "Operators", "members": ids}})  # fmt: skip
    group = next(o for o in s.project.objects.values() if o.kind == "group")
    store = ResultStore(root / "results")
    built = sch.build(s.project, store.runs(), root=root, store=store,
                      folded=frozenset({str(group.id)}))  # fmt: skip
    node = built.nodes[f"group:{group.id}"]
    assert node.value == "10 checks: 1 fail" and sch.value_look(node)[0] == "fail"


# --- SCH-UX-4: a layout that holds at 55 nodes -----------------------------------------


def test_limits_sit_beside_their_checks_and_nothing_overlaps(tmp_path):
    root, s = sp.build(tmp_path)
    built = _everything(root, s.project)
    allowed = [n for n in built.nodes.values() if n.label.startswith("Allowed ")]
    assert {n.column for n in allowed} == {sch.RESULTS}  # was Resources, three columns off
    lay = layout(built)
    boxes = [(*lay.positions[k], *lay.sizes[k]) for k in built.nodes]
    for i, (x, y, w, h) in enumerate(boxes):
        for x2, y2, w2, h2 in boxes[i + 1 :]:
            assert not (x < x2 + w2 and x2 < x + w and y < y2 + h2 and y2 < y + h)
    x0, _y0, x1, _y1 = lay.bounds()
    assert x1 - x0 < 8 * 400  # at most eight layers across


def test_a_parameter_the_model_reads_stays_in_resources(tmp_path):
    root = sp.copy(tmp_path)
    s = Session(root)
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "load", "type": "number",
        "inputs": {"value": {"value": {"value": 150, "unit": "N"}}}}})  # fmt: skip
    comp = s.project.of_type(Component)[0]
    s.submit({"command": "edit", "object": str(comp.id), "ops": [
        {"op": "set", "path": "physics.0.features.1.values.y",
         "value": {"value": -100.0, "unit": "N", "parameter": "load"}}]})  # fmt: skip
    s.submit({"command": "create", "kind": "operator", "data": {  # read by a check too
        "name": "C", "type": "check", "inputs": {"limit": {"source": str(next(
            o for o in s.project.of_type(Operator)).id)}}}})  # fmt: skip
    assert _named(sch.build(s.project), "load").column == sch.RESOURCES


def test_a_node_shows_its_wired_inputs_and_one_spare(tmp_path):
    """SCH-UX-5: a Report showed Item A to Item H; a Formula x, y and z."""
    root, s = sp.build(tmp_path, chains=1)
    check = next(o for o in s.project.of_type(Operator) if o.name == "Check 0")
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Rep", "type": "report", "inputs": {"a": {"source": str(check.id)}}}})  # fmt: skip
    built = sch.build(s.project)
    report = _named(built, "Rep")
    assert [p.key for p in report.inputs()] == ["a", "b"]
    assert not any(p.key == "use" for p in report.ports)  # no "Use in…" on an operator
    assert [p.key for p in _named(built, "Twice 0").inputs()] == ["x", "y"]


# --- SCH-9, SCH-10 ---------------------------------------------------------------------


def test_an_output_label_follows_its_type():
    from puzzle.model.schema import OperatorInput, Project, Quantity

    p = Project("p")
    a = p.add(Operator(name="A", type="number", inputs={
        "value": OperatorInput(value=Quantity(value=300, unit="MPa"))}))  # fmt: skip
    sf = p.add(Operator(name="SF", type="safety_factor", inputs={
        "a": OperatorInput(source=a.id), "b": OperatorInput(value=Quantity(
            value=200, unit="MPa"))}))  # fmt: skip
    port = sch.build(p).nodes[str(sf.id)].outputs()[0]
    assert port.type == "number" and port.label == "Number"  # was "Curve"


def test_joints_are_counted_and_twin_load_titles_say_their_component(assembly):
    s = sch.build(assembly)
    comp = _named(s, "Bracket on plate")
    assert comp.badge == "1 contact"
    base = assembly.of_type(Component)[0]
    held = Physics(
        type="solid_mechanics", features=[Feature(type="fixed", selection="Plate/Underside")]
    )
    twin = Component(name="Copy", parts=base.parts, mesh=base.mesh, physics=[held])
    assembly.add(twin)
    s = sch.build(assembly)
    titles = [n for n in s.nodes.values() if n.label == "Fixed on Plate/Underside"]
    assert len(titles) == 2
    assert {n.subtitle.split(" · ")[-1] for n in titles} == {"Bracket on plate", "Copy"}


# --- SCH-2: data wires removed, each one command --------------------------------------


def _wire(built, target_label, port):
    node = _named(built, target_label)
    return node, next(w for w in built.wires if w.target == node.id and w.target_port == port)


def test_each_kind_of_data_wire_is_removed_by_one_undoable_command(tmp_path):
    root = sp.copy(tmp_path)
    s = Session(root)
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "load", "type": "number",
        "inputs": {"value": {"value": {"value": 150, "unit": "N"}}}}})  # fmt: skip
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "loads", "type": "range", "inputs": {
            "start": {"value": {"value": 1, "unit": "N"}},
            "end": {"value": {"value": 3, "unit": "N"}},
            "count": {"value": {"value": 3, "unit": "dimensionless"}}}}})  # fmt: skip
    p = s.project
    load = next(o for o in p.of_type(Operator) if o.name == "load")
    loads = next(o for o in p.of_type(Operator) if o.name == "loads")
    comp = p.of_type(Component)[0]
    tip = sp.study(p, "Tip load")
    s.submit({"command": "edit", "object": str(comp.id), "ops": [
        {"op": "set", "path": "physics.0.features.1.values.y",
         "value": {"value": -100.0, "unit": "N", "parameter": "load"}}]})  # fmt: skip
    steps = [SweepStep(parameters=[SweepParameter(name="F", source=loads.id)])]
    steps = [x.model_dump(mode="json") for x in steps + list(tip.steps)]
    s.submit({"command": "edit", "object": str(tip.id), "ops": [
        {"op": "set", "path": "steps", "value": steps}]})  # fmt: skip
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Twice", "type": "expression", "expression": "2 * x",
        "inputs": {"x": {"source": str(load.id)}}}})  # fmt: skip
    s.submit({"command": "create", "kind": "transfer", "data": {
        "name": "Heat in", "source": str(sp.study(s.project, "Conduction").id),
        "target": str(tip.id), "field": "temperature", "role": "load"}})  # fmt: skip

    def removed(label, port, owner_path=""):
        built = sch.build(s.project)
        node, w = _wire(built, label, port)
        cmds, words = links.data_wire_removal(s.project, w, node.object, node.path)
        assert cmds, words
        before = s.project.objects[UUID(node.object)].model_dump(mode="json")
        s.submit_batch(cmds) if len(cmds) > 1 else s.submit(cmds[0])
        after = sch.build(s.project)
        assert not any(x.target_port == port for x in after.wires)
        s.undo()
        assert s.project.objects[UUID(node.object)].model_dump(mode="json") == before
        return words

    assert removed("Twice", "x") == "Unwire x"
    assert removed("Tip load", "sweep:0:0") == "Stop sweeping F"
    assert removed("Force on Bar/Tip", "value:y") == "y stops following load"
    transfer = next(o for o in s.project.objects.values() if o.kind == "transfer")
    built = sch.build(s.project)
    node, w = _wire(built, "Tip load", f"transfer:{transfer.id}")
    cmds, words = links.data_wire_removal(s.project, w, node.object, node.path)
    assert words == "Remove the transfer" and cmds[0]["command"] == "delete"
    built = sch.build(s.project)
    unbound = links.data_wire_removal(s.project, sch.Wire("a", "b", "data", "", "arg:0:dx"),
                                      str(sp.study(s.project, "Tip load").id))  # fmt: skip
    assert unbound[0] == [] and "history step" in unbound[1]


# --- copy and paste (SCH-UX-1) ---------------------------------------------------------


def test_copies_keep_their_chain_and_what_they_read(tmp_path):
    from puzzle.operators import clusters

    root, s = sp.build(tmp_path, chains=1)
    p = s.project
    chain = [o.id for o in p.of_type(Operator) if o.name in ("Peak 0", "Check 0", "Twice 0")]
    cmds, new = clusters.paste_commands(p, clusters.copied(p, chain))
    s.submit_batch(cmds)
    made = {o.name: o for o in s.project.of_type(Operator)}
    assert {"Peak 0 copy", "Check 0 copy", "Twice 0 copy"} <= set(made)
    check = made["Check 0 copy"]
    assert check.inputs["value"].source == made["Peak 0 copy"].id  # inside: the copy
    assert check.inputs["limit"].source == made["Allowed 0"].id  # outside: kept
    assert made["Peak 0 copy"].inputs["result"].source == sp.study(p, "Tip load").id
    s.undo()
    assert "Peak 0 copy" not in {o.name for o in s.project.of_type(Operator)}


# --- in the window ---------------------------------------------------------------------

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _settle(app, w, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while w.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    from conftest import delete_windows

    from puzzle.gui.window import MainWindow

    root, _s = sp.build(tmp_path)
    w = MainWindow(root)
    w.left_tabs.setCurrentWidget(w.schematic_side)
    w.ask_yes = lambda *_a: True
    w.schematic.wait()
    yield w
    _settle(app, w)
    w.close()
    delete_windows(app)


def _item(w, label):
    return next(i for i in w.schematic.items.values() if i.node.label == label)


def test_a_refresh_at_55_nodes_is_under_100_ms(window, app):
    """Done when (ADR 0090 package 13). Measured is what a refresh does on the GUI
    thread before painting: the one build (no evaluation) and the layout, the median of
    five, so a slow CI machine is held to the work and not to its screen. Before the
    package the same build took 114 ms and the panel's refresh 205 ms here; now about 5
    and 15 ms."""
    w = window
    panel = w.schematic
    assert len(panel.schematic.nodes) == 55
    store, project = w._store(), w.session.project
    runs = store.runs()
    times = []
    for _ in range(5):
        start = time.perf_counter()
        built = sch.build(project, runs, w.states, root=w.session.root, store=store,
                          keys=w.run_keys, evaluate=False)  # fmt: skip
        layout(built)
        times.append(time.perf_counter() - start)
    assert statistics.median(times) < 0.100, times
    builds, evaluations = panel.builds, panel.evaluations
    whole = []
    for _ in range(5):  # nothing changed: one build each, nothing worked out again
        start = time.perf_counter()
        panel.refresh()
        whole.append(time.perf_counter() - start)
    assert panel.builds == builds + 5 and panel.evaluations == evaluations
    print(f"build+layout {statistics.median(times) * 1000:.1f} ms, "
          f"panel.refresh {statistics.median(whole) * 1000:.1f} ms")  # fmt: skip


def test_values_are_worked_out_on_a_worker_once_per_change(window, app, monkeypatch):
    w = window
    panel = w.schematic
    where = []
    real = sch.values
    monkeypatch.setattr(sch, "values", lambda *a, **k: where.append(
        threading.current_thread() is threading.main_thread()) or real(*a, **k))  # fmt: skip
    before = panel.evaluations
    op = next(o for o in w.session.project.of_type(Operator) if o.name == "Allowed 0")
    w.submit({"command": "edit", "object": str(op.id), "ops": [
        {"op": "set", "path": "inputs.value", "value": {"value": {"value": 1e-4,
                                                                  "unit": "mm"}}}]})  # fmt: skip
    assert _item(w, "Check 0").node.value_state == "working"  # drawn, not yet worked out
    assert sch.value_look(_item(w, "Check 0").node)[0] == "working"  # never a verdict
    panel.wait()
    assert where == [False] and panel.evaluations == before + 1
    assert _item(w, "Check 0").node.verdict == "fail"
    panel.refresh()  # the same project: the values kept, nothing worked out
    assert panel.evaluations == before + 1 and where == [False]


def test_a_stale_check_is_drawn_muted_with_a_hatched_title(window, app):
    from PySide6.QtCore import Qt

    from puzzle.gui.schematic_view import _title_tint

    w = window
    comp = w.session.project.of_type(Component)[0]
    w.submit({"command": "edit", "object": str(comp.id), "ops": [
        {"op": "set", "path": "physics.0.features.1.values.y",
         "value": {"value": -200.0, "unit": "N"}}]})  # fmt: skip
    w.schematic.wait()
    node = _item(w, "Check 3").node
    look, _text = sch.value_look(node)
    assert look == "stale" and _title_tint(node, look).style() == Qt.BrushStyle.BDiagPattern
    assert "stale" in _item(w, "Check 3").toolTip()  # the whole value, as drawn


def test_bring_up_to_date_runs_what_the_selection_needs(window, app):
    w = window
    ran = []
    w.run_studies = lambda ids: ran.append(list(ids))
    panel = w.schematic
    assert panel.bring_up_to_date() == [] and ran == []  # everything current
    comp = w.session.project.of_type(Component)[0]
    w.submit({"command": "edit", "object": str(comp.id), "ops": [
        {"op": "set", "path": "physics.0.features.1.values.y",
         "value": {"value": -200.0, "unit": "N"}}]})  # fmt: skip
    panel.wait()
    panel.chosen(_item(w, "Check 1").node)
    tip = str(sp.study(w.session.project, "Tip load").id)
    assert panel.bring_up_to_date() == [tip] and ran == [[tip]]


def test_several_selected_go_with_one_delete_and_one_undo(window, app):
    from PySide6.QtCore import Qt

    w = window
    panel = w.schematic
    panel.chosen(_item(w, "Twice 0").node)
    panel.chosen(_item(w, "Twice 1").node, add=True)
    _item(w, "Twice 2").setSelected(True)  # as a rubber band chooses
    assert set(panel.selection()) == {str(o.id) for o in w.session.project.of_type(Operator)
                                      if o.name in ("Twice 0", "Twice 1", "Twice 2")}  # fmt: skip
    assert panel.key((Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    names = {o.name for o in w.session.project.of_type(Operator)}
    assert not names & {"Twice 0", "Twice 1", "Twice 2"} and "Twice 3" in names
    w.undo()
    assert {"Twice 0", "Twice 1", "Twice 2"} <= {
        o.name for o in w.session.project.of_type(Operator)
    }
    panel.key((Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier))
    assert len(panel.selection()) == 55


def test_ctrl_c_and_ctrl_v_paste_a_chain_beside_it(window, app):
    from PySide6.QtCore import Qt

    w = window
    panel = w.schematic
    panel.chosen(_item(w, "Peak 4").node)
    panel.chosen(_item(w, "Check 4").node, add=True)
    x0, y0 = _item(w, "Peak 4").pos().x(), _item(w, "Peak 4").pos().y()  # where it was
    assert panel.key((Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier))
    assert panel.key((Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))
    ops = {o.name: o for o in w.session.project.of_type(Operator)}
    assert ops["Check 4 copy"].inputs["value"].source == ops["Peak 4 copy"].id
    panel.wait()
    assert {panel.schematic.nodes[n].label for n in panel.selection()} == {
        "Peak 4 copy",
        "Check 4 copy",
    }  # the copies chosen, beside the originals as they were when copied
    copy = _item(w, "Peak 4 copy").pos()
    assert (copy.x(), copy.y()) == pytest.approx((x0 + 40, y0 + 40), abs=0.2)


def test_a_data_wire_is_removed_from_its_menu(window, app):
    w = window
    panel = w.schematic
    target = _item(w, "Twice 5")
    item = next(x for x in panel.wires if x.b is target and x.wire.kind == "data")
    band = item.shape().boundingRect()  # a 10 px band to hit, around a thin wire
    assert (
        min(band.height(), band.width()) >= 9.5
        and item.flags() & item.GraphicsItemFlag.ItemIsSelectable
    )
    menu = panel.wire_menu(item)
    first = menu.actions()[0]
    assert first.text() == "Unwire x" and first.isEnabled()
    first.trigger()
    op = next(o for o in w.session.project.of_type(Operator) if o.name == "Twice 5")
    assert "x" not in op.inputs
    w.undo()
    op = next(o for o in w.session.project.of_type(Operator) if o.name == "Twice 5")
    assert op.inputs["x"].source is not None


def test_a_wire_let_go_off_a_row_takes_an_open_input_and_never_replaces_one(window, app):
    from puzzle.gui import operators as gops

    w = window
    panel = w.schematic
    check_id = gops.new_operator(w, "check")  # nothing wired
    panel.wait()

    def drop(source_label: str) -> None:  # onto the check's title, off any input's row
        source, target = _item(w, source_label), panel.items[check_id]
        title = target.mapToScene(target.boundingRect().topLeft())
        title.setX(title.x() + 30)
        title.setY(title.y() + 10)
        panel.start_wire(source, "out")
        panel.end_wire(title)

    peak, allowed = (UUID(_item(w, n).node.object) for n in ("Peak 6", "Allowed 6"))
    drop("Peak 6")  # the first open input that takes a number
    check = w.session.project.objects[UUID(check_id)]
    assert check.inputs["value"].source == peak
    drop("Allowed 6")  # Value is taken: Limit, the open one
    check = w.session.project.objects[UUID(check_id)]
    assert check.inputs["value"].source == peak and check.inputs["limit"].source == allowed
    seq = len(w.session.history(limit=1000))
    drop("Allowed 7")  # both set: refused, nothing replaced
    assert len(w.session.history(limit=1000)) == seq
    assert "let go on its row" in w.statusBar().currentMessage()


def test_sockets_that_take_a_dragged_wire_are_lit(window, app):
    w = window
    panel = w.schematic
    peak = _item(w, "Peak 2")
    panel.start_wire(peak, "out")
    check = _item(w, "Check 8").node.id
    assert panel.socket_lit(check, "value", "in") is True
    assert panel.socket_lit(check, "limit", "in") is True  # a number takes a number
    twice = _item(w, "Twice 8").node.id
    assert panel.socket_lit(twice, "x", "in") is True
    component = _item(w, "Cantilever").node.id
    assert panel.socket_lit(component, "component", "in") is False  # dimmed
    run = _item(w, "Tip load: results")
    panel.end_wire(run.sceneBoundingRect().center())
    assert panel.socket_lit(check, "value", "in") is None  # drawn as ever again


def test_a_wire_let_go_on_nothing_opens_the_palette_with_what_fits(window, app):
    from PySide6.QtCore import QPointF

    w = window
    panel = w.schematic
    run = _item(w, "Tip load: results")
    panel.start_wire(run, "result")
    far = run.sceneBoundingRect().bottomRight() + QPointF(3000, 3000)
    palette = panel.end_wire(far)
    try:
        assert palette is not None and palette.source == str(sp.study(
            w.session.project, "Tip load").id)  # fmt: skip
        texts = [palette.list.item(i).text() for i in range(palette.list.count())]
        assert texts[0] == "Fits here"
        made = palette.choose("reduce")
        op = w.session.project.objects[UUID(made)]
        assert op.inputs["result"].source == UUID(palette.source)
        assert palette.at == (far.x(), far.y())  # made where the wire was let go
    finally:
        palette.close()


def test_a_typed_limit_is_edited_on_its_node(window, app):
    from puzzle.gui import operators as gops

    w = window
    panel = w.schematic
    check_id = gops.new_operator(w, "check")
    panel.wait()
    item = panel.items[check_id]
    row = next(r for r in sch.layout.rows(item.node) if r.kind == "in" and r.key == "limit")
    assert panel.edit_on_node(item, row)
    editor = panel.inline
    editor.setText("235 MPa")
    editor.returnPressed.emit()
    check = w.session.project.objects[UUID(check_id)]
    assert check.inputs["limit"].value.value == 235 and check.inputs["limit"].value.unit == "MPa"
    w.undo()
    check = w.session.project.objects[UUID(check_id)]
    assert "limit" not in check.inputs or check.inputs["limit"].value is None
    twice = _item(w, "Twice 9")
    value = next(r for r in sch.layout.rows(twice.node) if r.kind == "value")
    assert panel.edit_on_node(twice, value)
    panel.inline.setText("3 * x")
    panel.inline.returnPressed.emit()
    op = next(o for o in w.session.project.of_type(Operator) if o.name == "Twice 9")
    assert op.expression == "3 * x"


def test_search_finds_values_and_states_and_steps_through_them(window, app):
    w = window
    panel = w.schematic
    panel.chosen(_item(w, "Cantilever").node)
    panel.search.setText("pass")  # in a check's value, not its name
    found = panel.matches()
    assert len(found) == 10 and all(panel.schematic.nodes[n].label.startswith("Check")
                                    for n in found)  # fmt: skip
    assert panel.found.text() == "10 found"
    assert all(_item(w, f"Check {i}").opacity() == 1 for i in range(10))  # not the chain's
    first = panel._go_to_found(back=False)
    second = panel._go_to_found(back=False)
    assert first != second and panel.found.text() == "2 of 10"
    assert panel._go_to_found(back=True) == first
    panel.search.setText("result summary")  # an operator's type
    assert len(panel.matches()) == 10
    panel.search.setText("up to date")  # a state
    assert panel.matches()


def test_properties_show_an_operator_type_by_its_name_not_editable(window, app):
    w = window
    op = next(o for o in w.session.project.of_type(Operator) if o.name == "Peak 0")
    w.select_in_tree(("object", str(op.id)))
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    it = QTreeWidgetItemIterator(w.properties)
    rows = {}
    while it.value():
        rows[it.value().text(0)] = it.value()
        it += 1
    assert rows["Type"].text(1) == "Result summary"
    assert w.properties.itemWidget(rows["Type"], 1) is None  # no dropdown


def test_the_palette_is_used_from_the_keyboard_and_finds_initials(window, app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    from puzzle.gui import operators as gops

    w = window
    palette = gops.open_palette(w)
    try:
        palette.search.setText("res sum")  # word starts: Result summary first
        assert palette.list.currentItem().data(gops.ROLE) == "reduce"
        before = palette.list.currentRow()
        app.sendEvent(palette.search, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down,
                                                Qt.KeyboardModifier.NoModifier))  # fmt: skip
        assert palette.list.currentRow() != before
        assert palette.search.text() == "res sum"  # the box keeps its text
    finally:
        palette.close()


def test_what_fits_comes_ranked_and_capped(window, app):
    from puzzle.gui import operators as gops

    w = window
    tip = str(sp.study(w.session.project, "Tip load").id)
    fits = gops.fitting(w, tip, "result")
    assert fits[0] == "reduce" or gops.TYPES[fits[0]].inputs[0].types[0] == "result"
    palette = gops.open_palette(w, None, tip, "result")
    try:
        heads = [i for i in range(palette.list.count()) if not palette.list.item(i).data(gops.ROLE)]
        assert heads[1] - heads[0] - 1 == min(len(fits), gops.FITS_SHOWN)
    finally:
        palette.close()


def test_the_side_panel_is_a_help_button_not_a_page_of_text(window, app):
    w = window
    side = w.schematic_side
    assert side.help_button.text() == "?"
    shown = side.open_help()
    try:
        assert shown is not None
    finally:
        shown.close()
    from PySide6.QtWidgets import QLabel

    words = sum(len(label.text().split()) for label in side.findChildren(QLabel))
    assert words < 40  # was five paragraphs


def test_keys_typed_on_a_node_stay_in_its_edit(window, app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from puzzle.gui import operators as gops

    w = window
    panel = w.schematic
    check_id = gops.new_operator(w, "check")
    panel.wait()
    item = panel.items[check_id]
    row = next(r for r in sch.layout.rows(item.node) if r.kind == "in" and r.key == "limit")
    panel.chosen(item.node)
    assert panel.edit_on_node(item, row)
    seq = len(w.session.history(limit=1000))
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Delete,
                         Qt.KeyboardModifier.NoModifier)  # fmt: skip
    assert panel.canvas.event(override)  # kept from the window's Delete
    panel.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                         Qt.KeyboardModifier.NoModifier))  # fmt: skip
    assert check_id in panel.items and panel.inline is not None  # nothing deleted
    panel.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                         Qt.KeyboardModifier.NoModifier))  # fmt: skip
    assert panel.inline is None and len(w.session.history(limit=1000)) == seq


def test_the_side_panel_says_what_a_change_would_cost(window, app):
    w = window
    w.schematic.chosen(_item(w, "Cantilever").node)
    text = w.schematic_side.details.text()
    assert "A change here puts 3 studies out of date (Conduction, last solve 1 s;" in text
    assert "30 operators worked out again" in text  # the summaries, checks, formulas
    w.schematic.chosen(_item(w, "Check 0").node)
    assert "A change here" not in w.schematic_side.details.text()  # nothing reads it


def test_canvas_keys_reach_the_canvas_before_the_windows_shortcuts(window, app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    w = window
    canvas = w.schematic.canvas
    event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Delete,
                      Qt.KeyboardModifier.NoModifier)  # fmt: skip
    assert canvas.event(event) and event.isAccepted()
    other = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Z,
                      Qt.KeyboardModifier.ControlModifier)  # fmt: skip
    canvas.event(other)
    assert not other.isAccepted()  # Undo stays the window's
