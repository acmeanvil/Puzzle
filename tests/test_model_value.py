"""Model value (ADR 0071 step 3): the model's own values as an operator's output, read as
the model is now, for people (Properties, the schematic) and agents (MCP) alike."""

from __future__ import annotations

import asyncio

import pytest

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    Material,
    MeshRecipe,
    Operator,
    OperatorInput,
    ParameterSet,
    PartInstance,
    Physics,
    Project,
    PropertyTable,
    Quantity,
    TablePoint,
)  # fmt: skip
from puzzle.operators import evaluate as ev
from puzzle.operators.model_values import read, readable


def _project():
    p = Project(name="p")
    steel = p.add(Material(name="Steel", properties={
        "yield_strength": Quantity(value=250, unit="MPa"),
        "youngs_modulus": Quantity(value=200, unit="GPa"),
        "density": Quantity(value=7850, unit="kg/m^3")},
        tables={"youngs_modulus": PropertyTable(unit="GPa", points=[
            TablePoint(temperature=293.15, value=200),
            TablePoint(temperature=673.15, value=170)])}))  # fmt: skip
    geo = p.add(Geometry(name="Bracket", source="b.step"))
    mesh = p.add(MeshRecipe(name="Fine", global_size=Quantity(value=2, unit="mm")))
    load = Feature(type="pressure", selection="Bracket/Inner",
                   values={"p": Quantity(value=1.5, unit="MPa")})  # fmt: skip
    comp = p.add(Component(name="Assembly", parts=[PartInstance(name="Bracket", geometry=geo.id)],
                           mesh=mesh.id,
                           physics=[Physics(type="solid_mechanics", features=[load])]))  # fmt: skip
    params = p.add(ParameterSet(name="Design", values={"t": Quantity(value=6, unit="mm")}))
    return p, steel, comp, mesh, params


def test_what_each_object_holds_is_listed_in_the_trees_words():
    p, steel, comp, mesh, params = _project()
    by = {r.label: r for r in readable(p, steel)}
    assert by["Yield strength"].path == "properties.yield_strength"
    assert by["Yield strength"].kind == "pressure"
    assert by["Young's modulus against temperature"].table
    labels = {r.label: r.path for r in readable(p, comp)}
    assert labels["Pressure on Bracket/Inner: p"] == "physics.0.features.0.values.p"
    assert any(r.path == "global_size" for r in readable(p, mesh))
    assert [(r.path, r.label) for r in readable(p, params)] == [("values.t", "t")]


def test_values_are_read_in_si_and_a_table_against_temperature():
    p, steel, *_ = _project()
    y = read(p, steel, "properties.yield_strength")
    assert y.value == pytest.approx(250e6) and y.kind == "pressure" and y.label == "Yield strength"
    table = read(p, steel, "tables.youngs_modulus")
    assert table.frame.columns == ["temperature", "Young's modulus against temperature"]
    assert table.frame.row(1)[1] == pytest.approx(170e9)
    with pytest.raises(Exception, match="has no value 'properties.nope'"):
        read(p, steel, "properties.nope")


def test_the_operator_follows_the_model_and_feeds_a_check():
    p, steel, *_ = _project()
    yield_ = p.add(Operator(name="Yield", type="model_value", target=steel.id,
                            property="properties.yield_strength"))  # fmt: skip
    stress = p.add(
        Operator(
            name="Stress",
            type="number",
            inputs={"value": OperatorInput(value=Quantity(value=200, unit="MPa"))},
        )
    )
    check = p.add(Operator(name="Under yield", type="check", inputs={
        "value": OperatorInput(source=stress.id),
        "limit": OperatorInput(source=yield_.id)}))  # fmt: skip
    ctx = ev.Context(p, None)
    assert ev.value(ctx, check).passed
    p.objects[steel.id].properties["yield_strength"] = Quantity(value=180, unit="MPa")
    assert not ev.value(ev.Context(p, None), check).passed  # the model changed


def test_it_is_a_before_solving_value_a_parameter_by_its_name():
    from puzzle.operators import first_type, is_pre, parameter_values

    p, steel, *_ = _project()
    op = p.add(Operator(name="Sy", type="model_value", target=steel.id,
                        property="properties.yield_strength"))  # fmt: skip
    assert is_pre(p, op) and first_type(op) == "number"
    assert parameter_values(p)["Sy"] == pytest.approx(250e6)
    op.property = "tables.youngs_modulus"
    assert first_type(op) == "table"


def test_the_dropdowns_and_a_wire_choose_what_it_reads():
    from puzzle.gui.operators import model_value_choices
    from puzzle.schematic import links

    p, steel, comp, *_ = _project()
    op = p.add(Operator(name="Sy", type="model_value"))
    targets = model_value_choices(p, op, "target")
    assert targets[0] == ("Steel (material)", str(steel.id))  # materials first
    op.target = steel.id
    assert ("Yield strength", "properties.yield_strength") in model_value_choices(p, op, "property")
    pick = links.choices(p, steel, op)
    assert [c.label for c in pick] == ["What it reads"]
    measure = p.add(Operator(name="Mass", type="measure"))
    assert links.choices(p, steel, measure) == []  # Measure reads geometry, not a material


def test_agents_read_model_values_over_mcp(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import save_project

    p, *_ = _project()
    save_project(p, tmp_path / "p")
    Session(tmp_path / "p").submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    assert "Steel" in {o["name"] for o in call("model_values")["objects"]}
    held = {v["label"]: v for v in call("model_values", object="Steel")["values"]}
    assert held["Yield strength"]["kind"] == "pressure"
    out = call("operator_add", type="model_value", name="Sy",
               settings={"target": "Steel", "property": "Yield strength"})  # fmt: skip
    assert out["status"] == "applied"
    assert call("operator_value", name="Sy")["operators"][0]["outputs"]["out"][
        "value"
    ] == pytest.approx(250e6)
    card = call("operator_help", type="model_value")
    assert card["name"] == "Model value" and "yield" in card["purpose"]
