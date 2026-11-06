"""The Jobs panel (ADR 0067 phase 4): the project's solves with a job runner, in words,
the note when a project opens with some, Resume like a run, Open log and Dismiss."""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_gui_names import bad  # noqa: E402

from puzzle.commands import Session  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.jobs import overview, pending  # noqa: E402
from puzzle.model.schema import Study  # noqa: E402
from puzzle.runner import client  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
JOB = "20261105-000000-abcdef"
needs_posix = pytest.mark.skipif(os.name != "posix", reason="the runner needs POSIX")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                  "versions"))  # fmt: skip
    return dest


@pytest.fixture
def runner_on(tmp_path, monkeypatch):
    monkeypatch.setenv("PUZZLE_RUNNER", "on")
    monkeypatch.setenv("PUZZLE_RUNNER_IDLE", "3")
    monkeypatch.setenv("PUZZLE_RUNNER_POLL", "0.1")
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("PUZZLE_USER_DIR", str(user))
    base = user / "runner"
    yield base
    if (client.runner_dir(base) / "runner.json").exists():
        try:
            with client.local(base) as c:
                c.request("shutdown")
        except Exception:  # noqa: BLE001 - already gone
            pass


def _study(root: Path) -> Study:
    return next(s for s in Session(root).project.of_type(Study) if s.name == "Tip load")


def _pend(root: Path, job: str = JOB, where: str = "this computer") -> Path:
    case = root / ".puzzle" / "runs" / job
    case.mkdir(parents=True, exist_ok=True)
    pending.add(pending.RunContext(root, str(_study(root).id), case), job, where, case, {})
    return case


def wait(app, condition, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def cells(panel) -> list[list[str]]:
    t = panel.table
    return [[t.topLevelItem(i).text(c) for c in range(t.columnCount())]
            for i in range(t.topLevelItemCount())]  # fmt: skip


def test_window_jobs_and_the_empty_panel(app, root):
    w = MainWindow(root)
    try:
        action = w.actions_by_name["jobs"]
        assert action.text() == "Jobs" and action.shortcut().toString()
        assert w.jobs_dock.isHidden()
        action.setChecked(True)
        assert not w.jobs_dock.isHidden()
        w.jobs.refresh(wait=True)
        assert w.jobs.note.isVisibleTo(w.jobs) and not w.jobs.table.isVisibleTo(w.jobs)
        assert not any(s.text.endswith("closed") for s in w.toasts.shown)
    finally:
        w.close()


def test_a_project_opened_with_pending_jobs(app, root):
    case = _pend(root)
    (case / "solver.log").write_text("step 1\n increment 3 converged\n")
    _pend(root, "20261105-000001-abcdef", where="lab")
    w = MainWindow(root)
    try:
        (note,) = [s for s in w.toasts.shown if "closed" in s.text]
        assert note.text == "2 solves went on while Puzzle was closed"
        assert note.action == "Pick them up"
        sid = str(_study(root).id)
        assert w.states[sid].state == "running"  # the tree shows it running
        w.open_jobs()
        w.jobs.refresh(wait=True)
        here, there = cells(w.jobs)
        assert here == ["Tip load", "CalculiX", "This computer", overview.UNREACHABLE, "—",
                        "increment 3 converged"]  # fmt: skip
        assert there[2:4] == ["lab", overview.NOT_ASKED]
        for text in here + there:  # names, never ids or keys
            assert JOB not in text and bad(text) is None, text
        opened = []
        w.open_url = opened.append
        assert w.jobs.open_log() == case / "solver.log" and opened
        w.jobs.table.setCurrentItem(w.jobs.table.topLevelItem(1))
        fill(w, sure=True, delete=False)
        assert w.jobs.dismiss() is not None
        assert set(w.last_form.asks) == {"sure", "delete"}  # a host: its case too
        wait(app, lambda: len(pending.entries(root)) == 1)
        fill(w, sure=False)  # asked, then kept
        w.jobs.table.setCurrentItem(w.jobs.table.topLevelItem(0))
        assert w.jobs.dismiss() is None and len(pending.entries(root)) == 1
    finally:
        w.close()


@pytest.mark.parametrize("how", ["panel", "note"])
def test_resume_runs_like_a_run(app, root, monkeypatch, how):
    _pend(root)
    called = []

    def resume(self, job, actor=None, *, on_output=None, on_event=None, cancel=None):
        called.append((job, cancel is not None))
        time.sleep(0.3)  # a moment, so the window can be seen following it
        on_output("[puzzle] picked up job")
        pending.remove(self.root, job)
        from puzzle.commands.session import Outcome

        return Outcome(status="applied")

    monkeypatch.setattr(Session, "resume", resume)
    w = MainWindow(root)
    try:
        w.open_jobs()
        w.jobs.refresh(wait=True)
        sid = str(_study(root).id)
        if how == "panel":
            w.jobs.resume()
        else:  # the note's Pick them up: the runner asked first, off the GUI thread
            w.resume_all_jobs()
            wait(app, lambda: sid in w.running)
        assert sid in w.running and w.solution.title.text().startswith("Tip load")
        wait(app, lambda: sid not in w.running)
        assert called == [(JOB, True)]
        assert "[puzzle] picked up job" in w.log.toPlainText()
        assert any(s.text == "Tip load finished" for s in w.toasts.shown)
        assert w.states[sid].state != "running"
    finally:
        w.close()


@needs_posix
def test_a_live_job_shows_running_and_dismiss_cancels_it(app, root, runner_on):
    case = _pend(root)
    with client.local(runner_on) as c:
        job = c.request("submit", spec={"dir": str(case), "argv": [
            "sh", "-c", "echo solving away; sleep 30"]})["job"]["id"]  # fmt: skip
    pending.remove(root, JOB)
    pending.add(pending.RunContext(root, str(_study(root).id), case), job, "this computer",
                case, {})  # fmt: skip
    w = MainWindow(root)
    try:
        w.open_jobs()
        wait(app, lambda: (w.jobs.refresh(wait=True) or cells(w.jobs))
             and cells(w.jobs)[0][5] == "solving away")  # fmt: skip
        assert cells(w.jobs)[0][3] == "Running"
        w.jobs.refresh()  # the timer's way: off the GUI thread
        wait(app, lambda: not w.jobs._reading)
        fill(w, sure=True)
        w.jobs.dismiss()
        wait(app, lambda: not pending.entries(root))
        with client.local(runner_on) as c:
            assert c.request("status")["jobs"] == []
    finally:
        w.close()
