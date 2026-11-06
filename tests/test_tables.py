"""Table operators (ADR 0064 phase 1) on hand-made results (`test_operators`' two tets:
T = t·x kelvin and σxx = 100·t·x MPa at t = 1, 2, 3 s), and on typed tables and files:
values, units and row counts checked by hand."""

from __future__ import annotations

import numpy as np
import pytest
from test_operators import XYZ, Store, wire

from puzzle.errors import PuzzleError
from puzzle.model.schema import Operator, OperatorInput, Project, Quantity, Study
from puzzle.operators import evaluate as ev

pytest.importorskip("polars")


@pytest.fixture
def setup(tmp_path):
    from uuid import uuid4

    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    return p, study, ev.Context(p, Store(study.id), root=tmp_path)


def op(p, type_, source=None, port="out", key="table", **kw):
    inputs = {key: wire(source, port)} if source is not None else {}
    return p.add(Operator(name=kw.pop("name", type_), type=type_, inputs=inputs, **kw))


def value(ctx, o):
    return ev.value(ev.Context(ctx.project, ctx.store, root=ctx.root), o)


def test_a_result_table_long_and_wide(setup):
    p, study, ctx = setup
    t = op(
        p,
        "result_table",
        study,
        "result",
        "result",
        quantities=["temperature:value", "stress:von_mises"],
        steps="all",
        layout="long",
    )
    got = value(ctx, t)
    assert got.columns[:7] == ["node", "x", "y", "z", "part", "step", "time"]
    assert got.frame.height == 15  # five nodes, three steps
    assert dict(zip(got.columns, got.kinds, strict=True))["time"] == "time"
    rows = got.frame.filter(got.frame["step"] == 3).sort("node")
    temp = next(c for c in got.columns if "temperature" in c)
    assert rows[temp].to_list() == pytest.approx(3 * XYZ[:, 0])  # T = t x at t = 3 s
    assert set(rows["part"].to_list()) == {"Bar"}
    t.layout = "wide"
    wide = value(ctx, t)
    assert wide.frame.height == 5 and f"{temp} · step 2" in wide.columns
    t.selection, t.layout, t.steps = "Bar/Left", "long", "one"
    assert value(ctx, t).frame["node"].to_list() == [1, 2, 3, 4]


def test_filter_sort_top_new_column_group_and_number(setup):
    p, study, ctx = setup
    t = op(p, "result_table", study, "result", "result", quantities=["stress:von_mises"],
           steps="all", layout="long")  # fmt: skip
    vm = "von_mises"
    hot = op(p, "filter_rows", t, condition=f'{vm} > 150 MPa and part == "Bar"')
    got = value(ctx, hot)  # σ = 100 t x MPa > 150: x = 1 at t 2 and 3 (nodes 2 and 5)
    assert sorted(got.frame["step"].to_list()) == [2, 2, 3, 3]
    hot.syntax, hot.condition = "polars", f'pl.col("{vm}") > 150e6'
    assert value(ctx, hot).frame.height == 4
    hot.condition = '__import__("os")'
    with pytest.raises(PuzzleError, match="isn't allowed"):
        value(ctx, hot)
    hot.syntax, hot.condition = "puzzle", f"{vm} > 150 mm"
    with pytest.raises(PuzzleError, match="units don't agree"):
        value(ctx, hot)
    top = op(p, "top_n", t, column=vm)
    top.inputs["count"] = OperatorInput(value=Quantity(value=2))
    assert value(ctx, top).frame[vm].to_list() == pytest.approx([300e6, 300e6])
    top.by = ["step"]  # the largest per step: 100 t MPa at t = 1, 2, 3
    per = value(ctx, top).frame
    assert per.height == 6 and per.filter(per["step"] == 1)[vm].max() == pytest.approx(100e6)
    col = op(p, "new_column", t, new_name="margin", expression=f"250 MPa / {vm} - 1")
    got = value(ctx, col)
    m = got.frame.filter(got.frame[vm] > 0)["margin"].to_list()
    assert min(m) == pytest.approx(250 / 300 - 1)
    assert dict(zip(got.columns, got.kinds, strict=True))["margin"] is None
    aggs = [f"max({vm})", "count()", f"quantile({vm}, 0.5) as median"]
    grp = op(p, "group", t, by=["step"], aggregations=aggs)
    g = value(ctx, grp)
    assert g.columns == ["step", f"max {vm}", "count", "median"]
    assert g.frame[f"max {vm}"].to_list() == pytest.approx([100e6, 200e6, 300e6])
    assert g.frame["count"].to_list() == [5, 5, 5]
    assert dict(zip(g.columns, g.kinds, strict=True)) == {
        "step": None,
        f"max {vm}": "pressure",
        "count": None,
        "median": "pressure",
    }
    num = op(p, "to_number", grp, column=f"max {vm}", reduction="max")
    n = value(ctx, num)
    assert n.value == pytest.approx(300e6) and n.kind == "pressure"
    num.by, num.column = [f"max {vm}"], "step"  # the step where the peak is largest
    assert value(ctx, num).value == 3


