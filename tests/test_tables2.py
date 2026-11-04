"""ADR 0064 phase 2 on hand-made results (`test_operators`' two tets: T = t·x kelvin and
σxx = 100·t·x MPa at t = 1, 2, 3 s): joining, stacking, pivoting, filling and rolling
tables; the mesh, mode, probe-history, run and project tables; nearest nodes, a plane's
section, points along a path, clearance; text and the List's new modes."""

from __future__ import annotations

import math
from uuid import uuid4

import numpy as np
import pytest
from test_operators import Store, wire

from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    ConstructionGeometry,
    Operator,
    OperatorInput,
    Project,
    Quantity,
    ResultProbe,
    Study,
)
from puzzle.operators import evaluate as ev

pytest.importorskip("polars")


@pytest.fixture
def setup(tmp_path):
    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    return p, study, ev.Context(p, Store(study.id), root=tmp_path)


def op(p, type_, inputs=None, **kw):
    return p.add(Operator(name=kw.pop("name", type_ + str(len(p.objects))), type=type_,
                          inputs=inputs or {}, **kw))  # fmt: skip


def val(ctx, o, port="out"):
    return ev.value(ev.Context(ctx.project, ctx.store, root=ctx.root), o, port)


def typed(p, name, text):
    return op(p, "table", name=name, text=text)


def test_join_stack_and_a_difference(setup):
    p, _s, ctx = setup
    a = typed(p, "CalculiX", "node, vm [MPa]\n1, 100\n2, 200\n3, 300")
    b = typed(p, "Code_Aster", "node, vm [MPa]\n1, 101\n2, 198\n4, 400")
    j = op(p, "join", {"table": wire(a), "other": wire(b)}, by=["node"])
    got = val(ctx, j)
    assert got.columns == ["node", "vm", "vm (Code_Aster)"] and got.frame.height == 2
    assert got.kinds == [None, "pressure", "pressure"]
    j.how = "full"
    assert val(ctx, j).frame.height == 4
    j.how, j.suffix = "inner", "_aster"
    diff = op(p, "new_column", {"table": wire(j)}, new_name="diff", expression="vm_aster - vm")
    d = val(ctx, diff).frame["diff"].to_list()
    assert d == pytest.approx([1e6, -2e6])
    st = op(p, "stack", {"table": wire(a), "other": wire(b)})
    got = val(ctx, st)
    assert got.frame.height == 6 and got.frame["from"].to_list()[:1] == ["CalculiX"]
    bad = typed(p, "Bad", "node, vm [mm]\n9, 1")
    st.inputs["third"] = wire(bad)
    with pytest.raises(PuzzleError, match="is a pressure in one table and a length"):
        val(ctx, st)


def test_pivot_unpivot_distinct_fill_and_rolling(setup):
    p, study, ctx = setup
    t = op(p, "result_table", {"result": wire(study, "result")},
           quantities=["temperature:value"], steps="all", layout="long")  # fmt: skip
    temp = next(c for c in val(ctx, t).columns if "temperature" in c)
    pv = op(p, "pivot", {"table": wire(t)}, by=["node"], column="step", pick=[temp])
    wide = val(ctx, pv)
    assert wide.frame.height == 5 and f"{temp} · step 3" in wide.columns
    assert dict(zip(wide.columns, wide.kinds, strict=True))[f"{temp} · step 3"] == "temperature"
    un = op(p, "unpivot", {"table": wire(pv)}, by=["node"])
    long = val(ctx, un)
    assert long.frame.height == 15 and long.kinds[-1] == "temperature"
    ds = op(p, "distinct", {"table": wire(t)}, by=["part"])
    assert val(ctx, ds).frame.height == 1
    holes = typed(p, "Holes", "t [s], v [mm]\n1, 1\n2,\n3, 3\n4,")
    for fill, want in (("forward", [1, 1, 3, 3]), ("mean", [1, 2, 3, 2]), ("zero", [1, 0, 3, 0])):
        f = op(p, "fill_missing", {"table": wire(holes)}, fill=fill, pick=["v"])
        assert [x * 1e3 for x in val(ctx, f).frame["v"].to_list()] == pytest.approx(want)
    f.fill = "drop"
    assert val(ctx, f).frame.height == 2
    f.fill, f.inputs["value"] = "value", OperatorInput(value=Quantity(value=5, unit="mm"))
    assert val(ctx, f).frame["v"].to_list()[1] == pytest.approx(0.005)
    # the peak temperature so far at each node, along time: t x at x = 1 is 1, 2, 3
    r = op(p, "rolling", {"table": wire(t)}, column=temp, reduction="max", window=0,
           by=["time"], pick=["node"], new_name="peak")  # fmt: skip
    got = val(ctx, r).frame.filter(val(ctx, r).frame["node"] == 2).sort("time")
    assert got["peak"].to_list() == pytest.approx([1, 2, 3])
    r.window, r.reduction = 2, "mean"
    got = val(ctx, r).frame.filter(val(ctx, r).frame["node"] == 2).sort("time")
    assert got["peak"].to_list() == pytest.approx([1, 1.5, 2.5])


