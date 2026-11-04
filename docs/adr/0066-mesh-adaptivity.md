# 0066. Mesh convergence, error estimates and adaptive refinement

- Status: Proposed
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

1. **A mesh convergence study** (the first phase): a study's `convergence` setting
   runs it on a sequence of meshes:
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
   - It stops at the target error (default 5 % in the energy norm), when the quantities
     of interest change less than a tolerance, or at a budget (passes, elements, time).
   - The passes show as optimisation points do (ADR 0063): a table, and a plot of error
     and the quantities against elements.
   - The final pass is the study's result. Earlier passes' runs stay until deleted, as
     any runs do.

5. **Solvers and meshers**:
   - **Solvers**: every finite-element adapter, since the estimate is Puzzle's own.
     CFD uses the gradient indicator on velocity or a chosen field; boundary layers
     (ADR 0019) are kept as their own control.
   - **Gmsh**: takes the size field directly.
   - **Netgen**: takes a mesh-size file of points with sizes, which is coarser (review
     question 3).
   - **Anisotropic adaptation** (stretched elements following a metric from the
     field's Hessian, as MMG does): left for review question 4.

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
   1. The convergence study with GCI, its table and plot, the Advisor's step.
   2. Error estimates (ZZ structural and heat, gradient jumps), the indicator field.
   3. Sizes from data (tables and runs) and adaptive refinement on Gmsh.
   4. Netgen's size file, CFD indicators, and the review's answer on anisotropy.

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
