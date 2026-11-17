"""ADR 0024 decision 3: preferences never change results (ADR 0090, ADR-E-6, ADR-E-7).

One project holding one of every before-solving operator type and a results pipeline
(a result table, rows kept, rounded, a number taken out, a check, a report) is worked
out under different preferences: SI against engineering and imperial display units, a
user's own odd units, the long and the wide table layout. The parameters, every
operator's value in SI, the tables' contents and the run keys must be the same under
each; only how they are shown may differ. Rounding happened in the shown units and a
result table's layout followed the preference, so a parameter (and the model it built)
and a check's verdict depended on whose settings.toml was present.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from test_operators import Store, wire

import puzzle.settings as settings
from puzzle import operators
from puzzle.model.schema import (
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    Operator,
    OperatorInput,
    PartInstance,
    Project,
    Quantity,
    Study,
)
from puzzle.operators import GEOMETRY_READERS, TYPES, is_pre
from puzzle.operators import evaluate as ev
from puzzle.project.graph import run_hashes

PREFERENCES = [
    {"units.system": "si", "results.table_layout": "long"},
    {"units.system": "engineering", "results.table_layout": "wide"},
    {"units.system": "imperial", "results.table_layout": "wide"},
    {"units.system": "custom", "results.table_layout": "long",
     "units.custom.length": "in", "units.custom.pressure": "ksi", "units.custom.force": "kip"},
]  # fmt: skip


def use(prefs: dict) -> None:
    s = settings.Settings()
    for key, value in prefs.items():
        s.set(key, value)
    s.save()
    settings.reload()


def q(v, unit="dimensionless"):
    return OperatorInput(value=Quantity(value=v, unit=unit))


TEXT = "t [mm], F [kN], name\n4.4, 1.5, a\n6.6, 2.5, b\n5.25, 2.0, a"
# Settings per type where the defaults don't fit the typed table (column names, ...).
SETTINGS = {
    "table": {"text": TEXT},
    "expression": {"expression": "2 * x + 0.35 mm"},
    "slider": {"low": Quantity(value=1, unit="mm"), "high": Quantity(value=9, unit="mm"),
               "increment": Quantity(value=0.3, unit="mm")},
    "list": {"operation": "mean"},
    "columns": {"pick": ["t", "F as force"]},
    "filter_rows": {"condition": "t > 5 mm"},
    "sort": {"by": ["t"], "descending": True},
    "top_n": {"column": "t"},
    "new_column": {"new_name": "u", "expression": "t * 1.5"},
    "convert_units": {"column": "t", "unit": "in"},
    "round": {"decimals": 2},
    "group": {"by": ["name"], "aggregations": ["max(t)", "mean(F) as f"]},
    "to_number": {"column": "t", "reduction": "max"},
    "lookup": {"by": ["t"], "column": "F"},
    "join": {"by": ["name"]},
    "pivot": {"by": ["name"], "column": "name", "pick": ["t"]},
    "unpivot": {"by": ["name"], "pick": ["t"]},
    "distinct": {"by": ["name"]},
    "rolling": {"column": "t", "window": 2},
    "text": {"template": "{x}"},
    "choice": {"options": [Quantity(value=8, unit="mm"), Quantity(value=10, unit="mm")]},
    "slider2d": {"low": Quantity(value=1, unit="mm"), "high": Quantity(value=3, unit="mm"),
                 "low2": Quantity(value=2, unit="MPa"), "high2": Quantity(value=4, unit="MPa")},
    "merge_points": {"spacing": Quantity(value=1, unit="mm")},
    "thin_points": {"spacing": Quantity(value=1, unit="mm")},
    "curve_formula": {"expression": "y * 2"},
    "annotate": {"reduction": "max"},
    "envelope": {"reduction": "max"},
}  # fmt: skip


def pre_solve_project() -> tuple[Project, dict]:
    """One of every before-solving type (but those that read the geometry and scripts),
    each wired from a typed table, a list, points or a curve as its ports take."""
    p = Project("prefs")
    table = p.add(Operator(name="T", type="table", text=TEXT))
    rng = p.add(Operator(name="R", type="range", inputs={
        "start": q(1, "mm"), "end": q(3, "mm"), "count": q(3)}))  # fmt: skip
    pts = p.add(Operator(name="P", type="points", inputs={"x": wire(rng), "y": wire(rng),
                                                           "z": wire(rng)}))  # fmt: skip
    curve = p.add(Operator(name="C", type="curve_from_table", by=["t"], column="F",
                           inputs={"table": wire(table)}))  # fmt: skip
    made = {"table": table, "range": rng, "points": pts, "curve_from_table": curve}
    for key in sorted(TYPES):
        t = TYPES[key]
        if key in made or key in GEOMETRY_READERS or key == "script" \
                or key not in operators.DATA:  # fmt: skip
            continue
        inputs = {}
        for spec in t.inputs:
            if "table" in spec.types and spec.types[0] != "points":
                inputs[spec.key] = wire(table)
            elif "points" in spec.types:
                inputs[spec.key] = wire(pts)
            elif "series" in spec.types:
                inputs[spec.key] = wire(curve)
            elif "list" in spec.types:
                inputs[spec.key] = wire(rng)
            elif spec.typed and ("number" in spec.types):
                inputs[spec.key] = q(2.5, "mm") if spec.key not in ("count", "index") \
                    else q(2)  # fmt: skip
        made[key] = p.add(Operator(name=f"{key} op", type=key, inputs=inputs,
                                   **SETTINGS.get(key, {})))  # fmt: skip
    rounded = p.add(Operator(name="Rounded", type="round", decimals=2,
                             inputs={"table": wire(made["new_column"])}))  # fmt: skip
    p.add(Operator(name="thick", type="to_number", column="u",
                   inputs={"table": wire(rounded)}))  # fmt: skip
    in_mm = p.add(Operator(name="Rounded in mm", type="round", decimals=1, unit="mm",
                           inputs={"table": wire(table)}))  # fmt: skip
    p.add(Operator(name="thick_mm", type="to_number", column="t",
                   inputs={"table": wire(in_mm)}))  # fmt: skip
    # A study whose geometry reads `thick`, and one that reads nothing.
    mesh = p.add(MeshRecipe(name="M"))
    geo = p.add(Geometry(name="Plate", history=[HistoryStep(kind="box", name="B", args={
        "dx": "thick", "dy": 0.01, "dz": 0.01})]))  # fmt: skip
    comp = p.add(Component(name="C", mesh=mesh.id,
                           parts=[PartInstance(name="Plate", geometry=geo.id)]))  # fmt: skip
    p.add(Study(name="Reads thick", component=comp.id, solver="calculix", steps=[]))
    return p, made


def _plain(data):
    """A summary without what is only display: texts, a curve's default note ("max
    212 MPa", Annotate's label), and error messages (codes kept)."""
    if isinstance(data, dict):
        if "code" in data and "message" in data:
            return {"code": data["code"]}
        return {k: _plain(v) for k, v in data.items() if k not in ("text", "shown", "note")}
    if isinstance(data, list):
        return [_plain(v) for v in data]
    if isinstance(data, float):
        return round(data, 12) if abs(data) > 1e-300 else 0.0
    return data


