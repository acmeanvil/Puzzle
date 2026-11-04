# 0066. Mesh convergence, error estimates and adaptive refinement

- Status: Accepted (2026-11-03)
- Date: 2026-11-03

## Context

The Grasshopper review (ADR 0064A, row G3) accepted mesh sizes from a table:
refining where the last run's results say the mesh is too coarse. Several things
behind that are missing today:

- **No mesh convergence check.** ADR 0063 calls it "the honest answer" to mesh noise,
  and the Advisor names it, but nothing runs a study on a sequence of meshes and says
  whether its answer has converged.
- **No error estimate.** A result is shown without any measure of how far the mesh is
  from converged.
- **Sizes are only typed** (ADR 0019): a global size, sizes on selections, refinement
  near a selection, boxes and balls, boundary layers. They are Gmsh fields
  min-combined into a background field (`mesh/generate.py`). No size can come from
  data or a result.

What exists to build on:

- Gmsh can take a background size from a post-processing view (a `PostView` field: a
  size per node of an earlier mesh), combined with today's fields.
- The store keeps every run's mesh (meshes are stored once by content hash) and its
  results.
- Sweeps and optimisations (ADR 0018, 0063) already run one study at many points, each
  a stored, cached run.
- Derived results (ADR 0025) compute fields on demand from stored runs.

## Decision

1. **A mesh convergence study** (the first phase), two ways in (review answer 1):
   - a study's own `convergence` setting, for the quick check;
   - a **mesh study** object (kind `mesh_study`, under Studies, as an optimisation is)
     naming the study, the levels and the quantities, for full control and for
     several studies on one component.

   Either way it runs the study on a sequence of meshes:
   - Each level scales the recipe's sizes by a ratio (default 1.5, at least three
     levels).
   - Each level is a stored run, labelled as a point is ("mesh: 2.5 mm, 41 230
     nodes").
   - The quantities of interest are operators or result tools (ADR 0062, 0032), read
     at each level: a peak stress, a deflection, a reaction, a frequency, a pressure
     drop.
   - From the last three levels, Richardson extrapolation gives the observed order and
     the extrapolated value. Roache's grid convergence index (GCI, a safety factor of
     1.25) gives the uncertainty: "tip deflection 2.041 mm ± 0.3 %, converged".
   - A level whose order falls outside a sensible range, or whose results oscillate, is
     reported as not in the asymptotic range, not as converged.
   - The Advisor's Mesh step asks for it before results are trusted ("not checked",
     "converged within 0.3 %", "not converged: refine").
   - It is shown as a table and a plot of each quantity against elements, with the
     extrapolated value as a line.

2. **Error estimates, solver-neutral**, computed in Puzzle from the stored results,
   whichever solver ran them:
   - **Structural: Zienkiewicz–Zhu recovery.** Each element's stress comes from the
     stored displacement's gradient and the element's material (linear and quadratic
     tets and the other element types Puzzle reads). The recovered stress is the nodal
     average. Their difference in the energy norm gives a per-element error and a
     global relative error, "the energy-norm error is 4.2 %".
   - **Heat**: the same, on the flux from the temperature's gradient.
   - **Other fields (electromagnetics, CFD)**: a gradient-jump indicator on a chosen
     field, its jump across element faces. It gives relative, not absolute, error.
   - The estimate is a derived result field, "error indicator", drawn like any result,
     and a number on the run (the global error). The Advisor reads it: "error 12 % at
     the fillet: refine there".

3. **Sizes from data** (row G3):
   - A mesh recipe's control `size_field` takes a table (ADR 0064: x, y, z and size,
     or ids on an earlier mesh with a size) or a run's error indicator.
   - It becomes a Gmsh `PostView` background field, min-combined with the recipe's
     other controls, so typed controls still hold.
   - Its content hash covers the source (the table's value, or the source run's key),
     as a transfer's does (ADR 0009), so the mesh is reproducible and goes out of date
     when its source changes.

4. **Adaptive refinement**: a study's `adapt` setting repeats solve → estimate →
   new sizes:
   - Each element's new size is the old one × (target error / element error)^(1/p),
     where p is the element order, clamped per pass (at most 4× finer or 2× coarser)
     and between the recipe's minimum and maximum.
   - Each pass is a stored run with its mesh, labelled "pass 2: 63 104 nodes, error
     4.9 %".
   - It stops at a target the user picks (review answer 2): the energy-norm error, or
     a quantity of interest's change between passes, both defaulting to **2 %**. It
     also stops at a budget (passes, elements, time).
   - The passes show as optimisation points do (ADR 0063): a table, and a plot of error
     and the quantities against elements.
   - The final pass is the study's result. Earlier passes' runs are kept for review by
     default; an option deletes them when the run finishes, keeping their table (review
     answer 5).

