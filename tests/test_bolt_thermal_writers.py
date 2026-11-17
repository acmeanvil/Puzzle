"""Bolts under temperature, as written (ADR 0090 package 6): the CalculiX deck and the
Code_Aster command file hold each beam bolt's free thermal growth, give pretension twins
and bolt nodes their temperatures, and put a body temperature only in its own steps.
The solves are `tests/integration/test_bolt_thermal.py`."""

import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "integration"))
import written_or_refused as wor  # noqa: E402
from bolt_thermal_joint import T_REF, L, heat, joint  # noqa: E402

from puzzle.project import save_project  # noqa: E402

pytest.importorskip("OCP")
ALPHA_ST = 11.7e-6  # A36, 1/K


def _written(tmp_path, p, study) -> Path:
    from puzzle.solve import run_study

    root, work = tmp_path / "p", tmp_path / "w"
    save_project(p, root)
    with wor.no_solver(), pytest.raises(wor._Stopped):
        run_study(p, study.id, root, work)
    return work


def _steps(deck: str) -> list[str]:
    return deck.split("*STEP")[1:]


def test_calculix_writes_a_beam_bolts_growth_in_its_heated_step(tmp_path):
    """The beam bolt's growth node is held at alpha_b dT L in the heated step and at 0 in
    the preload step Puzzle puts in front; the bolt's equations carry it."""
    p, study, comp = joint("calculix", "beam")
    comp.physics[0].features.append(heat("S/Body", T_REF + 100.0))
    deck = (_written(tmp_path, p, study) / "job.inp").read_text(encoding="utf-8")
    preload, heated = _steps(deck)
    growth = ALPHA_ST * 100.0 * L
    rows = re.findall(r"^(\d+), 1, 1, ([-0-9.eE+]+)$", heated, re.M)
    grown = [(int(n), float(v)) for n, v in rows if float(v) == pytest.approx(growth, rel=1e-9)]
    assert len(grown) == 1
    node = grown[0][0]
    assert re.search(rf"^{node}, 1, 1, 0$", preload, re.M)
    assert "*TEMPERATURE\nN_ALL, 293.15" in preload  # the preload step at the reference
    model = deck.split("** puzzle step")[0]
    assert re.search(rf"(^|, ){node}, 1, -?[0-9.]", model.split("*EQUATION", 1)[1], re.M)


def test_calculix_gives_pretension_twins_their_nodes_temperature(tmp_path):
    """A modelled bolt's pretension twins: the reference temperature at the start, their
    own node's in each heated step."""
    p, study, comp = joint("calculix", "modelled", contact="frictionless")
    comp.physics[0].features += [heat("S/Body", T_REF + 100.0), heat("B/Body", T_REF + 150.0)]
    deck = (_written(tmp_path, p, study) / "job.inp").read_text(encoding="utf-8")
    mesh_nodes = deck.split("*NODE, NSET=N_ALL\n")[1].split("*")[0].splitlines()
    top = max(int(r.split(",")[0]) for r in mesh_nodes)
    heated = _steps(deck)[-1].split("*TEMPERATURE")[1].split("*")[0]
    temps = {int(n): float(t) for n, t in re.findall(r"^(\d+), ([0-9.]+)$", heated, re.M)}
    twins = {n: t for n, t in temps.items() if n > top}  # past the mesh: the twins
    assert twins and all(t == pytest.approx(T_REF + 150.0) for t in twins.values())
    initial = deck.split("*INITIAL CONDITIONS, TYPE=TEMPERATURE")[1].split("*")[0]
    first = {int(n) for n in re.findall(r"^(\d+), 293.15$", initial, re.M)}
    assert set(twins) <= first


def test_code_aster_writes_every_nodes_temperature_in_its_order(tmp_path):
    """One row per written node (Code_Aster's order) and a column per chained step: the
    preload step at the reference, the heated step's beam bolt nodes (past the mesh's)
    at its ends' temperature; the bolt's material takes ALPHA."""
    p, study, comp = joint("code_aster", "beam")
    comp.physics[0].features.append(heat("S/Body", T_REF + 100.0))
    work = _written(tmp_path, p, study)
    table = np.loadtxt(work / "transferred-temperature.txt", ndmin=2)
    comm = (work / "job.comm").read_text(encoding="utf-8")
    top = int((work / "job.meshnodes").read_text(encoding="utf-8"))
    assert table.shape[1] == 2 and np.allclose(table[:, 0], T_REF)
    assert np.allclose(table[top:, 1][:2], T_REF + 100.0)  # the beam's two end nodes
    assert re.search(r"matf1 = DEFI_MATERIAU\(ELAS=_F\([^)]*ALPHA=", comm)
    assert re.search(
        r"CHAM_GD=temperature1, INST=1(\.0)?\), _F\(NOM_CHAM='TEMP', "
        r"CHAM_GD=temperature2, INST=2(\.0)?\)",
        comm,
    )


def test_a_body_temperature_in_some_steps_is_theirs(tmp_path):
    """Feature.steps on a uniform body temperature: an input per step it is in, none in
    the others (it had applied in every step, the preload step included)."""
    from puzzle.fields import field_inputs
    from puzzle.mesh import generate_mesh
    from puzzle.model import SolveStep

    p, study, comp = joint("calculix", "beam", preload=0.0)
    comp.physics[0].features.append(heat("S/Body", T_REF + 100.0, steps=[2]))
    study.steps = [SolveStep(type="stationary"), SolveStep(type="stationary")]
    root = tmp_path / "p"
    save_project(p, root)
    mesh = generate_mesh(p, comp.id, root, tmp_path / "m").mesh
    made = [i for i in field_inputs(p, root, study, mesh) if i.field.name == "temperature"]
    assert [i.step for i in made] == [2]
