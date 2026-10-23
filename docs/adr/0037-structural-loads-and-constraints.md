# 0037. Structural loads and constraints: body loads, remote loads, springs, couplings, local axes, amplitudes

- Status: Proposed
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

## Consequences

- Most everyday structural cases can be modelled without solver keywords.
- The catalogue's feature list doubles. The add menus group features (Supports, Loads, Body loads, Connections) so they stay short.
- Load and support symbols (ADR 0035) get their glyphs for each new feature.
- Shoot-outs cover fewer solvers for remote loads at first (CalculiX and Code_Aster).

## Alternatives considered

- Leave these to the expert pass-through (ADR 0023): power users manage, but every user needs gravity and torque, and pass-through isn't solver-neutral.
- Model torques as tangential pressure distributions computed by Puzzle: it is solver-neutral, but wrong at the boundary for non-circular faces, and it doesn't give remote displacement.
