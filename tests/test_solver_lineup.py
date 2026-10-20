import pytest
from pydantic import ValidationError

from puzzle.adapters import available_adapters, check_study
from puzzle.adapters.code_aster import CodeAsterAdapter
from puzzle.adapters.code_saturne import CodeSaturneAdapter
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.model import (
    Component,
    CustomEquation,
    Feature,
    Geometry,
    MeshRecipe,
    PartInstance,
    Physics,
    Quantity,
    Shootout,
    SolveStep,
    Study,
    Unknown,
)


def _study(project, name):
    return next(s for s in project.of_type(Study) if s.name == name)


def _component_study(project, name, physics, steps, solver, **component):
    comp = project.add(
        Component(
            name=name,
            parts=[PartInstance(name="Bracket", geometry=project.of_type(Geometry)[0].id)],
            mesh=project.of_type(MeshRecipe)[0].id,
            physics=physics,
            **component,
        )
    )
    return project.add(Study(name=name, component=comp.id, solver=solver, steps=steps))


def test_all_six_solvers_are_registered():
    assert set(available_adapters()) == {
        "calculix",
        "elmer",
        "code_aster",
        "fenicsx",
        "code_saturne",
        "openfoam",  # ADR 0016
    }


def test_custom_pde_needs_an_equation():
    with pytest.raises(ValidationError):
        Physics(type="custom_pde")
    with pytest.raises(ValidationError):
        Physics(
            type="heat_transfer",
            equation=CustomEquation(unknowns=[Unknown(name="u")], weak_form="u*v_u*dx"),
        )


def test_custom_pde_runs_only_on_fenicsx(bracket):
    poisson = Physics(
        type="custom_pde",
        equation=CustomEquation(
            unknowns=[Unknown(name="u", degree=2)],
            weak_form="k*inner(grad(u), grad(v_u))*dx - f*v_u*dx",
            coefficients={"k": Quantity(value=1), "f": Quantity(value=10)},
        ),
        features=[Feature(type="dirichlet", selection="Bracket/Bolt holes")],
    )
    study = _component_study(
        bracket, "Poisson", [poisson], [SolveStep(type="stationary")], "fenicsx"
    )
    assert bracket.validate() == []
    assert FenicsxAdapter().check(bracket, study) == []
    assert CodeAsterAdapter().check(bracket, study) == [
        "code_aster does not support custom_pde physics"
    ]


def test_em_shootout_between_elmer_and_fenicsx(induction):
    study = _study(induction, "Induction heating")
    study.shootout = Shootout(solvers=["fenicsx"])
    assert check_study(induction, study) == {"elmer": [], "fenicsx": []}


def test_nonlinear_contact_study_on_code_aster(assembly):
    study = _study(assembly, "Assembly static")
    assert CodeAsterAdapter().check(assembly, study) == []
    assert FenicsxAdapter().check(assembly, study) == [
        "fenicsx does not support frictional contact ('Bracket on plate')"
    ]


def test_cfd_runs_on_code_saturne(bracket):
    flow = Physics(
        type="fluid_flow",
        settings={"turbulence_model": "k-omega-sst"},
        features=[
            Feature(
                type="velocity_inlet",
                selection="Bracket/Heated face",
                values={"U": Quantity(value=2, unit="m/s")},
            ),
            Feature(type="wall", selection="Bracket/Bolt holes"),
        ],
    )
    study = _component_study(
        bracket,
        "Cooling flow",
        [flow, Physics(type="heat_transfer")],
        [SolveStep(type="stationary")],
        "code_saturne",
        couplings=["non_isothermal_flow"],
    )
    assert CodeSaturneAdapter().check(bracket, study) == []
    assert CodeAsterAdapter().check(bracket, study) == [
        "code_aster does not support fluid_flow physics",
        "code_aster does not support non_isothermal_flow coupling",
    ]


def test_code_saturne_is_3d_only(bracket):
    study = _component_study(
        bracket,
        "Planar flow",
        [Physics(type="fluid_flow")],
        [SolveStep(type="stationary")],
        "code_saturne",
        dimension="2d_planar",
    )
    assert CodeSaturneAdapter().check(bracket, study) == [
        "code_saturne does not support 2d_planar models"
    ]