5. **Solvers and meshers**:
   - **Solvers**: every finite-element adapter, since the estimate is Puzzle's own.
     CFD uses the gradient indicator on velocity or a chosen field; boundary layers
     (ADR 0019) are kept as their own control.
   - **Gmsh**: takes the size field directly.
   - **Netgen**: supported as an adjunct to Gmsh (review answer 3), through its
     mesh-size file of points with sizes; coarser than Gmsh's field, and said so.
   - **Anisotropic adaptation** (review answer 4: now): elements stretched along a
     metric from the field's Hessian, by MMG (`mmg3d`, LGPL as a separate program,
     conda-forge). Gmsh's named groups are carried through by MMG's references, and
     checked after each pass. It is for CFD (boundary and shear layers) and thin
     features.

6. **Twins**:
   - The CLI: `puzzle convergence PROJECT STUDY` and `puzzle adapt PROJECT STUDY`, each
     with `--levels`/`--target`/`--budget`.
   - MCP `mesh_convergence`, `mesh_adapt` (at "act", as runs are) and `error_estimate`.
   - The outline says each study's convergence state.
   - A skill `mesh-convergence`: when to run it, reading the GCI, what an oscillating
     or super-convergent sequence means, and that stress at a re-entrant corner never
     converges.

7. **Tests**:
   - A cantilever's tip deflection converging to Euler–Bernoulli plus shear, with the
     observed order near 2 for quadratic tets.
   - The ZZ estimate falling at the expected rate under uniform refinement.
   - A plate with a hole (Kirsch, Kt = 3) adapted to 2 % error in fewer elements than
     uniform refinement needs.
   - An L-shaped corner's error refusing to converge, reported as a singularity.

8. **Phases**:
   1. Convergence studies (the study setting and the mesh study object) with GCI, the
      table and plot, the Advisor's step.
   2. Error estimates (ZZ structural and heat, gradient jumps), the indicator field.
   3. Sizes from data (tables and runs) and adaptive refinement on Gmsh and Netgen,
      with the 2 % default target and the keep-passes option.
   4. Anisotropic adaptation with MMG, and CFD indicators.

## Consequences

- Results come with a stated discretisation error, and the Advisor can say whether a
  mesh is fine enough instead of leaving it to judgement.
- Adaptive runs store several meshes and runs per study, and the store grows with
  them; the passes are deletable like any runs.
- A solver-neutral estimate needs each element type's shape-function gradients in
  Puzzle (not only the solvers'). This is work per element family, but it is checked
  against closed forms like everything else.
- Singularities (re-entrant corners, point loads) make convergence studies say "not
  converged" forever. The skill and the Advisor must explain that this is the model,
  not the mesh.

## Alternatives considered

- **Solvers' own estimators** (Code_Aster's `ERME_ELEM`, Elmer's adaptive meshing):
  real, but different on each solver, and missing on CalculiX and FEniCSx. Puzzle's
  own estimate compares like with like across a shoot-out.
- **Uniform refinement only**: simple, and enough for the convergence check (item
  1). It wastes elements away from the stress raisers, which is what adaptive
  refinement fixes.
- **MMG remeshing** (LGPL, conda-forge): strong for anisotropic metrics, but it
  remeshes outside Gmsh's named groups. That is question 4, not the default.

## Review questions

1. A convergence study as a study setting (`convergence`, as proposed) or its own
   object (like an optimisation)?
2. The default target for adaptive refinement: 5 % energy-norm error, or a quantity of
   interest's change (say 1 %)?
3. Netgen: coarser support through its size file, or adaptive refinement on Gmsh only?
4. Anisotropic adaptation (MMG, metric from the Hessian) for CFD and thin features:
   now, later or never?
5. Should passes before the final one be kept as runs, or deleted when the adaptive
   run finishes (keeping only their table)?

## Review (2026-11-03)

The maintainer's answers:

1. Both: a study's `convergence` setting and a mesh study object (item 1).
2. The target is the user's choice (energy-norm error or a quantity's change),
   defaulting to 2 % (item 4).
3. Netgen supported, as an adjunct to Gmsh (item 5).
4. Anisotropic adaptation now, with MMG (item 5, phase 4).
5. Earlier passes: kept for review by default, deleting them an option (item 4).

## Phase 1 (built, 2026-11-04)

- `puzzle/convergence.py`: `levels` plans the meshes (each recipe size divided by
  ratio^k; transfinite counts multiplied; Gmsh's `Mesh.MeshSizeFactor` when the recipe
  has no global size; Netgen refused without one). `run` solves each level as the
  study's own stored run (labels `mesh: level 2: 4 mm`, cached by run key, so a check
  resumes) or, `read_only`, reads back what ran. `verdicts` applies Roache's GCI to the
  three finest: refinement ratios from the element counts, observed order by
  fixed-point iteration, extrapolated value, GCI with safety 1.25, and states
  converged, not converged, oscillating, not in the asymptotic range (order outside
  0.5 to 6) and too few levels. `settings`, `report` and `lines` are shared by the
  clients.
