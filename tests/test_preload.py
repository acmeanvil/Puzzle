"""The preload step (puzzle.preload): put in front of a static step that tightens bolts
and loads at once, the loads moved after it, and the run key changed with it."""

from uuid import uuid4

from puzzle import preload
from puzzle.model.schema import (
    BeamFastener,
    Component,
    Feature,
    Geometry,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project.graph import run_hashes


def q(v, unit):
    return Quantity(value=v, unit=unit)


def _project(features, steps=None, connectors=(), solver="calculix"):
    p = Project(name="joint")
    mesh = p.add(MeshRecipe(name="Tets"))
    geo = p.add(Geometry(name="B", source="b.step"))
    physics = [Physics(type="solid_mechanics", features=features)]
    comp = p.add(Component(name="Joint", mesh=mesh.id, connectors=list(connectors),
                           parts=[PartInstance(name="B", geometry=geo.id)],
                           physics=physics))  # fmt: skip
    study = p.add(Study(name="Load", component=comp.id, solver=solver,
                        steps=steps or [SolveStep(type="stationary")]))  # fmt: skip
    return p, comp, study


PRE = Feature(type="bolt_pretension", selection="B/Section", values={"F": q(10, "kN")})
FIXED = Feature(type="fixed", selection="B/Bottom")
PULL = Feature(type="force", selection="B/Top", values={"z": q(4, "kN")})


def test_a_preload_step_goes_in_front_of_a_loaded_step():
    p, comp, study = _project([FIXED, PRE, PULL])
    got, new, k = preload.applied(p, study)
    assert k == 1
    assert [s.type for s in new.solve_steps()] == ["stationary", "stationary"]
    feats = {f.type: f for f in got.objects[comp.id].physics[0].features}
    assert feats["fixed"].steps == [] and feats["bolt_pretension"].steps == []
    assert feats["force"].steps == [2]
    assert preload.applied(got, new)[2] is None  # idempotent: it preloads on its own now
    assert p.objects[comp.id].physics[0].features[2].steps == []  # the original untouched


def test_nothing_changes_without_loads_bolts_or_a_locking_solver():
    for features, solver in (
        ([FIXED, PRE], "calculix"),  # nothing to load
        ([FIXED, PULL], "calculix"),  # nothing tightened
        ([FIXED, PRE, PULL], "elmer"),  # auto: only where a pretension locks
    ):
        p, _comp, study = _project(features, solver=solver)
        assert preload.applied(p, study)[2] is None
    p, _comp, study = _project([FIXED, PRE, PULL])
    assert preload.applied(p, study.model_copy(update={"preload_step": "never"}))[2] is None


def test_a_tightened_fastener_counts_and_later_steps_move_on():
    bolt = BeamFastener(name="M6", source="A/Hole", target="B/Hole", diameter=q(6, "mm"),
                        material=uuid4(), pretension=q(2, "kN"))  # fmt: skip
    steps = [SolveStep(type="stationary"), SolveStep(type="stationary")]
    later = PULL.model_copy(update={"steps": [2]})
    p, comp, study = _project([FIXED, PULL, later], steps=steps, connectors=[bolt])
    got, new, k = preload.applied(p, study)
    assert k == 1 and len(new.solve_steps()) == 3
    force, moved = got.objects[comp.id].physics[0].features[1:]
    assert force.steps == [2, 3] and moved.steps == [3]


def test_an_imposed_displacement_is_held_at_zero_while_preloading():
    push = Feature(type="displacement", selection="B/Top", values={"z": q(0.1, "mm")})
    p, comp, study = _project([FIXED, PRE, push])
    got, _new, _k = preload.applied(p, study)
    held, moved = [f for f in got.objects[comp.id].physics[0].features if f.type == "displacement"]
    assert held.steps == [1] and held.values["z"].value == 0.0
    assert moved.steps == [2] and moved.values["z"].value == 0.1


def test_the_run_key_follows_the_preload_step():
    p, _comp, study = _project([FIXED, PRE, PULL])
    auto = run_hashes(p)[study.id]["calculix"][0]
    study.preload_step = "never"
    never = run_hashes(p)[study.id]["calculix"][0]
    assert auto != never
    p2, _c, study2 = _project([FIXED, PULL])  # no bolts: auto changes nothing
    key = run_hashes(p2)[study2.id]["calculix"][0]
    study2.preload_step = "never"
    assert run_hashes(p2)[study2.id]["calculix"][0] != key  # a set field is in the key
    study2.preload_step = "auto"
    assert run_hashes(p2)[study2.id]["calculix"][0] == key


def test_chained_code_aster_studies():
    from puzzle.adapters.code_aster.comm import chained

    steps = [SolveStep(type="stationary"), SolveStep(type="stationary")]
    p, _comp, study = _project([FIXED, PRE, PULL.model_copy(update={"steps": [2]})],
                               steps=steps, solver="code_aster")  # fmt: skip
    assert chained(p, study)
    p, _comp, study = _project([FIXED, PULL], steps=steps, solver="code_aster")
    assert not chained(p, study)  # nothing tightened: independent steps, as before
    modal = [SolveStep(type="stationary"), SolveStep(type="modal")]
    p, _comp, study = _project([FIXED, PRE], steps=modal, solver="code_aster")
    assert not chained(p, study)
