"""Solve progress while solving (ADR 0054), on the plastic bar of test_plasticity.py:
ten increments of a pulled bar, each converged; the progress kept with the stored run
and in the run folder; and result trackers followed while it solves.

The tip is pulled to strain * L at time 1, ramped, so the tracked tip displacement is
strain * L * t at every increment; the root's reaction ends at the closed-form stress
times the bar's 10 x 10 mm section (CalculiX; Code_Aster tracks displacements only)."""

import pytest
from plastic_bar import STRAIN, STRESS, L, plastic_bar
from solvers import over

from puzzle import progress
from puzzle.model.schema import ResultProbe, Study
from puzzle.results.store import ResultStore
from puzzle.solve import run_study


@over("calculix", "code_aster")
def test_progress_and_trackers_while_solving(solver, tmp_path):
    p = plastic_bar(solver, increments=10)
    study = next(s for s in p.of_type(Study) if s.name == "Pull")
    p.add(ResultProbe(name="Tip pull", source=study.id, type="selection", selection="Bar/Tip",
                      quantity="displacement:x", reduction="max", track=True))  # fmt: skip
    p.add(ResultProbe(name="Root force", source=study.id, type="reaction", selection="Bar/Root",
                      track=True))  # fmt: skip
    seen = []
    store = ResultStore(tmp_path / "results")
    run = run_study(p, study.id, tmp_path, tmp_path / "run", store=store, on_event=seen.append,
                    timeout_s=900)  # fmt: skip

    increments = [e for e in seen if isinstance(e, progress.Increment)]
    assert [round(e.time, 6) for e in increments] == [round(0.1 * k, 6) for k in range(1, 11)]
    assert len([e for e in seen if isinstance(e, progress.Converged)]) == 10
    assert any(isinstance(e, progress.Iteration) for e in seen)
    stages = [e.name for e in seen if isinstance(e, progress.Stage) and e.state == "end"]
    assert stages == ["mesh", "transfers", "write", "solve", "read", "store"]
    s = progress.state(seen)
    assert s.fraction() == pytest.approx(1.0) and not s.failed and s.equations

    tip = [e for e in seen if isinstance(e, progress.Tracked) and e.name == "Tip pull"]
    assert len(tip) == 10
    for e in tip:
        assert e.value == pytest.approx(STRAIN * L * e.time, rel=1e-6)
    root = [e for e in seen if isinstance(e, progress.Tracked) and e.name == "Root force"]
    if solver == "calculix":
        assert abs(root[-1].value) == pytest.approx(STRESS * 0.01 * 0.01, rel=1e-3)
    else:  # SUIVI_DDL follows displacement components; the note says so
        assert not root
        notes = [e.text for e in seen if isinstance(e, progress.Message)]
        assert not any("Tip pull" in n for n in notes)

    # Kept with the run, and in its folder as it went.
    kept = store.read_progress(run.run_id)
    assert [type(e) for e in kept] == [type(e) for e in seen[: len(kept)]]
    assert len(progress.read(tmp_path / "run")) >= len(kept) - 1
