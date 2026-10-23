# 0018. A study's materials are a table: one material, or several run side by side

- Status: Accepted
- Date: 2026-10-22

## Context

The maintainer asked for two ways of using materials (2026-10-21, 2026-10-22). A study may keep one material where it is assigned, as now. Or it may give a location (a component's material assignment, e.g. "Bar/Body") several candidate materials. A study with several runs once per material, identically otherwise. Results then let the user pick which material's result to show, or "Compare" them.

The schema already has parametric sweeps (`SweepStep`: parameters with values, combined on a grid or paired), but `run_study` refused them: sweeps were never run. Copying a component per material (CopyComponent) stays possible, but it duplicates studies and splits the comparison.

## Decision

1. **A material choice is a sweep parameter.** `SweepParameter` gains `location`, a component material assignment's selection. Its `materials` are the project materials to try there, used in place of `values`; a plain parameter keeps `values`. A study's sweep step may mix the two, so "three materials × two thicknesses" is a grid, and `combination` (grid or paired) applies as for any sweep.
2. **Sweeps run.** `run_study` expands a study with sweep steps into one run per point. Each run uses a copy of the project with that point's parameter values set and its materials assigned, and is otherwise the same study. Each run is a normal run: cached by its run key (the key covers the point), stored, and reusable. Its record carries the point (`RunRecord.point`, e.g. `{"Bar/Body": "Copper", "t": "12 mm"}`). The runs table gains a nullable `point` column by schema merge; older stores read it as empty.
3. **The GUI's material table.** A study's properties show its locations, each with "the assigned material" or a list of candidates to tick (from the project's materials; Add material reaches the library). That writes the sweep step: one command, undoable. A study with a sweep shows "Runs 3 materials" in the tree.
4. **Results by point, and Compare.** A swept study's results appear under one heading in the tree, one entry per point ("Copper", "Steel", or "Copper, t = 12 mm"). Choosing one shows it like any run. "Compare" opens:
   - **Side-by-side views:** the same result for every point, in a grid of views with one colour scale and linked cameras.
   - **Difference from a baseline:** pick one point; each other point's result minus it (or divided by it) on the mesh. This needs the same mesh, which holds when only materials or non-geometric parameters change; otherwise the result is mapped (`transfer.map_field`).
   - **Summary:** the chosen result's maximum and minimum per point, and where they occur, as a table and a bar chart.

## Consequences

- Material studies and parametric studies are one mechanism, and combine.
- A sweep multiplies run time by its points. The GUI says how many runs a study will make before it starts, and the agent's autonomy budget counts each point as a run.
- Mesh reuse matters for Difference: a materials-only sweep keeps one mesh, cached by content.

## Alternatives considered

- A separate "material table" object outside sweeps: a second mechanism for the same thing, with no combination with parameters.
- Copying the study per material: simple, but scatters the results and makes Compare a cross-study search.
