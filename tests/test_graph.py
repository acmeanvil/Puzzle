import pytest

from puzzle.model import (
    Geometry,
    Material,
    NamedSelection,
    Quantity,
    Shootout,
    Study,
    SweepParameter,
    SweepStep,
    Transfer,
)
from puzzle.project import StudyGraph, content_hashes, run_hashes, stale_studies


def _mm(*values):
    return [Quantity(value=v, unit="mm") for v in values]


def _study(project, name):
    return next(s for s in project.of_type(Study) if s.name == name)


def test_nothing_stale_when_hashes_match(bracket):
    assert stale_studies(bracket, content_hashes(bracket)) == set()


def test_material_change_stales_every_study_using_it(bracket):
    computed = content_hashes(bracket)
    bracket.of_type(Material)[0].properties["youngs_modulus"] = Quantity(value=70, unit="GPa")
    assert stale_studies(bracket, computed) == {s.id for s in bracket.of_type(Study)}


def test_upstream_study_change_flows_through_transfers(bracket):
    computed = content_hashes(bracket)
    _study(bracket, "Thermal").solver = "calculix"
    assert stale_studies(bracket, computed) == {
        _study(bracket, n).id for n in ("Thermal", "Static", "Modal")
    }


def test_downstream_change_leaves_upstream_fresh(bracket):
    computed = content_hashes(bracket)
    _study(bracket, "Modal").solver = "elmer"
    assert stale_studies(bracket, computed) == {_study(bracket, "Modal").id}


def test_renaming_does_not_stale(bracket):
    computed = content_hashes(bracket)
    bracket.of_type(Geometry)[0].name = "Bracket v2"
    assert stale_studies(bracket, computed) == set()


def test_run_order_follows_transfers(bracket):
    order = StudyGraph(bracket).run_order()
    names = [bracket.objects[sid].name for sid in order]
    assert names == ["Thermal", "Static", "Modal"]


def test_downstream(bracket):
    graph = StudyGraph(bracket)
    assert graph.downstream(_study(bracket, "Thermal").id) == {
        _study(bracket, "Static").id,
        _study(bracket, "Modal").id,
    }


def test_transfer_cycle_is_rejected(bracket):
    bracket.add(
        Transfer(
            name="Loop",
            source=_study(bracket, "Modal").id,
            field="displacement",
            target=_study(bracket, "Thermal").id,
            role="initial_condition",
        )
    )
    with pytest.raises(ValueError, match="cycle"):
        content_hashes(bracket)


def test_global_selection_change_stales_its_users(bracket):
    computed = content_hashes(bracket)
    bracket.of_type(Geometry)[0].selections.append(NamedSelection(name="Rib", entity="face"))
    assert stale_studies(bracket, computed) == {s.id for s in bracket.of_type(Study)}


def test_each_sweep_point_is_its_own_run(bracket):
    static = _study(bracket, "Static")
    mm = [Quantity(value=v, unit="mm") for v in (1, 2, 3)]
    static.steps.insert(0, SweepStep(parameters=[SweepParameter(name="fillet_r", values=mm)]))
    runs = run_hashes(bracket)
    assert len(set(runs[static.id]["calculix"])) == 3
    assert len(runs[_study(bracket, "Thermal").id]["elmer"]) == 1


def test_adding_a_sweep_point_keeps_existing_runs(bracket):
    static = _study(bracket, "Static")
    sweep = SweepStep(parameters=[SweepParameter(name="fillet_r", values=_mm(1, 2))])
    static.steps.insert(0, sweep)
    before = set(run_hashes(bracket)[static.id]["calculix"])
    sweep.parameters[0].values.append(Quantity(value=3, unit="mm"))
    after = set(run_hashes(bracket)[static.id]["calculix"])
    assert before < after


def test_shootout_adds_runs_without_staling_the_primary(bracket):
    static = _study(bracket, "Static")
    computed = content_hashes(bracket)
    primary = run_hashes(bracket)[static.id]["calculix"]

    static.shootout = Shootout(solvers=["elmer"])
    runs = run_hashes(bracket)[static.id]
    assert stale_studies(bracket, computed) == set()
    assert runs["calculix"] == primary
    assert runs["elmer"] != primary


def test_shootout_runs_survive_a_primary_solver_swap(bracket):
    static = _study(bracket, "Static")
    static.shootout = Shootout(solvers=["elmer"])
    elmer_run = run_hashes(bracket)[static.id]["elmer"]
    static.solver, static.shootout = "elmer", Shootout(solvers=["calculix"])
    assert run_hashes(bracket)[static.id]["elmer"] == elmer_run
