# 0046. Construction geometry: paths and planes for result tools

- Status: Accepted (2026-10-25, the maintainer's request)
- Date: 2026-10-25

## Context

Result tools (ADR 0042) read results at points, over named selections, and along a line typed into each tool. Two things were missing that Ansys Mechanical's Construction Geometry gives:
- **an arbitrary path**, kept and reused: for stress linearization through a wall, and for a result along a line;
- **an arbitrary plane** (Ansys's Surface): for any result over the cut it makes, and for the force and moment carried across it (a "force reaction" on a surface: the internal shear, axial force and bending moment of a section).

Selections can't do this: they follow the CAD's faces, and a plane through the middle of a body is not one.

## Decision

1. **A project object, Construction geometry** (kind `construction`, its own section in the tree):
   - **a path:** two points;
   - **a plane:** a point and a normal, or one of a Cartesian coordinate system's planes (ADR 0037) moved along its normal.

   It is never meshed and never changes a solve or a run key: only result tools read it.
2. **Made in a panel** (New path…, New plane…) in the Properties dock, from the tree's section, the project's menu, or Project → New construction geometry. A panel, not a dialog, so the view stays live:
   - each point has a **Pick** button; the next double-click on the model gives that point, anywhere on a face (`Viewport.capture_point`);
   - **Picks snap to** mesh nodes (the default when a mesh or a result is shown, so a linearization runs node to node; a picked node is kept to the last digit), CAD corners, or anywhere on the surface. On a deformed result the point is taken back to the undeformed shape first;
   - a plane can also be tilted through three double-clicked points;
   - the path or plane is drawn as it changes.

   Without picks, a path crosses the model's thinnest side and a plane cuts across its longest side at the middle (or they start from corners already picked with Pick → Points). Selecting one in the tree draws it over the model: a line with its ends marked, or a translucent square at least a quarter of the model's size.
3. **Result tools take them.** `ResultProbe.construction` names one:
   - **Path** and **Linearized stress** run along a construction path, or along a line typed in the tool as before.
   - **Result on a plane** (`plane`): any listed result over the plane's cut, as a maximum or minimum (with where it is), an area-weighted average, or an integral. Selecting it cuts the result view at the plane, so the values on it are seen.
   - **Section force** (`section`): the stress's traction (σ·n) summed over the cut gives the force; the moment is taken about the cut's centroid. It is what the side the normal points to passes on to the rest, with its normal and shear parts.

   A tool reads its construction geometry each time it is shown, so moving a plane moves the tools that use it.
4. **The cut runs through the whole model.** Where it crosses several bodies, they are summed. A plane is placed to cross only what is wanted. Limiting a cut to some bodies can come later.
5. **Accuracy:** values are interpolated from nodal results onto the cut (VTK, linear on each sub-triangle). On the benchmark below that is within 2%. Ansys sums element nodal forces for its surface reactions, which is exact for equilibrium; ours converges with the mesh.

## Consequences

- One path or plane serves several tools and keeps them consistent; editing it in Properties re-reads them all.
- New probe types `plane` and `section`, and an optional construction on `path` and `linearized`. The outline, MCP `probes` and `probe_path`, and the Results ribbon and menu cover them.
- Section cuts (ADR 0038) take a placed plane (`Cut.origin`, `Cut.normal`) as well as an axis and fraction.

## Checks

`tests/integration/test_construction.py`, CalculiX cantilever 200 × 10 × 10 mm, 100 N tip load, cut at mid-span:
- the section force is −100 N in y within 2% and the moment 10 N·m within 2%, with the cut area exact;
- the largest normal stress on the plane is M c / I = 60 MPa within 3% (on the tension side), and its mean is zero;
- a plane from a coordinate system gives the same answer;
- linearization along a construction path matches beam theory within 2%.

`tests/test_gui_construction.py` covers the forms, the drawing, the cut view and Properties.

## Alternatives considered

- **Planes as named selections:** selections are faces of the CAD and are meshed; a plane through a body isn't one, and meshing it would change the model.
- **Element nodal forces for section forces:** exact for equilibrium, but each solver writes them differently (CalculiX only on request, Code_Aster as a separate field). The traction integral works the same on every solver's stresses; this can be revisited for coarse meshes.
