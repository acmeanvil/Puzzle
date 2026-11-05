---
link: https://www.elmerfem.org/blog/documentation/
---

# Elmer: what Puzzle has learned

Curated from Puzzle's own development notes: behaviour measured or decided while building the adapters. Read with the skills and ADRs.

## Element node order

Gmsh differs from CalculiX, Elmer and VTK for tet10 (last two edge nodes swap). Adapters own the reordering; shared face lookups live in `adapters/common.py`.

## Temperature tables per solver

CalculiX `*CONDUCTIVITY` value/temperature lines plus tight `*CONTROLS, PARAMETERS=FIELD` (its defaults stop 0.2 K short); Elmer `Variable Temperature` tables with nonlinear iterations; Code_Aster `THER_NL` (LAMBDA and enthalpy BETA functions) with `THER_NON_LINE`, steady via `ETAT_INIT=_F(STAT='OUI')`; FEniCSx a closed-form piecewise-linear UFL expression and Picard iteration. Code_Aster is written with `PARM_THETA` (1.0 by default, not its own 0.57). With temperature tables Code_Aster steps the enthalpy, the others rho cp(T); they differ by O(dt) (0.7 K at dt = 5 s in the shoot-out, 0.15 K at 1 s), Code_Aster being closer to the converged answer.

## Time schemes at t = 0

backward Euler agrees across all four solvers to 3 decimals and BDF2 between Elmer and FEniCSx. For theta < 1 the starting state matters: Code_Aster and FEniCSx start from the given initial field, while Elmer appears to apply the boundary values to the starting state first, and its Crank-Nicolson damps the initial jump (plain CN from a stepped start oscillates: 18.7 K in a FEniCSx experiment). So theta-scheme results differ between Elmer and the other two after a sudden load; prefer backward Euler or BDF2 there.

## Elmer output

lands in the mesh directory (`mesh/case_t0001.vtu`). Points come in Elmer node order (no point ids); `GeometryIds` there is cell data. Eigenvalues (omega^2) are parsed from the solver log.

## Native solver runs

must look activated: `NativeRuntime.environment()` puts the conda/pixi env on PATH and sets `CONDA_PREFIX` (Windows otherwise fails with 0xC0000135). Elmer from conda also needs `ELMER_HOME` = env prefix.

## Elmer image

is built without MUMPS (Debian trixie lacks ParMETIS).

## Elmer magnetics

the A-V edge system stalls under GCR/BiCGStab here, so it is solved directly (Elmer then tree-gauges it). Unlisted SIF keywords such as `Use Magnetic Norm` need an explicit type (`Logical True`), or parsing fails. Lowest-order edge elements give B constant per element; nodal B is an average, so check it away from material interfaces and use the stored energy for the whole field.

## Derived coordinate systems (ADR 0049 step 3)

a system from a face or edge, attached to a part, or relative to another is resolved by `puzzle.axes.absolute(project, cs, root)` (`run_study` hands adapters `resolved_project`); never read such a system's `centre()`/`orientation()` unresolved. Material directions: `MaterialAssignment.axes` (CalculiX `*ORIENTATION`; Code_Aster `MASSIF` ANGL_REP or ANGL_AXE, its cylindrical L T N being axial, tangential, radial, so `comm.CYLINDRICAL` reorders the constants; Elmer and FEniCSx refuse orthotropic and anisotropic models).
