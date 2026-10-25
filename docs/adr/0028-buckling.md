# 0028. Buckling: a linear eigenvalue step, with or without a preload

- Status: Accepted (2026-10-24); built on CalculiX and Code_Aster
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

## Built (2026-10-24)

- **One change to the decision: the preload is a static step in the same study,** not another study. The buckling step's `preload` setting (default off) holds the state of the static step just before it; each step's loads are its own through `Feature.steps` (ADR 0037), so the preload load sits in step 1 and the load to scale in step 2. That keeps both loads, and their difference, in one study.
- **`buckling` step type** (`SolveStep.type`), settings `modes` (default 4), `preload`, and `shift` (Code_Aster only: CalculiX's `*BUCKLE` takes none, so it refuses one). New steps start with `{"modes": 4, "preload": false}` (`catalog.STEP_DEFAULTS`), so Properties shows them.
- **CalculiX:** `*BUCKLE`. A plain `*BUCKLE` after `*STATIC` ignored the static stress, so with a preload the static step is written with `NLGEOM` and the buckling step as `*STEP, PERTURBATION` (as the prestress replay does). The `.frd` gives each buckling step a static block (value 0) first, then one block per mode without a mode record; the reader drops the first and numbers the rest (a `.buckling` marker lists the deck's buckling steps).
- **Code_Aster:** a static solve under the step's loads, `CALC_MATR_ELEM(OPTION='RIGI_GEOM')` from its `SIEF_ELGA`, and `CALC_MODES(TYPE_RESU='MODE_FLAMB')` with K (plus K_G of the preload, `COMB_MATR_ASSE`) against that K_G. `CHAR_CRIT` is the load factor itself. Code_Aster runs one job per step, so `run_study` hands a preloaded buckling step its static step too (`solve._preloaded`), with each feature's steps renumbered for that job. It takes plain supports and loads in a buckling step so far.
- **Elmer and FEniCSx:** refused (no `buckling` in their capabilities).
- **Results:** one result step per mode, value = load factor, kind `buckling`; `StepResult.label()` gives "Buckling mode 1: load factor 3.42", "(load reversed)" when negative, used by the view's title, plots, the derived-result dialog and `puzzle runs`.
- **Benchmarks** (`tests/integration/test_buckling.py`, a 10 × 10 × 300 mm column fixed at its base, 2.5 mm quadratic tets):

  | Check | CalculiX | Code_Aster | Theory |
  | --- | --- | --- | --- |
  | Euler, fixed-free | +0.085 % | +0.085 % | π²EI/(2L)² = 4797.7 N |
  | Preload P0 = 2400 N, factor on 1 N | +0.15 % | +0.17 % | P_cr − P0 |
  | Shoot-out, four modes | agree to 0.01 % | | |

  CalculiX's preloaded factor is 0.02 % below Code_Aster's: its preload step is geometrically nonlinear.
- **Not yet:** the critical load (factor × applied load) in the label for a single force; a pinned column (pins on solid ends need a remote-displacement hinge); nonlinear buckling with an imperfection (point 5).

## Consequences

- Stability checks of columns, struts and thin parts are one more step type, with preloads handled as in Ansys.
- Load factors are easy to misread. The labels say what multiplies what, and the tree shows the preload study by name.
- Linear buckling overestimates the capacity of imperfection-sensitive shells. The documentation says so, and nonlinear buckling with imperfections is the follow-up.
- Solid-element buckling of slender members needs fine meshes. Beams (ADR 0027) make it cheap.

## Alternatives considered

- Buckling as a setting of the modal step: the solvers treat it as a separate analysis, and its results mean something different (load factors, not frequencies).
- Nonlinear buckling only: more general, but slower, harder to set up, and not what a first check needs.

## Review notes (2026-10-23)

The interface review (item 10) found buckling in both reference tools: a buckling step in PrePoMax, and CALC_MODES in Code_Aster. A PrePoMax user found its workflow for an imperfection taken from a buckling mode shape easier than Abaqus/CAE's. The follow-up in point 5 should match that: one action, "Use mode N as an imperfection", that writes a geometry-modifying transfer.
