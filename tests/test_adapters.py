import pytest

from puzzle.adapters import check_study
from puzzle.adapters.calculix import CalculixAdapter
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.model import (
    Component,
    Quantity,
    Shootout,
    SolveStep,
    Study,
    SweepParameter,
    SweepStep,
)


def _study(project, name):
    return next(s for s in project.of_type(Study) if s.name == name)


def test_supported_study_passes_check(bracket):
    assert CalculixAdapter().check(bracket, _study(bracket, "Static")) == []


def test_unsupported_step_is_reported_per_physics(bracket):
    static = _study(bracket, "Static")
    static.steps.append(SolveStep(type="frequency_domain", physics=["solid_mechanics"]))
    assert CalculixAdapter().check(bracket, static) == [
        "calculix does not support frequency_domain solid_mechanics"
    ]


def test_sweep_steps_never_reach_the_adapter(bracket):
    static = _study(bracket, "Static")
    mm = [Quantity(value=v, unit="mm") for v in (1, 2)]
    static.steps.insert(0, SweepStep(parameters=[SweepParameter(name="fillet_r", values=mm)]))
    assert CalculixAdapter().check(bracket, static) == []


def test_contact_support_comes_from_the_capability_matrix(assembly):
    study = _study(assembly, "Assembly static")
    contact = next(c for c in assembly.of_type(Component) if c.contacts).contacts[0]
    assert CalculixAdapter().check(assembly, study) == []
    assert ElmerAdapter().check(assembly, study) == [
        "elmer does not support frictional contact ('Bracket on plate')"
    ]

    contact.type, contact.friction = "bonded", None
    assert ElmerAdapter().check(assembly, study) == []
    contact.meshing = "nonconformal"
    assert ElmerAdapter().check(assembly, study) == []
    assert CalculixAdapter().check(assembly, study) == []


def test_experimental_contacts_need_opt_in(assembly):
    study = _study(assembly, "Assembly static")
    assert ElmerAdapter().check(assembly, study, allow_experimental=True) == []
    assert CalculixAdapter().check(assembly, study, allow_experimental=True) == []


def test_induction_heating_runs_on_elmer_only(induction):
    study = _study(induction, "Induction heating")
    assert induction.validate() == []
    assert ElmerAdapter().check(induction, study) == []
    assert CalculixAdapter().check(induction, study) == [
        "calculix does not support 2d_axisymmetric models",
        "calculix does not support magnetic_fields physics",
        "calculix does not support joule_heating coupling",
    ]


def test_shootout_checks_every_solver(bracket):
    static = _study(bracket, "Static")
    static.shootout = Shootout(solvers=["elmer"])
    assert check_study(bracket, static) == {"calculix": [], "elmer": []}

    static.shootout = Shootout(solvers=["openfoam"])
    assert check_study(bracket, static)["openfoam"] == ["no adapter installed for openfoam"]


def test_time_schemes_are_checked(bracket):
    from puzzle.adapters import available_adapters
    from puzzle.adapters.common import InputError, time_scheme
    from puzzle.model import SolveStep, Study

    comp = next(iter(bracket.of_type(Component)))
    step = SolveStep(
        type="transient",
        physics=["heat_transfer"],
        settings={"time_step": 1.0, "end_time": 10.0, "time_scheme": "crank_nicolson"},
    )
    study = bracket.add(Study(name="cn", component=comp.id, solver="calculix", steps=[step]))
    adapters = available_adapters()
    assert any("crank_nicolson" in p for p in adapters["calculix"]().check(bracket, study))
    assert not any("crank_nicolson" in p for p in adapters["elmer"]().check(bracket, study))
    assert time_scheme(SolveStep(type="transient")) == ("backward_euler", 1.0)
    assert time_scheme(
        SolveStep(type="transient", settings={"time_scheme": "theta", "theta": 0.57})
    ) == ("theta", 0.57)
    with pytest.raises(InputError) as err:
        time_scheme(SolveStep(type="transient", settings={"time_scheme": "leapfrog"}))
    assert err.value.code == "invalid"
