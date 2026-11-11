"""Numbers wired into the model (ADR 0071): a quantity that names a parameter.

A pressure of "= p" takes parameter p's value before solving (in the pressure's own
unit), at every sweep point too, and from a Number operator by its name; a quantity that
names no parameter saves and hashes as before (no `parameter` key), a bound one changes
a study's run key when its parameter does, and a name that isn't a parameter is
refused. Properties shows "= p: 2 MPa" and takes "= p" typed.
"""

from uuid import uuid4

import pytest

from puzzle.errors import PuzzleError
from puzzle.model import Feature, Quantity
from puzzle.model.schema import (
    Component,
    Operator,
    OperatorInput,
    ParameterSet,
    PartInstance,
    Physics,
    Project,
    SolveStep,
    Study,
    SweepParameter,
    SweepStep,
)
from puzzle.operators import bound_quantities, with_parameters


def _project(bound: bool = True):
    p = Project(name="p")
    p.add(ParameterSet(name="Loads", values={"p": Quantity(value=2, unit="MPa")}))
    pressure = Quantity(value=1, unit="MPa", parameter="p" if bound else None)
    comp = p.add(Component(name="Plate", parts=[PartInstance(name="Plate", geometry=uuid4())],
                           mesh=uuid4(), physics=[Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Plate/Bottom"),
        Feature(type="pressure", selection="Plate/Top", values={"p": pressure})])]))  # fmt: skip
    study = p.add(Study(name="Static", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, comp, study


def _pressure(project, comp):
    return project.objects[comp.id].physics[0].features[1].values["p"]


def test_a_bound_value_takes_its_parameter():
    p, comp, _ = _project()
    assert [path for path, _ in bound_quantities(p.objects[comp.id])] == [
        "physics.0.features.1.values.p"]  # fmt: skip
    got = _pressure(with_parameters(p), comp)
    assert got.value == pytest.approx(2.0) and got.unit == "MPa" and got.parameter == "p"
    assert _pressure(p, comp).value == 1.0  # the project passed in is unchanged


def test_unbound_quantities_save_as_before():
    q = Quantity(value=5, unit="mm")
    assert q.model_dump(mode="json") == {"value": 5.0, "unit": "mm"}
    p, comp, _ = _project(bound=False)
    assert with_parameters(p) is p


def test_a_number_operator_by_its_name():
    p, comp, _ = _project()
    three = OperatorInput(value=Quantity(value=3, unit="MPa"))
    p.add(Operator(name="load", type="number", inputs={"value": three}))
    feature = p.objects[comp.id].physics[0].features[1]
    feature.values["p"] = Quantity(value=1, unit="MPa", parameter="load")
    assert _pressure(with_parameters(p), comp).value == pytest.approx(3.0)


def test_each_sweep_point_gets_its_value():
    from puzzle import sweep

    p, comp, study = _project()
    study.steps = [SweepStep(type="parametric_sweep", parameters=[SweepParameter(
        name="p", values=[Quantity(value=v, unit="MPa") for v in (4, 6)])]),
        SolveStep(type="stationary")]  # fmt: skip
    got = [_pressure(sweep.apply(p, study, point), comp).value for point in study.sweep_points()]
    assert got == pytest.approx([4.0, 6.0])


def test_the_run_key_follows_the_parameter():
    from puzzle.project.graph import run_hashes

    p, comp, study = _project()
    before = run_hashes(p)[study.id]["calculix"]
    p.of_type(ParameterSet)[0].values["p"] = Quantity(value=5, unit="MPa")
    assert run_hashes(p)[study.id]["calculix"] != before


def test_an_unknown_parameter_is_refused():
    p, comp, _ = _project()
    p.objects[comp.id].physics[0].features[1].values["p"] = Quantity(
        value=1, unit="MPa", parameter="nope")  # fmt: skip
    with pytest.raises(PuzzleError, match="parameter 'nope'"):
        with_parameters(p)


def test_properties_show_and_take_a_bound_value():
    from puzzle.gui.properties import format_value, parse_value

    old = {"value": 2.0, "unit": "MPa", "parameter": "p"}
    assert format_value(old).startswith("= p: ")
    assert parse_value("= t", {"value": 2.0, "unit": "MPa"}) == {
        "value": 2.0, "unit": "MPa", "parameter": "t"}  # fmt: skip
    assert "parameter" not in parse_value("3 MPa", old)  # a number typed unbinds it


def test_the_schematic_wires_a_parameter_into_a_load():
    """A parameter set (or a Number) wired onto a load's node offers "Sets p, following
    p"; done, the schematic draws a data wire into the load's node, and the wire's
    removal leaves the value as it was, following nothing."""
    from puzzle.schematic import build
    from puzzle.schematic.links import choices, clear_commands, commands, existing

    p, comp, _ = _project(bound=False)
    loads = p.of_type(ParameterSet)[0]
    within = "physics.0.features.1"
    offered = choices(p, loads, comp, within)
    pick = next(c for c in offered if c.label == "Sets p, following p")
    edit = commands(pick, loads, comp)[0]
    assert edit["ops"][0]["path"] == "physics.0.features.1.values.p"
    assert edit["ops"][0]["value"]["parameter"] == "p"
    q = p.objects[comp.id].physics[0].features[1].values
    q["p"] = Quantity(**edit["ops"][0]["value"])
    drawn = [w for w in build(p).wires if w.target.startswith("feature:") and w.kind == "data"]
    assert drawn and drawn[0].source == str(loads.id)
    gone = existing(p, loads.id, p.objects[comp.id], within)
    undo = clear_commands(next(c for c in gone if c.op == "unbind"), comp)[0]
    assert undo["ops"][0]["value"] == {"value": 1.0, "unit": "MPa"}


def test_a_missing_parameter_is_a_problem():
    from puzzle.operators import problems

    p, comp, _ = _project()
    assert not [x for x in problems(p) if "follows parameter" in x.message]
    p.of_type(ParameterSet)[0].values.pop("p")
    found = [x for x in problems(p) if "follows parameter 'p'" in x.message]
    assert found and found[0].details["path"] == "physics.0.features.1.values.p"
