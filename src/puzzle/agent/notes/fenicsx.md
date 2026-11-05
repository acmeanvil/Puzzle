---
link: https://docs.fenicsproject.org/
---

# FEniCSx: what Puzzle has learned

Curated from Puzzle's own development notes: behaviour measured or decided while building the adapters. Read with the skills and ADRs.

## Custom PDE weak forms

are evaluated with `eval` and no builtins in `study.py` (`UFL_NAMES` plus the unknown, `v_<u>`, `<u>_n`, `dt`, coefficients, `x`, `dx`/`ds` taking selection names, `prop(...)`). That limits mistakes, not a determined attacker, so equations (and script operators) run only in a trusted project (`puzzle.trust`, ADR 0062 phase 4). dolfinx wants each cell tagged once, so the case mesh tags volumes by material selections (or parts) only.

## FEniCSx `study.py`

runs inside the FEniCSx env and must not import puzzle. dolfinx 0.11 `LinearProblem` needs `petsc_options_prefix`; MUMPS is used only if PETSc has it. Windows dolfinx has no PETSc, so `study.py` falls back to SciPy (SuperLU, ARPACK; CG for the stress projection); `PUZZLE_FENICSX_BACKEND=scipy` forces that path for testing (LE1 ~54 s vs ~10 s with PETSc).

## Temperature tables per solver

CalculiX `*CONDUCTIVITY` value/temperature lines plus tight `*CONTROLS, PARAMETERS=FIELD` (its defaults stop 0.2 K short); Elmer `Variable Temperature` tables with nonlinear iterations; Code_Aster `THER_NL` (LAMBDA and enthalpy BETA functions) with `THER_NON_LINE`, steady via `ETAT_INIT=_F(STAT='OUI')`; FEniCSx a closed-form piecewise-linear UFL expression and Picard iteration. Code_Aster is written with `PARM_THETA` (1.0 by default, not its own 0.57). With temperature tables Code_Aster steps the enthalpy, the others rho cp(T); they differ by O(dt) (0.7 K at dt = 5 s in the shoot-out, 0.15 K at 1 s), Code_Aster being closer to the converged answer.

## Time schemes at t = 0

backward Euler agrees across all four solvers to 3 decimals and BDF2 between Elmer and FEniCSx. For theta < 1 the starting state matters: Code_Aster and FEniCSx start from the given initial field, while Elmer appears to apply the boundary values to the starting state first, and its Crank-Nicolson damps the initial jump (plain CN from a stepped start oscillates: 18.7 K in a FEniCSx experiment). So theta-scheme results differ between Elmer and the other two after a sudden load; prefer backward Euler or BDF2 there.

## Derived coordinate systems (ADR 0049 step 3)

a system from a face or edge, attached to a part, or relative to another is resolved by `puzzle.axes.absolute(project, cs, root)` (`run_study` hands adapters `resolved_project`); never read such a system's `centre()`/`orientation()` unresolved. Material directions: `MaterialAssignment.axes` (CalculiX `*ORIENTATION`; Code_Aster `MASSIF` ANGL_REP or ANGL_AXE, its cylindrical L T N being axial, tangential, radial, so `comm.CYLINDRICAL` reorders the constants; Elmer and FEniCSx refuse orthotropic and anisotropic models).