def test_the_mesh_table_by_hand(setup):
    p, study, ctx = setup
    m = op(p, "mesh_table", {"result": wire(study, "result")})
    got = val(ctx, m).frame.sort("element")
    # Left: the cube's corner tet, V = 1/6, edges 1, 1, 1, √2, √2, √2: 12 (1/2)^(2/3) / 9
    assert got["shape"][0] == pytest.approx(12 * 0.5 ** (2 / 3) / 9)
    assert got["aspect"][0] == pytest.approx(math.sqrt(2))
    assert got["shape"][1] == pytest.approx(1.0) and got["aspect"][1] == pytest.approx(1.0)
    assert got["part"].to_list() == ["Bar", "Bar"] and got["x"][0] == pytest.approx(0.25)


def test_probe_history_run_history_and_modes(setup):
    from test_operators import record

    from puzzle.results.model import Results, StepResult

    p, study, ctx = setup
    probe = p.add(ResultProbe(name="Hot end", source=study.id, quantity="temperature:value",
                              point=(1.0, 0.0, 0.0)))  # fmt: skip
    h = op(p, "history_table", {"probe": OperatorInput(source=probe.id, port="out")})
    got = val(ctx, h)
    assert got.frame["Hot end"].to_list() == pytest.approx([1, 2, 3])
    assert got.frame["step"].to_list() == [1, 2, 3] and got.kinds[-1] == "temperature"
    rh = op(p, "run_history", {"source": wire(study, "result")})
    runs = val(ctx, rh)
    assert runs.frame["solver"].to_list() == ["calculix"] and runs.kinds[
        runs.columns.index("seconds")] == "time"  # fmt: skip
    ctx.store.records.append(record("modes", study.id, 9))  # newer than the first
    ctx.store.results["modes"] = Results("calculix", [
        StepResult(1, "modal", 12.5, mode=1), StepResult(1, "modal", 40.0, mode=2)])  # fmt: skip
    md = op(p, "mode_table", {"result": wire(study, "result")})
    got = val(ctx, md)  # the newest run is read: the modal one
    assert got.frame["frequency"].to_list() == [12.5, 40.0] and got.kinds[-1] == "frequency"


def test_nearest_section_and_divide(setup):
    p, study, ctx = setup
    pts = typed(p, "Gauges", "gauge, x [m], y [m], z [m]\ng1, 0.98, 0.01, 0\ng2, 0.6, 0.6, 0.6")
    near = op(p, "nearest", {"result": wire(study, "result"), "points": wire(pts)},
              quantities=["temperature:value"])  # fmt: skip
    got = val(ctx, near)
    assert got.frame["node"].to_list() == [2, 5]  # (1, 0, 0) and (1, 1, 1)
    assert got.frame["distance"][0] == pytest.approx(math.hypot(0.02, 0.01))
    temp = next(c for c in got.columns if "temperature" in c)
    assert got.frame[temp].to_list() == pytest.approx([3.0, 3.0])  # t x at t = 3, x = 1
    plane = p.add(ConstructionGeometry(name="Mid", type="plane", origin=(0.5, 0, 0),
                                       normal=(1, 0, 0)))  # fmt: skip
    sec = op(p, "section", {"result": wire(study, "result")}, target=plane.id,
             quantities=["temperature:value"])  # fmt: skip
    cut = val(ctx, sec)
    temp = next(c for c in cut.columns if "temperature" in c)
    assert np.allclose(cut.frame["x"].to_numpy(), 0.5)
    assert cut.frame[temp].to_list() == pytest.approx([1.5] * cut.frame.height)  # 3 × 0.5
    path = p.add(ConstructionGeometry(name="Along", type="path", start=(0, 0, 0),
                                      end=(0.2, 0, 0)))  # fmt: skip
    dv = op(p, "divide", {"count": OperatorInput(value=Quantity(value=5))}, target=path.id)
    got = val(ctx, dv)
    assert got.frame["s"].to_list() == pytest.approx([0, 0.05, 0.1, 0.15, 0.2])


