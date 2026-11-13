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
    with gzip.open(log, "rt", encoding="utf-8", errors="replace") as f:
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


def test_an_imprecise_mumps_solve_is_explained(tmp_path):
    """The 882,309-equation pressure vessel that looked hung on Windows (2026-11-08):
    MUMPS's answer failed RESI_RELA (FACTOR_57); the run says so in English, with the fix."""
    events = _aster("code_aster_imprecise", tmp_path)
    errors = [e for e in events if isinstance(e, Message) and e.code == "FACTOR_57"]
    assert errors and "not accurate enough" in errors[0].text
    assert [d.code for d in progress.diagnose(events)] == ["imprecise_solve"]


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
    (folder / progress.FILE).write_text(progress.dumps(events), encoding="utf-8")
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


# --- OpenFOAM and Code_Saturne (ADR 0054 phase 3) -----------------------------------


def _case(name: str, tmp_path) -> Path:
    """A recorded CFD run folder, its logs unzipped as the solver left them."""
    where = tmp_path / name
    shutil.copytree(DATA / name, where)
    for log in where.rglob("*.gz"):
        log.with_suffix("").write_bytes(gzip.decompress(log.read_bytes()))
        log.unlink()
    return where


def _run(parser, case: Path, ok: bool = True) -> list:
    """The parser as the job runner drives it: stdout lines, polls, then the end."""
    events = []
    log = case / "solver.log"
    for line in (
        log.read_text(errors="replace", encoding="utf-8").splitlines() if log.exists() else []
    ):
        events += parser.safe_feed(line)
        events += parser.safe(parser.poll)
    return events + parser.safe(parser.poll) + parser.finish(ok)


def _openfoam(name: str, tmp_path, ok: bool = True) -> list:
    from puzzle.adapters.openfoam.progress import OpenFoamProgress

    case = _case(name, tmp_path)
    return _run(OpenFoamProgress(case / "Allrun"), case, ok)


def _saturne(name: str, tmp_path, ok: bool = True) -> list:
    from puzzle.adapters.code_saturne.progress import CodeSaturneProgress

    case = _case(name, tmp_path)
    return _run(CodeSaturneProgress(case / "case.json"), case, ok)


def test_openfoam_laminar_channel_converges_in_170_iterations(tmp_path):
    """The channel benchmark on simpleFoam: residualControl p 1e-6 and U 1e-7 (as the
    log's "SIMPLE: convergence criteria" says), met at iteration 170 of 500."""
    events = _openfoam("openfoam_channel", tmp_path)
    [step] = _of(events, Step)
    assert (step.type, step.iterations, step.end_time) == ("stationary", 500, None)
    its = _of(events, Iteration)
    assert [e.number for e in its] == list(range(1, 171))
    assert its[0].residuals["Ux"] == pytest.approx(0.999999999961)
    assert its[0].residuals["p"] == pytest.approx(1.0)
    assert its[0].criteria == {"Ux": 1e-7, "Uy": 1e-7, "Uz": 1e-7, "p": 1e-6}
    assert its[-1].residuals["Uz"] == pytest.approx(8.49767261407e-08)
    assert its[-1].residuals["continuity"] == pytest.approx(1.17507139995e-10)
    assert "continuity" not in its[-1].criteria  # simpleFoam has none for it
    [done] = _of(events, Converged)
    assert done.iterations == 170
    texts = [m.text for m in _of(events, Message)]
    assert texts[:2] == [
        "Converting the mesh (gmshToFoam)",
        "Faces outside every selection become walls",
    ]
    assert "Solving (simpleFoam)" in texts
    s = progress.state(events)
    assert s.fraction() == 1.0 and not s.failed
    assert "iteration 170 of 500" in s.line() and "(needs 1e-07)" in s.line()
    assert progress.diagnose(events) == []


def test_openfoam_heated_channel_reads_the_scalar_after_the_flow(tmp_path):
    """scalarTransport solves T after each iteration's ExecutionTime line, and after
    "SIMPLE solution converged in 170 iterations": T belongs to that iteration, and
    T's 1e-8 criterion counts."""
    events = _openfoam("openfoam_heated", tmp_path)
    its = _of(events, Iteration)
    assert len(its) == 170 and all("T" in e.residuals for e in its)
    assert its[0].residuals["T"] == pytest.approx(0.999999999766)
    assert its[-1].residuals["T"] == pytest.approx(3.95096217959e-10)
    assert its[-1].criteria["T"] == 1e-8
    [done] = _of(events, Converged)
    assert done.iterations == 170 and events.index(done) > events.index(its[-1])
    assert progress.state(events).fraction() == 1.0


