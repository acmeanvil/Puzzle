# 0023. Full access to each solver's settings: typed, with an expert pass-through

- Status: Accepted (2026-10-22); built for all six solvers, with presets (2026-10-25)
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
3. **The expert pass-through is explicit and validated** (the power-user level, see Decided below). For what the schema doesn't cover, each adapter defines one structured way in:

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

## Decided (2026-10-22, maintainer)

- **A common language.** Settings that solvers truly share become neutral study settings, which each adapter translates into its own terms, the way `time_scheme` works today. This is how the preprocessor "speaks" to every solver:
  - linear solver family (direct or iterative);
  - preconditioner family;
  - relative tolerance;
  - nonlinear iteration limit;
  - output frequency.

  Only what is solver-specific stays per solver.
- **Two levels, for every solver.**
  - *Standard*: the typed settings most users need, with readable names and defaults.
  - *Power user*: the solver's whole option set, typed where the solver documents it, plus the validated pass-through for the rest.

  An application setting (ADR 0024) shows the power-user level; standard is the default.
- **Presets.** A project can save settings presets ("robust contact", "fast transient") and apply them to any study. A preset is a project object: a set of neutral settings and per-solver blocks.

## Built: the framework, CalculiX and Code_Aster (2026-10-25)

- **Definitions** (`adapters/settings.py`): `SolverSetting` (key, English label, the solver's own name, kind, default, choices, unit, description, power-user only); `SHARED`, the shared settings each solver translates (`linear_solver` direct or iterative, `tolerance`, `max_iterations`, `output_every`), kept on each solve step's settings as `time_scheme` is; `resolved` refuses unknown keys and bad values.
- **Storage:** `Study.solver_settings[solver]` and `Study.expert_settings[solver]`. A run's key covers its own solver's blocks only, and only when they hold something, so existing results keep their keys and a shoot-out's other solvers stay current when one solver's settings change.
- **Adapters:** `settings_schema()`, `solver_settings(study)`, `expert_help()`, `check_expert(entries)`; `Adapter.check` reports unknown settings, bad values and pass-through it can't place. Only what is set is written, so the defaults stay each solver's own and no result moves.
- **CalculiX:** `solver` (CalculiX's choice, SPOOLES, PARDISO, iterative Cholesky, iterative scaling) as `SOLVER=` on the procedure card; shared: iterative → `ITERATIVE CHOLESKY`, tolerance → `*CONTROLS, PARAMETERS=FIELD`, iteration limit → `I_C` of `*CONTROLS, PARAMETERS=TIME INCREMENTATION`, output every n → `FREQUENCY=` on `*NODE FILE`/`*EL FILE`. Pass-through: cards at `model` (before the first step), `after_procedure` (every step) or `after_procedure:N`; `*STEP` and `*END STEP` are refused.
- **Code_Aster:** `method` (MUMPS, multifrontal, GCPC; PETSc for parallel builds), `reac_iter` (power user); shared: iterative → GCPC (this build has no PETSc: it stopped with "PETSc n'est pas installé"), tolerance → `CONVERGENCE/RESI_GLOB_RELA`, iteration limit → `ITER_GLOB_MAXI`, output every n → `ARCHIVAGE/PAS_ARCH`. Settings and pass-through are both keyword overrides on the written command file (`code_aster/expert.py`: `COMMAND.KEYWORD` or `COMMAND.FACTOR.KEYWORD`, placed in every call of the command, inside each factor of a tuple; a command the file doesn't use, a malformed path or a value that isn't Code_Aster syntax is refused).
- **GUI:** Solver settings… on a study (and Model → Solver settings…): one form with the shared settings ("Default" leaves them to the solver), each solver's own named in English with the solver's word beside it, and at the power-user level (Settings > Solvers, now live) each solver's pass-through box (`PATH = value` lines, or `[place]` headers with cards), checked before OK; one Edit, one undo.
- **Shoot-outs** list each solver's non-default settings and pass-through under the comparison table (`shootout.settings_in_use`).
- **Agents:** MCP `solver_settings(study)` lists them with current values and the pass-through help; changes go through `submit` like any edit.
- **Checks:** `tests/test_solver_settings.py` (overrides placed and refused, run keys per solver, the CalculiX deck, the shoot-out list, MCP), `tests/test_gui_solver_settings.py`, `tests/integration/test_solver_settings_runs.py` (the cantilever's tip with every linear solver: CalculiX to 1e-8, its iterative solver 2e-6; Code_Aster's three identical; a pass-through keyword reaches the command file).
- **Not yet** (at this stage): the other four solvers and presets, built next, below.

## Built: Elmer, FEniCSx, Code_Saturne, OpenFOAM and presets (2026-10-25)

- **Which shared settings each solver takes** is declared (`Capabilities.shared_settings`); a study setting one its solver can't translate is a problem in `Adapter.check`, never ignored. CalculiX, Code_Aster, Elmer and FEniCSx take all four; Code_Saturne and OpenFOAM are iterative only (`linear_solver = direct` is refused), OpenFOAM takes the tolerance as `residualControl`, and neither keeps intermediate output of a steady solve.
- **Elmer:** `linear_method` (Puzzle's choice, direct UMFPACK, BiCGStab(l), CG, GCR) and `preconditioner` (power user); the shared settings set every solver section's linear system, nonlinear tolerance and iterations, and `Output Intervals` (the output times file follows it). The magnetic A-V solve keeps its direct method. Pass-through: `Section/Keyword = value` (`Solver */...` for every solver), placed by `elmer/sif_edit.py`, replacing a keyword and its continuation lines or adding it before `End`.
- **FEniCSx:** `linear_method` (LU; CG or GMRES with algebraic multigrid; CG with Jacobi) for each field's own solve; the shared tolerance and iteration limit drive the Picard (temperature tables) and Newton (custom PDE) loops, and `output_every` thins transient series. Pass-through: PETSc options (`ksp_type = gmres`), over the method's. They reach `study.py` in `case.json`'s `settings`; on the SciPy backend (Windows) a method or PETSc option stops the run with the reason.
- **Code_Saturne:** pressure and velocity solvers and, for power users, the velocity convection scheme, through its own case model (`setSolverChoice`, `setScheme`; the choices are its own list). Pass-through: setup.xml paths (`path` for an element's text, `path@attribute`, `[@name='...']` predicates), applied after the case model saves; a missing element is made, or the run stops naming it when the path has a predicate.
- **OpenFOAM:** pressure solver (GAMG, PCG), velocity solver (smoothSolver, PBiCGStab), velocity relaxation (power user). Pass-through: dictionary entries (`system/fvSolution/solvers/p/tolerance = 1e-8`) set by `foamDictionary` in `Allrun` before `simpleFoam`, every argument shell-quoted.
- **Presets** (`SettingsPreset`, project kind `preset`, under "Solver presets" in the tree): a study's settings saved under a name (Save settings as preset…) and applied to another (Apply settings preset ▸), one Edit each; the outline lists presets and each study's own settings and pass-through.
- **Checks** (`tests/integration/test_solver_settings_runs.py`): the cantilever's tip with every Elmer and FEniCSx linear method, identical to the printed digits; Elmer's pass-through and shared iterative setting in its SIF; FEniCSx's PETSc options in its case; the laminar channel with other solvers, a convection scheme and a pass-through on Code_Saturne and OpenFOAM, the peak velocity still 1.5 times the mean within 1 % (1.4976, 1.4969), the settings found in setup.xml and fvSolution. Refusals in `tests/test_solver_settings.py`, presets in `tests/test_gui_solver_settings.py`.
- **Not yet:** links to each solver's manual from the form; marking results from a hand-edited case (point 4).

## Alternatives considered

- Pass-through only: complete on day one, but unreadable, and gives no validation or GUI.
- Typed settings only: safe, but always behind what solvers offer.
- Raw input files as the source of truth: what each solver's own GUI does; it breaks the solver-neutral model and shoot-outs.
