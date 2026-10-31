"""Solve progress (ADR 0054): the CalculiX and Code_Aster parsers on recorded logs, the
events' storage, and the state a progress bar and a status line read.

The logs in tests/data/progress are real runs: the bolted bracket's frictional contact
on CalculiX (44 iterations, converged) and on Code_Aster (six cut-backs, then friction
multipliers going singular), and the plastic bar of test_plasticity.py on both."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pytest

from puzzle import progress
from puzzle.adapters.calculix.progress import CalculixProgress, steps_of
from puzzle.adapters.code_aster.progress import CodeAsterProgress
from puzzle.progress import (
    Converged,
    Diverged,
    Increment,
    Iteration,
    Message,
    Resource,
    Stage,
    Step,
)

DATA = Path(__file__).parent / "data" / "progress"


def _feed(parser, log: Path) -> list:
    events = []
    with gzip.open(log, "rt", errors="replace") as f:
        for line in f:
            events += parser.safe_feed(line.rstrip("\n"))
    return events + parser.finish(True)


def _calculix(name: str, tmp_path) -> list:
    deck = tmp_path / "job.inp"
    shutil.copy(DATA / f"{name}.inp", deck)
    return _feed(CalculixProgress(deck), DATA / f"{name}.log.gz")


def _aster(name: str, tmp_path) -> list:
    where = tmp_path / name
    where.mkdir()
    shutil.copy(DATA / name / "job.comm", where / "job.comm")
    return _feed(CodeAsterProgress(where / "job.export"), DATA / name / "solver.log.gz")


def _of(events, cls):
    return [e for e in events if isinstance(e, cls)]


def test_calculix_contact_iterations_match_its_cvg_file(tmp_path):
    """The residual is CalculiX's largest residual force over the time-averaged force:
    its .cvg file printed 0.1428E+05 % for the first iteration, 0.2775E+05 % for the
    second; the correction over the increment, 100 % then 15.64 %."""
    events = _calculix("calculix_contact", tmp_path)
    its = _of(events, Iteration)
    assert len(its) == 44
    assert its[0].residuals["force"] == pytest.approx(142.8, rel=1e-3)
    assert its[1].residuals["force"] == pytest.approx(277.5, rel=1e-3)
    assert its[0].residuals["displacement"] == pytest.approx(1.0)
    assert its[1].residuals["displacement"] == pytest.approx(0.1564, rel=1e-3)
    assert its[0].criteria == {"force": 0.005, "displacement": 0.01}
    assert its[0].contact == 108185 and its[1].contact == 79649  # as .cvg's CONT. EL.
    assert its[-1].residuals["force"] < 0.005  # it converged
    [done] = _of(events, Converged)
    assert (done.increment, done.iterations, done.time) == (1, 44, 1.0)
    assert {e.equations for e in _of(events, Resource) if e.equations} == {55607}
    s = progress.state(events)
    assert s.fraction() == 1.0 and not s.failed


def test_calculix_increments_and_step_time(tmp_path):
    events = _calculix("calculix_plastic", tmp_path)
    [step] = _of(events, Step)
    assert (step.number, step.of, step.type, step.end_time) == (1, 1, "static", 1.0)
    incs = _of(events, Increment)
    assert [round(i.time, 6) for i in incs] == [round(0.1 * k, 6) for k in range(1, 11)]
    assert not any(i.cutback for i in incs)
    # "convergence" and "convergence; the increment size is increased to ...": all ten
    assert [c.increment for c in _of(events, Converged)] == list(range(1, 11))
    s = progress.state(events)
    assert s.fraction() == pytest.approx(1.0) and s.time == pytest.approx(1.0)
    assert "step 1 of 1" in s.line() and "increment 10" in s.line()


def test_calculix_reads_step_periods_and_helper_steps():
    deck = "\n".join([
        "** puzzle step 0", "*STEP", "*FREQUENCY, STORAGE=YES", "10", "*END STEP",
        "** puzzle step 1", "*STEP", "*MODAL DYNAMIC", "0.001, 0.25", "*END STEP",
        "** puzzle step 2", "*STEP, NLGEOM", "*STATIC", "*END STEP",
    ])  # fmt: skip
    assert steps_of(deck) == [
        (0, "frequency", None),
        (1, "modal dynamic", 0.25),
        (2, "static", 1.0),
    ]


def test_code_aster_friction_cutbacks_and_the_singular_multiplier(tmp_path):
    """Six time steps, each cut back four times from the last; then a zero pivot on a
    friction multiplier (LAGS_F2) and the run gives up: all in English."""
    events = _aster("code_aster_friction", tmp_path)
    incs = _of(events, Increment)
    assert [i.time for i in incs] == [1.0, 0.25, 0.0625, 0.015625, 0.00390625, 0.0009765625]
    assert [i.level for i in incs] == [1, 2, 3, 4, 5, 6]
    assert [i.cutback for i in incs] == [False] + [True] * 5
    assert not _of(events, Converged)
    its = _of(events, Iteration)
    assert its[1].loops == {"geometry": 1, "friction": 1, "contact": 2}
    assert its[1].residuals["relative"] == pytest.approx(6.99741e-02)
    assert its[1].criteria == {"relative": 1e-6}
    final = [d for d in _of(events, Diverged) if d.final]
    assert len(final) == 1 and "Cutting the time step failed" in final[0].reason
    texts = [m.text for m in _of(events, Message) if m.level == "error"]
    assert "Degree of freedom at node 254, component LAGS_F2" in texts
    assert any("The maximum number of Newton iterations was reached" in t for t in texts)
    warnings = _of(events, Message)
    assert any(
        m.code == "MODELISA8_14" and m.text == "Element POI1 1 lies inside another."
        for m in warnings
    )
    assert {e.equations for e in _of(events, Resource) if e.equations} == {15203}
    s = progress.state(events)
    assert s.failed and s.cutbacks == 5 and s.iterations == len(its)


def test_code_aster_plastic_converges_every_increment(tmp_path):
    events = _aster("code_aster_plastic", tmp_path)
    incs = _of(events, Increment)
    assert len(incs) == 10 and incs[-1].time == pytest.approx(1.0)
    assert len(_of(events, Converged)) == 10
    s = progress.state(events)
    assert s.fraction() == pytest.approx(1.0) and not s.failed


def test_translate_keeps_what_it_does_not_know():
    assert progress.translate("Instant de calcul:  2.5e-01 - Niveau de découpe: 2") == (
        "Time 2.5e-01, cut-back level 2"
    )
    assert progress.translate("something else entirely") == "something else entirely"


def test_events_round_trip_and_unknown_kinds_are_skipped(tmp_path):
    events = [
        Stage(t=0.1, name="mesh", state="end", seconds=0.1),
        Iteration(t=1.0, number=3, residuals={"force": 0.1}, criteria={"force": 0.005}),
        Message(t=2.0, level="warning", text="w"),
    ]
    text = progress.dumps(events) + '{"kind":"from-the-future","x":1}\nnot json\n'
    assert progress.loads(text) == events


def test_a_parser_bug_never_stops_a_run():
    class Broken(progress.Parser):
        def feed(self, line):
            raise RuntimeError("bug")

    assert Broken().safe_feed("anything") == []


def test_the_recorder_writes_as_it_goes(tmp_path):
    seen = []
    record = progress.Recorder(seen.append)
    record.write_to(tmp_path)
    record(Step(number=1, of=2, end_time=1.0))
    record(Increment(time=0.5, end_time=1.0))
    record(Converged(time=0.5))
    assert progress.read(tmp_path) == record.events == seen
    assert all(e.t >= 0 for e in seen)
    assert progress.state(seen).fraction() == pytest.approx(0.25)  # half of step 1 of 2


def test_the_store_keeps_a_runs_history(tmp_path):
    from puzzle.results.store import ResultStore

    store = ResultStore(tmp_path / "results")
    events = [Step(number=1, of=1), Converged(iterations=4)]
    store.write_progress("abc123", events)
    assert store.read_progress("abc123") == events
    assert store.read_progress("other") == []


def test_the_diagnoses_of_the_friction_failure(tmp_path):
    """What took a morning to read out of the log on 2026-10-30, in three lines."""
    codes = [d.code for d in progress.diagnose(_aster("code_aster_friction", tmp_path))]
    assert codes == ["friction_singular", "cutbacks", "chattering"]
    assert progress.diagnose(_calculix("calculix_contact", tmp_path)) == []
    assert progress.diagnose(_aster("code_aster_plastic", tmp_path)) == []


def test_pivots_on_held_and_free_dofs():
    def on(component):
        return [d.code for d in progress.diagnose(
            [Message(level="error", text=f"Degree of freedom at node 7, component {component}")]
        )]  # fmt: skip

    assert on("LAGR") == ["redundant_constraints"]
    assert on("DY") == ["rigid_body"]


def _project_with_attempt(tmp_path, events):
    """A saved project whose study's run folder holds `events`."""
    from puzzle.model.schema import Project
    from puzzle.project import save_project
    from puzzle.project.layout import WORK_DIR

    p = Project(name="p")
    study = None
    root = tmp_path / "p"
    save_project(p, root)
    folder = root / WORK_DIR / "bolted-20261031-010101"
    folder.mkdir(parents=True)
    (folder / progress.FILE).write_text(progress.dumps(events))
    return root, study


