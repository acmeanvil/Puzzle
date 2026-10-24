# 0037. Structural loads and constraints: body loads, remote loads, springs, couplings, local axes, amplitudes

- Status: Accepted (2026-10-23); built; structural amplitudes come with ADR 0041
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

## Built: elastic support (2026-10-23)

- **`elastic_support`:** a stiffness per area, normal (kn) and tangential (kt), on a face.
  - Written per solver as:
    - CalculiX: SPRINGA elements to grounded helper nodes along each node's normal and tangents, binned into element sets by stiffness;
    - Elmer: `Spring i`, on faces along an axis (others refused);
    - FEniCSx: a Robin term;
    - Code_Aster: refused for now.
  - **Spring lumping:** springs are lumped as consistent loads are (∫N dA), so a uniform state is exact. Lumping by ∫N² first read +0.24 %, because it didn't match the pressure's consistent loads.
- **Two CalculiX fixes on the way:**
  - A stiffness printed as a bare integer was read as a DOF number. Stiffnesses are now written with an exponent.
  - Helper nodes past the mesh's (spring grounds, rigid-body nodes) are dropped from the results, using a `.meshnodes` marker that `write_input` leaves.
- **Benchmark** (`tests/integration/test_elastic_support.py`): a block on a foundation under pressure, top displacement p·h/E + p/kn. CalculiX, Elmer and FEniCSx are exact to 0.000 %.

## Built: local coordinate systems (2026-10-23)

- **`CoordinateSystem`** (kind `axes`): Cartesian (origin, x axis, a direction in the xy plane) or cylindrical (origin and axis; a feature's x, y, z are radial, tangential and axial). A feature names one in its `axes` field; the component's run key includes it.
- **How each solver takes them:**
  - A force, and a displacement giving all three components, in Cartesian axes are turned into global values before writing (`common.globalize`), so every solver takes them.
  - A displacement in cylindrical axes with all three components is written as functions of position: Code_Aster `DDL_IMPO` formulas in `AFFE_CHAR_MECA_F`, Elmer MATC expressions, a FEniCSx Dirichlet field interpolated at the nodes, CalculiX `*TRANSFORM` (TYPE=C).
  - Only some components given (the others free): CalculiX only, through `*TRANSFORM` (TYPE=R or C) on the face's nodes. The others refuse it (`Capabilities.axes_features`).
  - A node shared by two faces in different axes is refused on CalculiX (one transform per node).
- **Picked from a cylindrical face's axis:** not yet; origin and directions are typed in Properties.
- **Benchmarks** (`tests/integration/test_local_axes.py`):
  - A thin disc's rim moved by u_r = δ, u_θ = 2δ in cylindrical axes: uniform expansion plus a rigid turn. Inside r < R/2 the displacements match on all four solvers to 6 digits, and σ_r reads +0.35 % on all four (the rim's z restraint).
  - CalculiX: the cantilever's tip moved δ along an x axis turned 30°, the other components free: the tip's projection is δ exactly.
- **GUI:** a Coordinate systems section in the model tree (new Cartesian or cylindrical axes), an Axes dropdown on each feature, symbols drawn along the axes, the outline naming them.

## Built: bolt pretension, and features in some steps only (2026-10-23)

- **`bolt_pretension` (a force F) and `bolt_adjustment` (a shortening d)** on a section face inside a solid bolt. The geometry's `split` operation makes one: it cuts the bolt into two bodies and names the face between them `<split>/section`.
  - Applied in the first static or transient structural step the feature is active in, and locked at the length it reached in the steps after (as Ansys does).
- **`Feature.steps`:** the solve steps (1, 2, ...) a feature applies in; empty means every step. Properties shows it as "Applies in steps" ("All steps", or "2, 3"); the outline says "in steps 2". Without it, no load could start after the pretension. Solvers that split a study into one job per step refuse it for now (`Capabilities.feature_steps`).
- **CalculiX:** the deck cuts the mesh itself.
  - The section's nodes get twins; every element on one side of the plane that touches the section uses the twins.
  - Each pair is tied by `*EQUATION`: u_twin − u = n u_ref. The reference node's first dof is the section's opening, and the force on it is the force across the section. Pretension is `*CLOAD` −F on it, an adjustment is `*BOUNDARY` −d, and the lock is `*BOUNDARY, FIXED`.
  - CalculiX's own `*PRE-TENSION SECTION` was tried first. It moves only the elements with a face on the section, so on tets the elements touching it at an edge kept the two sides joined: 316 N of a 10 kN pretension. Its estimated normal was also 5° off on a round bolt.
- **Fixed on the way:** with an elastic support, a second step's `*BOUNDARY, OP=NEW` dropped the spring grounds' hold (the solve failed). The grounds are now held in every step.
- **Benchmark** (`tests/integration/test_pretension.py`): a bolt fixed at one end, its other end on an elastic support as stiff as the bolt (the clamped parts).

  | Check | CalculiX | Theory | Error |
  | --- | --- | --- | --- |
  | Step 1: pretension | 9998.6 N | F = 10 kN | −0.01 % |
  | Step 2: locked, then P pulls | 12003.8 N | F + P/2 = 12 kN | +0.03 % |
  | Adjustment d | 1655.6 N | d k_b/2 = 1649.3 N | +0.38 % |

