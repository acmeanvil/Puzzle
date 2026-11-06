---
name: meshing
description: making or changing a mesh recipe: global size, element order and shape, Gmsh or Netgen, local size controls, boundary layers, Gmsh options, mesh quality
adr: 0019
---
Read this before creating or editing a mesh recipe, when meshing fails, or when the
user asks about mesh quality or finer elements somewhere. To judge whether a mesh is
fine enough for a result, read the `mesh-convergence` skill instead.

A mesh recipe (kind `mesh`) says how a component is meshed. A component points at one
recipe by its id (`Component.mesh`); several components may share a recipe, so an edit
changes all of them. Meshes are cached by content: any change to a recipe means a new
mesh and new runs.

Recipe fields:

- `mesher`: "gmsh" (default) or "netgen";
- `element_order`: 1 or 2 (default 2);
- `element_shape`: "tetrahedra" (default) or "hexahedra" (structured, block-like
  bodies only: six faces of four edges; first order only; Gmsh only);
- `global_size`: a quantity, the largest element size. Without it Netgen's largest
  size is a tenth of the model; Gmsh sets no largest size and sizes from the
  geometry (its points and curvature); a refine-near control grows back to a tenth
  of the model away from its selection, and a size field is capped there;
- `curvature_points` (default 12 per 2π; 0 ignores curvature), `algorithm_2d`,
  `algorithm_3d`, `optimiser` (gmsh, netgen, off), `high_order_optimise`,
  `smoothing`;
- `quality_floor`: the smallest shape quality allowed (0..1); below it meshing fails,
  saying where;
- `controls`: a list, applied in order;
- `gmsh_options`: any other Gmsh option by its own name, `{"Mesh.AnisoMax": 10}`;
  `netgen_options`: grading, curvaturesafety, segmentsperedge, quad_dominated.

## Steps

1. `outline(ids=true)`: the component, its recipe (with its id), and its selections
   (`Part/Selection`).
2. A new recipe, then point the component at it:
   `{"command": "create", "kind": "mesh", "data": {"name": "Tets 2 mm",
   "global_size": {"value": 2, "unit": "mm"}, "element_order": 2}}`, then
   `{"command": "edit", "object": "<component>", "ops": [{"op": "set", "path": "mesh",
   "value": "<recipe id>"}]}`.
3. Change a setting: `{"command": "edit", "object": "<recipe>", "ops": [{"op": "set",
   "path": "global_size", "value": {"value": 1.5, "unit": "mm"}}]}`.
4. Add a control with `append` on `controls`. Each has a `kind`:
   - size on a selection's faces, edges or bodies:
     `{"kind": "size", "selection": "Bracket/Hole", "size": {"value": 0.5, "unit": "mm"}}`;
   - refine near a selection, growing back to the global size:
     `{"kind": "refine_near", "selection": "Bracket/Fillet", "size": {"value": 0.3,
     "unit": "mm"}, "near": {"value": 1, "unit": "mm"}, "far": {"value": 5, "unit":
     "mm"}}`;
   - a count along edges: `{"kind": "transfinite", "selection": "Bar/Edges", "count":
     20, "progression": 1.0}`;
   - a box: `{"kind": "box", "name": "Root", "low": [0, 0, 0], "high": [0.01, 0.02,
     0.02], "size": {"value": 0.5, "unit": "mm"}}` (corners in m; `axes` a Cartesian
     system's id to turn it);
   - a ball: `{"kind": "ball", "name": "Load point", "centre": [0.1, 0, 0], "radius":
     {"value": 5, "unit": "mm"}, "size": {"value": 0.5, "unit": "mm"}}`;
   - a boundary layer (Netgen only): `{"kind": "boundary_layer", "selection":
     "Pipe/Wall", "first_height": {"value": 0.1, "unit": "mm"}, "growth": 1.2,
     "layers": 5}`;
   - sizes from data: `{"kind": "size_field", "run": "<run id>", "target": 0.02}` (a
     run's error estimate) or `"table": "<operator id>"`; see `mesh-convergence`.

   Example: `{"command": "edit", "object": "<recipe>", "ops": [{"op": "append", "path":
   "controls", "value": {"kind": "size", "selection": "Bracket/Hole", "size": {"value":
   0.5, "unit": "mm"}}}]}`. Remove one with `{"op": "remove", "path": "controls.2"}`.
5. `mesh_quality(component)`: meshes with the recipe and returns `nodes`, `elements`,
   `element_types`, `measures` (shape quality and scaled Jacobian 0..1, 1 ideal; gamma;
   edge aspect ratio from 1; each with min, mean, histogram), the five `worst`
   elements with their centres in m, and `warnings`. A mesh that fails says why.
6. Optional: `mesh_shootout(study, quantities, tolerance)` solves the study on a Gmsh
   and a Netgen mesh of the recipe and compares peak values (default "displacement",
   tolerance 0.02). Slow: two solves.

## Units

Sizes, heights, near and far are quantities, `{"value": 2, "unit": "mm"}`. Box corners
and ball centres are plain numbers in metres. `growth`, `progression`, `count`,
`layers`, `quality_floor` and `target` have no unit.

## Refused, never skipped

- A control naming a selection the component doesn't have.
- Two size controls giving one entity different sizes (both are named).
- A transfinite count on anything but edges.
- A Gmsh option name Gmsh doesn't have.
- Boundary layers on Gmsh (set `mesher` to "netgen"); refine near, transfinite, box,
  ball and hexahedra on Netgen.
- Second-order hexahedra.

## Mistakes to avoid

- First-order tetrahedra for bending or stress: they lock and read low. Keep
  `element_order` 2 for structures.
- Refining everything with the global size when one fillet or hole matters: use a
  size or refine-near control there.
- Judging stress at a sharp corner: it never converges; read the `mesh-convergence`
  skill.
- Calling `mesh_quality` after every small edit on a large model: each call meshes.
- Editing a recipe that another component shares without saying so.
- The Mesh tab in the GUI shows edge seeds, each body's method and the elements
  coloured by quality; there is no MCP tool for seeds.
