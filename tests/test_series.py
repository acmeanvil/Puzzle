"""Curves and their operators (ADR 0071 decision 3), checked by hand.

The results are test_operators' two tets at t = 1, 2, 3 s with σxx = 100·t·x MPa and
T = t·x K. A path from (0, 0.05, 0.05) to (0.8, 0.05, 0.05) runs along x inside the
Left tet, so along it von Mises = 100·t·d MPa and T = t·d K at distance d.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from test_operators import MPA, Store, wire

from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Material,
    Operator,
    OperatorInput,
    Project,
    Quantity,
    ResultProbe,
    Study,
)
from puzzle.operators import accepts, first_type, output_type, parameter_values
from puzzle.operators import evaluate as ev
from puzzle.operators.series import Series


@pytest.fixture
def setup():
    from uuid import uuid4

    p = Project(name="p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    steel = p.add(Material(name="Steel", properties={
        "yield_strength": Quantity(value=250, unit="MPa")}))  # fmt: skip
    path = p.add(ResultProbe(name="Wall", source=study.id, type="path",
                             quantity="stress:von_mises", point=(0.0, 0.05, 0.05),
                             end=(0.8, 0.05, 0.05), samples=9))  # fmt: skip
    return p, study, steel, path, Store(study.id)


def op(p, type_, name=None, **inputs):
    fields = {k: v for k, v in inputs.items() if not isinstance(v, (OperatorInput, Quantity))}
    wired = {k: v if isinstance(v, OperatorInput) else OperatorInput(value=v)
             for k, v in inputs.items() if k not in fields}  # fmt: skip
    return p.add(Operator(name=name or type_, type=type_, inputs=wired, **fields))


def value(p, store, o):
    return ev.value(ev.Context(p, store), o)


def test_a_path_is_a_curve_per_step(setup):
    p, study, steel, path, store = setup
    along = ev.source(ev.Context(p, store), path.id, "along")
    assert isinstance(along, Series) and len(along.curves) == 3
    assert along.x_kind == "length" and along.y_kind == "pressure"
    last = along.curves[-1]
    assert last.x[-1] == pytest.approx(0.8)
    assert last.y == pytest.approx(300 * last.x * MPA, rel=1e-5)
    assert last.label == "Equivalent (von Mises) stress at 3 s"
    assert output_type(p, path.id, "along") == "series"


def test_utilisation_along_a_path_against_yield(setup):
    """The ADR's first plot: von Mises along a wall ÷ the material's yield strength."""
    p, study, steel, path, store = setup
    last = op(p, "at_step", "Last step", series=wire(path, "along"))
    sy = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    use = op(p, "utilisation", "Use", a=wire(last), b=wire(sy))
    got = value(p, store, use)
    (curve,) = got.curves
    assert got.y_kind is None and got.label == "Utilisation"
    assert curve.y == pytest.approx(1.2 * curve.x, rel=1e-5)  # 300 d / 250
    assert first_type(use, p) == "series"
    # a safety factor at the peak: two numbers give a number
    peak = p.add(Operator(name="Peak", type="reduce", quantity="stress:von_mises",
                          inputs={"result": wire(study, "result")}))  # fmt: skip
    sf = op(p, "safety_factor", "SF", a=wire(peak), b=wire(sy))
    assert first_type(sf, p) == "number"
    assert value(p, store, sf).value == pytest.approx(250 / 300)
    margin = op(p, "margin", "MS", a=wire(peak), b=wire(sy))
    assert value(p, store, margin).value == pytest.approx(250 / 300 - 1)


def test_normalised_by_its_own_largest_value(setup):
    p, study, steel, path, store = setup
    t_path = p.add(path.model_copy(update={"id": __import__("uuid").uuid4(), "name": "T",
                                           "quantity": "temperature:value"}))  # fmt: skip
    shape = op(p, "normalise", "Shape", series=wire(t_path, "along"))
    got = value(p, store, shape)
    for c in got.curves:  # t·d / (t·0.8): the same shape at every step
        assert c.y == pytest.approx(c.x / 0.8, rel=1e-5)
    assert got.y_kind is None
    by = op(p, "normalise", "By 2 K", series=wire(t_path, "along"),
            by=Quantity(value=2, unit="K"))  # fmt: skip
    assert value(p, store, by).curves[0].y == pytest.approx(got.curves[0].x / 2, rel=1e-5)


def test_every_step_against_a_step(setup):
    p, study, steel, path, store = setup
    ratio = op(p, "compare_steps", "Growth", series=wire(path, "along"), step=0,
               comparison="ratio")  # fmt: skip
    got = value(p, store, ratio)
    vm = "Equivalent (von Mises) stress"
    assert [c.label for c in got.curves] == [f"{vm} at 2 s ÷ step 1", f"{vm} at 3 s ÷ step 1"]
    for c, t in zip(got.curves, (2, 3), strict=True):
        assert c.y[1:] == pytest.approx(t, rel=1e-4)  # 0/0 at d = 0
    diff = op(p, "compare_steps", "More", series=wire(path, "along"), step=0)
    c = value(p, store, diff).curves[-1]
    assert c.y == pytest.approx(200 * c.x * MPA, rel=1e-4, abs=1)
    # a history: each step's value against the first's
    peak = p.add(Operator(name="Peak", type="reduce", quantity="temperature:value",
                          inputs={"result": wire(study, "result")}))  # fmt: skip
    hist = op(p, "over_steps", "Peak T", value=wire(peak))
    h = value(p, store, hist)
    assert h.along == "steps" and h.x_label == "Time" and h.x_kind == "time"
    assert h.curves[0].y == pytest.approx([1, 2, 3])
    rel = op(p, "compare_steps", "Rel", series=wire(hist), step=0, comparison="ratio")
    assert value(p, store, rel).curves[0].y == pytest.approx([1, 2, 3])


def test_envelope_slope_area_and_match(setup):
    p, study, steel, path, store = setup
    env = op(p, "envelope", "Worst", series=wire(path, "along"))
    c = value(p, store, env).curves[0]
    assert c.y == pytest.approx(300 * c.x * MPA, rel=1e-5)
    last = op(p, "at_step", "Last", series=wire(path, "along"))
    slope = op(p, "slope", "Gradient", series=wire(last))
    s = value(p, store, slope)
    assert s.y_kind == "stress_gradient" and s.curves[0].y == pytest.approx(300 * MPA, rel=1e-4)
    area = op(p, "area_under", "Line force", series=wire(last))
    a = value(p, store, area)  # ∫ 300 d dd over 0..0.8 = 150 · 0.64 MPa·m
    assert a.kind == "force_per_length" and a.value == pytest.approx(96 * MPA, rel=1e-4)
    with pytest.raises(PuzzleError, match="pick one with At step"):
        value(p, store, op(p, "area_under", "All", series=wire(path, "along")))
    table = p.add(Operator(name="Gauges", type="table",
                           text="d [mm],strain\n100,1\n500,2\n"))  # fmt: skip
    onto = op(p, "match_x", "At gauges", series=wire(last), onto=wire(table))
    m = value(p, store, onto).curves[0]
    assert m.x == pytest.approx([0.1, 0.5]) and m.y == pytest.approx(
        [30 * MPA, 150 * MPA], rel=1e-4
    )


def test_units_follow_and_mismatches_are_refused(setup):
    p, study, steel, path, store = setup
    t_path = p.add(path.model_copy(update={"id": __import__("uuid").uuid4(), "name": "T",
                                           "quantity": "temperature:value"}))  # fmt: skip
    wrong = op(p, "add", "Nonsense", a=wire(path, "along"), b=wire(t_path, "along"))
    with pytest.raises(PuzzleError, match="units don't agree"):
        value(p, store, wrong)
    plot = op(p, "plot", "Both", value=wire(path, "along"), b=wire(t_path, "along"))
    with pytest.raises(PuzzleError, match="plot them apart"):
        value(p, store, plot)
    half = op(p, "divide_by", "Half", a=wire(path, "along"), b=Quantity(value=2, unit="MPa"))
    assert value(p, store, half).y_kind is None  # stress ÷ stress
    two = op(p, "multiply", "Two", a=Quantity(value=2, unit="N"), b=Quantity(value=3, unit="m"))
    assert value(p, store, two).value == pytest.approx(6)
    assert first_type(two, p) == "number"


def test_plot_takes_curves_a_limit_line_and_a_note(setup):
    p, study, steel, path, store = setup
    sy = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    limit = op(p, "limit_line", "Yield", value=wire(sy))
    note = op(p, "annotate", "Peak", series=wire(path, "along"))
    plot = op(p, "plot", "Wall stress", value=wire(note), b=wire(limit))
    out = ev.outputs(ev.Context(p, store), plot)
    curves = out["curves"]
    assert [c.style for c in curves.curves][-1] == "limit"
    assert curves.curves[-1].y[0] == pytest.approx(250 * MPA)
    marked = [c for c in curves.curves if c.mark]
    assert len(marked) == 1 and marked[0].mark[1] == pytest.approx(240 * MPA, rel=1e-5)
    assert marked[0].note.startswith("max ")
    assert out["out"].columns == [
        "curve",
        "Distance along the path",
        "Equivalent (von Mises) stress",
    ]


def test_a_table_wires_where_a_curve_is_wanted_and_model_tables_too(setup):
    p, study, steel, path, store = setup
    from puzzle.model.schema import PropertyTable, TablePoint
    from puzzle.operators import TYPES

    assert accepts(TYPES["normalise"].input("series"), "table")
    steel.tables["youngs_modulus"] = PropertyTable(
        unit="GPa",
        points=[
            TablePoint(temperature=293.15, value=200),
            TablePoint(temperature=673.15, value=170),
        ],
    )
    e = p.add(Operator(name="E(T)", type="model_value", target=steel.id,
                       property="tables.youngs_modulus"))  # fmt: skip
    shape = op(p, "normalise", "E/E0", series=wire(e))
    got = value(p, store, shape)
    assert got.x_kind == "temperature" and got.curves[0].y == pytest.approx([1, 0.85])


def test_arithmetic_on_model_values_is_a_parameter(setup):
    p, study, steel, path, store = setup
    sy = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    op(p, "divide_by", "Allowable", a=wire(sy), b=Quantity(value=1.5, unit="dimensionless"))
    assert parameter_values(p)["Allowable"] == pytest.approx(250e6 / 1.5)


def test_agents_get_a_curve_as_a_summary(setup):
    p, study, steel, path, store = setup
    got = ev.summary(ev.Context(p, store), op(p, "at_step", "Last", series=wire(path, "along")))
    data = got["outputs"]["out"]
    assert data["y"]["unit"] == "Pa" and data["curve_count"] == 1
    assert data["curves"][0]["max"] == pytest.approx(240 * MPA, rel=1e-5)
    assert len(data["curves"][0]["sample"]) == 5 and "points" in data["curves"][0]


def test_curve_operators_over_mcp(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import save_project

    p = Project(name="p")
    p.add(Material(name="Steel", properties={"yield_strength": Quantity(value=250, unit="MPa")}))
    save_project(p, tmp_path / "p")
    Session(tmp_path / "p").submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    call("operator_add", type="model_value", name="Sy",
         settings={"target": "Steel", "property": "Yield strength"})  # fmt: skip
    call("operator_add", type="table", name="Readings",
         settings={"text": "d [mm],stress [MPa]\n0,0\n10,100\n20,200\n"})  # fmt: skip
    out = call("operator_add", type="utilisation", name="Use",
               inputs={"a": {"source": "Readings"}, "b": {"source": "Sy"}})  # fmt: skip
    assert out["status"] == "applied", out
    got = call("operator_value", name="Use")["operators"][0]["outputs"]["out"]
    assert got["curves"][0]["max"] == pytest.approx(0.8)
    assert call("operator_help", type="Utilisation")["key"] == "utilisation"
    assert np.isfinite(got["curves"][0]["x_at_max"])


# --- field operators (ADR 0071 step 5) ---------------------------------------------------


def test_fields_divide_subtract_and_normalise(setup):
    """The arithmetic on result fields: σvM = 100·t·x MPa at the nodes x = 0, 1, 0, 0, 1.
    Step 3 ÷ the yield (250 MPa) is 1.2 at x = 1; step 3 − step 1 is 200·x MPa; step 3
    normalised is x. Each gives a field (drawn in the view), units followed through, and
    a field plus a temperature is refused."""
    p, study, steel, path, store = setup
    last = p.add(Operator(name="Last", type="filter", quantity="stress:von_mises",
                          inputs={"result": wire(study, "result")}))  # fmt: skip
    first = p.add(Operator(name="First", type="filter", quantity="stress:von_mises", step=0,
                           inputs={"result": wire(study, "result")}))  # fmt: skip
    model = p.add(
        Operator(
            name="Sy", type="model_value", target=steel.id, property="properties.yield_strength"
        )
    )
    use = op(p, "utilisation", a=wire(last), b=wire(model))
    assert first_type(use, p) == "field" and output_type(p, use.id, "out") == "field"
    got = value(p, store, use)
    assert isinstance(got, ev.FieldValue) and got.item.unit is None
    assert dict(zip(got.ids.tolist(), got.values.tolist(), strict=True)) == pytest.approx(
        {1: 0.0, 2: 1.2, 3: 0.0, 4: 0.0, 5: 1.2})  # fmt: skip
    grown = value(p, store, op(p, "subtract", a=wire(last), b=wire(first)))
    assert grown.item.unit == "pressure"
    assert grown.values == pytest.approx(200 * np.array([0, 1, 0, 0, 1]) * MPA)
    shape = value(p, store, op(p, "normalise", series=wire(last)))
    assert shape.values == pytest.approx([0, 1, 0, 0, 1])
    hot = p.add(Operator(name="Hot", type="filter", quantity="temperature",
                         inputs={"result": wire(study, "result")}))  # fmt: skip
    with pytest.raises(PuzzleError):
        value(p, store, op(p, "add", a=wire(last), b=wire(hot)))


def test_a_field_formula(setup):
    """Field formula: f − g over two steps' von Mises, and f / a with a wired 300 MPa."""
    p, study, steel, path, store = setup
    last = p.add(Operator(name="Last", type="filter", quantity="stress:von_mises",
                          inputs={"result": wire(study, "result")}))  # fmt: skip
    first = p.add(Operator(name="First", type="filter", quantity="stress:von_mises", step=0,
                           inputs={"result": wire(study, "result")}))  # fmt: skip
    change = p.add(Operator(name="Change", type="field_formula", expression="f - g",
                            inputs={"f": wire(last), "g": wire(first)}))  # fmt: skip
    got = value(p, store, change)
    assert got.values == pytest.approx(200 * np.array([0, 1, 0, 0, 1]) * MPA)
    assert got.label == "Change" and got.item.unit == "pressure"
    ratio = p.add(Operator(name="Ratio", type="field_formula", expression="f / a", inputs={
        "f": wire(last), "a": OperatorInput(value=Quantity(value=300, unit="MPa"))}))  # fmt: skip
    assert value(p, store, ratio).values == pytest.approx([0, 1, 0, 0, 1])
    bare = p.add(Operator(name="Bare", type="field_formula", expression="f * 2"))
    with pytest.raises(PuzzleError, match="field f input is not wired"):
        value(p, store, bare)