- Schema: `Convergence` (levels, ratio, target, quantities as `DesignOutput`s) on
  `Study.convergence` (left out of the study's hash), and `MeshStudy` (kind
  `mesh_study`) for several checks of one study. The default quantity is the largest
  value of the study's first result.
- The Advisor's Mesh convergence step reads the verdicts, never solves: "1 of 3 meshes
  run", "converged within 0.03 % (GCI)", or "needed" with Check mesh convergence….
- CLI `puzzle convergence PROJECT STUDY [--levels] [--ratio] [--target] [--points]
  [--json]` (exit 0 only when converged); MCP `mesh_convergence` (solving needs "act");
  GUI: a study's Check mesh convergence… (one form; the settings kept on the study as
  one Edit), run in the background, and the Convergence panel (`gui/convergence_panel.py`:
  the meshes' table in display units to six digits, each quantity plotted against
  elements with its extrapolated line, the verdicts, Run the rest). Skill
  `mesh-convergence`. The outline gives each check's settings and the meshes run;
  the schematic draws a mesh study right of its study ("drives"), with the meshes run
  and the verdict.
- Benchmark `tests/integration/test_mesh_convergence.py` (CalculiX): the 200 × 20 × 6
  mm steel cantilever on second-order tets from 8 mm converges with observed order
  4.1 and GCI 0.03 %, extrapolated 3.5013 mm against Timoshenko's 3.5297 mm (−0.8 %,
  the wide beam's Poisson stiffening). First-order tets from 4 mm lock and are
  reported not converged (GCI 26 %), their extrapolation nearer the answer than the
  finest mesh.

## Phase 2 (built, 2026-11-04)

- `puzzle/post/estimates.py`, solver-neutral, from the stored nodal field, the mesh and
  the component's materials (isotropic E and ν, or k; a material's single values, not
  its temperature tables):
  - Zienkiewicz–Zhu: each element's stress (Voigt) or flux −k∇T from the field's
    gradient at its nodes, recovered by a volume-weighted nodal average per material
    (an interface's jump is not an error), and the difference in the energy norm per
    element. The rule is 4-point Gauss on linear tets, and the 4-point rule on the
    eight sub-tetrahedra (32 points) on quadratic ones. The model's
    η = √(Σe² / (Σe² + ΣU²)).
  - The gradient jump across each interior face, ½ Σ h_F A_F |[σ n]|² / E (heat: / k),
    vectorised over the faces; boundary faces are left out (their loads are not
    known). On any nodal field (`field` mode: each component's gradient, unit
    weight), as the relative indicator for other physics.
  - `Estimate.ratio(target)` is Zienkiewicz–Zhu's refinement ratio, and
    `sizes(target)` gives h ξ^(−1/p) (phase 3 meshes from it). `fields` gives element
    fields `error_estimate`, `error_ratio`, `error_jump` and the global
    `error_relative`. `of_run` reads a run.
  - Linear and quadratic tets; other element types are left out with a note.
  - `hot_spots` (the largest errors with their centres and the selections they
    touch), `summary` and `words` ("error 4.2 % (energy norm), largest at
    B/Fillet: refine there").
- Shown: a derived result of method `error_estimate` (`source`, `error_target`, a
  `quantity` for another field; ADR 0025) draws the element fields in the results
  view ("Error estimate (energy norm)", "Refinement ratio (above 1: refine)",
  "Gradient jump indicator"), with the relative error as its note. The GUI makes it
  from a study's or run's New error estimate… (one form; its fields from the run). The
  Advisor's Results step says the newest run's estimate (cached per run; left out
  past 100 000 elements). CLI `puzzle error-estimate`, MCP `error_estimate`; the
  skill `mesh-convergence` covers both.
- Verified (`tests/test_estimates.py`, on a cube of Kuhn tets with exact fields):
  - zero error for linear fields;
  - effectivity 1.01 against the exact energy-norm error of u = (x², 0, 0) on linear
    tets;
  - observed orders 1.0 (linear tets) and 1.96 (quadratic tets);
  - no false error at a conductivity interface.

  On CalculiX (`tests/integration/test_error_estimates.py`), the cantilever's η goes
  3.17, 2.23, 2.12 % on quadratic tets (flattening at the clamped root's singular
  edges, where the largest element errors sit) and 64, 54, 43 % on linear tets
  (locking). The jump indicator reads about 4× the exact error: an indicator, not a
  measure.
