# 0082. Element types: a solver-neutral choice per body, each solver's own types, and a library of them

- Status: Accepted; steps 1 to 3 built in part (2026-11-11): the element library, its listing, and the choice per body for four formulations
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

## Built: steps 1 and 2 (2026-11-11)

- **The library** (`puzzle/elements/`): a module per solver with its rows (`Element`:
  native, family, shapes, formulation, studies, use, avoid, written, reference,
  physics). The counts:
  - CalculiX: 27 (C3D4 to C3D20R, CPS/CPE/CAX/DCAX, S3/S4/S6/S8/S8R, B31/B32/B32R, U1,
    T3D, springs, GAPUNI, MASS, DASHPOTA);
  - Code_Aster: 23 modélisations (3D, 3D_SI, 3D_INCO_UP(G), C_PLAN, D_PLAN, AXIS,
    AXIS_INCO_UPG, DKT, DST, Q4G, COQUE_3D, POU_D_T/E/TG/TGM, BARRE, CABLE,
    DIS_T/DIS_TR, thermal 3D, 3D_DIAG, PLAN/AXIS);
  - Elmer: 8, FEniCSx: 7, PyNite: 4, and the finite-volume cells of Code_Saturne and
    OpenFOAM.

  The vocabularies are `FAMILIES`, `FORMULATIONS`, `STUDIES` and `SHAPES`. References
  name the manual's section, not sub-sections, where those weren't checked.
- **The listing.** `listing(solver, family, study)` gives Markdown: per family, a table
  of formulation and order (linear, quadratic) with each solver's element in its column,
  ★ where Puzzle writes it, then each element's use, what not to use it for, its
  studies and its reference. It is reached in several places:
  - `docs/elements.md` (generated; a test fails when it is stale);
  - Help → Element library (`gui/element_library.py`, filtered by solver, family and
    study);
  - `puzzle elements [--solver] [--family] [--study]`;
  - MCP `elements_list`.
- **Checks** (`tests/test_elements.py`): the vocabularies; every CalculiX ★ named by
  `inp`/`spring_beams`; every Code_Aster ★ in `comm.py`, and no unmarked one there as a
  MODELISATION; the listing's grouping and filters; the docs current; the MCP tool.
- **Next:** step 3, the choice per body on Code_Aster first (decision 3), then the rest
  of the order of work, and large deflection on Code_Aster (decision 6). The knowledge
  pack's index of `docs/elements.md` comes with ADR 0083's help pages.

## Built: step 3 in part (2026-11-11)

- **`Component.elements`** holds the choices (`ElementChoice`: a body selection or part,
  a formulation, the expert's `solver_types`); it is left out of hashes when empty.
- **`puzzle/element_choice.py`**:
  - `native` and `lookup` map a choice to the solver's written element for the block's
    family and cell, through the element library itself, so a ★ row is what gets
    written;
  - `check` refuses, in every adapter's `check`, a formulation the solver writes nothing
    for.
- **CalculiX** sets the block's `*ELEMENT` type from the choice.
- **Code_Aster** puts an AFFE_MODELE occurrence last per chosen body (a later one
  overrides), its group added to the MED. A body mixing cells that take different
  modelisations is refused. Mixed u–p goes through STAT_NON_LINE, since MECA_STATIQUE's
  results keep no behaviour for its reactions.
- **Written and benchmarked** (`tests/integration/test_element_choice.py`):

  | Element | Case | Result |
  | --- | --- | --- |
  | CalculiX C3D8I | a cantilever two linear bricks deep | 0.9934 of Timoshenko's deflection, against C3D8's 0.896 |
  | Code_Aster 3D_INCO_UPG | ν = 0.4999 on quadratic tetrahedra | 0.988 |
  | Code_Aster POU_D_E | Euler–Bernoulli cantilever | PL³/3EI to every digit |
  | Code_Aster 3D_DIAG | 100 °C on a 20 °C bar | no node below 20 °C |
- **Unmarked until they can be benchmarked:** C3D8R (hourglassing needs its own
  check), DST and S8R (a thick-plate benchmark). C3D20R and 3D_SI came with second-order
  bricks (below).
- **GUI and agent.** Model → Components → Element formulation… (one form), MCP
  `element_choose`, skill `element-types`. `tests/test_elements.py` checks the lookups,
  the refusals and the unchanged run keys.

## Built: second-order bricks, 20 and 27 nodes (2026-11-11)

- **The mesher.** `MeshRecipe.hexahedron_nodes` (20 or 27; the default 20 is left out of
  hashes) sets Gmsh's `Mesh.SecondOrderIncomplete` for a second-order hexahedral mesh:
  serendipity bricks with quad8 faces, or Lagrange bricks with quad9 faces. The refusal
  from the first hexahedral meshing (7f83b09) is gone.
- **Each solver:**
  - CalculiX: C3D20 (and C3D20R by a choice); it has no 27-node brick, so those are
    refused.
  - Code_Aster: HEXA20 and HEXA27 through Gmsh's MED export, and 3D_SI.
  - Elmer: 820 and 827. Its node orders come from its own `elements.def`: the bottom
    ring's mid-edges, then the uprights', then the top ring's; 827's faces y−, x+, y+,
    x−, z−, z+, then the centre.
  - FEniCSx: Q2 on 27 nodes; serendipity cells are refused, since DOLFINx has none.
  - The view draws a 27-node brick by its 20 serendipity nodes (VTK 25), and quad9
    faces as VTK 28.
- **Benchmarks** (`tests/integration/test_quadratic_bricks.py`; Timoshenko's cantilever,
  structured 5 mm bricks two deep):
  - 20 nodes: 0.9951 on CalculiX, Code_Aster and Elmer alike;
  - 27 nodes: 0.9956 on Code_Aster, Elmer and FEniCSx alike;
  - reduced 20-node bricks: C3D20R and 3D_SI 0.9957;
  - the two refusals.

  So C3D20R and 3D_SI are written now. `tests/test_geometry_mesh.py` checks the bricks
  and their faces; `tests/test_vtk_grid.py` the view of a 27-node brick.

## Built: decision 6 in part, large deflection on Code_Aster (2026-11-11)

- **Follower pressure.** Under `geometric_nonlinearity`, pressures go in a charge of
  their own (`loadp`), excited with `TYPE_CHARGE='SUIV'`.
- **Point elements at small strain.** Springs, pretension and gaps (the `_Points` sets)
  take `DEFORMATION='PETIT'`, overriding the solids' Green–Lagrange.
- **Beams, beam bolts and shells under large deflection are refused** up front
  (POU_D_TGM and COQUE_3D with GROT_GDEP aren't written yet), naming CalculiX.
- **The large-strain warning** (`solve._large_strain`, after every solve, any solver):
  above 5 % principal strain with elastic materials and large deflection, a run warning
  `large_strain` (the material wants a hyperelastic model).
- **Benchmarks** (`tests/integration/test_element_choice.py`):
  - the cantilever bar pressed on its top face to a tip deflection of L/7: CalculiX and
    Code_Aster land on the same tip to five digits (−27.2247 and −27.2248 mm down,
    −2.1355 mm in);
  - the elastica (Bisshopp & Drucker 1945) at PL²/EI = 1 on a 2 mm strip: 0.3004 L
    down (0.3017) and 0.0560 L in (0.0566) on both, within 0.5 % and 1.1 %.

  `tests/test_elements.py` checks the warning.
- **Not yet:** transient large deflection (DYNA_NON_LINE; CalculiX refuses it too), a
  preloaded buckling step from a large-deflection state, and plasticity with large
  deflection benchmarked on both solvers.

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
