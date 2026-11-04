"""ADR 0064 phase 3: tables before a solve. A table operator feeding an imported field
(its rows the points and values, reshaped first), a Toggle switching a feature off, a
Choice of presets, a 2D slider's two parameters, points merged and thinned, harmonic
parts, dates in a run's history, and tags that never change a solve."""

from __future__ import annotations

from uuid import uuid4

import pytest

from puzzle.model.schema import (
    Component,
    Feature,
    ImportedField,
    Operator,
    OperatorInput,
    Physics,
    Project,
    Quantity,
    Study,
)
from puzzle.operators import evaluate as ev
from puzzle.operators import parameter_values

pytest.importorskip("polars")


def wire(o, port="out"):
    return OperatorInput(source=o.id, port=port)


def test_a_table_feeds_an_imported_field(tmp_path):
    from puzzle import fields

    p = Project("p")
    raw = p.add(Operator(name="Scan", type="table", text="x [mm], y [mm], z [mm], T [degC], q\n"
                         "0, 0, 0, 20, 1\n10, 0, 0, 40, 1\n20, 0, 0, 60, 0"))  # fmt: skip
    kept = p.add(Operator(name="Kept", type="filter_rows", condition="q > 0",
                          inputs={"table": wire(raw)}))  # fmt: skip
    data = fields.from_table(p, kept.id)
    assert data["format"] == "table" and {c["role"] for c in data["columns"]} == {
        "x", "y", "z", "value"}  # fmt: skip
    f = p.add(ImportedField(**data))
    assert f.dependencies() == [kept.id]
    cloud = fields.cloud(p, tmp_path, f)
    assert cloud.points.ravel().tolist() == pytest.approx([0, 0, 0, 0.01, 0, 0])  # q > 0
    assert cloud.values["T"].tolist() == pytest.approx([293.15, 313.15])  # kelvin, as SI
    kept.condition = "q >= 0"
    assert len(fields.cloud(p, tmp_path, f).points) == 3
    from puzzle.project.graph import content_hashes

    before = content_hashes(p)[f.id]
    kept.condition = "q > 0"
    assert content_hashes(p)[f.id] != before  # a reshaped table stales what it feeds


def test_a_toggle_switches_a_feature_off():
    from puzzle.suppress import applied

    p = Project("p")
    sw = p.add(Operator(name="Heated", type="toggle"))
    from puzzle.model.schema import Geometry, MeshRecipe, NamedSelection, PartInstance

    g = p.add(Geometry(name="G", source="g.step", selections=[
        NamedSelection(name="Top", entity="face", members=["face-1"])]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=1, unit="mm")))
    comp = p.add(Component(name="C", parts=[PartInstance(name="B", geometry=g.id)],
                           mesh=mesh.id, physics=[Physics(type="heat_transfer", features=[
        Feature(type="heat_flux", selection="B/Top", when="Heated"),
        Feature(type="temperature", selection="B/Base")])]))  # fmt: skip
    assert parameter_values(p)["Heated"] == 1.0
    assert len(applied(p).objects[comp.id].physics[0].features) == 2
    sw.inputs["value"] = OperatorInput(value=Quantity(value=0))
    assert parameter_values(p)["Heated"] == 0.0
    got = applied(p).objects[comp.id]
    assert [f.type for f in got.physics[0].features] == ["temperature"]
    sw.inputs["value"] = OperatorInput(value=Quantity(value=1))
    assert len(applied(p).objects[comp.id].physics[0].features) == 2


def test_choice_and_the_2d_slider():
    p = Project("p")
    bolt = p.add(Operator(name="d", type="choice", labels=["M8", "M10", "M12"],
                          options=[Quantity(value=v, unit="mm") for v in (8, 10, 12)],
                          inputs={"index": OperatorInput(value=Quantity(value=1))}))  # fmt: skip
    got = ev.value(ev.Context(p, None), bolt)
    assert got.value == pytest.approx(0.010) and got.label == "M10" and got.kind == "length"
    assert ev.value(ev.Context(p, None), bolt, "options").values.tolist() == pytest.approx(
        [0.008, 0.010, 0.012])  # fmt: skip
    pad = p.add(
        Operator(
            name="sec",
            type="slider2d",
            low=Quantity(value=10, unit="mm"),
            high=Quantity(value=30, unit="mm"),
            low2=Quantity(value=4, unit="mm"),
            high2=Quantity(value=8, unit="mm"),
            inputs={"value": OperatorInput(value=Quantity(value=12, unit="mm"))},
        )
    )
    params = parameter_values(p)
    assert params["d"] == pytest.approx(0.010)
    assert params["sec_x"] == pytest.approx(0.012) and params["sec_y"] == pytest.approx(0.006)
    assert pad.type == "slider2d"


def test_points_merged_thinned_and_harmonic_parts():
    p = Project("p")
    pts = p.add(
        Operator(
            name="P",
            type="table",
            text=(
                "x [mm], y [mm], z [mm], v\n0, 0, 0, 1\n0.4, 0, 0, 3\n10, 0, 0, 5\n10.2, 0.1, 0, 7"
            ),
        )
    )
    mg = p.add(Operator(name="M", type="merge_points", spacing=Quantity(value=1, unit="mm"),
                        inputs={"table": wire(pts)}))  # fmt: skip
    got = ev.value(ev.Context(p, None), mg).frame
    assert got["v"].to_list() == [2.0, 6.0] and got["x"][0] == pytest.approx(0.0002)
    th = p.add(Operator(name="T", type="thin_points", spacing=Quantity(value=5, unit="mm"),
                        inputs={"table": wire(pts)}))  # fmt: skip
    assert ev.value(ev.Context(p, None), th).frame["v"].to_list() == [1.0, 5.0]
    h = p.add(Operator(name="H", type="expression",
                       expression="real(2 mm, 60) + imag(2 mm, 90)"))  # fmt: skip
    assert ev.value(ev.Context(p, None), h).value == pytest.approx(0.001 + 0.002)


def test_dates_in_a_run_history_and_tags(tmp_path):
    from test_operators import Store

    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    rh = p.add(
        Operator(
            name="Runs",
            type="run_history",
            inputs={"source": OperatorInput(source=study.id, port="result")},
        )
    )
    late = p.add(Operator(name="Late", type="filter_rows", condition='started > "2026-10-01"',
                          inputs={"table": wire(rh)}))  # fmt: skip
    ctx = ev.Context(p, Store(study.id))
    assert ev.value(ctx, rh).frame["started"].dtype.is_temporal()
    assert ev.value(ev.Context(p, ctx.store), late).frame.height == 1  # 2026-10-01 00:00 < 1 s
    late.condition = 'started > "2026-10-02"'
    assert ev.value(ev.Context(p, ctx.store), late).frame.height == 0
    from puzzle.project.graph import content_hashes

    before = content_hashes(p)[study.id]
    study.tags = ["baseline", "for the report"]
    assert content_hashes(p)[study.id] == before
