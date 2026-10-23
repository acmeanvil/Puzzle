# 0032. Read results in the view: extreme markers, probes, and the range in Properties

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

Today a result shows as a colour map with a colour bar; the status bar gives its range. You can't see where the maximum is, or read a value at a point, without exporting to ParaView. Agents can already query a point (`result_value_at`) and statistics (`result_stats`). The review (item 4) points to:

- **PrePoMax's Query:** node, element, surface or part values with min, max and sum, kept as annotations.
- **Ansys's probes,** which live in the tree and update with each solve.

## Decision

1. **Extreme markers.** Every shown result draws a marker at its maximum and minimum, labelled with the value in display units ("Max 121 MPa"). They can be switched off in the view toolbar (ADR 0031). They follow the step and the deformation.
2. **Probing.** In results, double-clicking the part reads the value at that point (`post.sample`, nodal results interpolated in the element). The value shows as a label in the view and as a row in a Probes list under the view: point, value, and all components. Shift adds more probes. Esc clears them. The list copies as CSV.
3. **Probes can be kept.** "Keep" turns a probe into a project object, `ResultProbe` (kind `probe`: a point or a selection, a quantity key, and a reduction of value, max, min, mean, sum or integral). Kept probes sit under Results in the tree. Each is evaluated for the current run of its study, and for derived results (ADR 0025). They give:
   - the numbers a report quotes;
   - history plots over steps (ADR 0038);
   - shoot-out metrics (ADR 0011), which are already this shape.
4. **Properties of a shown result** list its range, where each extreme is, the step, and the deformation scale. Today they show only the run's timing.

## Built (2026-10-23)

- **Extremes:**
  - every shown result marks its maximum and minimum with a dot and a label in display units ("Max 120.7 MPa");
  - the view toolbar switches them off;
  - Properties lists the run, the range, where each extreme is (undeformed, display length) and the deformation scale.
- **Probing:**
  - in results, a double-click reads the value there; Shift adds a probe, and Esc or Clear removes them;
  - `post/probes.py` `at_point` interpolates in the element with VTK's shape functions on the undeformed grid (a quadratic tet is read quadratically), then computes the listed result from the interpolated components;
  - on a deformed view, the picked point is taken back to the undeformed geometry first;
  - the Probes list under the view gives each probe's point and value and copies as CSV.
- **Labels:** drawn as Qt overlays placed by projecting each 3D point (`SceneView.screen_point`) after every frame, not as VTK 2D text, which left clipped copies on Retina before.
- **Kept probes:**
  - "Keep" makes a `ResultProbe` (kind `probe`: source study or derived result, quantity, undeformed point, step);
  - it sits under Results in the tree, takes its source's state, and selecting it shows its source's result with the probe marked and its current value;
  - the outline lists them, and MCP `probes` reads them all;
  - the test doubles the tip load and the kept probe doubles.
- **Selection reductions** (a probe over a selection: max, min, mean, sum) are not built yet. They come with the plots over steps (ADR 0038).

## Consequences

- One query path (`results.query`, `post.sample`) serves the view, kept probes and MCP, so they agree.
- Labels in the view must be drawn as Qt overlays or VTK 2D actors that render cleanly off screen. VTK's 2D text left a clipped copy along the bottom edge on Retina before; this needs checking.
- Kept probes add an object kind; they never store values, only what to evaluate.

## Alternatives considered

- Hover read-out: a pick per mouse move is too slow with off-screen rendering at about 10 ms a frame plus picking.
- Probes as view-only annotations: they would be lost between sessions and unusable by agents and plots.
