"""Solve progress on FEniCSx and Elmer (ADR 0054, phase 2): the parsers on recorded
logs of the transient bar in tests/integration/test_progress_live_fe.py (20 steps of
20 s) and, for Elmer, steady conduction with temperature tables (six iterations)."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.elmer.progress import ElmerProgress
from puzzle.adapters.fenicsx.progress import FenicsxProgress
from puzzle.progress import (
    Converged,
    Diverged,
    Increment,
    Iteration,
    Message,
    Resource,
    Step,
    Tracked,
    state,
)
from puzzle.trackers import Tracker

DATA = Path(__file__).parent / "data" / "progress"


def _feed(parser, log: Path, ok: bool = True) -> list:
    events = []
    with gzip.open(log, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            events += parser.safe_feed(line.rstrip("\n"))
    return events + parser.finish(ok)


def _of(events, cls):
    return [e for e in events if isinstance(e, cls)]


def _elmer(name: str, tmp_path, step=1, of=1) -> ElmerProgress:
    shutil.copy(DATA / name / "case.sif", tmp_path / "case.sif")
    (tmp_path / "mesh").mkdir()
    shutil.copy(DATA / name / "mesh.header", tmp_path / "mesh" / "mesh.header")
    return ElmerProgress(tmp_path / "case.sif", step, of)


def test_fenicsx_reads_the_events_study_py_prints(tmp_path):
    events = _feed(FenicsxProgress(tmp_path / "study.py", step=2, of=3),
                   DATA / "fenicsx_transient" / "solver.log.gz")  # fmt: skip
    (step,) = _of(events, Step)
    assert (step.number, step.of, step.type, step.end_time) == (2, 3, "transient", 400.0)
    increments = _of(events, Increment)
    assert [e.time for e in increments] == [20.0 * k for k in range(1, 21)]
    assert all(e.step == 2 for e in increments)
    assert len(_of(events, Converged)) == 20
    assert _of(events, Resource)[0].equations > 0
    tracked = _of(events, Tracked)
    assert len(tracked) == 20 and tracked[-1].name == "Far end" and tracked[-1].unit == "K"
    assert 293.15 < tracked[0].value < tracked[-1].value < 373.15  # warming toward 100 °C


def test_fenicsx_trackers_are_written_for_study_py(tmp_path):
    parser = FenicsxProgress(tmp_path / "study.py")
    point = Tracker("Tip", "displacement", "y", np.array([7]), points=np.array([[0.2, 0, 0]]))
    reaction = Tracker("Root", "reaction_force", "total", np.array([1, 2]), reduce="sum",
                       points=np.zeros((2, 3)))  # fmt: skip
    parser.trackers = [point, reaction]
    import json

    (written,) = json.loads((tmp_path / "trackers.json").read_text(encoding="utf-8"))
    assert written["name"] == "Tip" and written["points"] == [[0.2, 0.0, 0.0]]
    notes = [e.text for e in parser.safe_feed("hello") if isinstance(e, Message)]
    assert notes and "Root" in notes[0]
    parser.trackers = []
    assert not (tmp_path / "trackers.json").exists()


def test_fenicsx_failure_says_why(tmp_path):
    parser = FenicsxProgress(tmp_path / "study.py")
    for line in ("Traceback (most recent call last):", "  File study.py, line 3",
                 "temperature iteration did not converge in 100 steps"):  # fmt: skip
        parser.safe_feed(line)
    (diverged,) = _of(parser.finish(False), Diverged)
    assert diverged.final and "did not converge" in diverged.reason


def test_elmer_time_steps_and_iterations(tmp_path):
    events = _feed(_elmer("elmer_transient", tmp_path), DATA / "elmer_transient" / "solver.log.gz")
    (step,) = _of(events, Step)
    assert step.type == "transient" and step.end_time == 400.0
    assert [e.time for e in _of(events, Increment)] == [20.0 * k for k in range(1, 21)]
    converged = _of(events, Converged)
    assert [c.increment for c in converged] == list(range(1, 21))
    assert converged[-1].time == 400.0
    its = _of(events, Iteration)
    assert len(its) == 40  # two per step: the solve, then no change
    assert its[0].criteria == {"heat equation change": 1e-10}
    s = state(events)
    assert s.fraction() == 1.0 and s.equations == 1860  # one temperature per node


def test_elmer_nonlinear_iterations_fall_below_the_tolerance(tmp_path):
    events = _feed(_elmer("elmer_nonlinear", tmp_path), DATA / "elmer_nonlinear" / "solver.log.gz")
    its = _of(events, Iteration)
    values = [e.residuals["heat equation change"] for e in its]
    assert len(values) == 6 and values[-1] < 1e-10 < values[-2]
    assert len(_of(events, Converged)) == 1 and _of(events, Step)[0].end_time is None


def test_elmer_errors_and_a_failed_run(tmp_path):
    parser = _elmer("elmer_transient", tmp_path)
    parser.safe_feed("WARNING:: LoadInputFile: > Equation 1 < not used in any Body!")
    events = parser.safe_feed("ERROR:: ElmerSolver: Linear system solver failed")
    (error,) = _of(events, Message)
    assert error.level == "error" and error.text.startswith("ElmerSolver")
    assert _of(parser.finish(False), Diverged)[0].final


def test_elmer_trackers_read_each_vtu_once_it_is_complete(tmp_path):
    parser = _elmer("elmer_transient", tmp_path)
    mesh = tmp_path / "mesh"
    (mesh / "puzzle-node-ids.txt").write_text("10\n11\n12\n", encoding="utf-8")
    (mesh / "puzzle-times.txt").write_text("20\n40\n", encoding="utf-8")
    vtu = ('<VTKFile><UnstructuredGrid><Piece><PointData><DataArray Name="temperature" '
           'NumberOfComponents="1" format="ascii">{}</DataArray></PointData></Piece>'
           '</UnstructuredGrid></VTKFile>')  # fmt: skip
    parser.trackers = [Tracker("Hot", "temperature", "value", np.array([12]), reduce="max")]
    (mesh / "case_t0001.vtu").write_text(vtu.format("1 2 3"), encoding="utf-8")
    assert parser.poll() == []  # the newest file may be half written
    (mesh / "case_t0002.vtu").write_text(vtu.format("4 5 6"), encoding="utf-8")
    (first,) = parser.poll()
    assert (first.value, first.time, first.increment) == (3.0, 20.0, 1)
    (second,) = parser.finish(True)
    assert (second.value, second.time) == (6.0, 40.0)


def test_elmer_harmonic_scan_counts_frequencies(tmp_path):
    events = _feed(_elmer("elmer_harmonic", tmp_path), DATA / "elmer_harmonic" / "solver.log.gz")
    assert [(e.time, e.end_time) for e in _of(events, Increment)] == [
        (float(k), 4.0) for k in range(1, 5)
    ]
    assert state(events).fraction() == 1.0
