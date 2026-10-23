# 0019. Mesh in a workbench with the full breadth of Gmsh and Netgen

- Status: Accepted
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
