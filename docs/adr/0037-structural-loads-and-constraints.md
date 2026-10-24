# 0037. Structural loads and constraints: body loads, remote loads, springs, couplings, local axes, amplitudes

- Status: Accepted (2026-10-23); body loads and amplitudes built
- Date: 2026-10-23

## Context

Puzzle's structural features are fixed, displacement, force, pressure and temperature (plus transferred temperature and prestress). Both reference tools offer far more (review item 9). PrePoMax has:

- moment, gravity, centrifugal, hydrostatic and imported pressure, surface traction;
- pretension (force or displacement);
- point and surface springs, compression-only supports;
- rigid bodies, kinematic and distributing couplings;
- rectangular and cylindrical coordinate systems;
- tabular amplitudes.

Code_Aster has all of them as commands. These are what real parts need: self weight, rotating parts, a shaft's torque on a keyway, a support with given stiffness, bolts.

Each solver has them in its own form, and Puzzle's rule holds: an adapter writes a feature only once a benchmark passes, and refuses it otherwise (`write_input` refuses, never ignores).

## Decision

New features in `model/catalog.py` (structural physics), each typed with readable names and units:

1. **Body loads** (no selection, or a body selection):
   - **Gravity:** an acceleration vector, default 9.81 m/s² along −Z.
   - **Centrifugal:** angular velocity, axis point and axis direction.
   - **Linear acceleration.**

   Writing them:
   - CalculiX: `*DLOAD` GRAV and CENTRIF.
   - Code_Aster: `PESANTEUR` and `ROTATION`.
   - Elmer: `Stress Bodyforce` (gravity first).
   - FEniCSx: the body-force term ρb in the weak form.
2. **Remote loads and supports** through a reference point: remote force, moment, and remote displacement or rotation on a face selection. Each says how it attaches:
   - **deformable:** distributing, like RBE3 or Ansys deformable;
   - **rigid:** kinematic, like RBE2.

   Writing them:
   - CalculiX: `*DISTRIBUTING COUPLING` or `*KINEMATIC` coupling to a reference node (how rotations enter the distributing coupling is to be checked first).
   - Code_Aster: `LIAISON_RBE3` or `LIAISON_SOLIDE` with a discrete element carrying rotations.
   - Elmer and FEniCSx: refused at first.
3. **Elastic support:** a stiffness per area, normal and tangential, on a face, like Ansys elastic support.
   - CalculiX: `SPRINGA` elements to ground, stiffness by nodal tributary area.
   - Code_Aster: `DISCRET` on the face's nodes.
   - Elmer: the elastic solver's `Spring` boundary condition.
   - FEniCSx: a Robin term.

   **Compression-only support** follows. It is CalculiX `GAPUNI` and Code_Aster contact with a rigid plane, and is nonlinear, so it is ramped.
4. **Bolt pretension** on a solid bolt: a section face selection (or a cut at a plane) and a force, or a length adjustment. It is applied in a first step and then locked, as Ansys does.
   - CalculiX: `*PRE-TENSION SECTION` on the section surface.
   - Code_Aster: an imposed relative displacement across a cut (the exact formulation is to be checked).

   Beam-fastener pretension stays with ADR 0027.
5. **Local coordinate systems:** a project-level object (kind `axes`): Cartesian or cylindrical, by origin and two directions, or picked from a cylindrical face's axis. Displacement, force and remote features can name one.
   - CalculiX: `*TRANSFORM` (R and C).
   - Code_Aster: `LIAISON_OBLIQUE` and `DDL_IMPO` in local axes where available.
   - Elmer and FEniCSx: refused when not along global axes.