def test_columns_convert_round_and_exports(setup):
    import polars as pl

    p, study, ctx = setup
    t = op(p, "table", name="Loads", text="case, F [kN], L [mm]\nA, 1.2345, 100\nB, 2.5, 250")
    cols = op(p, "columns", t, pick=["case", "F as force"])
    got = value(ctx, cols)
    assert got.columns == ["case", "force"] and got.kinds == [None, "force"]
    assert got.frame["force"].to_list() == pytest.approx([1234.5, 2500])  # SI inside
    conv = op(p, "convert_units", cols, column="force", unit="kN")
    rnd = op(p, "round", conv, decimals=1)
    got = value(ctx, rnd)
    assert got.frame["force"].to_list() == pytest.approx([1200, 2500])  # 1.2 kN, shown
    exp = op(p, "export", rnd, key="value", file="out/loads.csv")
    ev.export(ev.Context(p, ctx.store, root=ctx.root), exp, ctx.root)
    text = (ctx.root / "out/loads.csv").read_text(encoding="utf-8").splitlines()
    assert text[0] == "case,force [kN]" and text[1] == "A,1.2"
    exp.file = "out/loads.xlsx"
    ev.export(ev.Context(p, ctx.store, root=ctx.root), exp, ctx.root)
    back = pl.read_excel(ctx.root / "out/loads.xlsx", engine="openpyxl")
    assert back.columns == ["case", "force [kN]"] and back["force [kN]"].to_list() == [1.2, 2.5]
    exp.file = "out/loads.parquet"
    ev.export(ev.Context(p, ctx.store, root=ctx.root), exp, ctx.root)
    assert pl.read_parquet(ctx.root / "out/loads.parquet")["force [kN]"].to_list() == [1.2, 2.5]
    # the files read back as tables, SI again, stacked with a file column
    f = op(p, "file", file="out/loads.*")  # CSV, Excel and Parquet stack, SI each
    both = value(ctx, f)
    assert both.frame.height == 6 and both.frame["force"].to_list() == [1200, 2500] * 3
    (ctx.root / "out/notes.doc").write_text("x", encoding="utf-8")
    f.file = "out/notes.doc"
    with pytest.raises(PuzzleError, match="not CSV, Parquet or Excel"):
        value(ctx, f)
    f.file = "out/loads.parquet"
    got = value(ctx, f)
    assert got.columns == ["case", "force"] and got.frame["force"].to_list() == [1200, 2500]
    (ctx.root / "tests").mkdir()
    for i in (1, 2):
        (ctx.root / f"tests/run{i}.csv").write_text(
            f"gauge, strain\ng1, {i}e-4\n", encoding="utf-8"
        )
    f.file = "tests/*.csv"
    got = value(ctx, f)
    assert got.frame.height == 2 and got.frame["file"].to_list() == ["run1.csv", "run2.csv"]
    f.file = "../outside.csv"
    with pytest.raises(PuzzleError, match="inside the project"):
        value(ctx, f)


def test_tables_keep_working_where_lists_read_them(setup):
    p, _study, ctx = setup
    t = op(p, "table", name="T", text="t [mm]\n4\n6\n8")
    lst = p.add(Operator(name="Ts", type="list", operation="column", column="t",
                         inputs={"list": wire(t)}))  # fmt: skip
    got = value(ctx, lst)
    assert got.values.tolist() == pytest.approx([0.004, 0.006, 0.008]) and got.kind == "length"
    table = value(ctx, t)
    assert table.rows == [[0.004], [0.006], [0.008]] and "3 rows × 1" in table.text()


