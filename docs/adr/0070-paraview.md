# 0070. ParaView beside Puzzle, and its filters inside

- Status: Accepted (2026-11-07)
- Date: 2026-11-07

## Context

ParaView (BSD-3, Kitware) is the open post-processor engineers reach for when a
pre/post tool's own results views run out: streamlines, volume rendering, contours of
any array, its calculators, spreadsheet and charts, animation, state files, client-server
rendering on a cluster. Puzzle exports ParaView files already (ADR 0043: `.pvd` and
`.vtu`), but the trip is by hand and the view the user built in Puzzle (field, step,
colours, deformation, cut, camera) is lost on the way.

ParaView has two halves:

- **its engine**: the server manager, its filters and readers, its rendering, all
  scriptable from Python (`paraview.simple`);
- **its GUI**: the pipeline browser, properties panel, colour-map editor, spreadsheet,
  chart and animation views, the Python shell. These are C++ Qt widgets (`pq*` classes)
  with no Python bindings.

Measured on macOS Intel (2026-11-06), conda-forge's packages:

- ParaView 6.2 is built against conda-forge's shared `vtk-base` 9.7 and `qt6-main` 6.11
  (it no longer carries its own copies), so ParaView, PySide6 and pyvista install into
  one environment and import into one process (Qt 6.11.3, VTK 9.7.1).
- A grid made by pyvista handed to ParaView in memory (a `TrivialProducer`, no files),
  sliced and integrated (`Slice`, `IntegrateVariables`) and fetched back as pyvista:
  the slice's area exact, 68 ms. A ParaView render view rendered off screen to an image:
  1.4 s for the first frame, which includes setting rendering up.
- The package is about 30–40 MB per platform; most of what it needs (VTK, Qt, NumPy) the
  installers carry already.

## Decision

1. **Level 1, ParaView beside Puzzle: Open in ParaView.** From a results view (the
   Results ribbon and menu, a run's right-click, `puzzle paraview`), Puzzle writes into
   the project's `exports/paraview/<run>/`:
   - the shown step as a `.vtu` holding the shown quantity under its readable name and
     display unit (`Equivalent stress [MPa]`), with the displacement for the warp;
   - the whole run as a `.pvd` series (every step, every stored field, as ADR 0043);
   - `view.py`, a ParaView Python script that opens both and rebuilds Puzzle's view:
     colouring by the shown quantity with the legend's colour map, range and bands, the
     deformation and its scale, the section cut (a Slice or Clip on the same plane), the
     camera (position, focal point, up, orthographic or not).

   and starts ParaView (`paraview --script=view.py`) detached, so closing either leaves
   the other. ParaView then has everything it has: filters, spreadsheet, charts,
   animation, Python shell, state files.
2. **ParaView ships in the installers, in Puzzle's own environment.** `paraview` joins
   the GUI feature in `pixi.toml` and `puzzle-fem`'s requirements, sharing Puzzle's VTK
   and Qt (one copy of each, and what level 2 needs). Its menu entry stays Puzzle's
   (no second Start-menu item); `post/paraview_link.find()` finds the executable in the
   environment (`bin/paraview`, `Library/bin/paraview.exe`), then on PATH.
3. **Level 2, ParaView's filters inside Puzzle** (a later step of this ADR):
   ParaView's filters as Puzzle post-processing objects, evaluated on demand and never
   stored (as derived results, ADR 0025): a stored recipe (filter, its settings, its input
   result) whose form is generated from the filter's own property descriptions, shown in
   Puzzle's view (fetched back as pyvista, so the legend, probes, picks, symbols and the
   3D mouse work on it) and reachable by commands, undo, the agent (MCP) and the schematic.
   First the filters a structural or thermal user needs: Slice, Clip, Contour,
   Threshold, Plot over line, Integrate variables, Stream tracer, Calculator. Python
   Calculator and Programmable Filter run code, so they are gated by trust (ADR 0062).
