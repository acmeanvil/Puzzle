# 0019. Mesh in a workbench with the full breadth of Gmsh and Netgen

- Status: Accepted; built (2026-10-24, completed 2026-10-25) but for sweeping and mixed hex-tet meshes
- Date: 2026-10-22

## Context

A `MeshRecipe` today exposes a handful of settings: the mesher, element order and shape, a global size, and local sizes per selection. Behind it, Gmsh offers far more: 2D and 3D algorithms, size fields, boundary layers, transfinite and recombined meshes, optimisers and quality measures. Netgen is listed as a mesher (`mesher = "netgen"`) but not implemented. Users coming from Ansys and COMSOL expect mesh controls that sit on named selections, a mesh preview, and a quality report that points at the bad elements. ADR 0017's Geometry tab sets the interaction pattern: tools in a ribbon, options inline, a live preview, picks in the view.

Netgen 6.2 is on conda-forge for every platform Puzzle supports (`netgen`, LGPL-2.1), with Python bindings that mesh OCC geometry directly (`netgen.occ`). It is also one of Gmsh's optimisers.

## Decision (proposed)

1. **A Mesh tab**, beside Model and Geometry, for one mesh recipe and the component it meshes.
   - The recipe's controls are listed in order, like a geometry history.
   - Ribbon groups:
     - Sizing: global, local per selection, from curvature, growth;
     - Method: algorithm, tetrahedra, hexahedra (transfinite), recombine, sweep;
     - Inflation: boundary layers on selections;
     - Quality: optimise, check;
     - Mesher: Gmsh or Netgen.
   - The view shows the mesh (or a coarse preview), with elements coloured by quality.
2. **Controls attach to named selections**, so they survive geometry edits (ADR 0014 G3).
   - Each control is typed and validated, with readable names and units:
     - size on faces, edges or bodies;
     - distance-threshold refinement around a selection;
     - boundary layers (first height, growth ratio, layer count);
     - transfinite counts on edges;
     - box and ball refinement regions.
   - New schema: `MeshRecipe.controls: list[MeshControl]`, a discriminated union in the style of `HistoryStep`.
3. **Gmsh's full breadth: typed where it matters, pass-through for the rest.**
   - The common options are typed fields: 2D and 3D algorithm, curvature points, optimiser, high-order optimisation, smoothing.
   - `MeshRecipe.gmsh_options` takes any other Gmsh option by its own name (`Mesh.AnisoMax`), validated against Gmsh's option list (an unknown name is refused, never ignored).
   - The GUI lists these advanced options, searchable, with Gmsh's own descriptions.
4. **Netgen is the second mesher.**
   - Shared options: element order and global size.
   - Netgen's own options: maxh, grading, curvature safety, segments per edge, local h on selections, quad-dominant surfaces, boundary-layer prisms.
   - Netgen meshes the same BREP the history writes. Selections map to its faces by the same signature matching as Gmsh (`naming.resolve_geometry`), and the result becomes the same solver-neutral `Mesh`.
   - A mesh shoot-out (the same study meshed by both) follows from this.
5. **Quality is reported, not just computed.**
   - Measures: scaled Jacobian, gamma and aspect ratio, as a histogram.
   - The worst elements are listed and highlighted in the view, with a jump-to action.
   - A recipe may set a floor below which meshing fails with a readable reason.

## Consequences

- Recipes become richer, and the run key changes with any control: meshes stay cached by content.
- Pass-through options trade typing for completeness. Validating names against Gmsh's option list keeps typos from passing silently.
- Netgen adds a dependency of about 50 MB; it could be an optional pixi feature for installs that don't need it.
- Agents get the same controls through commands. An MCP `mesh_quality` tool reports the histogram and the worst elements.

## Decided (2026-10-22, maintainer)

- **Element types.** By default each solver's preferred element types drive the defaults (CalculiX C3D10, Code_Aster TETRA10, Elmer 504/510). An application setting (ADR 0024) can turn this off. Either way, mesh setup offers each study's solvers' appropriate element options, those available for the study at hand.
- **Preview.** An application setting chooses between previewing on every control change (debounced) and previewing on demand.
- **Netgen as Gmsh's optimiser.** In the first version, Netgen is offered as Gmsh's optimiser, with an application setting to turn it on or off.

## Built (2026-10-24)

- **Controls** (`MeshRecipe.controls`, a discriminated union on `kind`), each on a named selection or a region, with `describe()` for lists and the outline:
  - `size` (Gmsh `Constant` field over the selection, combined by `Min`);
  - `refine_near` (`Distance` + `Threshold`: the size within `near`, back to the global size by `far`);
  - `transfinite` (a count of elements along edges, optionally graded);
  - `box` and `ball` regions;
  - `boundary_layer` (first height, growth, layers): Netgen only, since Gmsh's 3D boundary layers don't work on OpenCASCADE geometry.
  - Older recipes' `local_sizes` load as size controls (a before-validator), so old projects and tests still work; their mesh run keys change once.
  - **Refused, never skipped:** a control naming a selection the component doesn't have, two size controls giving one entity different sizes (both named), a transfinite count on anything but edges.