def test_the_newest_attempt_is_read_from_its_folder(tmp_path):
    events = _aster("code_aster_friction", tmp_path)
    root, _ = _project_with_attempt(tmp_path, events)
    assert progress.latest_attempt(root, "Bolted") == events
    assert progress.latest_attempt(root, "Other") == []


def test_the_advisor_says_why_the_last_run_failed(tmp_path):
    from puzzle.advisor import _failed_attempt
    from puzzle.model.schema import Study

    events = _aster("code_aster_friction", tmp_path)
    root, _ = _project_with_attempt(tmp_path, events)
    text = _failed_attempt(
        root, Study(name="Bolted", component=__import__("uuid").uuid4(), solver="code_aster")
    )
    assert text.startswith("The last run failed (Cutting the time step failed.)")
    assert "Friction multipliers went singular" in text


def test_mcp_run_progress(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    import uuid

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Study
    from puzzle.project import load_project, save_project

    events = _aster("code_aster_friction", tmp_path)
    root, _ = _project_with_attempt(tmp_path, events)
    p = load_project(root)
    p.add(Study(name="Bolted", component=uuid.uuid4(), solver="code_aster"))
    save_project(p, root)
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool("run_progress", {"study": "Bolted"}))
    out = result.structured_content.get("result", result.structured_content)
    assert out["status"] == "failed" and out["cutbacks"] == 5
    assert [d["code"] for d in out["diagnoses"]][0] == "friction_singular"
    assert out["equations"] == 15203


def test_the_solution_panel_draws_a_run(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.solution_panel import SolutionPanel, save_png

    app = QApplication.instance() or QApplication([])  # noqa: F841
    events = _aster("code_aster_friction", tmp_path)
    panel = SolutionPanel(None)
    panel.start("Bolted: Code_Aster")
    for e in events:
        panel.add(e)
    panel.finish(False, "Failed")
    assert panel.convergence.series and panel.convergence.series[0][0] == "Relative residual"
    assert any(kind == "cutback" for _, kind in panel.convergence.marks)
    assert [round(y, 6) for _, y in panel.times.series[0][1]] == [
        1.0,
        0.25,
        0.0625,
        0.015625,
        0.003906,
        0.000977,
    ]
    texts = [panel.messages.item(i).text() for i in range(panel.messages.count())]
    assert any("LAGS_F2" in t for t in texts)
    assert "15,203 equations" in panel.resources.text()
    save_png(events, "Bolted", tmp_path / "p.png")
    assert (tmp_path / "p.png").stat().st_size > 10_000
