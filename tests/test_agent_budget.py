"""The agents' budget set in the agent panel (ADR 0015): the Budget… button and what is
used of it show only at "act within the budget"; the form takes whole runs, minutes and
core-hours and saves them in one autonomy command (which starts the count again);
negative or unreadable values are refused. `puzzle autonomy --core-hours` sets the
third, and setting one value keeps the others."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.commands.model import SetAutonomy  # noqa: E402
from puzzle.model.schema import Project  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_panel_edits_the_budget(app, tmp_path):
    from puzzle.gui.agent_panel import _budget
    from puzzle.gui.window import MainWindow

    save_project(Project(name="p"), tmp_path)
    window = MainWindow(tmp_path)
    panel = window.agent
    assert panel.budget_button.isHidden()  # "propose": no budget to spend
    window.session.submit(SetAutonomy(level="act_in_budget"))
    panel.refresh()
    assert not panel.budget_button.isHidden()
    assert panel.budget_used.text() == "0 of 5 runs, 0 min of 10 min used"
    assert panel.edit_budget({"runs": "12", "minutes": "45", "core_hours": "0.5"})
    b = load_project(tmp_path).agent.budget
    assert (b.runs, b.solver_seconds, b.core_hours) == (12, 2700.0, 0.5)
    assert "12 runs" in panel.budget_used.text()
    with pytest.raises(ValueError, match="negative"):
        _budget({"runs": "-1", "minutes": "1", "core_hours": "0"})
    with pytest.raises(ValueError, match="whole runs"):
        _budget({"runs": "two", "minutes": "1", "core_hours": "0"})
    assert not panel.edit_budget({"runs": "x", "minutes": "1", "core_hours": "0"})
    window.close()


def test_the_cli_sets_core_hours_and_keeps_the_rest(tmp_path, capsys):
    from puzzle.cli import main

    save_project(Project(name="p"), tmp_path)
    assert main(["autonomy", str(tmp_path), "--runs", "7"]) == 0
    assert main(["autonomy", str(tmp_path), "--core-hours", "3"]) == 0
    b = load_project(tmp_path).agent.budget
    assert (b.runs, b.core_hours) == (7, 3.0)
