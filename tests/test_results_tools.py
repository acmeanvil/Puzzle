"""ADR 0038: section cuts, animation and plots over steps.

The cut keeps the side behind its plane and gives the rest for a ghost; deformation
frames run 0 → 1 → 0; GIFs hold every frame. On the cantilever (CalculiX), the view
cuts at a plane, an animation frame shows the deformation scaled, a kept probe plots
over the steps, and the CLI and agents give the same series.
"""

import asyncio
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from solvers import available  # noqa: E402

from puzzle.post.cuts import Cut, clip, deformation_frames, save_gif  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def test_cut_plane_clip_frames_and_gif(tmp_path):
    import pyvista as pv
    from PIL import Image

    grid = pv.ImageData(dimensions=(11, 3, 3), spacing=(0.1, 0.1, 0.1)).cast_to_unstructured_grid()
    origin, normal = Cut("x", 0.25).plane(grid.bounds)
    assert origin[0] == pytest.approx(0.25) and list(normal) == [1, 0, 0]
    kept, away = clip(grid, Cut("x", 0.25))
    assert kept.bounds[1] == pytest.approx(0.25) and away.bounds[0] == pytest.approx(0.25)
    kept, _ = clip(grid, Cut("x", 0.25, flip=True))
    assert kept.bounds[0] == pytest.approx(0.25)  # the other side
    frames = deformation_frames(8)
    assert frames[0] == 0 and max(frames) == pytest.approx(1) and len(frames) == 8
    out = save_gif([np.full((4, 6, 3), 30 * k, np.uint8) for k in range(5)], tmp_path / "a.gif")
    assert Image.open(out).n_frames == 5


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_cut_animation_plots_cli_and_agents(tmp_path, capsys):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.gui.tree import ROLE
    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import ResultProbe, Study
    from puzzle.project import load_project, save_project

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    next(s for s in p.of_type(Study) if s.name == "Tip load").solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    try:
        for item in w.tree._items():
            if item.data(0, ROLE).kind == "object" and item.data(0, ROLE).label == "Tip load":
                w.tree.setCurrentItem(item)
        w.run_selected()
        while w.tasks:
            app.processEvents()
            time.sleep(0.02)

        w.viewport.set_cut(Cut("x", 0.5))
        assert w.viewport.shown["cut"] == {"axis": "x", "fraction": 0.5}
        full = w.viewport.shown["scale"]
        frames = w.animation_frames()
        assert len(frames) == 24 and frames[0] == ("deform", 0.0)  # one static step
        w.show_frame(("deform", 0.5))
        assert w.viewport.shown["scale"] == pytest.approx(full / 2)
        w.animate(False)
        assert w.viewport.shown["scale"] == pytest.approx(full)

        bar = w.results_bar
        bar.field.setCurrentIndex(bar.field.findData("displacement:y"))
        w.keep_probe(w.viewport.probe_undeformed(w.viewport.shown["min_at"]))
        (probe,) = w.session.project.of_type(ResultProbe)
        w.plots.probes.item(0).setCheckState(Qt.CheckState.Checked)
        ((name, points),) = w.plots.series
        assert name == probe.name and len(points) == 1
        tip = points[0][1]
        assert tip < 0 and w.plots.csv().splitlines()[0] == "probe,x,value"
    finally:
        w.close()

    assert main(["plot", str(root), probe.name]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "step,value" and float(out[1].split(",")[1]) == pytest.approx(tip, rel=1e-5)
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool("probe_history", {"name": probe.name}))
    (row,) = result.structured_content["result"]
    assert row["value"] == pytest.approx(tip, rel=1e-9)
