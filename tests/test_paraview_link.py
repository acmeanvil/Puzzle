"""Open in ParaView (ADR 0070): the written script rebuilds Puzzle's view in ParaView."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from puzzle.post import paraview_link
from puzzle.post.paraview_link import View


def _pvbatch() -> str | None:
    here = Path(sys.prefix)
    for p in (here / "bin" / "pvbatch", here / "Library" / "bin" / "pvbatch.exe"):
        if p.is_file():
            return str(p)
    return shutil.which("pvbatch")


def _beam():
    import pyvista as pv

    grid = pv.ImageData(dimensions=(21, 4, 4), spacing=(0.01, 0.002, 0.002))
    grid = grid.cast_to_unstructured_grid()
    x = grid.points[:, 0]
    grid.point_data["Equivalent stress [MPa]"] = 100 * (0.2 - x) / 0.2
    u = np.zeros((grid.n_points, 3))
    u[:, 1] = -0.001 * x**2
    grid.point_data["displacement"] = u
    grid.point_data["temperature"] = x  # not shown: left out of shown.vtu
    return grid


VIEW = View(
    array="Equivalent stress [MPa]", title="Equivalent stress (MPa)", clim=(10.0, 90.0),
    colormap="viridis", bands=9, warp="displacement", scale=50.0,
    camera=[[0.1, -0.3, 0.2], [0.1, 0.003, 0.003], [0.0, 0.0, 1.0]], parallel=True,
    parallel_scale=0.06, cut={"origin": [0.1, 0.003, 0.003], "normal": [1.0, 0.0, 0.0]},
)  # fmt: skip


def test_the_files_hold_what_was_shown(tmp_path):
    import pyvista as pv

    script = paraview_link.write(tmp_path, _beam(), VIEW)
    shown = pv.read(tmp_path / "shown.vtu")
    assert set(shown.point_data) == {"Equivalent stress [MPa]", "displacement"}
    text = script.read_text(encoding="utf-8")
    assert '"preset": "Viridis"' in text and "shown.vtu" in text
    assert paraview_link.folder_for(Path("p"), "abc") == Path("p/exports/paraview/abc")


CHECK = r"""
import json, runpy, sys
runpy.run_path(sys.argv[1])
from paraview.simple import *
view = GetActiveView()
src = GetActiveSource() or FindSource("Section cut")
cut = FindSource("Section cut"); warp = FindSource("Deformed")
lut = GetColorTransferFunction("Equivalent stress [MPa]")
rgb = lut.RGBPoints
out = {
    "colour": GetDisplayProperties(cut, view).ColorArrayName[1],
    "range": [rgb[0], rgb[-4]], "bands": lut.NumberOfTableValues, "discrete": lut.Discretize,
    "scale": warp.ScaleFactor, "origin": list(cut.ClipType.Origin), "invert": cut.Invert,
    "parallel": view.CameraParallelProjection, "pscale": view.CameraParallelScale,
    "position": list(view.CameraPosition),
    "cells": cut.GetDataInformation().GetNumberOfCells(),
    "whole": FindSource("Shown in Puzzle").GetDataInformation().GetNumberOfCells(),
}
print("CHECK " + json.dumps(out))
"""


@pytest.mark.skipif(_pvbatch() is None, reason="ParaView isn't installed here")
def test_paraview_rebuilds_the_view(tmp_path):
    script = paraview_link.write(tmp_path, _beam(), VIEW)
    check = tmp_path / "check.py"
    check.write_text(CHECK, encoding="utf-8")
    done = subprocess.run([_pvbatch(), str(check), str(script)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)  # fmt: skip
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("CHECK ")), None)
    assert line, done.stdout[-2000:] + done.stderr[-2000:]
    got = json.loads(line[6:])
    assert got["colour"] == "Equivalent stress [MPa]"
    assert got["range"] == pytest.approx([10.0, 90.0])
    assert got["bands"] == 9 and got["discrete"] == 1
    assert got["scale"] == pytest.approx(50.0)
    assert got["origin"] == pytest.approx([0.1, 0.003, 0.003]) and got["invert"] == 1
    assert got["parallel"] == 1 and got["pscale"] == pytest.approx(0.06)
    assert got["position"] == pytest.approx([0.1, -0.3, 0.2])
    assert 0 < got["cells"] < got["whole"]  # cut through the middle: about half kept


def test_the_results_view_is_read_for_paraview():
    from types import SimpleNamespace

    from puzzle.gui.paraview_open import view_of
    from puzzle.post.cuts import Cut
    from puzzle.post.legend import Legend

    base = _beam()
    base.point_data["Equivalent stress"] = base.point_data["Equivalent stress [MPa]"]
    step = SimpleNamespace(label=lambda: "step 1")
    viewport = SimpleNamespace(
        result_state={"base": base, "name": "Equivalent stress", "unit": "MPa", "scale": 40.0,
                      "step": step},
        legend=Legend(colormap="coolwarm", invert=True, bands=6),
        shown={"min": 0.0, "max": 100.0, "clim": (5.0, 95.0)},
        cut=Cut(axis="x", fraction=0.5),
        camera=lambda: [(0, 0, 1), (0, 0, 0), (0, 1, 0)],
        style={"parallel": False},
        parallel_scale=lambda: None,
    )  # fmt: skip
    shown, view = view_of(viewport)
    assert view.array == "Equivalent stress [MPa]" and view.title == "Equivalent stress (MPa)"
    assert "Equivalent stress [MPa]" in shown.point_data and view.points
    assert view.clim == (5.0, 95.0) and view.colormap == "coolwarm" and view.invert
    assert view.bands == 6 and view.warp == "displacement" and view.scale == 40.0
    assert view.cut["normal"] == [1.0, 0.0, 0.0] and view.step == "step 1"
    assert view_of(SimpleNamespace(result_state=None)) is None
