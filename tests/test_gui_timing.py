"""Where the GUI's time goes (gui/timing.py), and the view kept between tree items."""

from __future__ import annotations

import logging
import os
import re
import time

import pytest

from puzzle.gui import timing


def test_a_slow_action_is_logged_with_its_parts(monkeypatch, caplog):
    monkeypatch.setattr(timing, "SLOW_MS", 20)
    with caplog.at_level(logging.WARNING, "puzzle.gui.timing"):
        with timing.action("showing Bracket"):
            with timing.step("Properties"):
                time.sleep(0.03)
            with timing.step("view"):
                pass
        with timing.action("showing Plate"):  # quick: nothing logged
            pass
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and lines[0].startswith("slow: showing Bracket took")
    # a sleep of 30 ms takes longer on a busy machine (51 and 190 ms on CI's Macs)
    assert re.search(r"\(Properties \d+ ms\)", lines[0]) and "view" not in lines[0]


def test_a_menu_item_waiting_in_its_dialog_is_not_slow(monkeypatch, caplog):
    monkeypatch.setattr(timing, "SLOW_MS", 1)
    with caplog.at_level(logging.WARNING, "puzzle.gui.timing"):
        timing.named("Model → New study…", lambda: time.sleep(0.01))()
    assert not caplog.records
    assert timing.doing(since=0) == "Model → New study…"  # what a stall would name


def test_a_held_window_is_noticed(monkeypatch, caplog):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    watch = timing.StallWatch(QObject())
    watch._last = time.perf_counter() - 0.6  # the last tick was 600 ms ago
    with caplog.at_level(logging.WARNING, "puzzle.gui.timing"), timing.action("Mesh → Mesh"):
        watch._tick()
    assert watch.stalls and watch.stalls[0] > 500
    assert "held" in caplog.records[0].getMessage()
    assert "(Mesh → Mesh)" in caplog.records[0].getMessage()
    watch._tick()  # on time now
    assert len(watch.stalls) == 1


@pytest.mark.skipif(not os.environ.get("PUZZLE_RENDER_TESTS"), reason="needs OpenGL")
def test_another_highlight_keeps_the_model_drawn(tmp_path):
    from conftest_geometry import bar_project, write_bar_step
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.viewport import Viewport
    from puzzle.mesh import generate_mesh
    from puzzle.post.cuts import Cut

    QApplication.instance() or QApplication([])
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [], mesh_size_mm=5.0)
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh
    view = Viewport()
    view.show_mesh(mesh)
    base = set(view.plotter.renderer.actors)
    view.plotter.add_mesh(view.surface.extract_cells([0]), name="symbol")  # drawn after
    view.show_mesh(mesh, "Bar/Tip")
    now = set(view.plotter.renderer.actors)
    assert base < now and "symbol" not in now  # the model kept; what was over it redrawn
    assert view.shown["highlight"] == "Bar/Tip" and view.shown["cells"] > 0
    view.show_mesh(mesh)
    assert set(view.plotter.renderer.actors) == base
    view.set_style(edges=True)
    view.cut = Cut(axis="x")
    view.show_mesh(mesh)  # a cut: the model drawn again
    assert "cut" in view.shown
    QApplication.processEvents()  # the frames it queued, before the view goes
    view.close_plotter()
