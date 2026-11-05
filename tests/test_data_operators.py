"""Data operators (ADR 0062, phase 2): values by hand, units checked, and the model
driven by them: a Slider's value as a history argument, a Range as a sweep's points,
studies stale when an operator they read changes, and no loop from results back in.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Geometry,
    HistoryStep,
    Operator,
    OperatorInput,
    ParameterSet,
    Project,
    Quantity,
    Study,
    SweepParameter,
    SweepStep,
)
from puzzle.operators import evaluate as ev
from puzzle.operators import (
    is_pre,
    parameter_values,
    pre_solve,
    refuse,
    resolved,
)


def q(value, unit="dimensionless"):
    return OperatorInput(value=Quantity(value=value, unit=unit))


def wire(src, port="out"):
    return OperatorInput(source=src.id, port=port)


def val(p, op):
    return ev.value(ev.Context(p, None), op)


def test_number_and_slider():
    p = Project("p")
    n = p.add(Operator(name="t", type="number", inputs={"value": q(12, "mm")}))
    assert val(p, n).value == pytest.approx(0.012) and val(p, n).kind == "length"
    s = p.add(Operator(name="r", type="slider", inputs={"value": q(7.3, "mm")},
                       low=Quantity(value=2, unit="mm"), high=Quantity(value=10, unit="mm"),
                       increment=Quantity(value=0.5, unit="mm")))  # fmt: skip
    assert val(p, s).value == pytest.approx(0.0075)  # snapped to the 0.5 mm steps
    s.inputs["value"] = q(40, "mm")
    assert val(p, s).value == pytest.approx(0.010)  # held at its high
    del s.inputs["value"]
    assert val(p, s).value == pytest.approx(0.002)  # unset: its low
    ctx = ev.Context(p, None, overrides={s.id: 0.0061})
    assert ev.value(ctx, s).value == pytest.approx(0.006)  # dragged, still snapped
    s.inputs["value"] = q(5, "MPa")
    with pytest.raises(PuzzleError, match="compares pressure and stress with length"):
        val(p, s)


def test_series_range_and_lists():
    p = Project("p")
    series = p.add(Operator(name="S", type="series", inputs={
        "start": q(2, "mm"), "step": q(2, "mm"), "count": q(4)}))  # fmt: skip
    assert val(p, series).values == pytest.approx([0.002, 0.004, 0.006, 0.008])
    rng = p.add(Operator(name="R", type="range", inputs={
        "start": q(0, "kN"), "end": q(1, "kN"), "count": q(5)}))  # fmt: skip
    got = val(p, rng)
    assert got.values == pytest.approx([0, 250, 500, 750, 1000]) and got.kind == "force"
    assert "250 N" in got.text() and "(5 items)" in got.text()
    pick = p.add(Operator(name="P", type="list", operation="item",
                          inputs={"list": wire(rng), "index": q(-1)}))  # fmt: skip
    assert ev.outputs(ev.Context(p, None), pick)["first"].value == pytest.approx(1000)
    part = p.add(Operator(name="Part", type="list", operation="slice",
                          inputs={"list": wire(rng), "index": q(1), "end": q(3)}))  # fmt: skip
    assert val(p, part).values == pytest.approx([250, 500])
    for operation, want in (("reverse", [1000, 750, 500, 250, 0]), ("length", [5]),
                            ("sum", [2500]), ("max", [1000])):  # fmt: skip
        pick.operation = operation
        assert val(p, pick).values == pytest.approx(want)
    pairs = p.add(Operator(name="AB", type="pairs", combination="cross",
                           inputs={"a": wire(series), "b": wire(part)}))  # fmt: skip
    table = val(p, pairs)
    assert len(table.rows) == 8 and table.kinds == ["length", "force"]
    pairs.combination = "zip"  # the shorter repeats its last item
    assert [r[1] for r in val(p, pairs).rows] == pytest.approx([250, 500, 500, 500])


def test_a_table_and_its_columns():
    p = Project("p")
    t = p.add(Operator(name="Plates", type="table",
                       text="name, t [mm], load [kN]\nthin, 2, 1.5\nthick, 6, 4\n"))  # fmt: skip
    table = val(p, t)
    assert table.columns == ["name", "t", "load"] and table.kinds == [None, "length", "force"]
    assert table.rows[1] == ["thick", pytest.approx(0.006), pytest.approx(4000)]
    col = p.add(Operator(name="T", type="list", operation="column", column="t",
                         inputs={"list": wire(t)}))  # fmt: skip
    assert val(p, col).values == pytest.approx([0.002, 0.006])
    col.column = "name"
    assert val(p, col).values == ["thin", "thick"]
    col.column = "width"
    with pytest.raises(PuzzleError, match="no column 'width'"):
        val(p, col)
    t.text = "t [furlongs-ish]\n1"
    with pytest.raises(PuzzleError, match="unknown unit"):
        val(p, t)


def test_expressions_check_units():
    p = Project("p")
    p.add(ParameterSet(name="P", values={"gap": Quantity(value=1, unit="mm")}))
    t = p.add(Operator(name="t", type="number", inputs={"value": q(12, "mm")}))
    e = p.add(Operator(name="Width", type="expression", expression="2 * x + 5 mm + gap",
                       inputs={"x": wire(t)}))  # fmt: skip
    got = val(p, e)
    assert got.value == pytest.approx(0.030) and got.kind == "length"
    e.expression = "x * 200 MPa"  # a length times a stress: a force per length? no: N/m
    with pytest.raises(PuzzleError, match="not one Puzzle shows"):
        val(p, e)
    e.expression = "x * 200 MPa * 10 mm"
    assert val(p, e).value == pytest.approx(0.012 * 200e6 * 0.01) and val(p, e).kind == "force"
    e.expression = "x + 3 MPa"
    with pytest.raises(PuzzleError, match="the units don't agree"):
        val(p, e)
    e.expression = "sqrt(x * x) / mm"
    assert val(p, e).value == pytest.approx(12.0) and val(p, e).kind is None
    e.expression = "sin(x)"
    with pytest.raises(PuzzleError, match="units don't agree"):
        val(p, e)
    e.expression = "x + nope"
    with pytest.raises(PuzzleError, match="no input, parameter or unit 'nope'"):
        val(p, e)
    rng = p.add(Operator(name="R", type="range", inputs={
        "start": q(1, "mm"), "end": q(3, "mm"), "count": q(3)}))  # fmt: skip
    e.inputs = {"x": wire(rng)}
    e.expression = "x ** 2 / mm"
    out = val(p, e)
    assert isinstance(out, ev.ListValue) and out.values == pytest.approx([1e-3, 4e-3, 9e-3])
    e.expression = "__import__('os')"
    with pytest.raises(PuzzleError):
        val(p, e)


def test_pre_solve_operators_are_parameters_and_stale_studies():
    from puzzle.project.graph import content_hashes

    p = Project("p")
    study = p.add(Study(name="S", component=uuid4(), solver="calculix", steps=[]))
    mm = {"low": Quantity(value=1, unit="mm"), "high": Quantity(value=8, unit="mm")}
    r = p.add(Operator(name="hole_r", type="slider", inputs={"value": q(4, "mm")}, **mm))
    p.add(Operator(name="hole_d", type="expression", expression="2 * x",
                       inputs={"x": wire(r)}))  # fmt: skip
    reduce = p.add(Operator(name="Peak", type="reduce",
                            inputs={"result": wire(study, "result")}))  # fmt: skip
    assert {o.name for o in pre_solve(p)} == {"hole_r", "hole_d"} and not is_pre(p, reduce)
    assert parameter_values(p) == {"hole_r": pytest.approx(0.004), "hole_d": pytest.approx(0.008)}
    assert parameter_values(p, {r.id: 0.002})["hole_d"] == pytest.approx(0.004)
    before = content_hashes(p)[study.id]
    r.inputs["value"] = q(5, "mm")
    assert content_hashes(p)[study.id] != before  # the study reads it: out of date
    peak_before = content_hashes(p)[study.id]
    reduce.reduction = "min"  # a post-solve operator never stales the study
    assert content_hashes(p)[study.id] == peak_before
    assert "hole_r" in p.parameter_names()


def test_a_list_sweeps_a_study_and_a_slider_by_name():
    from puzzle import sweep
    from puzzle.project.graph import run_hashes

    p = Project("p")
    thick = p.add(Operator(name="t", type="slider", inputs={"value": q(2, "mm")}))
    rng = p.add(Operator(name="Ts", type="range", inputs={
        "start": q(2, "mm"), "end": q(6, "mm"), "count": q(3)}))  # fmt: skip
    study = p.add(Study(name="S", component=uuid4(), solver="calculix", steps=[
        SweepStep(parameters=[SweepParameter(name="t", source=rng.id)])]))  # fmt: skip
    assert study.dependencies()[1] == rng.id
    points = resolved(p).objects[study.id].sweep_points()
    assert [pt["t"].to_si() for pt in points] == pytest.approx([0.002, 0.004, 0.006])
    assert study.sweep_points() == []  # unresolved: no points; the project is unchanged
    assert len(run_hashes(p)[study.id]["calculix"]) == 3
    one = sweep.apply(p, study, points[2])
    assert parameter_values(one)["t"] == pytest.approx(0.006)  # the slider took the point
    assert thick.inputs["value"].value.value == 2  # in the copy only
    rng.inputs["count"] = q(5)
    assert len(run_hashes(p)[study.id]["calculix"]) == 5


def test_an_operator_feeding_the_model_cant_read_results():
    p = Project("p")
    study = p.add(Study(name="S", component=uuid4(), solver="calculix", steps=[]))
    peak = p.add(Operator(name="Peak", type="reduce", inputs={"result": wire(study, "result")}))
    w = p.add(Operator(name="w", type="expression", expression="x / 1 Pa * 1 mm",
                       inputs={"x": wire(peak)}))  # fmt: skip
    refuse(p, w)  # a post-solve expression is fine on its own
    box = HistoryStep(kind="box", name="B", args={"dx": "w", "dy": 0.1, "dz": 0.01})
    p.add(Geometry(name="Plate", history=[box]))
    with pytest.raises(PuzzleError, match="feeds the model .Plate: B dx., so it can't read"):
        refuse(p, w)


def test_a_slider_drives_a_history_argument():
    pytest.importorskip("OCP")
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.prep import shape_of

    def volume_of(shape) -> float:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return props.Mass()

    p = Project("p")
    p.add(Operator(name="width", type="slider", inputs={"value": q(30, "mm")}))
    box = HistoryStep(kind="box", name="B", args={"dx": "width", "dy": 0.02, "dz": 0.01})
    geo = p.add(Geometry(name="Plate", history=[box]))
    shape = shape_of(p, geo, ".")
    assert volume_of(shape) == pytest.approx(0.03 * 0.02 * 0.01, rel=1e-9)
    assert np.isclose(parameter_values(p)["width"], 0.03)


def test_renaming_a_parameter_renames_what_names_it(tmp_path):
    """ADR 0062: renaming a Slider follows into history arguments, sweeps, an
    expression and a derived result's equation, in one command; undo takes it back."""
    from puzzle.commands import Session
    from puzzle.commands.model import Edit, EditOp
    from puzzle.project import save_project

    p = Project("p")
    slider = p.add(Operator(name="width", type="slider", inputs={"value": q(30, "mm")}))
    box = HistoryStep(kind="box", name="B", args={"dx": "width", "dy": 0.02, "dz": 0.01,
                                                 "body": "width"})  # fmt: skip
    p.add(Geometry(name="Plate", history=[box]))
    p.add(Operator(name="Area", type="expression", expression="width * 2 + widths"))
    save_project(p, tmp_path)
    s = Session(tmp_path)
    s.submit(Edit(object="width", ops=[EditOp(op="set", path="name", value="span")]))
    geo = next(o for o in s.project.objects.values() if o.name == "Plate")
    assert geo.history[0].args["dx"] == "span"
    assert geo.history[0].args["body"] == "width"  # a body's name, not the parameter
    area = next(o for o in s.project.objects.values() if o.name == "Area")
    assert area.expression == "span * 2 + widths"  # whole words only
    assert s.project.objects[slider.id].name == "span"
    s.undo()
    geo = next(o for o in s.project.objects.values() if o.name == "Plate")
    assert geo.history[0].args["dx"] == "width"