def worked_out(p, store=None) -> dict:
    ctx = ev.Context(p, store)
    return {
        "parameters": operators.parameter_values(p),
        "keys": run_hashes(p),
        "values": {o.name: _plain(ev.summary(ctx, o)) for o in p.of_type(Operator)},
    }


def test_every_before_solving_operator_is_the_same_under_any_preferences():
    p, made = pre_solve_project()
    assert all(is_pre(p, o) for o in made.values())
    seen = []
    for prefs in PREFERENCES:
        use(prefs)
        seen.append(worked_out(p))
    for got in seen[1:]:
        assert got["parameters"] == seen[0]["parameters"]
        assert got["keys"] == seen[0]["keys"]
        for name, value in seen[0]["values"].items():
            assert got["values"][name] == value, name
    first = seen[0]
    errors = [n for n, v in first["values"].items() if "error" in v]
    assert len(errors) <= len(first["values"]) // 5, errors  # most work out, not just fail
    # Round in SI unless it names a unit: 0.0066 m to 2 places is 0.01 m; in mm, 6.6 mm.
    assert first["parameters"]["thick"] == pytest.approx(0.01)
    assert first["parameters"]["thick_mm"] == pytest.approx(0.0066)


def results_project():
    p = Project("results")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    table = p.add(
        Operator(
            name="Stress table",
            type="result_table",
            layout="setting",
            quantities=["stress:von_mises"],
            steps="all",
            inputs={"result": wire(study, "result")},
        )
    )  # an older file's
    hot = p.add(Operator(name="Hot rows", type="filter_rows", condition="von_mises > 150 MPa",
                         inputs={"table": wire(table)}))  # fmt: skip
    rounded = p.add(Operator(name="Rounded", type="round", decimals=0, significant=False,
                             inputs={"table": wire(hot)}))  # fmt: skip
    in_mpa = p.add(Operator(name="Rounded MPa", type="round", decimals=0, unit="MPa",
                            inputs={"table": wire(hot)}))  # fmt: skip
    peak = p.add(Operator(name="Peak", type="to_number", column="von_mises",
                          inputs={"table": wire(in_mpa)}))  # fmt: skip
    p.add(Operator(name="Peak raw", type="to_number", column="von_mises",
                   inputs={"table": wire(rounded)}))  # fmt: skip
    p.add(
        Operator(
            name="Strength",
            type="check",
            inputs={
                "value": wire(peak),
                "limit": OperatorInput(value=Quantity(value=300.4, unit="MPa")),
            },
        )
    )
    p.add(Operator(name="Report", type="report", inputs={"a": wire(peak)}))
    return p, Store(study.id, project=p)


