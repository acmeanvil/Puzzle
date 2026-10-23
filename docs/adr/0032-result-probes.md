# 0032. Read results in the view: extreme markers, probes, and the range in Properties

- Status: Proposed
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

## Consequences

- One query path (`results.query`, `post.sample`) serves the view, kept probes and MCP, so they agree.
- Labels in the view must be drawn as Qt overlays or VTK 2D actors that render cleanly off screen. VTK's 2D text left a clipped copy along the bottom edge on Retina before; this needs checking.
- Kept probes add an object kind; they never store values, only what to evaluate.

## Alternatives considered

- Hover read-out: a pick per mouse move is too slow with off-screen rendering at about 10 ms a frame plus picking.
- Probes as view-only annotations: they would be lost between sessions and unusable by agents and plots.
