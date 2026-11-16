"""Weak springs (ADR 0080): the split bar's right half, held only by frictional contact
on the held left half, pressed on its end. Without the springs its first solve is
singular; with them the contact carries the load (the left end's reaction is p A within
1e-4) and the springs next to none of it. (A body that never seats is reported:
tests/test_weak_springs.py.)"""

import numpy as np
import pytest
from conftest_geometry import BAR, split_bar_project
from solvers import over

from puzzle.model.schema import Contact, Feature, Quantity
from puzzle.solve import run_study

PRESSURE = 1e6  # Pa on the right end
SIDE = 1e-3  # a sideways force as a share of the end load


def _bar(tmp_path, solver, pressure, mode="auto", kind="frictional"):
    features = [
        Feature(type="fixed", selection="Left/Start"),
        Feature(type="pressure", selection="Right/End",
                values={"p": Quantity(value=pressure, unit="Pa")}),
    ]  # fmt: skip
    contact = Contact(name="Joint", type=kind, source="Right/Start", target="Left/End",
                      friction=0.3 if kind == "frictional" else None)  # fmt: skip
    settings = {"temperature_dependent_materials": False}
    project, study = split_bar_project(tmp_path, solver, features, contact, settings=settings)
    study.weak_springs = mode
    if solver == "code_aster":  # its friction runs on first-order meshes
        from puzzle.model.schema import MeshRecipe

        next(iter(project.of_type(MeshRecipe))).element_order = 1
    return project, study


@over("calculix", "code_aster")
def test_weak_springs_hold_a_body_until_its_contact_closes(tmp_path, solver):
    pytest.importorskip("OCP")
    project, study = _bar(tmp_path, solver, PRESSURE)
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    step = run.results.steps[-1]
    reaction = step.fields["reaction_force"].at(run.mesh.groups["Left/Start"].node_ids)
    total = reaction[:, 0].sum()
    load = PRESSURE * BAR[1] * BAR[2]
    springs = step.fields["weak_spring_force"].values
    carried = float(np.linalg.norm(springs.sum(axis=0)))
    print(f"{solver}: reaction {total:.6g} N of {load:.6g}; springs {carried:.3g} N")
    assert total == pytest.approx(load, rel=1e-4)
    assert carried < 1e-4 * load
    assert not [e for e in run.events if getattr(e, "code", "") == "weak_springs_carry"]


@over("calculix")
def test_weak_springs_hold_a_body_on_frictionless_contact(tmp_path, solver):
    """Frictionless, nothing but the springs holds the right half sideways and about the
    bar's axis; the contact still carries the whole load."""
    pytest.importorskip("OCP")
    project, study = _bar(tmp_path, solver, PRESSURE, kind="frictionless")
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    step = run.results.steps[-1]
    reaction = step.fields["reaction_force"].at(run.mesh.groups["Left/Start"].node_ids)
    load = PRESSURE * BAR[1] * BAR[2]
    carried = float(np.linalg.norm(step.fields["weak_spring_force"].values.sum(axis=0)))
    total = reaction[:, 0].sum()
    print(f"{solver}: reaction {total:.6g} N of {load:.6g}; springs {carried:.3g} N")
    assert total == pytest.approx(load, rel=1e-4)
    assert carried < 1e-4 * load


@over("calculix", "code_aster")
def test_friction_holds_a_sliding_load_under_its_limit(tmp_path, solver):
    """ADR 0080 decision 8, sliding: a sideways force under the friction limit μ N is held
    by friction, not by the springs. It is 1e-3 of the end load: on the end of the half
    bar its moment about the 10 mm interface must not lift an edge (M/S below the
    1 MPa pressure needs under 1.7 N; 10 N opened it and the body slid on its springs)."""
    pytest.importorskip("OCP")
    project, study = _bar(tmp_path, solver, PRESSURE)
    load = PRESSURE * BAR[1] * BAR[2]
    comp = next(c for c in project.objects.values() if getattr(c, "kind", "") == "component")
    comp.physics[0].features.append(Feature(type="force", selection="Right/End",
                                            values={"y": Quantity(value=SIDE * load,
                                                                  unit="N")}))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    step = run.results.steps[-1]
    reaction = step.fields["reaction_force"].at(run.mesh.groups["Left/Start"].node_ids)
    springs = step.fields["weak_spring_force"].values.sum(axis=0)
    print(f"{solver}: reaction {reaction.sum(axis=0)} N; springs {springs} N")
    assert reaction[:, 0].sum() == pytest.approx(load, rel=1e-4)
    assert -reaction[:, 1].sum() == pytest.approx(SIDE * load, rel=1e-2)
    assert float(np.linalg.norm(springs)) < 1e-2 * SIDE * load


@over("calculix")
def test_the_answer_does_not_depend_on_the_spring_factor(tmp_path, solver):
    """ADR 0080 decision 8, factor: the end's displacement changes by under 1e-5 between
    factors 1e-6 and 1e-7, so the springs hold the body without moving the answer."""
    pytest.importorskip("OCP")
    tips = []
    for factor in (1e-6, 1e-7):
        where = tmp_path / f"f{factor:g}"
        where.mkdir()
        project, study = _bar(where, solver, PRESSURE)
        study.weak_spring_factor = factor
        run = run_study(project, study.id, where, where / "w", timeout_s=900)
        u = run.results.steps[-1].fields["displacement"]
        tips.append(float(u.at(run.mesh.groups["Right/End"].node_ids)[:, 0].mean()))
    print(f"{solver}: end displacement {tips[0]:.9g} and {tips[1]:.9g} m")
    assert tips[1] == pytest.approx(tips[0], rel=1e-5)
