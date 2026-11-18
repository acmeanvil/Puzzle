"""The operator set after the design review (ADR 0090 package 13).

- OP-NEW-3: a curve goes into any operator that takes a table (its x and y columns),
  as a table already went into one that takes a curve.
- OP-NEW-13: searches that found nothing find the operator meant.
- OP-NEW-14: a Result summary's node says what it reduces, how and where; a Check's,
  which way it compares.
"""

from __future__ import annotations

import pytest

from puzzle import schematic as sch
from puzzle.model.schema import Operator, OperatorInput, Project
from puzzle.operators import TYPES, accepts
from puzzle.operators import evaluate as ev

CSV = "x [mm], force [N]\n1, 10\n2, 30\n3, 20\n"


def _curve(p: Project) -> Operator:
    table = p.add(Operator(name="Readings", type="table", text=CSV))
    return p.add(Operator(name="Curve", type="curve_from_table",
                          inputs={"table": OperatorInput(source=table.id)}))  # fmt: skip


def test_a_curve_goes_into_an_operator_that_takes_a_table():
    p = Project("p")
    curve = _curve(p)
    assert accepts(TYPES["sort"].input("table"), "series")
    ranked = p.add(Operator(name="Ranked", type="sort", by=["force"], descending=True,
                            inputs={"table": OperatorInput(source=curve.id)}))  # fmt: skip
    got = ev.value(ev.Context(p, None), ranked)
    assert isinstance(got, ev.Table) and got.frame.height == 3
    forces = got.frame.get_column(got.columns[1]).to_list()
    assert forces == pytest.approx([30.0, 20.0, 10.0])  # in N: SI, sorted largest first
    built = sch.build(p)  # drawn as a wire, the curve's colour
    assert any(w.target == str(ranked.id) and w.target_port == "table" for w in built.wires)


def test_a_curve_still_goes_into_a_curve_input_as_a_curve():
    p = Project("p")
    curve = _curve(p)
    slope = p.add(Operator(name="Slope", type="slope",
                           inputs={"series": OperatorInput(source=curve.id)}))  # fmt: skip
    assert getattr(ev.value(ev.Context(p, None), slope), "type", "") == "series"


@pytest.mark.parametrize(
    ("word", "finds"),
    [("von mises", "reduce"), ("vm", "reduce"), ("average", "reduce"),
     ("integrate", "reduce"), ("peak", "reduce"), ("hot spot", "reduce"),
     ("abs", "expression"), ("if", "expression"), ("switch", "expression"),
     ("threshold", "filter"), ("combine", "envelope")],
)  # fmt: skip
def test_searches_that_found_nothing_find_the_operator_meant(word, finds):
    assert word in TYPES[finds].aliases


def test_a_result_summary_says_what_it_reduces_and_where():
    p = Project("p")
    peak = p.add(Operator(name="Peak", type="reduce", quantity="stress:von_mises",
                          selection="Bar/Root"))  # fmt: skip
    low = p.add(Operator(name="Low", type="reduce", reduction="min"))
    check = p.add(Operator(name="Enough", type="check", compare="at_least"))
    nodes = sch.build(p).nodes
    assert nodes[str(peak.id)].subtitle == (
        "Result summary · maximum equivalent (von Mises) stress on Bar/Root"
    )
    assert nodes[str(low.id)].subtitle == "Result summary · minimum of its first result"
    assert nodes[str(check.id)].subtitle == "Check against limit · at least"


def test_a_summary_wired_from_a_field_port_says_that_field():
    from puzzle.model.schema import Study

    p = Project("p")
    study = p.add(Study(name="S", component=Project("q").add(Operator(
        name="x", type="number")).id, solver="calculix", steps=[]))  # fmt: skip
    peak = p.add(Operator(name="Peak", type="reduce", inputs={
        "result": OperatorInput(source=study.id, port="stress")}))  # fmt: skip
    assert sch.build(p).nodes[str(peak.id)].subtitle == "Result summary · maximum stress"
