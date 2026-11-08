"""Open in ParaView (ADR 0070, level 1): the result shown, rebuilt in ParaView.

`write` puts three files in the project's `exports/paraview/<run>/`:

- `shown.vtu`: the step shown, undeformed, with the quantity shown under its readable
  name and display unit ("Equivalent stress [MPa]") and the displacement for the warp;
- `<run>.pvd` and its `.vtu`s: every step with every stored field (ADR 0043);
- `view.py`: a ParaView script that opens both and rebuilds the view: colours by the
  quantity with the legend's colour map, direction, range and bands; the warp and its
  scale; the section cut as a Clip on the same plane (ParaView's Clip keeps the side
  pyvista's does); the camera, orthographic or not.

`launch` starts ParaView on it, detached. `find` looks for ParaView in Puzzle's own
environment (the installers carry it, ADR 0070) and then on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Puzzle's colour maps (post/legend.COLORMAPS) as ParaView's presets.
PRESETS = {
    "turbo": "Turbo",
    "jet": "Jet",
    "viridis": "Viridis",
    "plasma": "Plasma",
    "coolwarm": "Cool to Warm",
    "Greys": "Grayscale",
    "Blues": "Blues",
    "Reds": "Reds",
}


@dataclass
class View:
    """What Puzzle's results view shows, in the terms ParaView's script needs."""

    array: str  # the quantity's array in shown.vtu
    points: bool = True  # on nodes (else on elements)
    title: str = ""
    clim: tuple[float, float] | None = None  # the colour range, display units
    colormap: str = "turbo"
    invert: bool = False
    bands: int = 0
    warp: str | None = None  # the displacement array, when deformed
    scale: float = 0.0
    camera: list | None = None  # position, focal point, view up
    parallel: bool = False
    parallel_scale: float | None = None
    cut: dict | None = field(default=None)  # {"origin": [...], "normal": [...]}
    step: str = ""  # its label, for the window title


def find() -> Path | None:
    """ParaView's GUI executable: Puzzle's environment first, then PATH."""
    prefix = Path(sys.prefix)
    names = ["paraview.exe", "paraview"] if os.name == "nt" else ["paraview"]
    for folder in (prefix / "Library" / "bin", prefix / "bin", prefix / "Scripts", prefix):
        for name in names:
            if (folder / name).is_file():
                return folder / name
    found = shutil.which("paraview")
    return Path(found) if found else None


def environment(exe: Path) -> dict[str, str] | None:
    """ParaView's environment activated, as solvers' are: on Windows its DLLs are found
    only with the environment's folders on PATH (exit 0xC0000135 otherwise)."""
    from puzzle.jobs.runtime import NativeRuntime

    return NativeRuntime().environment([str(exe)])


def why_not() -> str | None:
    return None if find() is not None else "ParaView isn't installed (the paraview package)"


def folder_for(project_root: Path, run_id: str) -> Path:
    return Path(project_root) / "exports" / "paraview" / run_id


def write(folder: Path, shown, view: View, mesh=None, results=None) -> Path:
    """Write shown.vtu (from `shown`, the undeformed grid the view coloured), the run's
    series when `mesh` and `results` are given, and view.py; returns view.py."""
    folder.mkdir(parents=True, exist_ok=True)
    keep = [view.array] + ([view.warp] if view.warp else [])
    grid = shown.copy(deep=False)
    for data in (grid.point_data, grid.cell_data):
        for name in list(data):
            if name not in keep:
                del data[name]
    grid.save(folder / "shown.vtu")
    series = None
    if mesh is not None and results is not None:
        from puzzle.post.vtk import export

        series = export(mesh, results, folder / "series.pvd")[0]
    script = folder / "view.py"
    data = asdict(view) | {"preset": PRESETS.get(view.colormap, "Turbo")}
    script.write_text(SCRIPT.format(view=json.dumps(data, indent=1),
                                    series=repr(series.name if series else None)),
                      encoding="utf-8")  # fmt: skip
    return script


def launch(script: Path) -> subprocess.Popen:
    """ParaView on `script`, detached: closing Puzzle leaves it open, and the reverse."""
    exe = find()
    if exe is None:
        raise FileNotFoundError(why_not())
    argv = [str(exe), f"--script={script}"]
    kwargs: dict = {"cwd": str(script.parent), "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "env": environment(exe)}  # fmt: skip
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


# Run by ParaView (paraview --script, or pvpython): Puzzle's view, rebuilt. It imports
# only ParaView, so it works with any ParaView 5.11 or later.
SCRIPT = '''"""Written by Puzzle (Open in ParaView, ADR 0070): the result as Puzzle showed it."""
import json
import os

from paraview.simple import *  # noqa: F403

VIEW = json.loads(r"""{view}""")
SERIES = {series}
here = os.path.dirname(os.path.abspath(__file__))

shown = XMLUnstructuredGridReader(registrationName="Shown in Puzzle",
                                  FileName=[os.path.join(here, "shown.vtu")])
if SERIES:  # every step and field, for going further (hidden at first)
    XMLCollectionReader(registrationName="All steps", FileName=[os.path.join(here, SERIES)])
last = shown
if VIEW["warp"] and VIEW["scale"]:
    last = WarpByVector(registrationName="Deformed", Input=last, Vectors=["POINTS", VIEW["warp"]],
                        ScaleFactor=VIEW["scale"])
if VIEW["cut"]:
    last = Clip(registrationName="Section cut", Input=last, ClipType="Plane")
    last.ClipType.Origin = VIEW["cut"]["origin"]
    last.ClipType.Normal = VIEW["cut"]["normal"]
    last.Invert = 1  # the side Puzzle keeps
    if "Hide3DWidgets" in globals():  # the GUI's, not pvbatch's
        Hide3DWidgets(proxy=last.ClipType)

view = GetActiveViewOrCreate("RenderView")
display = Show(last, view)
where = "POINTS" if VIEW["points"] else "CELLS"
ColorBy(display, (where, VIEW["array"]))
lut = GetColorTransferFunction(VIEW["array"])
try:
    lut.ApplyPreset(VIEW["preset"], True)
except RuntimeError:  # an older ParaView names its presets differently
    lut.ApplyPreset("Turbo", True)
if VIEW["invert"]:
    lut.InvertTransferFunction()
if VIEW["clim"]:
    lut.RescaleTransferFunction(*VIEW["clim"])
    lut.AutomaticRescaleRangeMode = "Never"
if VIEW["bands"]:
    lut.Discretize = 1
    lut.NumberOfTableValues = VIEW["bands"]
display.SetScalarBarVisibility(view, True)
bar = GetScalarBar(lut, view)
bar.Title = VIEW["title"] or VIEW["array"]
bar.ComponentTitle = ""
display.SetRepresentationType("Surface With Edges")

Render(view)  # the first render fits the camera to the data; Puzzle's camera goes after
view.CameraParallelProjection = 1 if VIEW["parallel"] else 0
if VIEW["camera"]:
    view.CameraPosition, view.CameraFocalPoint, view.CameraViewUp = VIEW["camera"]
    if VIEW["parallel"] and VIEW["parallel_scale"]:
        view.CameraParallelScale = VIEW["parallel_scale"]
else:
    ResetCamera(view)
Render(view)
'''
