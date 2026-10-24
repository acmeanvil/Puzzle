"""ADR 0031: the model gets the room; a view toolbar; icons with labels, or not.

The agent dock starts hidden and opens when a proposal arrives; messages start folded,
open on a run or an error and fold after a good run. The toolbar groups its actions
with icons; whether labels show under them is a setting. The view toolbar's actions
reach the viewport (recorded in `Viewport.shown`/`style` off screen). The layout a
user leaves is kept, and Window → Reset layout forgets it.
"""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.commands.session import Actor  # noqa: E402
from puzzle.errors import PuzzleError  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    yield w
    w.close()


def test_docks_open_when_needed(window):
    assert not window.agent_dock.isVisibleTo(window)
    assert window.messages_folded
    window.report(PuzzleError("something went wrong", code="invalid"))
    assert not window.messages_folded  # news opens it, on the Output tab
    assert window.output_tabs.currentWidget() is window.log
    window.fold_messages(True)
    assert not window.actions_by_name["messages"].isChecked()
    window.actions_by_name["messages"].trigger()
    assert not window.messages_folded
    # A proposal opens the agent dock (without taking focus).
    s = window.session
    s.propose(
        [
            {
                "command": "edit",
                "object": "Cantilever",
                "ops": [
                    {
                        "op": "set",
                        "path": "physics.0.features.1.values.y",
                        "value": {"value": -300, "unit": "N"},
                    }
                ],
            }
        ],
        actor=Actor("agent", "test"),
        rationale="a test proposal",
    )
    window.refresh()
    assert window.agent_dock.isVisibleTo(window)


def test_toolbar_groups_icons_and_labels_setting(window):
    bar = window.toolbar
    shown = [a.text() for a in bar.actions() if a.text()]
    assert shown[:4] == ["New…", "Open…", "Undo", "Mesh"]
    assert "Delete" not in shown and "Reload from disk" not in shown  # menus only
    assert all(not window.actions_by_name[n].icon().isNull() for n in ("new", "run", "pick"))
    assert window.actions_by_name["run"].toolTip().startswith("Run  (")
    assert bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
    s = settings.current()
    s.set("interface.toolbar_labels", "icons")
    s.save()
    window.settings_changed()
    assert bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def test_view_toolbar_reaches_the_viewport(window):
    vb = window.viewport.view_bar
    for name in ("fit", "iso", "+x", "-z", "edges", "wireframe", "projection", "copy", "camera"):
        assert name in vb.actions_by_name, name
    vb.actions_by_name["-z"].trigger()
    assert window.viewport.style["view"] == "-z"
    vb.actions_by_name["edges"].trigger()
    vb.actions_by_name["projection"].trigger()
    wanted = {"edges": True, "wireframe": False, "parallel": True, "view": "-z"}
    assert {k: window.viewport.style[k] for k in wanted} == wanted
    assert window.viewport.shown["style"]["parallel"]
    assert vb.actions_by_name["fit"].shortcut().toString() == "F"


def test_layout_is_kept_and_reset_forgets_it(window):
    window._sized = True  # as if it had been on screen
    window.save_layout()
    assert settings.window_state_path().exists()
    window.reset_layout()
    assert not settings.window_state_path().exists()
    assert window.messages_folded and not window.agent_dock.isVisibleTo(window)


def test_status_bar_says_the_context(window):
    window.left_tabs.setCurrentWidget(window.workbench)
    assert window.context_label.text() == "Geometry"
    window.left_tabs.setCurrentIndex(0)
    assert window.context_label.text() == "Model"


def test_messages_drag_open_and_shut(window):
    """Folding resizes the Messages dock and never caps it, so its separator always
    drags: open past the tab bar unfolds it, back down folds it, and the open height
    is kept for the next unfold."""
    window.resize(1200, 800)
    window.show()
    QApplication.processEvents()
    window._restore_layout()  # as the first show's timer does
    QApplication.processEvents()
    assert window.messages_folded
    assert window.messages_dock.maximumHeight() > 10_000  # no cap while folded
    vertical = Qt.Orientation.Vertical
    window.resizeDocks([window.messages_dock], [260], vertical)  # a drag of the separator
    QApplication.processEvents()
    assert not window.messages_folded
    opened = window.messages_dock.height()
    window.resizeDocks([window.messages_dock], [window._folded_height()], vertical)
    QApplication.processEvents()
    assert window.messages_folded
    window.fold_messages(False)
    QApplication.processEvents()
    assert abs(window.messages_dock.height() - opened) <= 8
