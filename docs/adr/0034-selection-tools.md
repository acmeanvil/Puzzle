# 0034. Selection tools: by tangency and angle, by box, by likeness, with modifier keys

- Status: Proposed
- Date: 2026-10-23

## Context

Picking today:

- **Today's flow:** Pick, then a filter (faces, edges, bodies, points), then double-click. Each double-click adds the entity under the mouse.
- **Durable names:** selections are named entities that survive remeshing and geometry edits (G3).

PrePoMax's Set Selection window (review item 6) adds:

- selection by edge angle or surface angle (propagating across smooth edges);
- box selection;
- by part, or by ID;
- Shift to add and Ctrl to remove;
- undo within the selection.

Ansys adds "extend to tangent", "extend to limits" and "select all faces of the same size" (by radius), which is how bolt holes are picked in bulk. Beam fasteners (ADR 0027) and contact search (ADR 0039) both need picking many similar faces.

## Decision

1. **Modifier keys.**
   - In the Pick context, a click replaces the selection, Shift-click adds and Ctrl-click (⌘ on macOS) removes.
   - A single click picks; double-click is kept for "pick and finish".
   - Esc clears.
   - An undo stack within the pick session (⌘Z while picking undoes the last pick, not the project).
2. **Extend.**
   - **Tangent:** "Extend to tangent" (faces) adds neighbours across edges whose dihedral angle is below a set angle (default 20°), repeatedly.
   - **Edge chains:** "Extend to chain" (edges) follows edges meeting at small angles.
   - **Adjacency** comes from the geometry's own names: an edge named `A|B` joins faces A and B (G3). The angle at it comes from the faces' normals sampled at the edge, so no mesh is needed.
3. **Box select.** Drag with the box modifier (B, or the pick bar's Box button). It picks entities whose visible part lies inside the box, from the off-screen render's ids: an id pass with VTK's hardware selector, falling back to projecting entity centres.
4. **Select alike.** "Same as this" picks every face of the same kind and size as the picked one: same surface type, and radius or area within a tolerance. An example is all M10 holes. "By size" filters faces by area or radius. A "Part" filter picks whole bodies.
5. **Rules, not lists, when it makes sense.** "Same as" and "by size" can be saved as a selection rule (G3's `rule`), so the selection is recomputed on every regeneration. A new hole of that size then joins it.

## Consequences

- Picking many holes or a fillet chain becomes one action instead of dozens.
- Rules make selections robust to design changes, but they can grow unexpectedly. The selection shows its rule and its current count, and the reattach dialog reports changes in count.
- The hardware selector must be checked off screen on macOS (Intel Iris) before box select relies on it.

## Alternatives considered

- A separate selection window (PrePoMax): it puts the tools out of the view. A pick bar over the view keeps the user's eyes on the model.
- Mesh-based selection (by node or element id): it doesn't survive remeshing, and Puzzle's selections are geometric by design (ADR 0004).
