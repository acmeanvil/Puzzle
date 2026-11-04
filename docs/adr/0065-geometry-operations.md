# 0065. Geometry operations from the Grasshopper review: sweeps, lofts, splines, mirror, scale, SubD, faceted booleans, morphing and queries

- Status: Accepted (2026-11-03)
- Date: 2026-11-03

## Context

The Grasshopper review (ADR 0064A) accepted geometry that Puzzle's history (ADR 0014,
`geometry/history.py`) can't build yet:

- splines [E2];
- extruding along a path [F2];
- lofts, sweeps, pipes and patches [F9];
- B-rep and surface queries [F12, F13], with E7's question about curvature;
- SubD [F14];
- curve and region intersections [F15];
- booleans on faceted meshes [F16];
- mirror [H2] and scale [H3];
- morphing [H6].

These are kernel work, not operators, so they get their own ADR. Once they are history
operations, ADR 0062's operators drive their arguments like any other.

What exists to build on:

- **History operations** with robust face names (G3): box, cylinder, cone, sphere,
  extrude and revolve of a sketch, hole, fillet and chamfer by rule, move, rotate,
  pattern, split, imprint, split_face, enclosure, and the preparation operations
  (import, heal, remove, fragment).
- **Previews** through the pending history; the geometry check.
- **Faceted geometry** (STL, OBJ, 3MF) through Gmsh's `classifySurfaces`, with patches
  mapped back to the faces users select.
- **Morphing for export** (ADR 0043, `post/exports/morphed.py`): the B-rep converted to
  NURBS and its poles moved by a displacement field, checked for deviation.
- **Measure** (ADR 0042): exact lengths, areas, volumes, radii, distances and angles,
  for the GUI, CLI and MCP.
- **OpenCASCADE** has the building blocks for most of the list:
  - `BRepOffsetAPI_MakePipeShell` (sweeps), `BRepOffsetAPI_ThruSections` (lofts) and
    `BRepFill_Filling` (patches);
  - `GeomAPI_Interpolate` (splines) and `gp_Trsf`/`gp_GTrsf` (mirror, scale);
  - `BRepLProp`/`GeomLProp` (curvature) and `BRepOffsetAPI_MakeThickSolid` (thicken).
  It has no subdivision surfaces and no robust booleans on triangle meshes.

## Decision

1. **Every new operation is a history step**, with face names that follow ADR 0014's
   rules, arguments that may name parameters (so sliders drive them), a live preview,
   undo and the geometry check. Each is listed in `history.py`'s table and the step
   dialog, and is reachable from the Geometry ribbon and the menu bar.

2. **Mirror and scale** [H2, H3]:
   - `mirror` reflects the part (or a named body) in a plane through a point and
     normal, or in a construction plane (ADR 0046). It either keeps the original and
     fuses the copy (a symmetric part from its half) or replaces it (the other hand).
     Faces of the copy are named `<name>~m`.
   - `scale` takes one factor (uniform) or three (non-uniform), about a point.
     Non-uniform scaling turns cylinders and spheres into NURBS, so the step says so,
     and Measure's radii stop being exact there.
   - A scale used to repair an import's units is better done by the import's own unit
     (G1), and the step's help says that.

3. **Curves and paths** [E2, F2]:
   - `spline` is a wire through points (`GeomAPI_Interpolate`), with optional end
     tangents, or from control points. It is open or closed. Its points come from a
     list, a table (ADR 0064) or a construction path.
   - A spline is a **path** for the sweeps (item 4), or a **line body** for curved
     beams (ADR 0027), meshed into line elements as a polyline is.
   - DXF profiles keep their splines as splines (they are flattened to segments today).
   - The sketcher's own spline entity (SolveSpace's cubic) is phase 3.

4. **Sweeps, lofts and patches** [F2, F9]:
   - `sweep`: a sketch profile along a path (a spline, polyline, edge chain or
     construction path), by `BRepOffsetAPI_MakePipeShell`. It has Frenet, fixed or
     corrected frames, an optional twist, and a solid or a shell. Extrude along a path
     is the profile kept square to the path. Faces are named as extrude names them:
     `start`, `end`, and `side-…` after the profile's edges.
   - `pipe`: a circle or a tube (outer and inner radius) along a path: tubing, wires,
     welds as beads, or a solid model of a beam line.
   - `loft`: through two or more profiles (`ThruSections`), ruled or smooth, solid or
     shell. Faces are named `start`, `end` and `side-…`.
   - `patch`: a surface filling a closed chain of edges (`BRepFill_Filling`), to cap a
     gap in imported CAD or close a region; optional points it passes through.
   - `thicken`: a shell into a solid by an offset, one side or both
     (`MakeThickSolid`). A sheet then becomes a solid part, the reverse of ADR 0027's
     shells.

5. **Regions and intersections** [F15]:
   - Faces and profiles combine as 2D regions: union, difference and intersection of
     flat regions. This matters for 2D models (ADR 0048) and sheets, where a solid
     boolean doesn't apply. It is the same `mode` (add, cut, intersect) the solid
     steps have, on faces.
   - **Intersection points and curves** as construction geometry (ADR 0046):
     - where an edge or path crosses a face (points: a bolt position, a probe);
     - where two faces meet (a curve: a weld line);
     - where a plane cuts a part (a section's outline, as a path for result tools).
     They update with the geometry.

6. **Booleans on faceted geometry** [F16]: STL, OBJ and 3MF parts (scans,
   topology-optimised shapes) are united, cut and intersected with **manifold3d**
   (Apache 2.0, conda-forge and PyPI), whose results are always closed and manifold.
   The result goes through today's faceted path (`classifySurfaces`, patches named
   back). A boolean between a B-rep and a faceted part tessellates the B-rep first, and
   the result is faceted; the step says so.

