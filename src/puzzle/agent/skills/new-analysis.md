---
name: new-analysis
description: a first analysis of a geometry in one change (mesh recipe, component, material, physics, study), then its support and load
adr: 0090
---
Read this when the user has a geometry and wants it analysed ("is this bracket strong
enough?", "what are its natural frequencies?") and the project has no component for it
yet, or when the Advisor's Component step is needed.

## Steps

1. `outline()`: the geometries, and which no component uses yet.
2. `analysis_new(geometry, analysis, material, mesh)`: one batch, one undo.
   - `geometry`: its name (a list makes one component with a part each).
   - `analysis`: `static` (static structural), `modal`, `buckling` or `steady_heat`.
   - `material`: a project material's name, or a library key or name
     (`structural_steel` by default); it goes on every body.
   - `mesh`: `coarse`, `medium` (default) or `fine`: the parts' bounding-box diagonal
     over 10, 20 or 40 elements, rounded down to 1, 2 or 5 times a power of ten.
   - `solver`: leave it out for the first one that runs the analysis (CalculiX for all
     four); `name` the component's, the geometry's by default.
3. The study now reports `unconstrained` (static, buckling) or needs a temperature
   (steady heat): add the support and the load with `submit` edits appending features
   to the component's physics (skill `structural-loads`), on face selections. Name the
   faces first if they are not (`selections_check`, or the user picks them).
4. `next_steps()` says what is left; then run the study:
   `submit(command={"command": "run_study", "study": "<its name>"})`.

## Mistakes

- A geometry with no body selection is refused: an import always has `Body`.
- Calling it twice makes a second component and study, named apart: check `outline()`
  first; edit the existing ones instead.
- Values are SI in the commands; the mesh size is written in mm, as the GUI shows it.