- **Gmsh's settings typed:** `curvature_points`, `algorithm_2d` (default Frontal-Delaunay, Gmsh's own, so existing meshes are unchanged), `algorithm_3d`, `optimiser` (Gmsh, Netgen, off), `high_order_optimise`, `smoothing`. `gmsh_options` passes any other option by its own name; a name Gmsh doesn't have is refused (`set_gmsh_option`).
- **Quality** (`mesh.generate.mesh_quality`): for the volume elements, the shape quality (Gmsh's signed inverse condition number: 1 regular, 0 flat, negative inverted), the scaled Jacobian (1 for any straight-sided element; it catches folded curved ones), gamma and the edge aspect ratio, each with min, mean and a histogram; the ten worst-shaped elements with their centres. Written to `quality.json` beside the mesh. `quality_floor` makes meshing fail below a shape quality, saying where.
- **Netgen** (`mesh/netgen_mesher.py`, `mesh/netgen_run.py`):
  - In its own pixi environment (`pixi install -e netgen`): its OpenCASCADE bindings and OCP's can't share one process ("GeomAbs_Shape is already registered"), and adding it to the main environment downgraded OCCT.
  - Gmsh prepares the geometry as for its own mesh (parts placed, fragmented, groups) and writes BREP. Netgen meshes it in a subprocess, with local `maxh` on the size controls' entities and boundary layers (`BoundaryLayerParameters`).
  - Its first-order mesh comes back by edge, face and solid number. Those are matched to Gmsh's entities by centre and measure, and the nodes and elements are added to the Gmsh model on them, with parametric coordinates. Gmsh then raises the order (curving midside nodes onto the geometry) and the usual pipeline carries on: groups, quality, export.
  - Netgen's tets and prisms come the other way round from Gmsh's and are turned.
  - Refused on Netgen: refinement near, transfinite counts, regions and hexahedra (all name Gmsh).
  - **Benchmark:** NAFEMS LE1 on a Netgen mesh gives 92.74 MPa on CalculiX (target 92.7, +0.04 %). A three-layer boundary layer puts its node planes at exactly 0.1, 0.25 and 0.475 mm.
- **The Mesh tab** (`gui/mesh_bench.py`):
  - **Side panel:** a component picker, the recipe's controls in order (double-click edits; the menu moves or deletes), and the quality box (the measures, then the worst elements; double-clicking one pins it in the view).
  - **Ribbon above the view:** Sizing (Local size, Refine near, Box, Ball), Method (Edge count), Inflation (Boundary layer), Quality (Mesh), Mesher (Gmsh, Netgen).
  - **Control editor:** opens in the Properties dock, with selections as a dropdown of the component's own (of the right entity kind) and lengths in the display unit; Apply is one command.
  - **The view** colours the elements by shape quality once meshed. The recipe's settings are editable in Properties beside it.
- **Settings:** Meshing → Mesh preview ("on demand", the default, or mesh again after every change). Meshing → Offer Netgen as Gmsh's optimiser (whether the recipe's optimiser dropdown lists Netgen). The optimiser itself is a recipe setting, not a preference, because preferences never change results.
- **Agents:** MCP `mesh_quality(component)` meshes with the recipe and returns counts, the measures, the five worst elements and the warnings.
- **Added 2026-10-25:**
  - **Edge seeds:** Show seeds in the Mesh tab meshes the edges only, with the recipe's sizes and controls (`generate_mesh(..., seeds=True)`), and draws their nodes over the preview, as PrePoMax's Preview Edge Mesh does.
  - **Method detection:** the preview finds each body's method. A body is block-like when it has six faces, each bounded by four edges, which Gmsh's automatic transfinite meshing needs; then hexahedra can be used. Otherwise it gets tetrahedra, with the reason. The Mesh tab lists them, and the view highlights block-like bodies. Sweeping isn't offered: Gmsh can't sweep OpenCASCADE bodies after the fact.
  - **Gmsh options…:** Gmsh's own mesh options (`gmsh -help_options`: name, default, description; `mesh/gmsh_options.py`) in a searchable table. A value typed sets it for the recipe, an empty one clears it, and a non-number for a number option is refused.
  - **Element types setting** (Settings → Meshing): with "each solver's preferred types", a new recipe gets first-order elements when every study uses a finite-volume CFD solver, and second order otherwise; "neutral" always gives second order.
  - **Mesh shoot-out** (`shootout.run_mesh_shootout`, `puzzle mesh-shootout PROJECT STUDY`, MCP `mesh_shootout`): the study on its recipe meshed by Gmsh and by Netgen, compared by peak or global values, or by eigenfrequencies (node-by-node norms are refused: the meshes differ). The cantilever's tip load agrees to 0.005 %.
- **Not yet:** sweeping, and a recipe mode that meshes block-like bodies with hexahedra and the rest with tetrahedra together.

## Alternatives considered

- Expose only the typed common options: simpler, but leaves expert users stuck at the GUI's edge.
- Raw Gmsh `.geo` script editing: complete, but bypasses named selections and the command layer.
- Salome's SMESH: broad (hexa sweeps, NETGEN, MG-plugins), but a large dependency that is hard to package across platforms.

## Review notes (2026-10-23)

The interface review against Salome-Meca and PrePoMax (item 13) confirms this ADR's scope and adds three things taken from PrePoMax:

- **Edge-seed preview:** show the seeds on edges before meshing, as PrePoMax's "Preview Edge Mesh" does.
- **Automatic method detection:** a part that can be swept, revolved or meshed transfinitely is meshed that way, and the view colours parts by method and says why a part can't be swept (PrePoMax 3.0).
- **Conflicting local sizes stop the mesh with a reason,** rather than being changed silently.

SMESH's quality controls (about 27 measures) are the reference for item 5.
