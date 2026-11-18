"""Long reads off the GUI thread (ADR 0090 package 14: PANEL-13, PANEL-26), and run
folders that two runs started in one second don't share (BE-20)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).parent))

import schematic_project as sp  # noqa: E402
from conftest import delete_windows  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def stored(tmp_path):
    """The cantilever with a run stored for each study (two tets, a displacement)."""
    from puzzle.commands import Session
    from puzzle.model.schema import Study
    from puzzle.project.graph import run_hashes

    root = sp.copy(tmp_path)
    s = Session(root)
    keys = run_hashes(s.project, root)
    for st in s.project.of_type(Study):
        store = sp.store_run(root, st, keys[st.id][st.solver][0])
    return s, store


def test_the_fatigue_form_works_its_answer_out_on_a_worker(app, stored, monkeypatch):
    """PANEL-26: a change of any field evaluated the whole fatigue tool on the GUI
    thread. Now the form says it is working, answers when the worker has, and an
    answer for the form as it was before a later change is never shown."""
    from puzzle.gui import derived_dialog
    from puzzle.post import derived

    s, store = stored
    gui_thread = threading.get_ident()
    seen = []

    def slow(project, store_, d):
        import numpy as np

        seen.append(threading.get_ident())
        time.sleep(0.4)
        life = SimpleNamespace(values=np.full((3, 1), 2e6))
        sf = SimpleNamespace(values=np.full((3, 1), 1.7))
        step = SimpleNamespace(fields={"fatigue_life": life, "fatigue_safety_factor": sf})
        return SimpleNamespace(results=SimpleNamespace(steps=[step]), warnings=[])

    monkeypatch.setattr(derived, "evaluate", slow)
    d = derived_dialog.FatigueDialog(s.project, store)
    try:
        ok = d.buttons.button(QDialogButtonBox.StandardButton.Ok)
        t = time.perf_counter()
        d._update()
        assert time.perf_counter() - t < 0.2  # the form never waits for it
        assert "Working out" in d.status.text() and not ok.isEnabled()
        d.wait()
        assert seen and gui_thread not in seen  # on a worker
        assert "smallest safety factor 1.7" in d.status.text() and ok.isEnabled()

        # A change while one is being worked out: the older answer is dropped.
        d._later.stop()
        d._evaluate()  # one under way for the form as it is
        d.kf.setText("not a number")  # then the form changes, and can't be evaluated
        d.wait()
        assert not ok.isEnabled() and "smallest safety factor" not in d.status.text()
    finally:
        d.wait()
        d.deleteLater()
        delete_windows(app)


def test_result_tool_choices_read_names_only(stored, monkeypatch):
    """PANEL-13: the choices of the result-tool, export, shoot-out and error-estimate
    forms read the run's metadata and its mesh's group names, never its values."""
    from puzzle.gui import export_dialog, result_tools
    from puzzle.model.schema import Study
    from puzzle.results.store import ResultStore

    s, store = stored
    window = SimpleNamespace(session=s, _store=lambda: store)
    for name in ("read_mesh", "read_results", "read_field"):
        monkeypatch.setattr(ResultStore, name, lambda *a, **k: pytest.fail("a run read whole"))
    study = next(st for st in s.project.of_type(Study) if st.name == "Tip load")
    sid = str(study.id)
    assert ("Bar (bodies)", "Bar") in result_tools.selection_choices(window, sid)
    keys = [k for _, k in result_tools.quantity_choices(window, sid)]
    assert "displacement:total" in keys
    assert export_dialog._steps(window, sid) == [("Last", -1)]
    start, end = result_tools.default_path(window, sid)
    assert start[0] == 0.0 and end[0] == 1.0  # along x, the box's longest side here


def test_two_runs_started_in_one_second_get_folders_of_their_own(tmp_path, monkeypatch):
    """BE-20: the GUI's run and an agent's in the same second shared one folder."""
    from puzzle.commands import Session, session
    from puzzle.errors import PuzzleError
    from puzzle.solve import StudyError

    root = sp.copy(tmp_path)
    s = Session(root)
    folders = []

    def capture(project, study_id, root_, workdir, **kwargs):
        folders.append(Path(workdir))
        raise StudyError("stopped here", code="stopped")

    import puzzle.solve

    monkeypatch.setattr(puzzle.solve, "run_study", capture)

    class Frozen(session.datetime):
        @classmethod
        def now(cls, tz=None):
            return session.datetime(2026, 11, 18, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(session, "datetime", Frozen)
    for _ in range(3):
        with pytest.raises(PuzzleError):
            s.submit({"command": "run_study", "study": "Tip load"})
    assert len(folders) == 3 and len(set(folders)) == 3
    assert [f.name for f in folders] == ["tip-load-20261118-120000", "tip-load-20261118-120000-2",
                                         "tip-load-20261118-120000-3"]  # fmt: skip
    from puzzle import solver_logs

    assert solver_logs.latest(root, "Tip load") is not None  # still found by their names
