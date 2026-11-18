"""Operators that can't take the project down, and that say which run they read (ADR
0090 package 4).

- OP-1, OP-5: whatever goes wrong while an operator is worked out is that operator's
  error (code `operator_failed` when it was not one already), never a raw exception:
  the project opens, the schematic, the outline and the CLI draw, the problem is listed,
  and only a study whose model reads a failed parameter is refused at run.
- OP-4, decision 4: a wire from a study reads the study's run for its model now, a
  named sweep point, a shoot-out solver or one stored run; never "whichever ran last".
  Without a current run the newest is read and marked stale, everywhere it is shown.
- OP-6, OP-7: a check compares like with like, and its margin never contradicts it.
- OP-8 to OP-17, ADR-51: wrong ports, a report's summary, runaway tables, clamped
  steps, meaningless settings, missing units, unhelpful texts, unbounded constant
  folding, the shipped steel's yield.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_operators import Store, wire

from puzzle import operators
from puzzle.commands import Session
from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Operator,
    OperatorInput,
    Project,
    Quantity,
    Shootout,
    Study,
    SweepParameter,
    SweepStep,
)
from puzzle.operators import evaluate as ev
from puzzle.project import load_project
from puzzle.project.graph import run_hashes

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
MPA = 1e6


def copy(tmp_path) -> Path:
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl"))  # fmt: skip
    return root


def q(v, unit="dimensionless"):
    return OperatorInput(value=Quantity(value=v, unit=unit))


def heat(p):
    return p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))


# --- OP-1: a before-solving formula that raises ----------------------------------------

BROKEN = ["10.0**400", "'a' * 3", "1 m + 1 s", "min()", "2**2**2**2**2", "1/0"]


@pytest.mark.parametrize("text", BROKEN)
def test_a_formula_that_raises_is_its_own_problem(tmp_path, text, capsys):
    from puzzle import schematic
    from puzzle.cli import main
    from puzzle.project.status import status

    root = copy(tmp_path)
    s = Session(root)
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Big", "type": "expression", "expression": text}})  # fmt: skip
    s.submit({"command": "create", "kind": "operator", "data": {  # reads it
        "name": "Twice", "type": "expression", "expression": "2 * x",
        "inputs": operators.inputs_from(s.project, {"x": {"source": "Big"}})}})  # fmt: skip
    p = load_project(root)  # it opens
    assert "Big" not in operators.parameter_values(p)
    problems = {x.object: x for x in p.problems()}
    assert problems["Big"].code == "operator_failed"
    assert "failed" in problems["Twice"].message and "'Big'" in problems["Twice"].message
    run_hashes(p)
    status(p, [])
    built = schematic.build(p)
    big = next(n for n in built.nodes.values() if n.label == "Big")
    assert big.value and not big.value.startswith("'Big'")  # its error, in words
    for args in (
        ["outline", str(root)],
        ["operators", str(root)],
        ["operators", str(root), "--json"],
        ["schematic", str(root), "--json"],
    ):
        assert main(args) == 0, args
    capsys.readouterr()


def test_only_a_study_reading_a_failed_parameter_is_refused(tmp_path):
    from test_run_keys import _store_fake_run

    from puzzle.model.schema import Component, Geometry, HistoryStep, MeshRecipe, PartInstance
    from puzzle.solve import StudyError, run_study

    p = Project("p")
    p.add(Operator(name="width", type="expression", expression="10.0**400 * 1 m"))
    p.add(Operator(name="Lonely", type="check"))  # half wired, read by nothing
    mesh = p.add(MeshRecipe(name="M"))

    def study(name, dx):
        geo = p.add(Geometry(name=f"{name} plate", history=[HistoryStep(
            kind="box", name="B", args={"dx": dx, "dy": 0.01, "dz": 0.01})]))  # fmt: skip
        comp = p.add(Component(name=f"{name} part", mesh=mesh.id, parts=[
            PartInstance(name="Plate", geometry=geo.id)]))  # fmt: skip
        return p.add(Study(name=name, component=comp.id, solver="calculix", steps=[]))

    reads, other = study("Reads", "width"), study("Other", 0.02)
    assert ["too large" in e.message for e in operators.failures_read(p, reads)] == [True]
    assert operators.failures_read(p, other) == []
    root = tmp_path / "p"
    root.mkdir()
    with pytest.raises(StudyError) as exc:
        run_study(p, reads.id, root, tmp_path / "work")
    assert exc.value.code == "operator_failed" and "too large" in exc.value.message
    store = _store_fake_run(root, other, run_hashes(p)[other.id]["calculix"][0])
    assert run_study(p, other.id, root, tmp_path / "work", store=store).cached


def test_a_project_whose_formula_raises_opens_in_the_window(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    root = copy(tmp_path)
    Session(root).submit({"command": "create", "kind": "operator", "data": {
        "name": "Big", "type": "expression", "expression": "10.0**400"}})  # fmt: skip
    QApplication.instance() or QApplication([])
    w = MainWindow(root)
    try:
        w.refresh()
        w.schematic.refresh()
        node = next(i.node for i in w.schematic.items.values() if i.node.label == "Big")
        assert "too large" in node.value
    finally:
        w.close()


# --- OP-5: after-solving operators that raised -----------------------------------------


def _table_op(p, study, name, **kw):
    t = p.add(Operator(name=f"{name} table", type="result_table",
                       inputs={"result": wire(study, "result")}))  # fmt: skip
    return p.add(Operator(name=name, inputs={"table": wire(t)}, **kw))


SETTINGS = [
    ("mean of a text column", dict(type="to_number", column="part", reduction="mean"), "text"),
    ("part > 3", dict(type="filter_rows", condition="part > 3"), "text"),
    (
        "a polars condition that isn't one",
        dict(type="filter_rows", syntax="polars", condition='pl.col("temperature") * 2'),
        "true or false",
    ),
    (
        "a polars column of a tower",
        dict(
            type="new_column",
            syntax="polars",
            new_name="u",
            expression='pl.col("temperature") * 7**7**7',
        ),
        "too large",
    ),
    ("read off a text column", dict(type="lookup", by=["part"], column="temperature"), "text"),
]


@pytest.mark.parametrize(("what", "settings", "words"), SETTINGS, ids=[s[0] for s in SETTINGS])
def test_a_table_setting_that_raised_says_why(what, settings, words):
    from puzzle import schematic

    p = Project("p")
    study = heat(p)
    store = Store(study.id, project=p)
    op = _table_op(p, study, "Op", **settings)
    text, _ = ev.shown(ev.Context(p, store), op)
    assert words in text, text
    schematic.build(p, store=store)


LISTS = [
    ("max of an empty list", dict(operation="max"), "[]", "empty"),
    ("replace past the end", dict(operation="replace"), "[1, 2, 3]", "No item 7"),
    ("percentile 150", dict(operation="percentile"), "[1, 2, 3]", "0 to 100"),
]


@pytest.mark.parametrize(("what", "settings", "values", "words"), LISTS,
                         ids=[s[0] for s in LISTS])  # fmt: skip
def test_a_list_setting_that_raised_says_why(what, settings, values, words):
    p = Project("p")
    count = 0 if values == "[]" else 3
    lst = p.add(Operator(name="L", type="range", inputs={
        "start": q(1), "end": q(3), "count": q(count)}))  # fmt: skip
    other = p.add(Operator(name="O", type="range", inputs={
        "start": q(9), "end": q(9), "count": q(1)}))  # fmt: skip
    index = 150 if settings["operation"] == "percentile" else 7
    op = p.add(Operator(name="Op", type="list", inputs={
        "list": wire(lst), "index": q(index), "other": wire(other)}, **settings))  # fmt: skip
    text, _ = ev.shown(ev.Context(p, None), op)
    assert words in text, text


def test_a_raw_exception_anywhere_is_the_operators_error(monkeypatch):
    p = Project("p")
    n = p.add(Operator(name="N", type="number", inputs={"value": q(2, "mm")}))
    reader = p.add(Operator(name="R", type="expression", expression="x",
                            inputs={"x": wire(n)}))  # fmt: skip

    def boom(ctx, op):
        raise KeyError("no such thing")

    monkeypatch.setitem(ev._EVALUATE, "number", boom)
    with pytest.raises(PuzzleError) as exc:
        ev.value(ev.Context(p, None), n)
    assert exc.value.code == "operator_failed"
    with pytest.raises(PuzzleError) as exc:
        ev.value(ev.Context(p, None), reader)
    assert exc.value.code == "operator_failed" and "input, 'N', failed" in exc.value.message
    assert ev.summary(ev.Context(p, None), reader)["error"]["code"] == "operator_failed"
    failed = {}
    operators.parameter_values(p, failed=failed)
    assert set(failed) == {"N", "R"}

    def stop(ctx, op):
        raise KeyboardInterrupt

    monkeypatch.setitem(ev._EVALUATE, "number", stop)
    with pytest.raises(KeyboardInterrupt):  # never caught
        ev.value(ev.Context(p, None), n)


# --- OP-4: which run is read -------------------------------------------------------------


def swept(p):
    return p.add(Study(name="Plates", component=uuid4(), solver="calculix", steps=[
        SweepStep(parameters=[SweepParameter(name="t", values=[
            Quantity(value=v, unit="mm") for v in (1, 2, 4)])])]))  # fmt: skip


def test_a_swept_study_is_read_at_a_named_point_never_the_last():
    p = Project("p")
    plates = swept(p)
    store = Store(uuid4(), swept=plates.id, project=p)
    hot = p.add(Operator(name="Hot", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(plates, "result")}))  # fmt: skip
    text, _ = ev.shown(ev.Context(p, store), hot)
    assert "3 sweep points" in text and "choose" in text
    for title, peak in (("t = 1 mm", 1.0), ("t = 2 mm", 0.5), ("t = 4 mm", 0.25)):
        hot.inputs["result"] = OperatorInput(source=plates.id, port="result", point=title)
        got = ev.value(ev.Context(p, store), hot)
        assert got.value == pytest.approx(peak)
    hot.inputs["result"] = OperatorInput(source=plates.id, port="result", point="t = 3 mm")
    text, _ = ev.shown(ev.Context(p, store), hot)
    assert "no run at t = 3 mm" in text
    names = [label for label, _ in operators.run_choices(p, store, plates.id)]
    assert "Point: t = 2 mm" in names and names[0].startswith("Its run")


def test_a_stale_run_is_read_and_says_so_everywhere(tmp_path):
    from puzzle import schematic
    from puzzle.operators import report

    p = Project("p")
    study = heat(p)
    stale_store = Store(study.id)  # its run's key is not the study's now
    hot = p.add(Operator(name="Hot", type="reduce", quantity="stress:von_mises",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    check = p.add(Operator(name="Strength", type="check", inputs={
        "value": wire(hot), "limit": q(400, "MPa")}))  # fmt: skip
    ctx = ev.Context(p, stale_store)
    assert ev.value(ctx, hot).value == pytest.approx(300 * MPA)  # still usable
    text, verdict = ev.shown(ctx, check)
    assert verdict == "pass" and text.startswith("Pass (stale: from the run of Heat")
    assert "stale" in ev.shown(ctx, hot)[0]
    data = ev.summary(ev.Context(p, stale_store), check)
    assert data["stale"] is True and data["runs"][0]["stale"] is True
    assert "stale" in data["outputs"]["out"]
    built = schematic.build(p, store=stale_store)
    node = built.nodes[str(check.id)]
    assert node.state != "up_to_date" and "stale" in node.value
    rep = p.add(Operator(name="Rep", type="report", inputs={"a": wire(check), "b": wire(hot)}))
    page = report.write(ev.Context(p, stale_store), rep, tmp_path)[0].read_text(encoding="utf-8")
    assert "on runs no longer current" in page and page.count("tale: from the run of") >= 2
    # The same run, current: no word of staleness.
    fresh = Store(study.id, project=p)
    text, _ = ev.shown(ev.Context(p, fresh), check)
    assert text.startswith("Pass, margin") and "stale" not in text
    assert ev.summary(ev.Context(p, fresh), check)["stale"] is False


def test_a_pinned_run_and_a_shootout_solver():
    from dataclasses import replace

    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[],
                        shootout=Shootout(solvers=["code_aster"])))  # fmt: skip
    store = Store(study.id, project=p)
    store.records.append(replace(store.records[0], run_id="aster", solver="code_aster"))
    store.results["aster"] = store.results["run"]
    hot = p.add(Operator(name="Hot", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    ctx = ev.Context(p, store)
    ev.value(ctx, hot)
    assert list(ctx.reads[hot.id]) == ["run"]  # the primary solver's, not the newest
    hot.inputs["result"] = OperatorInput(source=study.id, port="result", solver="code_aster")
    ctx = ev.Context(p, store)
    ev.value(ctx, hot)
    assert list(ctx.reads[hot.id]) == ["aster"]
    hot.inputs["result"] = OperatorInput(source=study.id, port="result", run="gone")
    text, _ = ev.shown(ev.Context(p, store), hot)
    assert "no longer stored" in text
    hot.inputs["result"] = OperatorInput(source=study.id, port="result", solver="elmer")
    with pytest.raises(PuzzleError, match="doesn't run elmer"):
        operators.refuse(p, hot)
    number = p.add(Operator(name="N", type="number", inputs={"value": q(1)}))
    other = p.add(
        Operator(
            name="E",
            type="expression",
            expression="x",
            inputs={"x": OperatorInput(source=number.id, point="t = 1 mm")},
        )
    )
    with pytest.raises(PuzzleError, match="reads no study"):
        operators.refuse(p, other)


def test_a_run_choice_round_trips_and_older_files_keep_their_hashes(tmp_path):
    from puzzle.project import save_project
    from puzzle.project.graph import content_hashes

    p = Project("p")
    n = p.add(Operator(name="N", type="number", inputs={"value": q(2, "mm")}))
    before = content_hashes(p)[n.id]
    save_project(p, tmp_path)
    text = next((tmp_path / "operator").glob("*.toml")).read_text(encoding="utf-8")
    assert "point" not in text and "solver" not in text  # unset: not written
    n.layout = "setting"  # as older files hold it
    assert content_hashes(p)[n.id] == before
    plates = swept(p)
    r = p.add(Operator(name="R", type="reduce", inputs={"result": OperatorInput(
        source=plates.id, port="result", point="t = 2 mm")}))  # fmt: skip
    save_project(p, tmp_path)
    again = load_project(tmp_path)
    assert again.objects[r.id].inputs["result"].point == "t = 2 mm"


# --- OP-6, OP-7: units and margins --------------------------------------------------------


@pytest.mark.parametrize(("value", "limit", "compare"), [
    (-144.3, 250, "at_most"), (0.0, 250, "at_most"), (120, 250, "at_most"),
    (300, 250, "at_most"), (-300, -100, "at_most"), (-50, -100, "at_most"),
    (-144.3, 250, "at_least"), (0.0, 250, "at_least"), (300, 250, "at_least"),
    (120, 250, "at_least"), (-50, -100, "at_least"), (5, 0, "at_most"), (0, 0, "at_most"),
])  # fmt: skip
def test_a_margin_never_contradicts_its_verdict(value, limit, compare):
    passed = value <= limit if compare == "at_most" else value >= limit
    v = ev.Verdict(passed, value * MPA, limit * MPA, compare, "pressure")
    assert (v.margin >= 0) == passed, (v.margin, passed)


def test_a_margin_keeps_its_meaning_for_positive_values():
    assert ev.Verdict(True, 200.0, 250.0, "at_most").margin == pytest.approx(0.25)
    assert ev.Verdict(True, 300.0, 250.0, "at_least").margin == pytest.approx(0.2)
    assert ev.Verdict(True, -144.3, 250.0, "at_most").margin == pytest.approx(394.3 / 250)


def test_a_check_and_a_utilisation_compare_like_with_like():
    p = Project("p")
    study = heat(p)
    store = Store(study.id, project=p)
    hot = p.add(Operator(name="Hot", type="reduce", quantity="stress:von_mises",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    check = p.add(Operator(name="C", type="check", inputs={"value": wire(hot), "limit": q(200)}))
    with pytest.raises(PuzzleError, match="give the limit a unit"):
        ev.value(ev.Context(p, store), check)
    check.inputs["limit"] = q(2, "mm")
    with pytest.raises(PuzzleError, match="compares"):
        ev.value(ev.Context(p, store), check)
    check.inputs["limit"] = q(400, "MPa")
    assert ev.value(ev.Context(p, store), check).passed
    bare = p.add(Operator(name="B", type="check", inputs={"value": q(0.5), "limit": q(1)}))
    assert ev.value(ev.Context(p, store), bare).passed  # two pure numbers compare
    use = p.add(Operator(name="U", type="utilisation", inputs={"a": wire(hot), "b": q(235)}))
    with pytest.raises(PuzzleError, match="like by like"):
        ev.value(ev.Context(p, store), use)
    use.inputs["b"] = q(2, "mm")
    with pytest.raises(PuzzleError, match="like by like"):
        ev.value(ev.Context(p, store), use)
    use.inputs["b"] = q(600, "MPa")
    got = ev.value(ev.Context(p, store), use)
    assert got.value == pytest.approx(0.5) and got.kind is None


# --- OP-8 to OP-17 -------------------------------------------------------------------------


def test_an_unknown_study_port_is_refused():
    p = Project("p")
    study = heat(p)
    store = Store(study.id, project=p)
    peak = p.add(Operator(name="Peak", type="reduce", inputs={"result": wire(study, "banana")}))
    with pytest.raises(PuzzleError, match="no output 'banana'"):
        operators.refuse(p, peak)
    peak.inputs["result"] = wire(study, "velocity")  # a field Puzzle knows, not in this run
    operators.refuse(p, peak)
    with pytest.raises(PuzzleError, match="no results 'velocity'") as exc:
        ev.value(ev.Context(p, store), peak)
    assert "stress" in exc.value.message
    peak.inputs["result"] = wire(study, "stress")
    assert ev.value(ev.Context(p, store), peak).value == pytest.approx(300 * MPA)


def test_summary_is_a_dict_for_every_type():
    from puzzle.operators import TYPES

    p = Project("p")
    study = heat(p)
    ctx = ev.Context(p, Store(study.id, project=p))
    for key in TYPES:
        op = p.add(Operator(name=f"op {key}", type=key))
        got = ev.summary(ctx, op)
        assert isinstance(got, dict) and got["name"] == op.name, key
    rep = p.add(Operator(name="Rep2", type="report", inputs={"a": q(1, "mm")}))
    assert ev.summary(ctx, rep)["shown"].startswith("to exports/Rep2.html")


def test_rows_to_columns_refuses_a_runaway_table():
    p = Project("p")
    rows = "\n".join(f"n{i},{i * 0.5}" for i in range(500))
    t = p.add(Operator(name="T", type="table", text="id, v [mm]\n" + rows))
    piv = p.add(Operator(name="P", type="pivot", inputs={"table": wire(t)}))
    start = time.perf_counter()
    text, _ = ev.shown(ev.Context(p, None), piv)
    assert "Choose the column" in text and time.perf_counter() - start < 0.5
    piv.column = "v"
    assert "measured numbers" in ev.shown(ev.Context(p, None), piv)[0]
    piv.column = "id"
    assert "500 different values" in ev.shown(ev.Context(p, None), piv)[0]


def test_pairs_of_one_name_keep_both_columns():
    p = Project("p")
    a = p.add(Operator(name="R1", type="range", inputs={"start": q(1), "end": q(2),
                                                         "count": q(2)}))  # fmt: skip
    pair = p.add(Operator(name="P", type="pairs", inputs={"a": wire(a), "b": wire(a)}))
    got = ev.value(ev.Context(p, None), pair)
    assert got.columns == ["R1", "R1_2"] and got.frame.height == 2
    with pytest.raises(PuzzleError, match="two columns named"):
        ev.Table(["x", "x"], [[1, 2]])


def test_a_step_past_the_end_is_refused():
    p = Project("p")
    study = heat(p)
    store = Store(study.id, project=p)
    hot = p.add(Operator(name="Hot", type="reduce", quantity="temperature:value", step=9,
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    assert "there is no step 9" in ev.shown(ev.Context(p, store), hot)[0]
    hot.step = -9
    assert "there is no step -9" in ev.shown(ev.Context(p, store), hot)[0]
    hot.step = -3
    assert ev.value(ev.Context(p, store), hot).value == pytest.approx(1.0)


def test_settings_that_mean_nothing_are_refused():
    p = Project("p")
    s = p.add(Operator(name="S", type="slider", low=Quantity(value=10, unit="mm"),
                       high=Quantity(value=1, unit="mm")))  # fmt: skip
    with pytest.raises(PuzzleError, match="lowest value is above"):
        operators.refuse(p, s)
    s.low, s.high, s.increment = (Quantity(value=1, unit="mm"), Quantity(value=10, unit="mm"),
                                  Quantity(value=1, unit="N"))  # fmt: skip
    with pytest.raises(PuzzleError, match="steps in N"):
        operators.refuse(p, s)
    t = p.add(Operator(name="T", type="table", text="x [mm]\n1\n2\n3"))
    top = p.add(Operator(name="Top", type="top_n", inputs={"table": wire(t), "count": q(-3)}))
    assert "at least 1 row" in ev.shown(ev.Context(p, None), top)[0]
    new = p.add(Operator(name="New", type="new_column", expression="x * 2",
                         inputs={"table": wire(t)}))  # fmt: skip
    assert "give the new column a name" in ev.shown(ev.Context(p, None), new)[0].lower()
    f = p.add(Operator(name="F", type="expression", expression="1/0"))
    assert "division by zero" in ev.shown(ev.Context(p, None), f)[0]
    d = p.add(Operator(name="D", type="divide_by", inputs={"a": q(1), "b": q(0)}))
    assert "division by zero" in ev.shown(ev.Context(p, None), d)[0]


def test_units_a_structural_engineer_needs():
    from puzzle.operators.expression import evaluate

    p = Project("p")
    study = heat(p)
    whole = p.add(Operator(name="I", type="reduce", reduction="integral",
                           quantity="stress:von_mises", selection="Bar/Left",
                           inputs={"result": wire(study, "result")}))  # fmt: skip
    got = ev.value(ev.Context(p, Store(study.id, project=p)), whole)
    assert got.kind == "energy"  # Pa·m³, its unit kept (it was dropped)
    whole.quantity = "temperature:value"  # K·m³: no kind Puzzle shows
    assert ev.value(ev.Context(p, Store(study.id, project=p)), whole).kind is None

    assert evaluate("(10 mm)**4 / 12", {})[1] == "second_moment"
    assert evaluate("50 MPa * sqrt(1 m)", {})[1] == "stress_intensity"
    assert evaluate("5 %", {})[0] == pytest.approx(0.05)
    assert evaluate("10 % 3", {})[0] == pytest.approx(1.0)  # still the remainder


def test_messages_say_what_to_put_right(tmp_path):
    root = copy(tmp_path)
    s = Session(root)
    with pytest.raises(PuzzleError) as exc:
        s.submit({"command": "create", "kind": "operator", "data": {
            "name": "R", "type": "round", "decimals": -2}})  # fmt: skip
    assert "decimals" in exc.value.message and "greater than or equal" in exc.value.message
    p = Project("p")
    f = p.add(Operator(name="F", type="expression", expression="x.bit_length()",
                       inputs={"x": q(1)}))  # fmt: skip
    assert "can't be called" in ev.shown(ev.Context(p, None), f)[0]
    check = p.add(Operator(name="Missing", type="check"))
    assert ev.shown(ev.Context(p, None), check)[0].startswith("Its value input")


HOSTILE = ["9**9**9", "2**(10**6)", "'a' * 10**11", "[1] * 10**9", "7**7**7",
           "(" * 200 + "1" + ")" * 200, "+".join(["1"] * 5000)]  # fmt: skip


@pytest.mark.parametrize("text", HOSTILE)
def test_a_hostile_expression_returns_promptly(text):
    p = Project("p")
    t = p.add(Operator(name="T", type="table", text="x [mm]\n1\n2"))
    ops = [
        p.add(Operator(name="Polars filter", type="filter_rows", syntax="polars",
                       condition=f"pl.col('x') > {text}", inputs={"table": wire(t)})),
        p.add(Operator(name="Polars column", type="new_column", syntax="polars", new_name="y",
                       expression=f"pl.col('x') * {text}", inputs={"table": wire(t)})),
        p.add(Operator(name="Puzzle filter", type="filter_rows",
                       condition=f"x > {text}", inputs={"table": wire(t)})),
        p.add(Operator(name="Formula", type="expression", expression=text)),
    ]  # fmt: skip
    for op in ops:
        start = time.perf_counter()
        ev.shown(ev.Context(p, None), op)  # an error or a value, never a hang or a raise
        assert time.perf_counter() - start < 2.0, (op.name, text)


def test_a_material_with_no_yield_says_so():
    from puzzle.materials import library_material

    p = Project("p")
    steel = p.add(library_material("copper"))  # the shipped copper has none
    mv = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    text, _ = ev.shown(ev.Context(p, None), mv)
    assert "has no yield strength" in text and "type the value" in text