4. **Level 3, ParaView's GUI inside Puzzle, is not done.** It would need C++ bindings of
   ParaView's `pq*` widgets kept up per release on five platforms; ParaView keeps its own
   undo and state outside Puzzle's command layer, shows raw array names and SI only, has
   its own look, and the agent can't see it. Level 1 gives all of its GUI, in its own
   window.

## Plan

1. `post/paraview_link.py`: `find()`, `write_view(project, run, view)` (the files above),
   `open(...)`; the GUI's Open in ParaView (ribbon, menu, run menu), `puzzle paraview
   PROJECT --study NAME [--field F] [--step N]`; tests that run the written script in
   `pvpython` where ParaView is installed (array name, range, warp scale, cut, camera).
2. `paraview` in pixi.toml's GUI feature, the recipe, the lock and the installers; the
   smoke test runs `pvpython` on a written `view.py` off screen.
3. Level 2 as its own steps, starting with Slice and Plot over line.

## Consequences

- Users get all of ParaView from where they are, with their view, for little code.
- One VTK and one Qt for Puzzle and ParaView: their versions move together, and a
  ParaView release that needs a newer Qt or VTK moves Puzzle's lock too.
- Level 2 makes ParaView's filters part of the model's results, so they are saved, rerun
  on new results and usable by the agent; in-process filtering can double the memory a
  large result takes (ParaView's client-server mode is the answer there, later).
- Two windows for level 1; the way back to Puzzle is by hand.

## Alternatives considered

- **ParaView's GUI embedded (level 3).** See decision 4.
- **ParaView in an environment of its own.** No version coupling, but two copies of VTK
  and Qt in the installer, and level 2 impossible in-process.
- **trame (ParaView's web framework) in a Puzzle panel.** More of ParaView's interface
  inside Puzzle's window, but a web page in a separate process with its own look; kept
  as an option if level 1 proves too far away.
- **Only exporting files (today).** Leaves the view behind and the trip by hand.

## Built so far

**Level 1 (2026-11-07): Open in ParaView.**

- `post/paraview_link.py`: `find` (Puzzle's environment, then PATH), `write` (shown.vtu
  with the shown quantity and the warp only, the run's `series.pvd`, `view.py`),
  `launch` (detached). `view.py` colours by the quantity with the legend's colour map
  as ParaView's preset (`PRESETS`; ParaView 6.2 names Viridis and Plasma without
  "(matplotlib)"), direction, range and bands, warps by the view's scale, clips on the
  cut's plane with Invert on (the side pyvista keeps), and sets the camera after a first
  render (which otherwise fits the camera to the data again).
- `gui/paraview_open.py`: `view_of(viewport)` and `open_in_paraview(window)`, from the
  Results ribbon (Show → Open in ParaView) and Results → Open in ParaView.
- `tests/test_paraview_link.py`: the files; the view read from a results view; and
  `view.py` run in `pvbatch`, checking the coloured array, range, bands, warp scale, cut
  origin and side, orthographic scale and camera position.
- Packaging: `paraview >=6.2` in the GUI feature and `puzzle-fem`'s requirements; the
  smoke test writes a view with the installed Puzzle and runs it in the installed
  `pvbatch`.

Found:

- **macOS 14 or later** (the maintainer's decision, 2026-11-07): ParaView 6.2 needs Qt
  6.11.2+, which conda-forge builds for macOS 14+ only. pixi declares it on the
  platform entries (`osx-64-macos14`, `osx-arm64-macos14`, its `[system-requirements]`
  being deprecated), so the lock names those platforms so, and `make_installer` reads
  each lock platform's `subdir`.
- **VTK's build matters, not only its version.** ParaView 6.2's VTK 9.7.0 builds link
  VTK's own CGNS module (`libvtkcgns`), which vtk-base 9.7.0 has in its build 0 and not
  in build 1, so pvbatch failed to load with build 1. OCP holds VTK at 9.7.0, so
  `vtk-base ==9.7.0 *_0` is pinned (pixi.toml and the recipe) until both move on.
- Not yet: `puzzle paraview` on the command line (it needs the results view's
  quantities computed outside the GUI), and level 2.
