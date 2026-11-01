# 0049. Coordinate systems everywhere: Cartesian, cylindrical and spherical, defined many ways, used by every input and result

- Status: Accepted (2026-10-29, with the maintainer's answers); built: steps 1–2 2026-10-29, steps 3–5 2026-11-01
- Date: 2026-10-26

## Context

ADR 0037 added local axes (`CoordinateSystem`, kind `axes`).
- **Types:** Cartesian (an origin, an x axis and a direction in the xy plane) and cylindrical (an origin and an axis).
- **Made by:** "New Cartesian axes" or "New cylindrical axes" with default values edited in Properties, or "New cylindrical axes from this face", which fits a cylinder's axis.
- **Used by:**
  - displacement, force and remote features (written per solver, `common.globalize`, `*TRANSFORM`, `Capabilities.axes_features`);
  - elastic supports' normal and tangential stiffness;
  - construction planes, which can be a Cartesian system's plane (ADR 0046).

The maintainer asked for arbitrary Cartesian, cylindrical and polar systems, used anywhere a coordinate system can be. What's missing:
- **a polar (spherical) system:** domes, spherical heads, radial loads on a sphere;
- **ways to place a system:** three points, a picked face or edge, rotation angles, relative to another system, attached to a part so it moves with it;
- **results in a system:** radial, hoop and axial stress in a vessel, a bolt's axial force direction, the deformation of a flange in its own frame;
- **every other input that has a direction:**
  - pressure on a face (already normal), and traction (ADR 0044);
  - orthotropic materials' fibre directions;
  - beam section orientation;
  - mesh controls' boxes;
  - imported field placement (ADR 0044);
  - section cuts;
  - probes, paths and linearization (the hoop direction, ADR 0042).

## Decision

1. **Three types, one object.** `CoordinateSystem.type`: `cartesian`, `cylindrical`, or **`spherical`** (r, θ polar angle from the axis, φ azimuth about it, right-handed; Ansys's spherical convention).

   Every system has an origin and an orientation, a rotation from global axes. For cylindrical and spherical systems the orientation's z is the axis and its x is where θ (cylindrical) or φ (spherical) starts. A component's x, y, z values then mean:
   - Cartesian: x, y, z;
   - cylindrical: r, θ, z;
   - spherical: r, θ, φ.

   `frames(points)` gives the local axes at any points (already so for cylindrical), so every use reads the same function.
2. **Ways to define one**, in one form (New coordinate system…), each stored as how it was made so it updates:
   - **typed:** origin, and either two directions or three rotation angles (about x, y, z in turn, as Ansys's "rotate X/Y/Z");
   - **three points:** an origin, a point on x, a point in the xy plane, picked in the view (Pick, snap to nodes or corners, as construction geometry does, ADR 0046) or typed;
   - **from geometry:**
     - an edge or curve: x along its tangent at its start, end, middle or a fraction along it (z along the axis for a circle);
     - a planar face (origin at its centroid, z along its normal);
     - a cylindrical or conical face's axis (as now);
     - a spherical face's centre;
   - **relative:** an offset and rotation from another system, so a chain of systems stays consistent;
   - **attached to a part:** follows the part's placement (PartInstance translation and rotation), so an assembly can be moved without redoing its systems.

   A system made from geometry re-derives itself when the geometry regenerates (G3 naming), and reports a problem if its face or edge is gone.
3. **Shown in the view:** a triad (x, y, z arrows in red, green, blue) at the origin when the system or anything using it is selected; for cylindrical and spherical systems, the axis and the θ start. A preference (Settings > View > Coordinate systems: always, when used, or never) decides when.
4. **Used by inputs**, each with an **Axes** dropdown (global by default):
   - **supports and loads:** displacement, force, remote force and displacement, elastic and compression-only supports (as now); acceleration and rotation body loads; traction and body force density (ADR 0044);
   - **materials:** orthotropic elasticity and anisotropic conductivity directions (`Material.models`) as an orientation on the material assignment;
   - **beams:** the section's orientation (local y) taken from a system's axis;
   - **mesh controls:** box controls aligned to a system;
   - **imported fields:** the placement (ADR 0044 already allows a system);
   - **construction geometry:** planes as any system's xy, yz or zx plane (Cartesian now; any type then); paths along a system's axis.

   Solvers: where a solver can't apply a local direction natively, the adapter turns it into global terms (as `globalize` does) or refuses with the reason (`Capabilities.axes_features`), never ignores it. Spherical displacements and forces are written as functions of position, as cylindrical ones are.
5. **Used by results:**
   - **components in a system:** every result starts in global axes. A plot created in a system keeps it: the results bar's "In axes" dropdown, set when a result is shown, and saved plots and result tools carry their own. Vectors (displacement, force) and tensors (stress, strain, heat flux) are rotated into the system at each node, so a component names one of its axes: normal stress along θ of a cylindrical system is the hoop stress, along r the radial, along z the axial; r, θ, φ in a spherical one. Quantities that don't depend on axes (von Mises, principal stresses, magnitudes) are unchanged and say so;
   - **probes, paths and selection results** take an axes choice for their components;
   - **linearization's** line axes can follow a system (hoop direction from a cylindrical system's θ);
   - **reactions and section forces:** components and moments in a system, the moment about its origin;
   - **exports** (ADR 0043) can write components in a system.

   Transformations happen in post-processing (`post/axes.py`), so every solver's results read alike and nothing is solved again.
6. **Checks:**
   - rotation round-trips (local to global to local) for every type, at points on and off the axis;
   - a thick cylinder (Lamé) whose stress in a cylindrical system gives σr, σθ, σz to the closed form;
   - a sphere under internal pressure whose stress in a spherical system gives σr and σθ = σφ to the closed form;
   - a radial displacement in spherical axes on a sphere against the closed form;
   - a three-point system and a face-derived one that follow their geometry after an edit;
   - the results bar's components in axes against hand-rotated values.
7. **Twins:** MCP and CLI take an `axes` name wherever they take a quantity (`result_stats`, `probe`, `export`); the outline lists systems with how each is defined.

**Order of work:**
1. spherical type, rotation-angle and three-point definitions, the triad in the view;
2. results in axes (results bar, probes, paths, reactions), with the Lamé checks;
3. definitions from faces and edges, relative and attached systems;
4. materials' orientations and beam orientation;
5. the remaining inputs (body loads, mesh boxes) and exports.

## Consequences

- One object answers "which way?" for every input and result, so a vessel's radial and hoop stresses, a flange's frame and a composite's fibres are all set the same way.
- Results in axes are a post-processing step, cheap and solver-neutral; they don't change a run key.
- Systems tied to geometry or to other systems need re-deriving on regeneration and a problem when they can't be. That's the same machinery as selections (G3).
- Each solver's support for local directions is claimed only after its benchmark (CLAUDE.md); the rest is converted to global terms or refused.

## The maintainer's answers (2026-10-29)

1. **Types:** Cartesian, cylindrical and spherical (spherical as spherical; no separate "polar").
2. **Spherical angles:** θ from the axis, φ about it. A Cartesian system's rotation angles are given relative to the global system.
3. **Rotations:** about x, then y, then z of the system as rotated so far. A system can also be **aligned to an edge or a curve**: its x along the edge's tangent at a chosen point (its start, end, middle or a fraction along it). For a circular edge, z goes along the circle's axis.
4. **Results relative to an axis:** a component names an axis of a system. Normal stress along a cylindrical system's θ axis is a hoop stress plot; along r, the radial stress; along z, the axial stress.
5. **Global unless asked:** every result starts in the global system. A system is chosen when a plot (or result tool) is created, and that plot keeps it.

## Alternatives considered

- **A coordinate system per feature** (axes typed on each load): no sharing, and results can't follow the same frame.
- **Transforming results in each solver:** some solvers can (Code_Aster's `MODI_REPERE`), others can't. Post-processing does it once for all.
- **Only Cartesian systems with rotation:** radial and hoop components need the per-point frames of cylindrical and spherical systems.

## Built (2026-10-29): steps 1 and 2

- **Types and definitions:** `CoordinateSystem.type` is Cartesian, cylindrical or spherical. `orientation()` comes from `angles` (about x, then the turned y, then the turned z, from global), `points` (origin, a point on x, one in the xy plane), or the directions. `frames(points)` gives each type's axes at points: cylindrical r, θ, z; spherical r, θ (polar), φ (azimuthal); on the axis or at the centre the system's own x, y, z stand in. Adapters read `centre()` and `orientation()`; a spherical system is refused where a solver can't write it (`Capabilities.axes_features`, and CalculiX's `*TRANSFORM` explicitly).
- **GUI:** New coordinate system… (Model → New coordinate system, the section's and the project's menus) is a panel: type; by directions, rotation angles or three points (each point picked: Pick, then double-click, snapping to nodes or corners). The triad shows as it changes and when a system is selected (x, y, z in red, green, blue; a cylindrical or spherical system's axis, and where θ or φ starts). Properties shows only the fields its definition uses.
- **Results in axes:** `post/axes.py` rotates vectors and tensors into a system per node (or element centre), after the solve, for every solver; the global displacement is kept as `displacement_global` for the deformed shape and for probes' back-mapping. The results bar's **In axes** dropdown starts at Global each time a result is shown; the components are named for the system (`quantities.catalog(..., axes)`): "Normal stress θ (hoop)", "Deformation r (radial)".
- **Checks:** frames orthonormal and right-handed for each type, rotation angles, three points, spherical axes on the equator, round trips (`tests/test_coordinate_systems.py`); **Lamé's thick cylinder** under 100 MPa solved in global axes and read in a cylindrical system: hoop stress at the bore p (ro² + ri²)/(ro² − ri²) and outside 2p ri²/(ro² − ri²) within 3 %, radial −p and 0 (`tests/integration/test_results_in_axes.py`); the panel, the triad, Properties and the results bar (`tests/test_gui_axes.py`).
- **Next (then):** steps 3 to 5: systems from faces and edges (aligned to an edge or curve, at a fraction along it), relative and attached systems; material and beam orientations; result tools (probes, paths, reactions) and exports with an axes choice of their own.

## Built (2026-11-01): steps 3 and 4

- **Systems that follow something** (`puzzle.axes`). A `CoordinateSystem` may be:
  - **from geometry:** `geometry` (or the attached part's), `entity` face or edge, and `entity_name`:
    - a planar face gives its centroid and outward normal as z, with x from the system's own `x_axis` laid in the face;
    - a cylindrical or conical face gives its axis, the origin on the axis level with the face's centre;
    - a spherical face gives its centre;
    - an edge gives the point `along` it (0 to 1) and its tangent there as x, with z along a circle's axis, else from `xy_direction`.
  - **attached to a part** (`component`, `part`): it follows the part instance's placement;
  - **relative to another system** (`relative_to`): its origin and axes are in that system's.

  `axes.absolute(project, cs, root)` turns any of these into plain global points; `run_study` hands adapters `axes.resolved_project`, and the results bar, imported fields and load glyphs resolve their systems the same way. The stored system keeps how it was made, so it re-derives after an edit. A face or edge that is gone is an error naming it, and a cycle of relative systems is refused. At their defaults the new fields are left out of content hashes.
- **GUI:** the New coordinate system panel adds "From a face or edge" (a geometry's named face or edge selection; "along" for an edge), "Relative to the axes of" and "Attached to the part". The triad previews the resolved system. Properties shows only the fields a definition uses.
- **Material directions:** `MaterialAssignment.axes`, a system whose x, y, z (or a cylindrical one's r, θ, z) are the material's 1, 2, 3; without it, the material's own axis1 and axis2.
  - CalculiX writes `*ELASTIC, TYPE=ENGINEERING CONSTANTS` and `*CONDUCTIVITY, TYPE=ORTHO`, with `*ORIENTATION` (RECTANGULAR, or CYLINDRICAL through two points on the axis) on the `*SOLID SECTION`. Spherical material axes are refused.
  - Code_Aster (2026-11-01) writes `ELAS_ORTH` and `THER_ORTH` with `AFFE_CARA_ELEM` `MASSIF` (Cartesian: `ANGL_REP`, nautical angles; cylindrical: `ANGL_AXE` and `ORIG_AXE`), passed to the thermal solves too. Its cylindrical frame has L axial, T tangential and N radial, so the constants are written in that order (`comm.CYLINDRICAL`; ν_ji = ν_ij E_j / E_i). 3D only; orthotropic plasticity and anisotropic conductivity with temperature tables (THER_NL) are refused.
  - Elmer and FEniCSx refuse these models, as before.
  - Beam orientation from a system was built with ADR 0055 (`BeamSection.orient = "axes"`).
- **Checks:**
  - `tests/test_axes_derived.py`: a face's centroid and normal, a pin's axis, a ball's centre, an edge's tangent, a relative system, an attached system following its part, re-derivation after an edit, a lost face named, older hashes kept;
  - `tests/integration/test_axes_derived_solve.py`: a force in axes taken from the tip face stretches the bar by FL/EA on CalculiX and Code_Aster;
  - `tests/integration/test_orthotropic.py`, with strain read in the bar's middle half:
    - material axes turned 90°: E2 (exact), unturned E1;
    - cylindrical axes on a transversely isotropic rod: E3 (to 1e-5). With E1 ≠ E2 a cylindrically orthotropic rod builds radial and hoop stresses (Lekhnitskii), 1.8 % stiffer;
    - conduction along a turned axis: k2;
    - radial conduction in a tube heated through its bore, in cylindrical axes: the bore's rise q rᵢ ln(rₒ/rᵢ) / k1 (CalculiX 1.3861 K against 1.3863 K), which shows a swapped radial axis;
    - all on CalculiX and Code_Aster.
## Built (2026-11-01): step 5

- **Body loads in a system:** `common.globalize` turns gravity and acceleration given in a Cartesian system into global vectors. A rotation in a Cartesian or cylindrical system turns about that system's z through its origin, whatever the feature's own axis says. All four FE solvers claim these (`axes_features`). Elmer's body loads now go through `globalize` too.
- **Mesh boxes along a system:** `BoxControl.axes` takes a Cartesian system, the corners then in its axes. Gmsh's Box field follows the global axes, so a turned box is a MathEval field: the inside size where every local coordinate is within the half-widths (`Max` of `Fabs(u_i) - h_i`, then `Step`). The Mesh tab's box form has "Along the axes of".
- **Exports in a system:** `export(..., axes=)`, CLI `export --axes` and MCP `export(axes=)`. ParaView files and point clouds get their vectors and tensors in the system's axes, and the sidecar says so. The deformed-shape exports refuse axes, since a shape is global.
- **Checks:**
  - `tests/integration/test_body_loads_in_axes.py` on CalculiX, Code_Aster, FEniCSx and Elmer: gravity in turned axes, and a rotation about a cylindrical system's axis (the feature's own axis set wrong), each equal to its global twin to 1e-6;
  - `tests/test_axes_derived.py`: a 45° box refines inside itself and not beside;
  - `tests/integration/test_exports.py`: a point cloud's x in turned axes is the global y.
- **Not yet:** orthotropic materials on Elmer and FEniCSx, and in 2D models on Code_Aster; result tools (probes, paths, reactions) with an axes choice of their own (the results bar has one).