def test_text_list_modes_and_vectors(setup):
    p, _s, ctx = setup
    n = op(p, "number", {"value": OperatorInput(value=Quantity(value=212.34, unit="MPa"))},
           name="Peak")  # fmt: skip
    tx = op(p, "text", {"x": wire(n)}, template="σ max = {x:.1f}, or {x}")
    assert val(ctx, tx).text() == "σ max = 212340000.0 Pa, or 2.123e+08 Pa"  # SI shown
    src = typed(p, "L", "v [mm]\n1\n2\n4\n8")
    col = op(p, "list", {"list": wire(src)}, operation="column", column="v")

    def mode(how, **inputs):
        o = op(p, "list", {"list": wire(col), **inputs}, operation=how)
        return [round(v * 1e3, 9) for v in val(ctx, o).values]  # in mm

    def num(v, unit=""):
        return OperatorInput(value=Quantity(value=v, unit=unit or "dimensionless"))

    assert mode("differences") == [1, 2, 4]
    assert mode("cumulative_sum") == [1, 3, 7, 15]
    assert mode("shift", index=num(1)) == [2, 4, 8, 1]
    assert mode("every_nth", index=num(2)) == [1, 4]
    assert mode("repeat", index=num(2)) == [1, 2, 4, 8, 1, 2, 4, 8]
    assert mode("remap", index=num(0, "mm"), end=num(7, "mm")) == pytest.approx([0, 1, 3, 7])
    edges = typed(p, "E", "e [mm]\n3\n6")
    ecol = op(p, "list", {"list": wire(edges)}, operation="column", column="e")
    o = op(p, "list", {"list": wire(col), "other": wire(ecol)}, operation="bin")
    assert val(ctx, o).values.tolist() == [0, 0, 1, 2]
    o.operation = "union"
    assert len(val(ctx, o).values) == 6
    vec = op(p, "expression", expression="dist(1 m, 2 m, 2 m, 0 m, 0 m, 0 m)")
    assert val(ctx, vec).value == pytest.approx(3.0)
    vec.expression = "degrees(angle(1, 0, 0, 0, 1, 0))"
    assert val(ctx, vec).value == pytest.approx(90)
    vec.expression = "norm(3 m, 4 m, 0 m)"
    assert val(ctx, vec).value == pytest.approx(5) and val(ctx, vec).kind == "length"


def test_a_plot_of_a_table_and_the_project_table(setup):
    from puzzle.materials import add_library_material

    p, _s, ctx = setup
    t = typed(p, "Peaks", "part, peak [MPa]\nA, 120\nB, 210")
    pl = op(p, "plot", {"value": wire(t)}, by=["part"], column="peak", chart="bar")
    got = val(ctx, pl)
    assert got.columns == ["part", "peak"] and got.rows[1] == ["B", pytest.approx(210e6)]
    add_library_material(p, "structural_steel")
    pt = op(p, "project_table", objects="materials")
    got = val(ctx, pt)
    assert got.frame["material"].to_list()[0].startswith("Structural steel")
    assert got.frame["density"][0] == pytest.approx(7850)
    assert dict(zip(got.columns, got.kinds, strict=True))["youngs_modulus"] == "pressure"


def test_clearance_and_part_masses(tmp_path):
    pytest.importorskip("OCP")
    from optimise_beam import beam_project

    from puzzle.model.schema import Component, PartInstance
    from puzzle.project import save_project

    p, _ = beam_project()  # a steel box 200 x 20 x 6 mm from the origin
    comp = next(iter(p.of_type(Component)))
    comp.parts.append(
        PartInstance(name="B2", geometry=comp.parts[0].geometry, translation=(0.205, 0.0, 0.0))
    )  # 5 mm past its end
    root = tmp_path / "beam"
    save_project(p, root)
    ctx = ev.Context(p, None, root=root)
    c = p.add(Operator(name="Gap", type="clearance", target=comp.id, selection="B", other="B2"))
    got = ev.value(ctx, c)
    assert got.value == pytest.approx(0.005, abs=1e-6) and got.kind == "length"
    parts = p.add(Operator(name="Parts", type="project_table", objects="parts"))
    t = ev.value(ev.Context(p, None, root=root), parts).frame
    row = t.filter(t["part"] == "B")
    assert row["mass"][0] == pytest.approx(0.2 * 0.02 * 0.006 * 7850, rel=1e-3)
    assert t.filter(t["part"] == "B2")["mass"][0] is None  # no material assigned to it
