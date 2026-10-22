# 0023. Full access to each solver's settings: typed, with an expert pass-through

- Status: Proposed
- Date: 2026-10-22

## Context

Studies are solver-neutral (ADR 0006): a step has a type, physics and settings such as `time_step`, `time_scheme` and `modes`, and features may carry `extensions` per solver. Each adapter chooses the solver's own settings (linear solver, tolerances, iterations, output requests), and those choices are fixed in code.

Experienced users need to reach them, for example:

| Solver | Settings experts reach for |
| --- | --- |
| CalculiX | solver choice (SPOOLES, PARDISO), increment control |
| Elmer | linear system method, preconditioner, nonlinear iterations |
| Code_Aster | `SOLVEUR` (MUMPS, PETSc), `NEWTON` options, `CONVERGENCE` |
| FEniCSx | PETSc options |
| Code_Saturne | numerical schemes, relaxation, iteration counts |
| OpenFOAM | `fvSchemes`, `fvSolution`, `controlDict` |

Shoot-outs (ADR 0011) need settings to be comparable, and run keys need to change when they do. Code_Aster's and Code_Saturne's keywords are French, and the GUI must show English (the translation-layer memory).

## Decision (proposed)

1. **Each adapter publishes a settings schema**: `Adapter.settings_schema(step_type, physics)`. It lists every setting it supports, typed, each with:
   - its key, an English label, the solver's own name (e.g. `RESI_GLOB_RELA` shown as "Relative residual"), a unit and a default;
   - allowed values, and a one-line description.

   The adapter writes exactly these settings. The defaults are today's hard-coded choices, so existing results don't move.
2. **Settings live on the study, per solver**: `Study.solver_settings: {solver: {key: value}}`. A shoot-out study holds one block per solver. The run key covers the block of the solver that runs. The GUI shows a Solver settings panel for the study's solver, generated from the schema (readable names, dropdowns, units), with "restore defaults" and links to the solver's manual.
3. **The expert pass-through is explicit and validated.** For what the schema doesn't cover, each adapter defines one structured way in:

   | Solver | Pass-through |
   | --- | --- |
   | CalculiX | extra cards after a step's procedure card |
   | Elmer | keyword overrides per SIF section |
   | Code_Aster | keyword overrides per command (`STAT_NON_LINE.NEWTON.REAC_ITER`) |
   | FEniCSx | PETSc options |
   | Code_Saturne | XML paths in `setup.xml` |
   | OpenFOAM | dictionary paths (`system/fvSolution/solvers/p/tolerance`), applied with `foamDictionary` |

   Pass-through entries are stored like settings and shown in their own "Expert" section with a warning. They are recorded in the run, and the adapter refuses ones it cannot place (an unknown section, a malformed path); it never drops them.
4. **"Write the input, let me edit it" stays a separate mode.** Exporting the case, editing it and running it by hand is possible (the job directory is already a full case). Results from an edited case can be imported, but they are marked as not reproducible from the model.
5. **Shoot-outs compare settings too.** The shoot-out report lists each solver's non-default settings and pass-throughs. Mapping neutral settings to all solvers stays the default, as `time_scheme` does now.

## Consequences

- Every adapter gains a schema to keep in step with what it writes. A test checks that the schema's defaults reproduce today's input files byte for byte.
- Experts can tune without forking an adapter, and the record still says what ran.
- The GUI panel and the agent read the same schema; agents may change settings only within their autonomy.
- English labels for French keywords are curated once, in the schemas, and serve the GUI and the agent alike.

## Open questions

- Should settings shared by several solvers (linear solver family, tolerances) be lifted into neutral study settings, or stay per solver?
- How much of OpenFOAM's and Code_Saturne's large option space should be typed in the first schema, versus left to the pass-through?
- Should a project be able to save settings presets ("robust contact", "fast transient") to reuse across studies?

## Alternatives considered

- Pass-through only: complete on day one, but unreadable, and gives no validation or GUI.
- Typed settings only: safe, but always behind what solvers offer.
- Raw input files as the source of truth: what each solver's own GUI does; it breaks the solver-neutral model and shoot-outs.
