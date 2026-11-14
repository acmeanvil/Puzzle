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
