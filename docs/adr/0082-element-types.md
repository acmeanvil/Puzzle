# 0082. Element types: a solver-neutral choice per body, each solver's own types, and a library of them

- Status: Proposed
- Date: 2026-11-11

## Context

Each adapter fixes its elements today:
- **CalculiX:** C3D4/C3D10/C3D20 by the mesh's order, CPS/CPE/CAX in 2D, S6/S8 shells,
  B31/B32 beams.
- **Code_Aster:** `3D`, `C_PLAN`/`D_PLAN`/`AXIS`, `DKT`, `POU_D_T`.
- **Elmer and FEniCSx:** their defaults (Lagrange P1/P2; Nédélec for magnetics).
- **The CFD solvers:** finite-volume cells.

A user can't choose anything else, and neither can the agent:
- reduced integration (C3D20R);
- incompatible modes (C3D8I);
- an incompressible or mixed formulation for rubber or plasticity near the limit
  (Code_Aster `3D_INCO_UPG`, CalculiX hybrid elements via its own choices);
- a thick shell (Code_Aster `DST`, `COQUE_3D`);
- Euler–Bernoulli against Timoshenko beams (`POU_D_E`, `POU_D_T`);
- lumped thermal elements (`3D_DIAG`).

Code_Aster matters most: its *modélisations* decide which studies and material models an
element supports, and the right one is often the difference between a run and a refusal.

The maintainer asked (2026-11-11) for:
- a way to specify element types on a geometry or mesh, across all seven solvers, for
  the user and the agent;
- a grouped, distilled listing of every element available in each solver, saying what
  it is for and which studies support it.

## Decision

1. **A library of elements, as data.** `puzzle/elements/` holds one table per solver
   (CalculiX, Code_Aster, Elmer, FEniCSx, Code_Saturne, OpenFOAM, PyNite). Each row has:
   - the native name (C3D20R, `3D_INCO_UPG`, Elmer 820, Lagrange P2);
   - the family: solid 3D, solid 2D (plane stress, plane strain, axisymmetric), shell,
     beam, truss, spring/discrete, thermal, electromagnetic, fluid cell;
   - the cell shapes and order it takes (tet4/10, hex8/20/27, wedge, pyramid, tri/quad,
     line);
   - its formulation in neutral words: full or reduced integration, incompatible modes,
     mixed u–p, hybrid, thick or thin shell, Euler–Bernoulli or Timoshenko, lumped;
   - the physics and study types it supports (static, modal, buckling, transient,
     harmonic, heat steady and transient, contact, plasticity, large deflection);
   - a one-line "use it for"/"avoid it for";
   - whether Puzzle writes it yet (`written`), so the library also says what is not.

   Each table cites the solver's own manual section. The tables are checked by a test:
   names unique, families and studies from the fixed vocabularies, every `written` row
   used by its adapter.
2. **The listing.** Grouped by family, then by what the element is for, with each solver's
   type in one row. For example, "quadratic tetrahedron, full integration: CalculiX
   C3D10, Code_Aster 3D on TETRA10, Elmer 510, FEniCSx P2". It is reached in several
   places:
   - generated from the library as `docs/elements.md`;
   - shown in the GUI's Help → Element library (search, filter by solver, family or
     study);
   - `puzzle elements [--solver S] [--family F] [--study T]` on the command line;
   - MCP `elements_list`;
   - indexed in the agent's knowledge pack.
3. **The choice, on the component.** `Component.elements: list[ElementChoice]`, each with:
   - `selection`: a body or part;
   - `formulation`: neutral, from the library's vocabulary (`reduced`,
     `incompatible_modes`, `mixed_up`, `thick_shell`, `euler_bernoulli`, `lumped`, …);
   - `solver_types`: `{solver: native name}`, optional, an expert's override.

   Each adapter maps the neutral formulation to its own type on the meshed cells, or
   refuses it by name. When it refuses, it lists the library's alternatives for that
   solver.

   The mesh recipe's order and shape still decide the cells. The adapter checks that the
   chosen type takes them; a C3D20R on tetrahedra is refused, with the fix.
