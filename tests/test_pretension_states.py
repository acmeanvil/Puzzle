"""A pretension section's state in every step type, one rule for every adapter (ADR 0090
CCX-9, CCX-10, ASTER-5: `common.pretension_states`), and the CalculiX deck written from
it. tests/integration/test_pretension.py solves the bolt; here only the decks."""

from __future__ import annotations

import types

import numpy as np
import pytest
import written_or_refused as wor

from puzzle.adapters.common import (
    CLOSED,
    DRIVEN,
    LOCKED,
    InputError,
    check_pretension_steps,
    pretension_states,
)
from puzzle.model import Feature, SolveStep, Study


def _steps(*kinds):
    return [SolveStep(type=k) for k in kinds]


def test_driven_in_the_first_static_step_closed_before_locked_after():
    assert pretension_states(_steps("stationary", "stationary")) == [DRIVEN, LOCKED]
    assert pretension_states(_steps("modal")) == [CLOSED]
    assert pretension_states(_steps("frequency_domain")) == [CLOSED]
    assert pretension_states(_steps("buckling")) == [CLOSED]
    assert pretension_states(_steps("modal", "stationary", "modal")) == [CLOSED, DRIVEN, LOCKED]
    assert pretension_states(_steps("stationary", "frequency_domain", "transient")) == [
        DRIVEN, LOCKED, LOCKED]  # fmt: skip
    # active from step 2 only: closed in step 1
    assert pretension_states(_steps("stationary", "stationary"), lambda k: k == 2) == [
        CLOSED, DRIVEN]  # fmt: skip
    # a prestress replay drove it: the study's own steps hold it
    assert pretension_states(_steps("modal"), replayed=True) == [LOCKED]


def test_a_transient_never_drives_a_preload():
    comp = types.SimpleNamespace(
        physics=[types.SimpleNamespace(type="solid_mechanics", features=[
            Feature(type="bolt_pretension", selection="B/Section")])],
        connectors=[])  # fmt: skip
    with pytest.raises(InputError, match="no static step before it") as info:
        check_pretension_steps(comp, _steps("transient"), "CalculiX")
    assert info.value.code == "unsupported"
    check_pretension_steps(comp, _steps("stationary", "transient"), "CalculiX")
    check_pretension_steps(comp, _steps("modal", "frequency_domain"), "CalculiX")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return wor.World(tmp_path_factory.mktemp("pretension-states"))


def _deck(world, steps, inputs=()):
    from puzzle.adapters.calculix.inp import write_inp

    place = world.place("tet10")
    case = wor.Case("calculix", "solid_mechanics", "stationary")

    def change(project, comp, study):
        comp.physics[0].features = [
            Feature(type="fixed", selection="Bar/Root"),
            Feature(type="bolt_pretension", selection="Bar/Section",
                    values={"F": wor.q(1000, "N")}),
        ]  # fmt: skip
        study.steps = steps

    project, sid = wor.make(world, case, change)
    return project, sid, write_inp(project, project.get(sid, Study), place.meshed.mesh, inputs)


def _step_blocks(deck: str) -> list[str]:
    return deck.split("** puzzle step")[1:]


def test_calculix_closes_a_section_in_a_modal_step(world):
    """CCX-9: with no preload before it a modal step held nothing on the reference node,
    so the section slid open (21,529 Hz for the test bolt's 33,556 Hz)."""
    *_, deck = _deck(world, [SolveStep(type="modal", settings={"modes": 2})])
    (modal,) = _step_blocks(deck)
    ref = int(world.place("tet10").meshed.mesh.node_ids.max()) + 1  # the reference node
    assert f"{ref}, 1, 1, 0." in modal and "*CLOAD" not in modal.replace("*CLOAD, OP=NEW", "")


def test_calculix_locks_a_section_after_its_preload_and_drives_none_in_a_harmonic(world):
    *_, deck = _deck(world, [SolveStep(type="stationary"),
                             SolveStep(type="modal", settings={"modes": 2})])  # fmt: skip
    static, modal = _step_blocks(deck)
    assert "*CLOAD" in static and "-1000" in static
    assert "*BOUNDARY, FIXED" in modal
    # CCX-10's sibling: a harmonic step's modes step had no states (KeyError)
    *_, deck = _deck(world, [SolveStep(type="frequency_domain",
                                       settings={"frequencies": "100, 200"})])  # fmt: skip
    assert "-1000" not in deck  # never the preload as a harmonic load


def test_calculix_writes_a_prestressed_study_with_a_pretension(world):
    """CCX-10: the replayed static step looked its reference node up in an empty map."""
    from puzzle.results.model import Field

    project, sid, _ = _deck(world, [SolveStep(type="stationary")])
    source = project.get(sid, Study)
    modal = project.add(Study(name="Modes", component=source.component, solver="calculix",
                              steps=[SolveStep(type="modal", settings={"modes": 2})]))  # fmt: skip
    stress = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], np.array([1]),
                   np.zeros((1, 6)))  # fmt: skip
    given = types.SimpleNamespace(role="prestress", field=stress, source="s", study=source.id,
                                  step=None, times=None, frequencies=None)  # fmt: skip
    from puzzle.adapters.calculix.inp import write_inp

    deck = write_inp(project, modal, world.place("tet10").meshed.mesh, [given])
    replay, own = _step_blocks(deck)
    assert "NLGEOM" in replay and "-1000" in replay  # driven in the replay
    assert "PERTURBATION" in own and "*BOUNDARY, FIXED" in own  # locked after it
