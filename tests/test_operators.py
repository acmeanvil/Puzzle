"""Operators (ADR 0062, phase 1) on hand-made results: values checked by hand.

Two tets: Left (nodes 1-4, the corner tet of the unit cube at the origin) and Right
(nodes 2-5, sharing a face). Three transient steps at t = 1, 2, 3 s with the
temperature T = t·x kelvin and σxx = 100·t·x MPa, so the maximum is t (node 2 at x = 1),
and the integral of T over the Left tet is t·V·x̄ = t · 1/6 · 1/4.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import numpy as np
import pytest

from puzzle.commands import Session
from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.mesh.mesh import CellBlock, Group
from puzzle.model.schema import (
    Operator,
    OperatorInput,
    ParameterSet,
    Project,
    Quantity,
    ResultProbe,
    Study,
    SweepParameter,
    SweepStep,
)
from puzzle.operators import accepts, refuse_cycles, sources
from puzzle.operators import evaluate as ev
from puzzle.project import save_project
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord

XYZ = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
IDS = np.arange(1, 6)
MPA = 1e6


def mesh() -> Mesh:
    left = CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))
    right = CellBlock("tet4", 3, (3, 2), np.array([2]), np.array([[2, 3, 4, 5]]))
    groups = {
        "Bar": Group("Bar", 3, [(3, 1), (3, 2)], IDS.copy()),
        "Bar/Left": Group("Bar/Left", 3, [(3, 1)], np.array([1, 2, 3, 4])),
        "Bar/Right": Group("Bar/Right", 3, [(3, 2)], np.array([2, 3, 4, 5])),
    }
    return Mesh(IDS.copy(), XYZ.copy(), [left, right], groups)


def step(t: float, scale: float = 1.0) -> StepResult:
    x = XYZ[:, 0]
    zeros = np.zeros(5)
    sxx = 100 * t * x * scale * MPA
    stress = np.stack([sxx, zeros, zeros, zeros, zeros, zeros], axis=1)
    return StepResult(1, "transient", t, fields={
        "temperature": Field("temperature", "node", [""], IDS, (t * x * scale)[:, None]),
        "stress": Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], IDS, stress),
    })  # fmt: skip


def record(run_id, study, day, point=None):
    when = datetime(2026, 10, day)
    return RunRecord(run_id, "k", str(study), "Heat", "calculix", "native", "m", "completed",
                     when, 1.0, 3, when, json.dumps(point) if point else None)  # fmt: skip


class Store:
    root = "memory"

    def __init__(self, study, swept=None):
        self.records = [record("run", study, 1)]
        self.results = {"run": Results("calculix", [step(1), step(2), step(3)])}
        if swept is not None:  # thickness 1, 2, 4 mm scales the field by 1/t
            for i, t in enumerate((1, 2, 4)):
                rid = f"t{t}"
                self.records.append(record(rid, swept, 2 + i, {"t": f"{t} mm"}))
                self.results[rid] = Results("calculix", [step(1, 1 / t)])

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.results[run_id]

    def read_mesh(self, mesh_id):
        return mesh()


def wire(source, port="out"):
    return OperatorInput(source=source.id, port=port)


@pytest.fixture
def setup():
    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    return p, study, ev.Context(p, Store(study.id))


def test_reduce_max_min_mean_and_integral(setup):
    p, study, ctx = setup
    hot = p.add(Operator(name="Hottest", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    got = ev.value(ctx, hot)
    assert got.value == pytest.approx(3.0) and got.kind == "temperature"
    assert got.point == [1.0, 0.0, 0.0] and got.label == "Maximum temperature"
    hot.reduction, hot.step = "min", 0
    assert ev.value(ev.Context(p, ctx.store), hot).value == 0.0
    hot.reduction, hot.selection, hot.step = "integral", "Bar/Left", -1
    assert ev.value(ev.Context(p, ctx.store), hot).value == pytest.approx(3 / 6 / 4)
    hot.reduction = "mean"  # the volume-weighted mean: t·x̄
    assert ev.value(ev.Context(p, ctx.store), hot).value == pytest.approx(3 / 4)


def test_a_study_field_port_picks_its_first_result(setup):
    p, study, ctx = setup
    peak = p.add(Operator(name="Peak", type="reduce", inputs={"result": wire(study, "stress")}))
    got = ev.value(ctx, peak)  # von Mises of uniaxial σxx = |σxx|
    assert got.value == pytest.approx(300 * MPA) and got.kind == "pressure"


def test_filter_then_reduce_and_check(setup):
    p, study, ctx = setup
    mpa = {"allowable": 250, "floor": 150}
    limits = p.add(ParameterSet(name="Limits", values={
        k: Quantity(value=v, unit="MPa") for k, v in mpa.items()}))  # fmt: skip
    hot = p.add(Operator(name="Hot", type="filter", quantity="stress:von_mises", keep="above",
                         inputs={"result": wire(study, "result"),
                                 "limit": wire(limits, "floor")}))  # fmt: skip
    kept = ev.value(ctx, hot)
    assert np.isfinite(kept.values).sum() == 2  # nodes 2 and 5, at 300 MPa
    inputs = {"value": wire(hot), "limit": wire(limits, "allowable")}
    check = p.add(Operator(name="Strength", type="check", inputs=inputs))
    out = ev.outputs(ctx, check)
    assert not out["out"].passed and out["margin"].value == pytest.approx(250 / 300 - 1)
    assert out["margin"].text() == "-17%"
    check.inputs["limit"] = OperatorInput(value=Quantity(value=400, unit="MPa"))
    verdict = ev.value(ev.Context(p, ctx.store), check)
    assert verdict.passed and verdict.text().startswith("Pass, margin +33%: ")
    check.inputs["limit"] = OperatorInput(value=Quantity(value=5, unit="mm"))
    with pytest.raises(PuzzleError, match="compares pressure and stress with length"):
        ev.value(ev.Context(p, ctx.store), check)


def test_a_result_tool_feeds_a_check(setup):
    p, study, ctx = setup
    probe = p.add(ResultProbe(name="Corner", source=study.id, type="point",
                              quantity="temperature:value", point=(1.0, 0.0, 0.0)))  # fmt: skip
    inputs = {"value": wire(probe), "limit": OperatorInput(value=2.0)}
    check = p.add(Operator(name="Cool enough", type="check", compare="at_least", inputs=inputs))
    verdict = ev.value(ctx, check)
    assert verdict.passed and verdict.value == pytest.approx(3.0, rel=1e-5)


def test_plot_against_the_steps(setup):
    p, study, ctx = setup
    hot = p.add(Operator(name="Hottest", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    plot = p.add(Operator(name="Heating", type="plot", inputs={"value": wire(hot)}))
    table = ev.value(ctx, plot)
    assert table.columns == ["Time", "Maximum temperature"]
    assert table.rows == [[1.0, pytest.approx(1.0)], [2.0, pytest.approx(2.0)],
                          [3.0, pytest.approx(3.0)]]  # fmt: skip
    assert table.csv().splitlines()[0] == "Time [s],Maximum temperature [K]"


def test_plot_against_a_sweep(setup):
    p, _, _ = setup
    swept = p.add(Study(name="Plates", component=uuid4(), solver="calculix", steps=[
        SweepStep(parameters=[SweepParameter(name="t", values=[
            Quantity(value=v, unit="mm") for v in (1, 2, 4)])])]))  # fmt: skip
    ctx = ev.Context(p, Store(uuid4(), swept=swept.id))
    hot = p.add(Operator(name="Hottest", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(swept, "result")}))  # fmt: skip
    plot = p.add(Operator(name="By thickness", type="plot", against="sweep",
                          inputs={"value": wire(hot)}))  # fmt: skip
    table = ev.value(ctx, plot)
    assert table.columns[0] == "t" and table.kinds[0] == "length"
    assert [r[0] for r in table.rows] == pytest.approx([0.001, 0.002, 0.004])
    assert [r[1] for r in table.rows] == pytest.approx([1.0, 0.5, 0.25])


def test_panel_shows_and_export_writes(setup, tmp_path):
    p, study, ctx = setup
    hot = p.add(Operator(name="Hottest", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    panel = p.add(Operator(name="Show", type="panel", inputs={"value": wire(hot)}))
    assert ev.shown(ctx, panel) == ("3 K", "")
    empty = p.add(Operator(name="Empty", type="panel"))
    assert ev.shown(ctx, empty)[0] == "show value 'Empty': its value input is not wired"
    out = p.add(Operator(name="Max T", type="export", inputs={"value": wire(hot)}))
    written = ev.export(ctx, out, tmp_path)
    assert written == [tmp_path / "exports" / "Max_T.csv"]
    assert written[0].read_text(encoding="utf-8").splitlines() == [
        "name,value [K]",
        "Maximum temperature,3",
    ]
    out.file = "../outside.csv"
    with pytest.raises(PuzzleError, match="inside the project folder"):
        ev.export(ctx, out, tmp_path)


def test_port_types_and_what_can_be_wired(setup):
    p, study, _ = setup
    from puzzle.operators import TYPES

    assert accepts(TYPES["check"].input("limit"), "number")
    assert not accepts(TYPES["check"].input("limit"), "result")
    hot = p.add(Operator(name="Hot", type="reduce", inputs={"result": wire(study, "result")}))
    check = p.add(Operator(name="C", type="check", inputs={"value": wire(hot)}))
    offered = {(label, port) for label, _, port in sources(p, check, "limit")}
    assert ("Hot", "out") in offered  # a number
    assert not any(label.startswith("Heat") for label, _ in offered)  # results don't fit
    assert not any(label == "C: Margin" for label, _ in offered)  # its own output
    with pytest.raises(ValueError, match="has no input wrong"):
        Operator(name="X", type="check", inputs={"wrong": OperatorInput(value=1.0)})


def test_commands_refuse_wrong_wires_and_loops(tmp_path):
    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    root = tmp_path / "p"
    save_project(p, root)
    s = Session(root)
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Hot", "type": "reduce",
        "inputs": {"result": {"source": str(study.id), "port": "result"}}}})  # fmt: skip
    hot = next(o for o in s.project.of_type(Operator))
    with pytest.raises(PuzzleError, match="takes a number, not a result"):
        s.submit({"command": "create", "kind": "operator", "data": {
            "name": "C", "type": "check",
            "inputs": {"limit": {"source": str(study.id), "port": "result"}}}})  # fmt: skip
    assert len(s.project.of_type(Operator)) == 1  # nothing was left behind
    out = s.submit({"command": "create", "kind": "operator", "data": {
        "name": "C", "type": "check", "inputs": {"value": {"source": str(hot.id)}}}})  # fmt: skip
    assert any(pr.code == "operator_input" and "limit" in pr.message for pr in out.problems)
    check = next(o for o in s.project.of_type(Operator) if o.name == "C")
    filt = s.submit({"command": "create", "kind": "operator", "data": {
        "name": "F", "type": "filter", "inputs": {"result": {"source": str(study.id),
                                                             "port": "result"},
                                                  "limit": {"source": str(check.id),
                                                            "port": "margin"}}}})  # fmt: skip
    f = filt.created[0]["id"]
    with pytest.raises(PuzzleError, match="would make a loop"):  # Hot reads F reads C reads Hot
        s.submit({"command": "edit", "object": "Hot", "ops": [
            {"op": "set", "path": "inputs.result", "value": {"source": f}}]})  # fmt: skip
    assert s.project.get(hot.id, Operator).inputs["result"].source == study.id
    refuse_cycles(s.project)  # the project is still free of loops
    with pytest.raises(PuzzleError, match="used by C"):
        s.submit({"command": "delete", "object": "Hot"})


def test_states_follow_what_they_read(setup):
    from puzzle.project.status import status

    p, study, ctx = setup
    hot = p.add(Operator(name="Hot", type="reduce", inputs={"result": wire(study, "result")}))
    panel = p.add(Operator(name="Show", type="panel", inputs={"value": wire(hot)}))
    lonely = p.add(Operator(name="Lonely", type="reduce"))
    states = status(p, [])
    assert states[str(study.id)].state == "missing"  # its component is gone
    assert states[str(hot.id)].state == states[str(panel.id)].state == "missing"
    assert states[str(lonely.id)].state == "missing"


def test_summary_is_data(setup):
    p, study, ctx = setup
    hot = p.add(Operator(name="Hot", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    data = ev.summary(ctx, hot)
    assert data["outputs"]["out"]["value"] == pytest.approx(3.0)
    assert data["outputs"]["out"]["unit"] == "K" and data["shown"] == "3 K"


def test_the_schematic_draws_operators_with_typed_ports_and_values(setup):
    from puzzle import schematic as sch
    from puzzle.schematic.layout import layout
    from puzzle.schematic.svg import to_svg

    p, study, ctx = setup
    limits = p.add(ParameterSet(name="Limits", values={"hot": Quantity(value=5, unit="K")}))
    hot = p.add(Operator(name="Hot", type="reduce", quantity="temperature:value",
                         inputs={"result": wire(study, "temperature")}))  # fmt: skip
    check = p.add(Operator(name="Safe", type="check",
                           inputs={"value": wire(hot), "limit": wire(limits, "hot")}))  # fmt: skip
    runs = list(ctx.store.runs())
    s = sch.build(p, runs, store=ctx.store)
    node = s.nodes[str(check.id)]
    assert node.kind == "operator" and node.subtitle == "Check against limit"
    assert node.column == sch.RESULTS
    assert [(x.key, x.side, x.type) for x in node.ports] == [
        ("out", "out", "verdict"), ("margin", "out", "number"),
        ("value", "in", "number"), ("limit", "in", "number")]  # fmt: skip
    assert node.verdict == "pass" and node.value == "Pass, margin +67%"
    assert ev.detail(ctx, check) == "Pass, margin +67%: 3 K ≤ 5 K"
    assert s.nodes[str(limits.id)].port("hot").type == "number"
    into = {(w.source, w.source_port, w.target_port, w.type) for w in s.wires
            if w.target in (str(hot.id), str(check.id))}  # fmt: skip
    assert into == {(f"runs:{study.id}", "temperature", "result", "result"),
                    (str(hot.id), "out", "value", "number"),
                    (str(limits.id), "hot", "limit", "number")}  # fmt: skip
    assert sch.wire_colour(next(w for w in s.wires if w.type == "number")) == "#2b8a3e"
    assert sch.socket_shape(node.port("out")) == "triangle"
    svg = to_svg(s, layout(s), title="p")
    assert "Pass, margin +67%" in svg and "<polygon" in svg
    lay = layout(s)  # the Check sits right of the Reduce it reads
    assert lay.positions[str(check.id)][0] > lay.positions[str(hot.id)][0]
    assert s.downstream(f"runs:{study.id}") >= {str(hot.id), str(check.id)}


def test_outline_cli_and_mcp(setup, tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.commands import outline

    p, study, _ = setup
    p.add(Operator(name="Hot", type="reduce", quantity="temperature:value", selection="Bar/Left",
                   inputs={"result": wire(study, "result")}))  # fmt: skip
    text = outline(p)
    assert ("Operator Hot: result summary max of temperature:value on Bar/Left; result <- Heat"
            in text)  # fmt: skip
    root = tmp_path / "p"
    save_project(p, root)
    assert main(["operators", str(root)]) == 0
    assert "Hot: study 'Heat' has no run yet" in capsys.readouterr().out

    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server

    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    out = call("operator_add", type="check", name="Cool",
               inputs={"value": {"source": "Hot"}, "limit": {"value": "300 K"}})  # fmt: skip
    assert out["status"] == "applied"
    assert "no run yet" in out["value"]["error"]["message"]
    assert out["value"]["wiring"] == "check against limit at most; value <- Hot, limit = 300 K"
    (cool,) = [o for o in Session(root).project.of_type(Operator) if o.name == "Cool"]
    assert cool.inputs["limit"].value == Quantity(value=300, unit="K")
    listed = call("operator_value")["operators"]
    assert sorted(o["name"] for o in listed) == ["Cool", "Hot"]


def test_a_check_on_a_linearization_runs_the_asme_checks(setup):
    """σxx = 300 x MPa along the edge from x = 0 to 1 (the last step): Pm = 150 MPa and
    PL + Pb = 300 MPa, so against S = 250 MPa all pass and membrane + bending (300 ≤
    375) governs; against 120 MPa both primary checks fail, membrane + bending the worse
    (300 against 180)."""
    p, study, ctx = setup
    line = p.add(ResultProbe(name="Wall", source=study.id, type="linearized",
                             quantity="stress:von_mises", point=(0.0, 0.0, 0.0),
                             end=(1.0, 0.0, 0.0), samples=41))  # fmt: skip
    inputs = {"value": wire(line), "limit": OperatorInput(value={"value": 250, "unit": "MPa"})}
    check = p.add(Operator(name="ASME", type="check", inputs=inputs))
    out = ev.value(ctx, check)
    assert out.passed and out.value == pytest.approx(300 * MPA, rel=0.01)
    assert out.limit == pytest.approx(375 * MPA)
    assert ev.value(ctx, check, "margin").value == pytest.approx(0.25, rel=0.02)
    check.inputs["limit"] = OperatorInput(value={"value": 120, "unit": "MPa"})
    ctx.memo.clear()
    failed = ev.value(ctx, check)
    assert not failed.passed and failed.limit == pytest.approx(180 * MPA)