6. **Amplitudes:** a feature's value may carry a time table (a factor against time or load fraction), shared by name (kind `amplitude`).
   - CalculiX: `*AMPLITUDE`.
   - Code_Aster: `DEFI_FONCTION` with `FONC_MULT`.
   - Elmer: `Variable Time` tables.
   - FEniCSx: an interpolated factor in t.

   Transient heat uses them at once. Static steps use them over increments, for load-deflection curves (ADR 0038's history plots).

**Benchmarks, one per feature, before any adapter claims it:**

- a cantilever under self weight against wL⁴/8EI;
- a spinning disc against the closed-form hoop stress;
- a torque on a shaft end through a distributing coupling against Tr/J;
- a block on an elastic support against the stiffness sum;
- a bolt section reaching its pretension;
- a radial displacement in cylindrical axes;
- a ramped load matching its table.

## Built: body loads and amplitudes (2026-10-23)

- **Body loads:** gravity, linear acceleration (written as gravity −a) and rotation (centrifugal), on a body selection, through the material's density.
  - Written per solver as:
    - CalculiX: `*DLOAD` GRAV and CENTRIF on the body's element set;
    - Code_Aster: `PESANTEUR` and `ROTATION`;
    - Elmer: a Body Force per material body (ρb, the centrifugal field as a MATC expression in the coordinates);
    - FEniCSx: ρ b·v over the body's cells.
  - Elmer and FEniCSx need the load on a material's body selection, and refuse it otherwise.
  - A body load makes density a required property, so a material without one is `incomplete_material` before any run.
  - **Benchmarks** (`tests/integration/test_body_loads.py`), with all four solvers agreeing to six digits:
    - the cantilever under self weight against a tip force: the deflection ratio is −0.128 % from Timoshenko beam theory;
    - an acceleration equals gravity reversed;
    - a spinning steel quarter disc: rim displacement −0.007 % from plane-stress theory.
- **Amplitudes:** an `Amplitude` object (kind `amplitude`) holds a table of time (s) against factor, linear between points and held beyond them. A feature names one (`Feature.amplitude`), and the component depends on it, so the table is in the run key.
  - Written per solver as:
    - CalculiX: `*AMPLITUDE`, with cards split per amplitude;
    - Code_Aster: a charge per amplitude with `FONC_MULT` (DEFI_FONCTION held constant outside);
    - Elmer: `Variable Time` tables, held with far end points;
    - FEniCSx: a factor constant updated every step.
  - **This round:** heat flux in transient heat steps (`Capabilities.amplitude_features`).
  - **Refused, not ignored:** other features and non-transient steps.
  - **On temperatures:** an amplitude would scale the absolute value in kelvin, so it needs an offset form first.
  - **Benchmark:** a trapezoid heat flux into the insulated cantilever. Backward Euler at 1 s sums the amplitude to the trapezoid's area, so the mean temperature rise is exactly q·A·20 s/(ρcV). All four solvers give 298.943 K.
- **GUI and agents:**
  - the catalogue's add menus offer the three body loads;
  - an Amplitudes section with a table editor (New amplitude…, Edit points…);
  - a feature's Amplitude dropdown;
  - body-load arrows (ADR 0035);
  - the outline lists amplitudes and which features follow them.
## Built: remote loads (2026-10-23)

- **Deformable remote force and moment** (`remote_force`): F and M at a point (default: the face's centroid) become the traction an area-weighted distributing coupling (RBE3) gives, t(x) = F/A + (K M_c) × (x − c). K inverts the face's polar second-moment tensor, all integrated on the faces' own curved geometry (`adapters.common.remote_load`).
  - It is solver-neutral, needing no coupling elements:
    - CalculiX: consistent nodal forces;
    - Code_Aster: FORCE_FACE FORMULEs;
    - Elmer: MATC tractions;
    - FEniCSx: a UFL traction.
  - Integrating on flat corner triangles first left the moment about 2 % short on a round face (the polygon's polar moment). Code_Aster, integrating its curved faces, then read +2 %; the curved integration fixed it.
- **Remote displacement** (`remote_displacement`, rigid face): all six components, small rotations, u = u0 + θ × (x − p) as a prescribed field on every solver. Modal steps keep it as a constraint.
- **Rigid remote force** (`remote_force_rigid`):
  - CalculiX: `*RIGID BODY` with reference and rotation nodes added past the mesh's ids;
  - Code_Aster: `LIAISON_SOLIDE` plus the same traction;
  - Elmer and FEniCSx refuse it.
- **Benchmarks** (`tests/integration/test_remote_loads.py`), a steel shaft in torsion against Saint-Venant:

  | Check | CalculiX | Code_Aster | Elmer | FEniCSx |
  | --- | --- | --- | --- | --- |
  | Remote moment: mid-length shear | +0.04 % | +0.04 % | +0.07 % | +0.06 % |
  | Prescribed rotation: shear | +0.04 % | +0.04 % | +0.07 % | +0.06 % |
  | Rigid moment: twist | 0.00 % | 0.00 % | refused | refused |

- **Symbols:** a remote force shows as an arrow at its point, and a remote displacement as a blue tint.

- **Not yet** (the rest of this ADR): elastic supports, bolt pretension, local coordinate systems, and amplitudes on structural loads (with ADR 0041).

## Consequences

- Most everyday structural cases can be modelled without solver keywords.
- The catalogue's feature list doubles. The add menus group features (Supports, Loads, Body loads, Connections) so they stay short.
- Load and support symbols (ADR 0035) get their glyphs for each new feature.
- Shoot-outs cover fewer solvers for remote loads at first (CalculiX and Code_Aster).

## Alternatives considered

- Leave these to the expert pass-through (ADR 0023): power users manage, but every user needs gravity and torque, and pass-through isn't solver-neutral.
- Model torques as tangential pressure distributions computed by Puzzle: it is solver-neutral, but wrong at the boundary for non-circular faces, and it doesn't give remote displacement.