7. **SubD** [F14]: OpenCASCADE has no subdivision surfaces. The options (review
   question 1):
   - **a. Through NURBS:** Rhino and others export SubD as NURBS patches in STEP, which
     Puzzle reads already. Nothing new; say so in the import docs. A SubD control
     cage itself isn't read.
   - **b. A `subd` step from a control cage:** a quad mesh (OBJ, or 3DM through
     rhino3dm, MIT) refined to its Catmull–Clark limit surface, then converted to
     bicubic B-spline patches sewn into a solid. Regular quads are exact; patches round
     extraordinary vertices are approximate (G1 there), within a stated tolerance.
     Evaluated by OpenSubdiv (Apache 2.0, conda-forge) or Puzzle's own Catmull–Clark in
     numpy.
   - **c. A faceted limit surface:** the cage subdivided n times into triangles, then
     the faceted path (item 6). Simple, but its faces aren't smooth for meshing.
   The review chose a, then b (phase 4).

8. **Morphing** [H6], two kinds with different jobs:
   - **Mesh morphing, for shape optimisation.** Handles (faces or construction points)
     move by amounts that parameters drive. Radial basis functions spread that motion
     into the volume mesh's nodes, keeping its connectivity, and an element-quality
     check (scaled Jacobian, ADR 0019) refuses a morph that inverts elements.
     - Optimisation points then differ only in node positions, with no remeshing, so
       there is none of the mesh noise ADR 0063 warns of.
     - A study takes a morph as a mesh step: `run_study` meshes once, then morphs per
       point. Its run key covers the handles' values.
     - The optimised shape goes out as a morphed STEP through ADR 0043's existing B-rep
       morph.
   - **B-rep deformation, for modelling**: a history step `deform`, bending, twisting,
     tapering or a free-form box with handles, built on the export's pole moving
     (faces become NURBS). Grasshopper's Twist, Taper, Bend and Box Morph.

9. **Queries** [F12, F13, E7]: Measure (ADR 0042) gains, for the GUI, CLI, MCP
   `geometry_measure` and the Measure operator (ADR 0064) alike:
   - the radius of curvature at a point on a face or edge (principal radii, the
     directions);
   - a face's least radius of curvature (the sharpest fillet, which sets the mesh
     size);
   - tangency breaks: the edges where faces meet at a corner rather than smoothly (G0,
     G1, G2);
   - topology counts (solids, shells, faces, edges, free edges), planarity, the
     oriented bounding box.
   NURBS internals (control points, knots, derivatives) stay internal (E7 answered no).

10. **Twins**: each step is in the CLI's and MCP's history editing (as every step is),
    a skill `geometry-operations` covers the new steps and the mistakes (sweep frames
    flipping, lofts twisting when profiles start at different points, faceted results),
    and tests check each against closed forms: a pipe's volume π (R² − r²) L, a loft
    between two squares a frustum's volume, a mirror's mass doubled, a spline through
    its points.

11. **Phases** (reordered by the review: mesh morphing earlier):
    1. Mirror, scale, spline (as a path and a line body), sweep and pipe, loft; the
       Measure queries.
    2. Mesh morphing for optimisation (with ADR 0063): handles, the radial-basis morph,
       the quality check, morphs per optimisation point.
    3. Patch, thicken, regions on faces, intersection points and curves; DXF splines
       kept; the sketcher's spline.
    4. Faceted booleans (manifold3d), SubD (a: documented through NURBS, then b: the
       `subd` step from a control cage), B-rep `deform`.

## Consequences

- Tubing, welds, ducts, curved beams, transitions and symmetric parts become history
  steps, and sliders drive them.
- Shape optimisation without remeshing (mesh morphing) removes the mesh noise that
  makes local optimisers stall.
- One or two new dependencies (manifold3d; OpenSubdiv if 7b uses it).
- Sweeps and lofts fail in ways boxes don't (self-intersection, twisted frames). The
  geometry check and clear refusals (the step and the reason) are part of each.

## Alternatives considered

- **CadQuery or build123d** as a modelling layer: Pythonic, on OCCT too, but a second
  naming scheme beside G3's; their algorithms are the same OCCT calls used directly
  here.
- **Remeshing every optimisation point** (today's way): simple, but noisy for shape
  changes; morphing complements it rather than replacing it.
- **Gmsh's OCC kernel for sweeps**: it has `addPipe` and `addThruSections`, but
  Puzzle's history builds in OCP directly, for names and history.

## Review questions

1. SubD: a (through NURBS only), b (a `subd` step from a control cage), c (a faceted
   limit surface), or a then b as proposed?
2. manifold3d for faceted booleans: acceptable as a dependency?
3. Mesh morphing in phase 3 alongside ADR 0063, or earlier, since it fixes the
   optimisation's mesh noise?
4. `thicken` (shell to solid) and `patch` in scope?
5. Anything missing from the lists for welds (a fillet bead along an edge), since pipe
   and sweep come close?


## Review (2026-11-03)

The maintainer's answers:

1. SubD: a (through NURBS, documented), then b (a `subd` step from a control cage).
2. manifold3d for faceted booleans: yes.
3. Mesh morphing earlier: now phase 2, before patches and regions.
4. `thicken` and `patch`: in scope.
5. Welds: no operation of their own. Pipe and sweep along an edge chain make a bead
   ("we can make it work"), and the skill shows how.