def test_slice_clip_and_contour_on_a_field(setup):
    """ParaView's filters on VTK (ADR 0070 level 2): σvM = 300·x MPa at the last step on
    test_operators' two tets. A plane x = 0.5 slices it at 150 MPa; a clip above it keeps
    the nodes at x = 1 (300 MPa); the 150 MPa contour lies on x = 0.5."""
    from puzzle.model.schema import ConstructionGeometry

    p, study, steel, path, store = setup
    vm = p.add(Operator(name="VM", type="filter", quantity="stress:von_mises",
                        inputs={"result": wire(study, "result")}))  # fmt: skip
    plane = p.add(ConstructionGeometry(name="Mid", type="plane", origin=(0.5, 0, 0),
                                       normal=(1, 0, 0)))  # fmt: skip
    cut = value(p, store, p.add(Operator(name="Cut", type="slice", target=plane.id,
                                         inputs={"field": wire(vm)})))  # fmt: skip
    frame = cut.frame
    assert frame.height > 0 and np.allclose(frame["x"].to_numpy(), 0.5)
    assert frame[frame.columns[-1]].to_numpy() == pytest.approx(150 * MPA, rel=1e-6)
    kept = value(p, store, p.add(Operator(name="Clip", type="clip", target=plane.id,
                                          keep="above", inputs={"field": wire(vm)})))  # fmt: skip
    assert isinstance(kept, ev.FieldValue)
    assert np.nanmax(kept.values) == pytest.approx(300 * MPA)
    assert int(np.isfinite(kept.values).sum()) == 2  # x = 1 at nodes 2 and 5
    level = OperatorInput(value=Quantity(value=150, unit="MPa"))
    iso = value(p, store, p.add(Operator(name="Iso", type="contour",
                                         inputs={"field": wire(vm), "level": level})))  # fmt: skip
    assert np.allclose(iso.frame["x"].to_numpy(), 0.5, atol=1e-9)
    nowhere = p.add(Operator(name="None", type="slice", inputs={"field": wire(vm)}))
    with pytest.raises(PuzzleError, match="pick its plane"):
        value(p, store, nowhere)


