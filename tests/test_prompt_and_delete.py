"""Two GUI details: the agent's message box (wraps, grows, sends by the preferred keys,
names them in its placeholder) and deleting a stored result set (asked first; the
store gives back its files and keeps other runs)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.gui.agent_panel import PromptBox  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _key(box, key=Qt.Key.Key_Return, mods=Qt.KeyboardModifier.NoModifier):
    box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def test_prompt_box_keys_and_growth(app):
    box = PromptBox()
    box.resize(300, 40)
    sent = []
    box.submitted.connect(lambda: sent.append(box.text()))
    assert settings.SEND_KEYS in box.placeholderText()  # the default: Ctrl or ⌘ + Enter
    one = box.height()
    box.setText("first line")
    _key(box)  # Enter: a new line, not sent
    box.insertPlainText("second line")
    assert sent == [] and box.text() == "first line\nsecond line"
    assert box.height() > one  # it grew
    box.setText("\n".join(f"line {i}" for i in range(20)))
    assert box.height() < 12 * box.fontMetrics().lineSpacing()  # capped, then scrolls
    _key(box, mods=Qt.KeyboardModifier.ControlModifier)
    assert len(sent) == 1

    settings.current().set("agent.send_key", "enter")
    try:
        box.update_hint()
        assert "Enter to send, Shift+Enter for a new line" in box.placeholderText()
        box.setText("ask")
        _key(box, mods=Qt.KeyboardModifier.ShiftModifier)  # a new line
        assert box.text() == "ask\n" and len(sent) == 1
        _key(box)  # Enter sends
        assert len(sent) == 2
    finally:
        settings.current().set("agent.send_key", "modifier")


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_delete_a_result_set(app, tmp_path):
    pytest.importorskip("OCP")
    from conftest_geometry import bar_project, write_bar_step

    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study
    from puzzle.project import save_project
    from puzzle.results.store import ResultStore
    from puzzle.solve import run_study

    root = tmp_path / "bar"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    held = Feature(type="fixed", selection="Bar/Root")
    physics = Physics(type="solid_mechanics", features=[held, force])
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], 10.0)
    steps = [SolveStep(type="stationary")]
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    store = ResultStore(root / "results")
    first = run_study(project, study.id, root, tmp_path / "a", store=store, reuse=False).run_id
    second = run_study(project, study.id, root, tmp_path / "b", store=store, reuse=False).run_id
    size = sum(f.stat().st_size for f in (root / "results").rglob("*.parquet"))
    window = MainWindow(root)
    try:
        window.ask_yes = lambda *a: False
        assert not window.delete_run(first)  # asked, and declined
        window.ask_yes = lambda *a: True
        assert window.delete_run(first)
        assert [r.run_id for r in store.runs()] == [second]
        assert store.read_results(second).steps  # the other run, and the shared mesh, stay
        assert window.delete_run(second)
        assert store.runs() == []
        left = sum(f.stat().st_size for f in (root / "results").rglob("*.parquet"))
        assert left < size / 4  # the files went, not only the records
    finally:
        window.close()
