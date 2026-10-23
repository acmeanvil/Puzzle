# 0038. Results tools: section cuts, animation, and plots over steps

- Status: Proposed
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

## Consequences

- Most result reading stays inside Puzzle; ParaView export remains for the rest.
- Animation and cuts multiply render work. Frames render off screen at about 10 ms each, so a 60-frame animation is about a second to build, and it is cached per result and setting.
- `imageio` and QtCharts availability on all three platforms is to be checked in the pixi environments.

## Alternatives considered

- Embed ParaView (like ParaVis): very powerful, but heavy, and against ADR 0003's single PySide6 window with off-screen VTK.
- Line cuts first: useful, but a plane cut answers more questions. Line plots come with the Plots dock as a probe along a line.