def test_a_report_gathers_checks_numbers_and_curves(setup, tmp_path):
    """Report (ADR 0071): two checks (one passing), a number and a curve into one HTML
    file in the project, the curve drawn, '1 of 2 checks pass' at the top."""
    p, study, steel, path, store = setup
    vm = p.add(Operator(name="Peak", type="reduce", quantity="stress:von_mises",
                        inputs={"result": wire(study, "result")}))  # fmt: skip
    lax = p.add(
        Operator(
            name="Lax",
            type="check",
            inputs={
                "value": wire(vm),
                "limit": OperatorInput(value=Quantity(value=400, unit="MPa")),
            },
        )
    )
    tight = p.add(
        Operator(
            name="Tight",
            type="check",
            inputs={
                "value": wire(vm),
                "limit": OperatorInput(value=Quantity(value=250, unit="MPa")),
            },
        )
    )
    sy = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    use = op(p, "utilisation", "Use", a=wire(path, "along"), b=wire(sy))
    report = p.add(Operator(name="Bracket checks", type="report", inputs={
        "a": wire(lax), "b": wire(tight), "c": wire(vm), "d": wire(use)}))  # fmt: skip
    written = ev.export(ev.Context(p, store), report, tmp_path)
    assert [w.relative_to(tmp_path).as_posix() for w in written] == [
        "exports/Bracket_checks.html"]  # fmt: skip
    page = written[0].read_text(encoding="utf-8")
    assert "1 of 2 checks pass" in page and "<h2>Lax</h2>" in page and "<h2>Peak</h2>" in page
    assert "class=pass" in page and "class=fail" in page
    assert "data:image/png;base64," in page or "<table>" in page  # the curve
    assert ev.shown(ev.Context(p, store), report)[0] == "to exports/Bracket_checks.html"


