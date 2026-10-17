from uuid import uuid4

import pytest
from pydantic import ValidationError

from puzzle.model import (
    ComparisonMetric,
    Component,
    Contact,
    Geometry,
    NamedSelection,
    Quantity,
    Shootout,
    SolveStep,
    Study,
    SweepParameter,
    SweepStep,
)


def _mm(*values):
    return [Quantity(value=v, unit="mm") for v in values]


def _component(project, name):
    return next(c for c in project.of_type(Component) if c.name == name)


def test_example_projects_are_consistent(bracket, assembly):
    assert bracket.validate() == []
    assert assembly.validate() == []


def test_unknown_selection_is_reported(bracket):
    _component(bracket, "Bracket").physics[1].features[0].selection = "Bracket/Missing"
    assert bracket.validate() == ["component 'Bracket' uses unknown selection 'Bracket/Missing'"]


def test_part_selections_need_the_part_prefix(bracket):
    _component(bracket, "Bracket").physics[1].features[0].selection = "Bolt holes"
    assert bracket.validate() == ["component 'Bracket' uses unknown selection 'Bolt holes'"]


def test_contact_selections_are_checked(assembly):
    _component(assembly, "Bracket on plate").contacts[0].target = "Plate/Nowhere"
    assert assembly.validate() == [
        "component 'Bracket on plate' uses unknown selection 'Plate/Nowhere'"
    ]


def test_local_geometry_needs_an_owner():
    with pytest.raises(ValidationError):
        Geometry(name="Loose", source="x.step", scope="local")


def test_frictional_contact_needs_a_coefficient():
    with pytest.raises(ValidationError):
        Contact(name="c", type="frictional", source="a", target="b")
    with pytest.raises(ValidationError):
        Contact(name="c", type="bonded", source="a", target="b", friction=0.2)


def test_local_geometry_cannot_be_borrowed(assembly):
    plate = next(g for g in assembly.of_type(Geometry) if g.name == "Plate")
    _component(assembly, "Bracket").parts[0].geometry = plate.id
    assert (
        "component 'Bracket' uses geometry 'Plate', which is local to another component"
        in assembly.validate()
    )


def test_copy_shares_global_geometry_and_copies_local(assembly):
    original = _component(assembly, "Bracket on plate")
    copy = assembly.copy_component(original.id, "Variant")

    assert copy.parts[0].geometry == original.parts[0].geometry  # global: shared
    plate, plate_copy = (assembly.get(c.parts[1].geometry, Geometry) for c in (original, copy))
    assert plate_copy.id != plate.id
    assert plate_copy.owner == copy.id
    assert plate_copy.selections == plate.selections

    plate_copy.selections.append(NamedSelection(name="Edge", entity="edge"))
    assert len(plate.selections) == 2  # no link back to the parent
    assert assembly.validate() == []


def test_global_selection_edits_are_seen_by_every_user(bracket):
    geo = bracket.of_type(Geometry)[0]
    geo.selections.append(NamedSelection(name="Rib", entity="face", members=["rib"]))
    comp = _component(bracket, "Bracket")
    copy = bracket.copy_component(comp.id, "Copy")
    assert "Bracket/Rib" in bracket.selection_names(comp)
    assert "Bracket/Rib" in bracket.selection_names(copy)


def test_grid_sweep_runs_every_combination():
    sweep = SweepStep(
        parameters=[
            SweepParameter(name="fillet_r", values=_mm(1, 2, 3)),
            SweepParameter(name="thickness", values=_mm(4, 5)),
        ]
    )
    assert len(sweep.points()) == 6


def test_paired_sweep_needs_equal_lengths():
    with pytest.raises(ValidationError):
        SweepStep(
            combination="paired",
            parameters=[
                SweepParameter(name="a", values=_mm(1, 2)),
                SweepParameter(name="b", values=_mm(1)),
            ],
        )


def test_nested_sweeps_multiply():
    study = Study(
        name="s",
        component=uuid4(),
        solver="calculix",
        steps=[
            SweepStep(parameters=[SweepParameter(name="a", values=_mm(1, 2))]),
            SweepStep(parameters=[SweepParameter(name="b", values=_mm(1, 2, 3))]),
            SolveStep(type="stationary"),
        ],
    )
    assert len(study.sweep_points()) == 6
    assert len(study.solve_steps()) == 1


def test_sweep_must_precede_solve_steps_and_use_known_parameters(bracket):
    static = next(s for s in bracket.of_type(Study) if s.name == "Static")
    static.steps.append(SweepStep(parameters=[SweepParameter(name="width", values=_mm(1))]))
    problems = bracket.validate()
    assert "study 'Static': sweep steps must come first" in problems
    assert "study 'Static' sweeps unknown parameter 'width'" in problems


def test_transfer_from_swept_study_needs_a_point(bracket):
    thermal = next(s for s in bracket.of_type(Study) if s.name == "Thermal")
    thermal.steps.insert(
        0, SweepStep(parameters=[SweepParameter(name="fillet_r", values=_mm(1, 2))])
    )
    assert bracket.validate() == [
        "transfer 'Temperature' needs source_point for swept parameter 'fillet_r'"
    ]


def test_quantity_converts_to_si():
    assert Quantity(value=2, unit="mm").to_si() == pytest.approx(0.002)
    assert Quantity(value=68.9, unit="GPa").to_si() == pytest.approx(68.9e9)


def test_bonded_contacts_default_to_conformal_meshing():
    bonded = Contact(name="c", type="bonded", source="a", target="b")
    assert bonded.capability == "bonded_conformal"
    tied = Contact(name="c", type="bonded", source="a", target="b", meshing="nonconformal")
    assert tied.capability == "bonded_nonconformal"
    sliding = Contact(name="c", type="frictionless", source="a", target="b")
    assert sliding.effective_meshing == "nonconformal"


def test_only_bonded_contacts_can_be_conformal():
    with pytest.raises(ValidationError):
        Contact(name="c", type="frictionless", source="a", target="b", meshing="conformal")


def test_step_physics_must_exist_on_the_component(bracket):
    static = next(s for s in bracket.of_type(Study) if s.name == "Static")
    static.steps[0].physics = ["magnetic_fields"]
    assert bracket.validate() == [
        "study 'Static' solves magnetic_fields, which component 'Bracket' lacks"
    ]


def test_shootout_solvers_must_differ_from_the_primary(bracket):
    static = next(s for s in bracket.of_type(Study) if s.name == "Static")
    static.shootout = Shootout(
        solvers=["calculix"],
        metrics=[ComparisonMetric(quantity="displacement", norm="relative_l2", tolerance=0.02)],
    )
    assert bracket.validate() == ["study 'Static': shoot-out solvers must differ"]