4. **Checks before a run.** The library's study list says when a choice can't run the
   study (Code_Aster `DKT` in a buckling step with plasticity) or the material model
   (hyperelasticity needs a mixed formulation). These come up in Problems and the Advisor
   before the solve.
5. **Order of work.**
   1. The library tables with a first pass of every solver.
   2. The listing in all its places.
   3. The choice, on Code_Aster first: `3D_SI`, `3D_INCO_UPG`, `DST`, `COQUE_3D`,
      `POU_D_E`, `3D_DIAG`.
   4. CalculiX: R, I and H variants, S4R/S8R, B31R/B32R.
   5. Elmer and FEniCSx: element order and p-elements, mixed spaces.
   6. PyNite: plate and member types.
   7. The CFD solvers: cell types are information only; there is nothing to choose.
   8. Large deflection on Code_Aster (point 6), with the beam and shell forms from step 3.

   Each type is claimed once a benchmark against a closed form passes: a bending-dominated
   cantilever (C3D8 locking against C3D8I), a nearly incompressible block, and a thick
   plate (DKT against DST).
6. **Large deflection on Code_Aster, done properly** (asked 2026-11-11; today
   `geometric_nonlinearity` writes `GREEN_LAGRANGE` for every elastic group and
   `GDEF_LOG` for yielding ones, nothing more):
   - **Follower pressure:** `TYPE_CHARGE='SUIV'` on pressure under large deflection, as
     CalculiX's `*DLOAD` follows the face under NLGEOM. Checked against CalculiX on a
     pressure-loaded strip, since today the two disagree once rotations are large.
   - **`DEFORMATION` per element group, from the element library:**
     - solids `GREEN_LAGRANGE`, or `GROT_GDEP` where the cells take it;
     - beams as `POU_D_TGM` with `GROT_GDEP`;
     - shells `DKT`/`COQUE_3D` with `GROT_GDEP`;
     - point elements (springs, pretension, gaps) `PETIT`.

     Anything Code_Aster can't take is refused before the run, never left to its keyword
     check.
   - **Transient large deflection:** `DYNA_NON_LINE` with the same per-group choice,
     where today it is refused.
   - **Preloaded buckling** from a large-deflection static state, as CalculiX does.
   - **A warning** when the solved strains pass about 5 % on an elastic material, which
     needs a hyperelastic model, not Green–Lagrange with plain elasticity.
   - **Benchmarks:**
     - the elastica, a cantilever under a tip load to large rotation, against the
       closed form;
     - a follower-pressure ring;
     - plasticity with large deflection on both solvers.
7. **What the GUI shows.** A body's Properties gain Element formulation (a dropdown of the
   neutral choices the study's solvers take), with "Solver types…" for the override. The
   mesh view's legend names the elements each solver will write.

## Consequences

- Users and the agent see, for any body, which element each solver will use and what
  else exists, in words.
- Run keys gain the choice. Defaults are left out of dumps, so no run moves.
- The library is a lot of data to keep right. The test catches inconsistencies, and the
  manuals' citations let each row be checked.
- Some solvers' element sets are large (Code_Aster has hundreds of modélisation and
  cell pairs). The listing distils them to the ones a structural, thermal or EM user would
  pick, and a row says where the full list is.

## Alternatives considered

- **Native names only, per solver:** expert-friendly but not intent-level, and a
  shoot-out couldn't ask every solver for "reduced integration" at once.
- **Pass-through only (expert settings):** element types change the deck's structure
  (element cards, AFFE_MODELE), which pass-through deliberately can't touch.
- **Choosing on the mesh recipe:** the mesh decides shapes and order. The formulation is
  the solver's, and differs per solver on one mesh.