def test_openfoam_stopped_short_is_not_converged(tmp_path):
    """A k-epsilon channel (test_turbulent_channel.py's) given 150 iterations where it
    needs about 1500: k and epsilon match residualControl's "(k|epsilon|omega)" key;
    the run ends at its limit with Uz, the spanwise velocity, flat far above 1e-7."""
    events = _openfoam("openfoam_turbulent_short", tmp_path)
    its = _of(events, Iteration)
    assert len(its) == 150 and not _of(events, Converged)
    assert set(its[0].residuals) == {"Ux", "Uy", "Uz", "p", "k", "epsilon", "continuity"}
    assert its[0].criteria["k"] == its[0].criteria["epsilon"] == 1e-6
    assert its[-1].residuals["Uz"] == pytest.approx(0.183145184987)
    [short] = _of(events, Diverged)
    assert not short.final  # the result exists; it may not have converged
    assert any("iteration limit (150)" in m.text for m in _of(events, Message))
    s = progress.state(events)
    assert s.fraction() == 1.0 and not s.failed and "Uz 1.83e-01" in s.line()
    [d] = progress.diagnose(events)
    assert d.code == "not_converged"
    # Uz fell 0.25 decades over the last 38 iterations; 6.3 more decades to go
    assert "all 150 iterations" in d.text and "Uz" in d.text
    assert "about 950 more iterations" in d.text  # the benchmark runs 1500


def test_openfoam_fatal_error_in_an_expert_setting(tmp_path):
    """An expert setting naming a pressure solver that doesn't exist: simpleFoam stops
    in its first iteration with a FOAM FATAL IO ERROR."""
    events = _openfoam("openfoam_fatal", tmp_path, ok=False)
    [error] = [m for m in _of(events, Message) if m.level == "error"]
    assert error.code == "FOAM FATAL IO ERROR"
    assert error.text.startswith("OpenFOAM input error: Unknown symmetric matrix solver type")
    assert "file: system/fvSolution/solvers/p" in error.text
    [final] = _of(events, Diverged)
    assert final.final and final.reason == "Unknown symmetric matrix solver type Nonsense"
    assert progress.state(events).failed
    assert [d.code for d in progress.diagnose(events)] == ["bad_setting"]


def test_code_saturne_laminar_channel_listing(tmp_path):
    """The channel benchmark on Code_Saturne, its listing printed every iteration: the
    time residuals match its residuals.csv (35.877771 and 36.631608 at step 1), all
    500 steps run, and the residuals fell over seven decades: converged."""
    events = _saturne("code_saturne_channel", tmp_path)
    [step] = _of(events, Step)
    assert (step.type, step.iterations) == ("stationary", 500)
    its = _of(events, Iteration)
    assert [e.number for e in its] == list(range(1, 501))
    assert its[0].residuals == {"velocity": pytest.approx(35.8778),
                                "pressure": pytest.approx(36.6316)}  # fmt: skip
    assert its[1].residuals["pressure"] == pytest.approx(599.758)  # its peak
    assert its[-1].residuals["velocity"] == pytest.approx(1.20155e-06)
    assert not its[0].criteria  # Code_Saturne runs its iteration count
    [done] = _of(events, Converged)
    assert done.iterations == 500
    texts = [m.text for m in _of(events, Message)]
    assert texts[:3] == ["Preparing the calculation", "Reading the mesh (preprocessor)", "Solving"]
    s = progress.state(events)
    assert s.fraction() == 1.0 and "iteration 500 of 500" in s.line()
    assert progress.diagnose(events) == []


def test_code_saturne_k_epsilon_variables(tmp_path):
    """test_turbulent_channel.py's k-epsilon channel on Code_Saturne, 300 iterations:
    k and epsilon get their own residuals."""
    events = _saturne("code_saturne_turbulent", tmp_path)
    its = _of(events, Iteration)
    assert len(its) == 300
    assert set(its[0].residuals) == {"velocity", "pressure", "k", "epsilon"}
    assert its[-1].residuals["epsilon"] == pytest.approx(0.00401226)
    assert len(_of(events, Converged)) == 1


def test_code_saturne_short_of_three_decades_is_not_converged(tmp_path):
    """The same listing cut at step 40, as if that were the limit: velocity had fallen
    from 35.9 to 6.3e-2, 2.8 decades, short of three, and still falling."""
    from puzzle.adapters.code_saturne.progress import CodeSaturneProgress

    case = _case("code_saturne_channel", tmp_path)
    listing = case / "CASE" / "RESU" / "puzzle" / "run_solver.log"
    text = listing.read_text(encoding="utf-8")
    listing.write_text(text[: text.index("TIME STEP NUMBER              41")], encoding="utf-8")
    (case / "case.json").write_text(
        (case / "case.json")
        .read_text(encoding="utf-8")
        .replace('"iterations": 500', '"iterations": 40'),
        encoding="utf-8",
    )
    events = _run(CodeSaturneProgress(case / "case.json"), case)
    assert len(_of(events, Iteration)) == 40 and not _of(events, Converged)
    assert any("fell only 2.8 decades" in m.text for m in _of(events, Message))
    [d] = progress.diagnose(events)
    assert d.code == "not_converged" and "velocity" in d.text
    assert "about 10 more iterations" in d.text


