# 0028. Buckling: a linear eigenvalue step, with or without a preload

- Status: Proposed
- Date: 2026-10-22

## Context

The maintainer asked whether CalculiX and Code_Aster include linear buckling and preloaded buckling (2026-10-22). Both solvers do; Puzzle doesn't offer it yet.

- **CalculiX:** a `*BUCKLE` step gives buckling load factors and mode shapes for the load in that step. A `*STATIC` step before it supplies the preload: its stresses enter the geometric stiffness, and with NLGEOM so does its deformed shape. Puzzle's CalculiX prestress transfer already replays a static study as the first step of the deck.
- **Code_Aster:** `MECA_STATIQUE` gives the stresses, `CALC_MATR_ELEM(OPTION='RIGI_GEOM')` the geometric stiffness, and `CALC_MODES(TYPE_RESU='MODE_FLAMB')` the critical load factors and modes. A preload is a second geometric stiffness added to the stiffness matrix. `STAT_NON_LINE` with `PILOTAGE` (arc-length) handles nonlinear buckling.
- **Elmer and FEniCSx:** neither has a ready step. FEniCSx could assemble the geometric stiffness form and solve the generalised eigenproblem with SLEPc, as its modal step does.
- In Ansys terms, the buckling load factor multiplies the load of the buckling step only; a preload (a "dead" load, e.g. a bolt pretension or self weight) is held fixed.

## Decision

1. **A `buckling` solve step.** Its settings:
   - **modes:** how many buckling modes to find, default 4;
   - **shift:** an optional value to search near (for load factors far from 1, or negative);
   - **preload:** off, or a static study on the same component whose state is held fixed.

   The step's own loads are the ones scaled by the factor.
2. **Per solver:**
   - **CalculiX:** a `*BUCKLE` step after a `*STATIC` preload step, the replayed preload study when one is given. The preload step's results are dropped, as for prestress.
   - **Code_Aster:**
     - the matrices: K + K_G(preload) as `MATR_RIGI`, and K_G(step load) as `MATR_RIGI_GEOM`;
     - the solve: `CALC_MODES(TYPE_RESU='MODE_FLAMB')`.
   - **Elmer and FEniCSx:** refused until implemented and benchmarked. FEniCSx is a candidate: the geometric stiffness in UFL, then SLEPc.
3. **Results.**
   - Each mode is a result step whose value is the load factor (as a modal step's is its frequency) and whose field is the normalised mode shape.
   - The tree and results bar label it "Buckling mode 1: load factor 3.42".
   - A negative factor is labelled "(load reversed)".
   - The critical load (factor × applied load) is given where the load is a single force.
4. **Benchmarks.**
   - **Euler column:** a slender solid bar, pinned and then fixed-free, with P_cr = π²EI/(KL)² within 2% (the solid mesh is stiffer than Euler-Bernoulli theory as the bar gets stockier).
   - **The same column with a compressive preload P0:** the factor λ on the extra load P1 satisfies P0 + λP1 = P_cr.
   - **A shoot-out:** CalculiX and Code_Aster agree on the factors.
5. **Later.**
   - **Nonlinear buckling:** CalculiX `*STATIC, RIKS` and Code_Aster `PILOTAGE`, with an imperfection seeded from a buckling mode shape scaled to a given amplitude.
   - **Shells and beams:** buckling of beam and shell models, once ADR 0027 brings those elements.

## Consequences

- Stability checks of columns, struts and thin parts are one more step type, with preloads handled as in Ansys.
- Load factors are easy to misread. The labels say what multiplies what, and the tree shows the preload study by name.
- Linear buckling overestimates the capacity of imperfection-sensitive shells. The documentation says so, and nonlinear buckling with imperfections is the follow-up.
- Solid-element buckling of slender members needs fine meshes. Beams (ADR 0027) make it cheap.

## Alternatives considered

- Buckling as a setting of the modal step: the solvers treat it as a separate analysis, and its results mean something different (load factors, not frequencies).
- Nonlinear buckling only: more general, but slower, harder to set up, and not what a first check needs.
