# 0038. Results tools: section cuts, animation, and plots over steps

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

Puzzle's results view shows one quantity at one step, deformed or not. Everything else needs ParaView export. Review item 12 notes what both reference tools have:

- **PrePoMax:** section views (a plane cut), animation by scale factor, by increments or harmonic (saved as .avi), and history outputs plotted and exported to CSV.
- **Salome-Meca:** gives all of ParaView (ParaVis) and line cuts.

Puzzle's queries already compute a quantity over a selection at every step (`results.query.history`, MCP `result_history`).

## Decision

1. **Section cuts.** A plane (by point and normal, a standard plane through the model's centre, or a picked face's plane) clips the shown result. The cut face is coloured by the result.
   - A slider moves the plane along its normal, and a toggle shows the cut part as ghosted.
   - Several cuts are allowed; each is listed with its own toggle.
   - It is drawn with VTK clip and cutter filters on the grid already built for the result.
2. **Animation.**
   - **Over the deformation scale** (0 → 1 → 0, for a static result) or **over the steps** (transient, increments, or a mode shape's phase).
   - Controls: play, pause and a frame slider in the results bar, with frames per second.
   - **Saved as** GIF or MP4 at the view's size: frames from the off-screen render, encoded with `imageio` (with its ffmpeg plugin for MP4), an optional dependency.
3. **Plots over steps.** A Plots dock draws:
   - a kept probe (ADR 0032) or a selection reduction (total reaction, mean temperature) against time, increment or frequency;
   - one quantity against another (load against deflection).
   - **Several series**, each with its own axis label and unit. Copied as CSV, saved as PNG.
   - **Drawn with QtCharts** (PySide6 Addons, LGPL). It will be checked in the pixi environment first; if it is missing, the material builder's `Curve` widget is extended instead.
4. **Everything has a command-line and MCP twin.** `puzzle show --cut`, `--animate out.gif`, `puzzle plot PROBE` and the matching MCP tools, so agents and scripts make the same pictures.

## Built (2026-10-23)

- **Section cuts** (`post/cuts.py` `Cut`, `clip`):
  - a plane across X, Y or Z at a fraction of the model's extent, placed on the undeformed model;
  - the kept side is coloured through by the result;
  - Flip keeps the other side, and Ghost shows the cut-away side faintly;
  - controlled from the view toolbar's "Section cut" with a slider.
  - **Not yet:** one cut at a time; several cuts, and a plane from a picked face.
- **Animation:**
  - Animate plays over the deformation (0 → full → 0 along a sine, 24 frames) for a single step, or over the steps when there are several;
  - Save GIF records one loop with Pillow;
  - MP4 waits for ffmpeg (imageio isn't in the environment).
- **Plots:**
  - a Plots tab beside Messages draws ticked kept probes (ADR 0032) against time, increment or frequency, or against another probe (load against deflection);
  - copied as CSV, saved as PNG;
  - drawn with the material builder's `Curve`, since QtCharts isn't in the pixi environment (its Qt library is missing);
  - `post/probes.history` gives the series.
- **Twins:**
  - `puzzle show --cut x=0.5 --animate out.gif`;
  - `puzzle plot PROJECT PROBE` (CSV);
  - MCP `probe_history`.
- **Not yet:** selection reductions over steps (a total reaction against time), which wait for probe reductions.

## Built: the legend (2026-10-25)

Asked by the maintainer: a custom range, size and placement, other colours, and Ansys's single colour outside the range.

- **`post/legend.py`:** `Legend` holds the look and is kept as preferences (Settings > Results), so it lasts between sessions:
  - the colour map (turbo, jet, viridis, plasma, cool to warm, greys, blues, reds) and whether it runs the other way;
  - smooth colours or bands (6, 9 as Ansys, 12, 16), labelled at each band edge on a vertical bar;
  - a colour for values above the top of a custom range and one for values below its bottom, chosen separately (the end colour, grey, black, white, magenta), shown as "above" and "below" swatches on the bar;
  - the bar's place (left or right vertical, top or bottom horizontal; a horizontal bar puts its numbers under it, five of them) and its size (bar and text together, small to largest).
- **Custom ranges belong to a result**, not the look: `Viewport.ranges` keeps one per result key for the session, so deformation and stress each keep their own; the others stay automatic.
- **GUI:** Legend… in the Results ribbon and menu opens a panel in the Properties dock; every change redraws at once; "From the result" fills the custom range with the result's extremes to start from; a top not above the bottom is refused in red.
- **CLI:** `puzzle show` draws the same legend from the preferences, and `--range bottom,top` sets a custom range; `puzzle settings` now takes numbers for numeric choices (bands 9, size 1.5).
- **Checks:** `tests/test_gui_legend.py`.

## Built: placed cut planes and saved sections (2026-10-30)

Asked by the maintainer: besides cuts across X, Y or Z, a custom plane from a face (picked live, or a selection set) with an offset, or a construction plane; and custom sections saved and recalled from a Sections panel tab in the side bar.

- **Placed planes:** the cut bar's plane list has Across X, Y and Z (with the slider), then Picked face, each face selection ("Face selection: Tip (Bar)") and each construction plane (ADR 0046). A placed plane shows an offset field in the display length unit, moving it along its normal.
  - `post/cuts.py` `face_plane(mesh, entities)`: through the faces' area-weighted centroid, along their area-weighted normal; a curved face whose normals cancel (round a cylinder) takes the plane that best fits its points. `face_at(mesh, point)` finds the face under a point.
  - **Picked face:** Pick face, then a double-click on the result; a face already picked in the mesh view is taken first.
  - `Cut.offset` and `Cut.source`; the view records the plane's origin and normal (`Viewport.shown["cut"]`).
- **Saved sections:** a `SectionCut` (kind `cut`) keeps how the cut is placed: across an axis at a fraction, a face selection, a construction plane (both re-placed each time, so they follow the geometry), or a picked face's plane as a fixed origin and normal (mesh faces aren't kept), plus the offset, flip and ghost. `cuts.from_saved` turns one back into a `Cut`.
  - **Sections panel** (`gui/sections_panel.py`, a tab beside Properties; Window → Sections, Ctrl+4): Save current cut ("Section n"), a click recalls one (opening the cut bar set to it), double-click or F2 renames, Delete; each a command, so they undo. The tooltip says where each one is ("along Tip (Bar), offset 50 mm").
- **Checks:** `tests/test_section_cut_planes.py`: the Tip face's plane and an offset cut 50 mm in, a cylinder's side, the bar's list and modes, saving, recalling, renaming, deleting and undo.

## Consequences

- Most result reading stays inside Puzzle; ParaView export remains for the rest.
- Animation and cuts multiply render work. Frames render off screen at about 10 ms each, so a 60-frame animation is about a second to build, and it is cached per result and setting.
- `imageio` and QtCharts availability on all three platforms is to be checked in the pixi environments.

## Alternatives considered

- Embed ParaView (like ParaVis): very powerful, but heavy, and against ADR 0003's single PySide6 window with off-screen VTK.
- Line cuts first: useful, but a plane cut answers more questions. Line plots come with the Plots dock as a probe along a line.