def test_a_field_from_another_mesh_is_mapped_first():
    """Two studies on different meshes (the second twice the size about the first's
    centre, so it holds the first): its field is interpolated onto the first's nodes
    before they are subtracted. σvM is
    linear in x (300·x MPa), so the mapped field is exact: the difference is zero."""
    from dataclasses import replace

    from test_operators import mesh, step

    from puzzle.post.quantities import Item

    first = mesh()
    item = Item("stress:von_mises", "Von Mises", "stress", "von_mises", "Stress", "pressure")
    a_values = 300 * first.coords[:, 0] * MPA
    a = ev.FieldValue(first, step(3), item, first.node_ids, a_values, "node", label="A")
    centre = first.coords.mean(axis=0)  # twice the size about its centre: it holds the first
    moved = replace(first, coords=(first.coords - centre) * 2.0 + centre)
    b_values = 300 * moved.coords[:, 0] * MPA
    b = ev.FieldValue(moved, step(3), item, moved.node_ids, b_values, "node", label="B")
    from puzzle.operators.series import field_formula

    got = field_formula(Operator(name="Diff", type="subtract"), "a - b", {"a": a, "b": b},
                        "Diff")  # fmt: skip
    inside = np.isfinite(got.values)
    assert inside.any()
    assert got.values[inside] == pytest.approx(0.0, abs=1e-3 * MPA)


