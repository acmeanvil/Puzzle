"""The window's panels through `gui/docking.py` (ADR 0045): Agent and Advisor tabbed
together, built-in and saved layouts (Window → Layouts), and auto-hide into a side bar."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.docking import layouts_dir  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    end = time.monotonic() + 0.5  # the startup layout pass settles first
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    yield w
    w.close()


def test_agent_and_advisor_share_one_place_as_tabs(window):
    window.docks.show("advisor")
    window.docks.show("agent")
    assert window.agent_dock.isVisible()
    assert window.advisor_dock in window.tabifiedDockWidgets(window.agent_dock)


def test_built_in_and_saved_layouts(window):
    names = dict(window.docks.names())
    assert {"Modelling", "Results", "Agent beside the model"} <= set(names)
    window.docks.apply(names["Results"])
    assert not window.messages_folded and window.output_tabs.currentWidget() is window.plots
    window.docks.apply(names["Agent beside the model"])
    assert not window.agent_dock.isHidden() and window.messages_folded
    key = window.docks.save("Two screens")
    assert (layouts_dir() / "two-screens.layout").exists()
    window.docks.apply(names["Modelling"])
    assert window.agent_dock.isHidden()
    assert ("Two screens", key) in window.docks.names()
    assert window.docks.apply(key)
    assert not window.agent_dock.isHidden()  # as it was saved
    window.docks.delete(key)
    assert ("Two screens", key) not in window.docks.names()


def test_auto_hide_into_the_side_bar_and_back(window):
    app = QApplication.instance()
    window.docks.set_auto_hide("model", True)
    assert window.model_dock.isHidden() and "model" in window.docks.auto
    bar = next(iter(window.docks.bars.values()))
    assert bar.isVisible() and len(bar.actions()) == 1
    window.docks.show("model")  # as its side-bar button does
    assert window.docks.peeking == "model" and window.model_dock.isVisible()
    window.viewport.setFocus()  # using the view hides it again
    app.processEvents()
    assert window.model_dock.isHidden() and window.docks.peeking is None
    state = window.docks.state()
    window.docks.set_auto_hide("model", False)
    assert window.model_dock.isVisible() and not bar.isVisible()
    assert window.docks.restore(state)
    assert "model" in window.docks.auto  # part of the saved layout


def test_a_closed_floating_messages_comes_back_docked(window):
    # A user floated Messages, then closed it: opening Messages must bring it back.
    app = QApplication.instance()
    window.fold_messages(True)
    window.messages_dock.setFloating(True)
    window.messages_dock.close()
    app.processEvents()
    window.actions_by_name["messages"].setChecked(True)  # the toolbar's Messages button
    end = time.monotonic() + 0.5  # the layout settles
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert window.messages_dock.isVisible() and not window.messages_dock.isFloating()
    assert not window.messages_folded