def test_lookup_list_statistics_and_verdicts(setup):
    p, _study, ctx = setup
    curve = op(p, "table", name="Curve", text="T [degC], E [GPa]\n20, 210\n200, 190\n400, 160")
    at = OperatorInput(value=Quantity(value=110, unit="degC"))
    look = p.add(Operator(name="E at T", type="lookup", by=["T"], column="E",
                          inputs={"table": wire(curve), "at": at}))  # fmt: skip
    got = value(ctx, look)  # halfway between 20 and 200 °C: 200 GPa
    assert got.value == pytest.approx(200e9) and got.kind == "pressure"
    look.interpolation = "step"  # the row at or before 110 °C
    assert value(ctx, look).value == pytest.approx(210e9)
    look.interpolation = "nearest"
    look.inputs["at"] = OperatorInput(value=Quantity(value=150, unit="degC"))
    assert value(ctx, look).value == pytest.approx(190e9)
    nums = op(p, "table", name="N", text="v [MPa], w\n100, 1\n200, 1\n600, 2")
    stats = {}
    for how in ("mean", "median", "std", "percentile", "weighted_mean"):
        lst = p.add(Operator(name=f"L {how}", type="list", operation=how, column="v",
                             inputs={"list": wire(nums)}))  # fmt: skip
        if how == "percentile":
            lst.inputs["index"] = OperatorInput(value=Quantity(value=50))
        if how == "weighted_mean":
            w = p.add(Operator(name="W", type="list", operation="column", column="w",
                               inputs={"list": wire(nums)}))  # fmt: skip
            lst.inputs["weights"] = wire(w)
        stats[how] = value(ctx, lst)
    assert stats["mean"].values[0] == pytest.approx(300e6) and stats["mean"].kind == "pressure"
    assert stats["median"].values[0] == pytest.approx(200e6)
    assert stats["percentile"].values[0] == pytest.approx(200e6)
    assert stats["std"].values[0] == pytest.approx(np.std([100e6, 200e6, 600e6]))
    assert stats["weighted_mean"].values[0] == pytest.approx((100 + 200 + 1200) / 4 * 1e6)
    from puzzle.model.schema import ParameterSet

    p.add(ParameterSet(name="Loads", values={"stress": Quantity(value=200, unit="MPa")}))
    ok = p.add(Operator(name="OK", type="expression", expression="stress <= 250 MPa"))
    v = ev.value(ev.Context(p, ctx.store), ok, "passes")
    assert v.passed and v.margin == pytest.approx(0.25) and v.kind == "pressure"
    ok.expression = "stress > 250 MPa or stress < 0 MPa"
    v = ev.value(ev.Context(p, ctx.store), ok, "passes")
    assert not v.passed and v.short().startswith("Fail")
    ok.expression = "round(stress / MPa / 3, 1) * MPa + clamp(5 MPa, 0 MPa, 2 MPa)"
    assert ev.value(ev.Context(p, ctx.store), ok).value == pytest.approx(68.7e6)
    ok.expression = "degrees(atan2(1 m, 1 m))"
    assert ev.value(ev.Context(p, ctx.store), ok).value == pytest.approx(45)


def test_measure_from_the_geometry(tmp_path):
    pytest.importorskip("OCP")
    from optimise_beam import beam_project

    from puzzle.model.schema import Component
    from puzzle.project import save_project

    p, _ = beam_project()  # a steel box 200 x 20 x t, t = 6 mm
    root = tmp_path / "beam"
    save_project(p, root)
    comp = next(iter(p.of_type(Component)))
    m = p.add(Operator(name="M", type="measure", target=comp.id, selection="B",
                       measurement="volume"))  # fmt: skip
    ctx = ev.Context(p, None, root=root)
    assert ev.value(ctx, m).value == pytest.approx(0.2 * 0.02 * 0.006)
    m.measurement = "mass"  # structural steel, 7850 kg/m³
    got = ev.value(ev.Context(p, None, root=root), m)
    assert got.value == pytest.approx(0.2 * 0.02 * 0.006 * 7850, rel=1e-3) and got.kind == "mass"
    m.selection, m.measurement = "B/Tip", "area"
    assert ev.value(ev.Context(p, None, root=root), m).value == pytest.approx(0.02 * 0.006)
    m.selection, m.measurement = "B", "centroid_x"
    assert ev.value(ev.Context(p, None, root=root), m).value == pytest.approx(0.1)
    m.measurement = "size_z"
    assert ev.value(ev.Context(p, None, root=root), m).value == pytest.approx(0.006)
    from puzzle.operators import is_pre

    assert is_pre(p, m)  # a geometry's measure is known before solving


def test_the_table_command(tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.project import save_project

    p = Project("p")
    p.add(Operator(name="Loads", type="table", text="case, F [kN]\nA, 1.5\nB, 2.5"))
    save_project(p, tmp_path / "p")
    assert main(["table", str(tmp_path / "p"), "Loads"]) == 0
    out = capsys.readouterr().out
    assert "2 rows × 2" in out and "case" in out
    assert main(["table", str(tmp_path / "p"), "Loads", "--csv", str(tmp_path / "l.csv")]) == 0
    assert (tmp_path / "l.csv").read_text(encoding="utf-8").splitlines()[
        1
    ] == "A,1500"  # SI display units
