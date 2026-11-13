"""Several studies one after another (ADR 0073): sources of transfers first, a failure
goes on to the next, a stop ends the queue; the CLI and the GUI share the order."""

import os
import shutil
import threading
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from puzzle import run_queue  # noqa: E402
from puzzle.model.schema import Project, Study, Transfer  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def _studies():
    p = Project(name="p")
    comp = uuid4()
    names = ["Structural", "Modal", "Thermal"]
    made = {n: p.add(Study(name=n, component=comp, solver="calculix", steps=[])) for n in names}
    # Structural reads Thermal's temperature: Thermal must run first.
    p.add(Transfer(name="T", source=made["Thermal"].id, target=made["Structural"].id,
                   field="temperature", role="load"))  # fmt: skip
    return p, made


def test_sources_of_transfers_run_first_the_rest_in_order():
    p, made = _studies()
    assert [s.name for s in run_queue.in_order(p)] == ["Modal", "Thermal", "Structural"]
    picked = run_queue.in_order(p, [made["Structural"].id, made["Modal"].id])
    assert [s.name for s in picked] == ["Structural", "Modal"]  # Thermal not asked for


def test_a_failure_goes_on_and_a_stop_ends_the_queue():
    p, made = _studies()
    order = run_queue.in_order(p)

    def submit(study):
        if study.name == "Modal":
            raise RuntimeError("diverged")
        return "ok"

    done = run_queue.run_all(None, order, submit=submit)
    assert [(o.study.name, o.ok) for o in done] == [
        ("Modal", False), ("Thermal", True), ("Structural", True)]  # fmt: skip
    assert run_queue.summary(done) == "3 studies: 2 finished, 1 failed (Modal)"
    stop = threading.Event()
    done = run_queue.run_all(None, order, stop=stop, submit=lambda s: stop.set())
    assert [o.study.name for o in done] == ["Modal"]  # stopped after the first


@pytest.fixture
def window(tmp_path):
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    root = tmp_path / "c"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    w = MainWindow(root)
    yield w
    w.close()


def test_the_gui_runs_them_one_after_another(window, monkeypatch):
    """Run all queues every study; each starts when the last ends, failed or not."""
    started = []

    def fake_start(study, call, *, then=None, cancel=None, verb="Running"):
        started.append(study.name)
        window._pending_then = then

    monkeypatch.setattr(window, "_start_run", fake_start)
    window.run_studies(None)
    names = [s.name for s in run_queue.in_order(window.session.project)]
    assert started == names[:1] and window.queue_busy()
    for _ in names[1:]:
        window._pending_then(False)  # a failure goes on to the next
    assert started == names
    window._pending_then(True)
    assert not window.queue_busy()

    started.clear()
    window.run_studies(None)
    window.stop_all()  # the running one is cancelled, the rest never start
    assert window._queue_cancel.is_set() and not window.run_queue
    window._pending_then(False)
    assert started == names[:1] and not window.queue_busy()


def test_run_selected_runs_the_ticked_ones(window, monkeypatch):
    from PySide6.QtCore import Qt

    from puzzle.gui import menus

    chosen = []
    monkeypatch.setattr(window, "run_studies", lambda ids: chosen.extend(ids))
    first = window.session.project.of_type(Study)[0]

    def tick(dialog):
        dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
        return True

    window.run_dialog = tick
    menus.run_selected_studies(window)
    assert chosen == [str(first.id)] and window.last_run_selection == chosen