- **Caveat:** elements of another part that touch the section's rim (a nut meshed conformally with the bolt) would be cut too. Keep the section clear of other parts.

## Built: Code_Aster elastic support and pretension; per-step features on every solver (2026-10-24)

- **`Feature.steps` on solvers that run one job per step** (Elmer, Code_Aster, FEniCSx, the CFD solvers): `run_study` hands each step's job a project copy holding only the features active in that step. A test on all four FE solvers (`test_multistep.py`: a pressure in step 1, a force in step 2) matches one-step studies exactly.
- **CalculiX fix:** a later step that wrote no card of a kind kept the step before's loads of that kind (`OP=NEW` applies per card). Each later step now resets `*BOUNDARY`, `*CLOAD`, `*DLOAD` (and `*DFLUX`, `*FILM` for heat) when it writes none.
- **Elastic support on Code_Aster:** a point element on each face node (a `DIS_T` discrete to ground), with `K_T_D_N` kn·a along the node's normal and kt·a along the face (`REPERE='LOCAL'`; the default is global axes), lumped by ∫N dA as on CalculiX. The block benchmark is exact, and on a block turned 30° with kn and kt (`test_tilted_support_agrees`) Code_Aster and CalculiX agree to 4e-6 of the largest displacement (CalculiX's six printed digits).
- **Pretension on Code_Aster:** the same cut as the CalculiX deck, written into the MED mesh: twins and a reference node, the elements on one side (faces too) moved onto the twins, `LIAISON_DDL` u_twin − u = n u_ref between single-node groups, the force as `FORCE_NODALE` on the reference node's DX (its DY and DZ held). That DX lies only in the relations, so it carries a stiffness of 1e-9 E × size, or the solver stops on a zero pivot.

  | One step | CalculiX | Code_Aster | Theory |
  | --- | --- | --- | --- |
  | Pretension F = 10 kN | −0.01 % | −0.01 % | F |
  | Adjustment d | +0.38 % | +0.38 % | d k_b/2 |

- **Locking across steps** needs one deck: a solver that runs each step as its own analysis refuses a pretension in a study with more than one structural step.
- **Refused:** pretension on Elmer and FEniCSx (no benchmark yet; neither has a linear relation to a reference dof written here).
- **Axes from a face:** a face selection's menu has "New cylindrical axes from this face". `mesh.adjacency.cylinder_axis` fits the axis to the face's mesh (the component's mesh, else its preview): the normals reject planes and other shapes, a quadric through the nodes gives the axis exactly (flat triangles tilt their normals by ~2e-5), and a circle fit gives the centre and radius (`tests/test_cylinder_axis.py`).
- **Compression-only support** (`compression_only_support`, kn per area): a foundation that pushes back but never pulls, so a face may lift off.
  - CalculiX: a `GAPUNI` element per face node to a grounded node, its stiffness kn × the node's ∫N dA, its direction the face's outward normal. CalculiX switches gaps only in a nonlinear step, so the step gets `NLGEOM` (without it the gaps were solved once, closed both ways: the linear Winkler answer).
  - Code_Aster: the same point elements as the elastic support, with `DIS_CHOC` (`DIS_CONTACT` `RIGI_NOR` per node) in `STAT_NON_LINE`; its local x points into the face (along it, every gap opened and Newton diverged).
  - Elmer and FEniCSx: refused.
  - **Benchmark** (`tests/integration/test_compression_only.py`): a stiff block on such a foundation loaded down at e = B/4 from its middle. Rigid-footing theory: contact length 3 (B/2 − e), toe settlement 2P / (3 D (B/2 − e) kn).

    | | CalculiX | Code_Aster |
    | --- | --- | --- |
    | Contact length | −0.6 % | +0.1 % |
    | Toe settlement | +0.7 % | −0.05 % |

    CalculiX's small difference is NLGEOM's large-rotation terms (the block turns 0.4 % of a radian).
- **Not yet:** amplitudes on structural loads, which come with ADR 0041 (dynamics).

## Consequences

- Most everyday structural cases can be modelled without solver keywords.
- The catalogue's feature list doubles. The add menus group features (Supports, Loads, Body loads, Connections) so they stay short.
- Load and support symbols (ADR 0035) get their glyphs for each new feature.
- Shoot-outs cover fewer solvers for remote loads at first (CalculiX and Code_Aster).

## Alternatives considered

- Leave these to the expert pass-through (ADR 0023): power users manage, but every user needs gravity and torque, and pass-through isn't solver-neutral.
- Model torques as tangential pressure distributions computed by Puzzle: it is solver-neutral, but wrong at the boundary for non-circular faces, and it doesn't give remote displacement.
