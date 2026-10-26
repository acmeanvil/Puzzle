# 0049. Coordinate systems everywhere: Cartesian, cylindrical and spherical, defined many ways, used by every input and result

- Status: Proposed
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

## Decision (proposed)

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
     - a planar face (origin at its centroid, z along its normal);
     - a cylindrical or conical face's axis (as now);
     - a spherical face's centre;
     - a straight edge (x along it);
     - a circular edge (z along its axis, origin at its centre);
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
   - **components in a system:** the results bar gets an "In axes" dropdown (global by default). Vectors (displacement, force) and tensors (stress, strain, heat flux) are rotated into the system at each node: radial, hoop and axial stress in a cylindrical system; r, θ, φ in a spherical one. Derived quantities that don't depend on axes (von Mises, principal stresses, magnitudes) are unchanged and say so;
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

## Questions for the maintainer

1. **"Polar":** spherical in 3D (r, θ, φ), as above, with cylindrical serving as polar in 2D (ADR 0048)? Or did you mean something else?
2. **Angle convention for spherical:** θ from the axis (polar angle) and φ about it (azimuth), as Ansys and physics texts use (recommended)? Or latitude, measured from the equator?
3. **Rotation angles:** applied about x, then y, then z, each about the system as rotated so far (Ansys's way, recommended)? Or about fixed global axes?
4. **Results in axes:** a results-bar choice that applies to everything shown (recommended)? Or a choice per result tool only?
5. **Order:** results in axes early (step 2, recommended for vessel work), or the definitions from geometry first?

## Alternatives considered

- **A coordinate system per feature** (axes typed on each load): no sharing, and results can't follow the same frame.
- **Transforming results in each solver:** some solvers can (Code_Aster's `MODI_REPERE`), others can't. Post-processing does it once for all.
- **Only Cartesian systems with rotation:** radial and hoop components need the per-point frames of cylindrical and spherical systems.