def test_code_saturne_parameter_error(tmp_path):
    """A negative viscosity (through the expert pass-through): Code_Saturne's parameter
    check stops it before the first time step; the message is its own, without the
    source path and the call stack, and given once though the listing repeats it."""
    events = _saturne("code_saturne_fatal", tmp_path, ok=False)
    errors = [m for m in _of(events, Message) if m.level == "error"]
    assert errors[0].code == "cs_parameters"
    assert errors[0].text == (
        "Error while reading reference dynamic viscosity value Parameter: "
        "cs_glob_fluid_properties->viscl0 = -1 while its value must be greater than 0. "
        "1 parameter error(s) reported."
    )
    assert [m.text for m in errors[1:]] == ["Error running the calculation."]  # its stdout
    assert not _of(events, Iteration)
    [final] = _of(events, Diverged)
    assert final.final and "viscl0 = -1" in final.reason
    assert [d.code for d in progress.diagnose(events)] == ["bad_setting"]


def test_the_solution_panel_draws_cfd_residuals(tmp_path):
    """Each field over its criterion (continuity has none, so it isn't drawn); a solver
    without criteria (Code_Saturne) has its residuals drawn as they are."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.solution_panel import SolutionPanel

    app = QApplication.instance() or QApplication([])  # noqa: F841
    panel = SolutionPanel(None)
    panel.show_history("Channel: OpenFOAM", _openfoam("openfoam_channel", tmp_path))
    names = [n for n, _, _ in panel.convergence.series]
    assert names == ["Velocity x", "Velocity y", "Velocity z", "Pressure"]
    assert len(panel.convergence.series[0][1]) == 170
    assert panel.convergence.y_label == "Residual / criterion" and panel.convergence.lines
    assert "steady" in panel.times.empty
    raw = [Step(iterations=2), Increment(),
           Iteration(number=1, residuals={"velocity": 30.0}),
           Iteration(number=2, residuals={"velocity": 3.0})]  # fmt: skip
    panel.show_history("Channel: Code_Saturne", raw)
    assert panel.convergence.series[0][0] == "Velocity"
    assert panel.convergence.series[0][1] == [(1, 30.0), (2, 3.0)]
    assert panel.convergence.y_label == "Residual" and not panel.convergence.lines


def test_a_flat_residual_at_the_limit_has_stalled():
    events = [Step(iterations=40), Increment()] + [
        Iteration(number=n, residuals={"p": 1e-3 * (1 - 0.001 * n)}, criteria={"p": 1e-6})
        for n in range(1, 41)
    ]
    [d] = progress.diagnose(events)
    assert d.code == "not_converged" and "stalled" in d.text and "mesh quality" in d.text
    assert progress.diagnose(events[:-1]) == []  # short of its limit: still running


def test_native_code_aster_on_windows_is_followed_in_fort6(tmp_path, monkeypatch):
    """ADR 0072: run_aster.bat writes the run to fort.6 in its temporary folder and
    stdout says nothing, so the parser follows that file (the newest run_aster folder
    made since the job started) and ignores stdout meanwhile: the plastic case's ten
    increments come from fort.6 alone."""
    import tempfile

    from puzzle.adapters.code_aster import progress as cp

    where = tmp_path / "case"
    where.mkdir()
    shutil.copy(DATA / "code_aster_plastic" / "job.comm", where / "job.comm")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    parser = CodeAsterProgress(where / "job.export")
    parser.native_windows = True
    assert parser.poll() == []  # no run_aster folder yet
    run = temp / "run_aster__abc123"
    run.mkdir()
    with gzip.open(DATA / "code_aster_plastic" / "solver.log.gz", "rt", encoding="utf-8",
                   errors="replace") as f:  # fmt: skip
        text = f.read()
    half = len(text) // 2
    (run / "fort.6").write_text(text[:half], encoding="utf-8")
    events = parser.poll()
    with (run / "fort.6").open("a", encoding="utf-8") as f:
        f.write(text[half:] + "\n")
    events += parser.poll()
    events += parser.safe_feed("Instant de calcul:  9.0e-01 - Niveau de découpe: 1")  # stdout
    events += parser.finish(True)
    assert len(_of(events, Increment)) == 10  # the stdout line didn't count twice
    cp._CLAIMED.discard(str(run))