def test_a_results_pipeline_is_the_same_under_any_preferences():
    p, store = results_project()
    seen = []
    for prefs in PREFERENCES:
        use(prefs)
        seen.append(worked_out(p, store))
    for got in seen[1:]:
        assert got == seen[0]
    values = seen[0]["values"]
    assert values["Hot rows"]["outputs"]["out"]["row_count"] == 4  # long, as before
    assert values["Peak"]["outputs"]["out"]["value"] == pytest.approx(300e6)
    assert values["Strength"]["outputs"]["out"]["passed"] is True


def test_a_new_result_table_takes_the_layout_once_when_it_is_made(tmp_path):
    from puzzle.commands import Session
    from puzzle.project import save_project

    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    save_project(p, tmp_path)
    s = Session(tmp_path)
    use({"results.table_layout": "wide"})
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Wide", "type": "result_table",
        "inputs": {"result": {"source": str(study.id), "port": "result"}}}})  # fmt: skip
    use({"results.table_layout": "long"})
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Long", "type": "result_table",
        "inputs": {"result": {"source": str(study.id), "port": "result"}}}})  # fmt: skip
    ops = {o.name: o for o in s.project.of_type(Operator)}
    assert ops["Wide"].layout == "wide" and ops["Long"].layout == "long"
    old = s.project.add(Operator(name="Old", type="result_table", layout="setting",
                                 inputs={"result": wire(study, "result")}))  # fmt: skip
    save_project(s.project, tmp_path)
    s = Session(tmp_path)
    s.submit({"command": "edit", "object": "Old", "ops": [
        {"op": "set", "path": "steps", "value": "all"}]})  # fmt: skip
    assert s.project.objects[old.id].layout == "long"  # as it was read, now its own
