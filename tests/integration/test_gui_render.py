"""The 3D view on the real platform: rotate, zoom and pick with the mouse.

Runs only with PUZZLE_RENDER_TESTS=1 (it opens a window). A subprocess, because the
other GUI tests put Qt on its offscreen platform, where VTK cannot draw.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PUZZLE_RENDER_TESTS"), reason="set PUZZLE_RENDER_TESTS=1 to open windows"
)

SCRIPT = r"""
import json, shutil, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from puzzle.gui.window import MainWindow

root = Path(sys.argv[1])
app = QApplication([])
w = MainWindow(root); w.resize(1400, 900); w.show()
def pump(t):
    end = time.monotonic() + t
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.01)
pump(3)  # the geometry preview
view = w.viewport.view; cam = view.plotter.camera
LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
out = {"kind": w.viewport.shown.get("kind")}
start = np.array(cam.position); c = view.rect().center()
before = view.grab().toImage()
QTest.mousePress(view, LEFT, NONE, c)
for i in range(1, 11):
    QTest.mouseMove(view, c + QPoint(10 * i, 0)); pump(0.02)
QTest.mouseRelease(view, LEFT, NONE, c + QPoint(100, 0))
pump(0.3)
out["rotated"] = bool(not np.allclose(start, cam.position))
out["redrawn"] = bool(view.grab().toImage() != before)  # the picture, not just the camera
d0 = np.linalg.norm(np.subtract(cam.position, cam.focal_point))
wheel = QWheelEvent(QPointF(c), QPointF(view.mapToGlobal(c)), QPoint(0, 0), QPoint(0, 240),
    Qt.MouseButton.NoButton, NONE, Qt.ScrollPhase.NoScrollPhase, False)
app.sendEvent(view, wheel)
pump(0.2)
out["zoomed"] = bool(np.linalg.norm(np.subtract(cam.position, cam.focal_point)) < d0)
w.toggle_picking(True)
r = view.plotter.renderer
r.SetWorldPoint(*w.viewport.mesh.coords.mean(axis=0), 1.0); r.WorldToDisplay()
x, y, _ = r.GetDisplayPoint(); dpr = view.devicePixelRatioF()
QTest.mouseDClick(view, LEFT, NONE,
    QPoint(int(x / dpr), int(view.height() - y / dpr)))
pump(0.5)
out["picked"] = w.picked_names()
# The bottom rows of the view must be the background, not stale pixels.
img = view.grab().toImage(); h = img.height()
dark = sum(1 for xx in range(0, img.width(), 2) for yy in range(h - 8, h)
           if img.pixelColor(xx, yy).lightness() < 120)
out["dark_bottom_pixels"] = dark
print("RESULT", json.dumps(out))
w.close()
"""


def test_view_rotates_zooms_and_picks(tmp_path):
    import shutil

    root = tmp_path / "cantilever"
    shutil.copytree(
        Path(__file__).parents[2] / "examples" / "cantilever",
        root,
        ignore=shutil.ignore_patterns("results", ".puzzle"),
    )
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(root)], capture_output=True, text=True, env=env
    )
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-2000:]
    out = json.loads(line[len("RESULT ") :])
    assert out["kind"] == "preview"
    assert out["rotated"] and out["redrawn"] and out["zoomed"]
    assert len(out["picked"]) == 1 and out["picked"][0][0] == "Bar"
    assert out["dark_bottom_pixels"] == 0  # no stray marks along the bottom edge