def test_stream_tracer_and_glyph_on_a_vector_field(setup):
    """Stream tracer and Glyph (ADR 0070 level 2) on a uniform velocity of 2 m/s along x
    over test_operators' two tets: the streamlines from a seed line inside run along x
    at 2 m/s; the glyphs' arrows are the field at the nodes."""
    from test_operators import IDS

    from puzzle.model.schema import ConstructionGeometry
    from puzzle.results.model import Field

    p, study, steel, path, store = setup
    ev._READ.clear()  # runs are cached by id: this one gains a field
    for results in store.results.values():
        for s in results.steps:
            s.fields["velocity"] = Field("velocity", "node", ["x", "y", "z"], IDS,
                                         np.tile([2.0, 0.0, 0.0], (len(IDS), 1)))  # fmt: skip
    line = p.add(ConstructionGeometry(name="Seeds", type="path", start=(0.2, 0.1, 0.1),
                                      end=(0.2, 0.15, 0.1)))  # fmt: skip
    trace = p.add(Operator(name="Flow", type="stream", quantity="velocity", target=line.id,
                           inputs={"result": wire(study, "result"),
                                   "seeds": OperatorInput(value=Quantity(value=3))}))  # fmt: skip
    got = value(p, store, trace)
    frame = got.frame
    assert frame.height > 3
    speed = frame[frame.columns[-1]].to_numpy()
    assert speed == pytest.approx(2.0, rel=1e-6)
    assert np.ptp(frame["x"].to_numpy()) > 0.1  # it travelled along x
    assert np.ptp(frame["z"].to_numpy()) == pytest.approx(0.0, abs=1e-9)
    arrows = value(p, store, p.add(Operator(name="Arrows", type="glyph", quantity="velocity",
                                            inputs={"result": wire(study, "result")})))  # fmt: skip
    assert arrows.frame["velocity_x"].to_numpy() == pytest.approx(2.0)
    no_line = p.add(Operator(name="Lost", type="stream", quantity="velocity",
                             inputs={"result": wire(study, "result")}))  # fmt: skip
    with pytest.raises(PuzzleError, match="construction path"):
        value(p, store, no_line)
    ev._READ.clear()
