"""A solve's logs read whole and searched (the maintainer, 2026-11-11: the solver output
in a larger window with search and highlighting): markers per solver, a stored run's
folder, the log window's search, problems and following."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from puzzle import solver_logs
from puzzle.errors import PuzzleError
from puzzle.results.store import RunRecord

ASTER = """\
mesh = DEFI_GROUP(ALARME='OUI',
EVENEMENT='ERREUR',
  ║ <A> <MODELISA8_14>                                                  ║
  ║ Ceci est une alarme. Si vous ne comprenez pas le sens de cette      ║
  ║ <F> <FACTOR_10>                                                     ║
--- DIAGNOSTIC JOB : <A>_ALARM
"""


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (" *ERROR in e_c3d: nonpositive jacobian", "error"),
        (" *WARNING in gen3dnor: shell normals", "warning"),
        ("  ║ <A> <MODELISA8_14>   ║", "warning"),
        ("  ║ <F> <FACTOR_10>   ║", "error"),
        ("ERROR:: ElmerSolver: unknown keyword", "error"),
        ("--> FOAM FATAL ERROR:", "error"),
        ("Traceback (most recent call last):", "error"),
        ("SIMPLE solution converged in 412 iterations", "good"),
        ("iteration 12 not converged", "warning"),
        ("EVENEMENT='ERREUR',", None),
        ("mesh = DEFI_GROUP(ALARME='OUI',", None),
        ("relative error 1.2e-9", None),
        (" 0 errors, 0 warnings", None),
        ("   increment 3 attempt 1", None),
    ],
)
def test_lines_are_classed_by_the_solvers_markers(line, kind):
    assert solver_logs.classify(line) == kind


def _run_folder(root: Path, name: str) -> Path:
    folder = root / ".puzzle" / "runs" / name
    folder.mkdir(parents=True)
    (folder / "solver.log").write_text(ASTER, encoding="utf-8")
    (folder / "job.mess").write_text("  <A> <CATAMESS_41>\n", encoding="utf-8")
    (folder / "mesh").mkdir()
    (folder / "mesh" / "solver.log").write_text("not this one\n", encoding="utf-8")
    return folder


def _record(started: datetime) -> RunRecord:
    return RunRecord("r1", "k", "s", "Vessel CA", "code_aster", "docker", "m", "completed",
                     started, 1.0, 1, started)  # fmt: skip


def test_a_stored_run_finds_its_folder_and_logs(tmp_path):
    _run_folder(tmp_path, "vessel-ca-20261111-140456")
    newer = _run_folder(tmp_path, "vessel-ca-20261111-143111")
    (tmp_path / ".puzzle" / "runs" / "vessel-ca-cache").mkdir()  # not a run folder
    run = _record(datetime(2026, 11, 11, 14, 31, 13, tzinfo=UTC))
    assert solver_logs.folder_of(tmp_path, run) == newer
    first = replace(run, started_at=datetime(2026, 11, 11, 14, 5, tzinfo=UTC))
    assert solver_logs.folder_of(tmp_path, first).name.endswith("140456")
    assert solver_logs.latest(tmp_path, "Vessel CA") == newer
    assert [p.name for p in solver_logs.logs(newer)] == ["solver.log", "job.mess"]
    assert solver_logs.counts(newer) == {"error": 1, "warning": 3}


def test_lines_problems_patterns_and_tails(tmp_path):
    folder = _run_folder(tmp_path, "vessel-ca-20261111-143111")
    found = solver_logs.lines(folder, problems=True)
    assert [(x.file, x.number, x.kind) for x in found] == [
        ("solver.log", 3, "warning"), ("solver.log", 5, "error"), ("solver.log", 6, "warning"),
        ("job.mess", 1, "warning")]  # fmt: skip
    boxed = solver_logs.lines(folder, pattern="modelisa8", context=1)
    assert [x.number for x in boxed] == [2, 3, 4]  # any case, a line either side
    assert [x.number for x in solver_logs.lines(folder, tail=2)][:2] == [5, 6]


def test_find_by_run_or_study_and_none(tmp_path):
    with pytest.raises(PuzzleError) as exc:
        solver_logs.find(tmp_path, run="nothing")
    assert exc.value.code == "not_found"


# --- the window ---------------------------------------------------------------------


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_window_searches_marks_and_jumps_to_problems(app, tmp_path):
    from puzzle.gui.text_viewer import TextViewer

    folder = _run_folder(tmp_path, "vessel-ca-20261111-143111")
    viewer = TextViewer(title="Solver output", files=solver_logs.logs(folder), folder=folder)
    try:
        assert viewer.choice.isVisibleTo(viewer) is False or viewer.choice.count() == 2
        assert viewer.problems.text() == "1 error line, 2 warning lines"
        viewer.query.setText("<a>")
        assert viewer.found.text() == "2 matches" and len(viewer.text.extraSelections()) == 2
        viewer.case.setChecked(True)
        assert viewer.found.text() == "not found"  # "<A>" in capitals only
        viewer.case.setChecked(False)
        cursor = viewer.text.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        viewer.text.setTextCursor(cursor)
        assert viewer.find(True) and viewer.text.textCursor().blockNumber() == 2
        assert viewer.find(True) and viewer.text.textCursor().blockNumber() == 5
        assert viewer.find(True) and viewer.text.textCursor().blockNumber() == 2  # wraps
        viewer.text.setTextCursor(cursor)
        assert [viewer.problem(True) for _ in range(4)] == [3, 5, 6, 3]
        assert viewer.problem(False) == 6
        viewer.choice.setCurrentIndex(1)
        assert "CATAMESS_41" in viewer.text.toPlainText()
        out = viewer.save_as(str(tmp_path / "saved.log"))
        assert out.read_text(encoding="utf-8") == "  <A> <CATAMESS_41>\n"
        with (folder / "job.mess").open("a", encoding="utf-8") as f:
            f.write("  *ERROR more\n")  # the solve writes on
        os.utime(folder / "job.mess", ns=(1, 2))
        viewer.fill()
        assert "*ERROR more" in viewer.text.toPlainText()
    finally:
        viewer.close()


def test_the_window_follows_a_text_box(app):
    from PySide6.QtWidgets import QPlainTextEdit

    from puzzle.gui.text_viewer import TextViewer

    box = QPlainTextEdit()
    box.appendPlainText("--- vessel: calculix")
    viewer = TextViewer(title="Output", follow=box)
    try:
        assert viewer.text.toPlainText() == "--- vessel: calculix"
        box.appendPlainText(" *WARNING in contact")
        viewer.fill()
        assert viewer.text.toPlainText().endswith("*WARNING in contact")
        assert viewer.problems.text() == "0 error lines, 1 warning line"
    finally:
        viewer.close()


def test_help_log_is_the_same_window(app, tmp_path, monkeypatch):
    from puzzle.gui import errors, log_viewer

    log = tmp_path / "gui.log"
    log.write_text("=== Puzzle started\n--- 2026-11-11T10:56:57\nIndexError: index -1\n",
                   encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(errors, "log_path", lambda: log)
    viewer = log_viewer.LogViewer()
    try:
        assert viewer.problems.text() == "1 error line, 0 warning lines"
        viewer.only_errors.setChecked(True)
        assert viewer.text.toPlainText().startswith("--- 2026-11-11")
        viewer.query.setText("indexerror")
        assert viewer.found.text() == "1 match"
    finally:
        viewer.close()
