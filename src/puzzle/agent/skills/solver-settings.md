---
name: solver-settings
description: changing how a solver solves: linear solver, tolerances, load stepping, cut-backs, line search and other convergence controls, each solver's own settings, pass-through, presets
adr: 0023
---
Read this when the user asks for a different linear solver, a tighter tolerance, more
iterations, less output, a solver keyword Puzzle doesn't show, or a saved preset; or
when a run is slow or does not converge and a setting may help.

A study's settings come in three layers. All are optional: an unset one leaves the
solver's default, so no result moves until you set something.

1. **Shared settings**, on each solve step's `settings` (beside `time_step`,
   `time_scheme`, `modes`). Each solver translates them into its own terms:
   - `linear_solver`: "direct" (default) or "iterative";
   - `tolerance`: relative residual of a nonlinear solve (default 1e-6);
   - `max_iterations`: nonlinear iterations per increment (default 50);
   - `output_every`: keep results every n-th increment or time step (default 1);
   - load stepping of static steps (unset: the solver's own choice, the full load at
     once for contact alone, ten increments for plasticity, lift-off and large
     deflection): `increments` (at most 1/n of the load an increment),
     `initial_increment` (fraction of the load, 0..1), `min_increment` (how far a cut
     goes before the solve stops), `fixed_increments` (true: never cut back or grown).
   CalculiX, Code_Aster, Elmer and FEniCSx take the first four. CalculiX takes all the
   load stepping; Code_Aster all but `initial_increment`; the others none (refused). The CFD solvers take few:
   Code_Saturne only `linear_solver` = "iterative"; OpenFOAM `linear_solver` =
   "iterative" and `tolerance`. Any other shared setting set on a step (even
   `linear_solver` = "direct") is a problem there ("does not translate the setting")
   and the run is refused; leave it unset and use the solver's own settings instead.
2. **Each solver's own**, in the study's `solver_settings`, a block per solver:
   `{"calculix": {"solver": "PARDISO"}}`. Examples: CalculiX `solver`; Code_Aster
   `method` (MUMPS, MULT_FRONT, GCPC), `acceleration`; Elmer `linear_method`,
   `preconditioner`; FEniCSx `linear_method` (lu, cg_gamg, ...); Code_Saturne
   `pressure_solver`, `velocity_solver`; OpenFOAM `p_solver`, `U_solver`. Some are
   power-user only.
3. **Expert pass-through**, in the study's `expert_settings`, a block per solver of
   `path: value` text. The adapter places each entry in its input file or refuses it;
   it never drops one.

A run's key covers only its own solver's blocks. Changing CalculiX's block leaves a
shoot-out's Code_Aster runs up to date.

## Steps

1. `solver_settings(study)`: the shared settings and, for each of the study's solvers,
   its own settings with label, the solver's own name, `kind`, `choices` (label, value
   pairs), `default`, `current`, `power`, the step types it applies to, the current
   `expert` block and `expert_help` (the path format). Use the values from `choices`,
   not the labels.
2. `get_object(name="<study>")`: find the index of each solve step in `steps`
   (sweep steps count too).
3. Shared settings, on every solve step (one `submit` with a list, or one edit with
   several ops):
   `{"command": "edit", "object": "<study>", "ops": [
   {"op": "set", "path": "steps.0.settings.tolerance", "value": 1e-8},
   {"op": "set", "path": "steps.0.settings.max_iterations", "value": 100}]}`.
   Remove one to go back to the default: `{"op": "remove", "path":
   "steps.0.settings.tolerance"}`.
4. A solver's own block. Set the whole block, since the path below a missing solver key
   fails with `not_found`:
   `{"command": "edit", "object": "<study>", "ops": [{"op": "set", "path":
   "solver_settings.calculix", "value": {"solver": "PARDISO"}}]}`.
5. Pass-through, the same way, values as text:
   - CalculiX: `{"op": "set", "path": "expert_settings.calculix", "value":
     {"after_procedure": "*CONTROLS, PARAMETERS=FIELD\n0.001, 0.002"}}`. Places:
     `model` (before the first step), `after_procedure` (every step),
     `after_procedure:2` (step 2 only).
   - Code_Aster: `{"STAT_NON_LINE.NEWTON.REAC_ITER": "2"}` (COMMAND.KEYWORD or
     COMMAND.FACTOR.KEYWORD, a Code_Aster value).
   - Elmer: `{"Solver 1/Linear System Max Iterations": "10000"}`; `Solver */...` for
     every solver; an unlisted keyword needs its type (`Logical True`, `Real 1e-8`).
   - FEniCSx: PETSc options, `{"ksp_type": "gmres", "pc_type": "hypre"}`.
   - Code_Saturne: setup.xml paths, `path = value` or `path@attribute = value`.
   - OpenFOAM: `{"system/fvSolution/solvers/p/tolerance": "1e-8"}`.
   Or all of steps 3-5 in one call, one undo, values checked first:
   `solver_settings_set(study="<study>", shared={"increments": 20}, solvers={"code_aster":
   {"line_search": "MIXTE"}})`. It sets the shared ones on every solve step they apply
   to, merges each solver's block, and `null` removes a setting; `expert` replaces a
   solver's pass-through block.
6. `check_study(study)` before running: unknown keys, bad values, a shared setting the
   solver can't take, and pass-through it can't place all show up here.

## When a nonlinear solve won't converge

Read the run first (`run_progress`, `run_log` with `problems`): cut-backs, residuals
that grow, a contact that chatters. Then, one change at a time:

1. More, smaller increments: shared `increments` (20, 50) and a small
   `initial_increment` (0.05) so contact settles before the full load.
2. Let it cut further: CalculiX `cutbacks` (default 5) and `cutback_factor` (0.25; 0.5
   cuts more gently); Code_Aster `cutback_levels` (5) and `cutback_parts` (4), or a
   smaller shared `min_increment`.
3. Code_Aster `line_search` "MIXTE" (or "CORDE") when residuals overshoot or oscillate;
   `prediction` "ELASTIQUE" for plasticity and contact; `matrix` "ELASTIQUE" when the
   tangent misleads (softening). CalculiX has no line search: smaller increments and
   CalculiX's `divergence_check_after` (power, default 4; 6-8 is more patient with
   contact) instead.
4. Elmer: `relaxation` 0.5-0.8 for an oscillating nonlinear iteration;
   `newton_after_iterations` 3-5 to switch from Picard to Newton.
5. Contact stiffness itself is the contact's `stiffness` (accurate, balanced, fast), not
   a solver setting.

These change how the solver gets there, not the answer: the cantilever's tip is the
same to eight digits with and without them (`test_solver_settings_runs.py`).

## Time schemes

`time_scheme` is a solve step setting too (transient steps), set as in step 3:
"backward_euler" (the default everywhere, so shoot-outs compare like with like),
"crank_nicolson", "bdf2", or "theta" with a `theta` setting. `check_study` says if the
solver lacks the scheme. After a sudden load prefer backward Euler or BDF2: theta
schemes start differently on Elmer.

## Presets

A preset (kind `preset`) holds `shared`, `solver_settings` and `expert_settings`:
`{"command": "create", "kind": "preset", "data": {"name": "Tight tolerance",
"shared": {"tolerance": 1e-8}, "solver_settings": {"code_aster": {"method": "MUMPS"}}}}`.
There is no command that applies one. The GUI does it (a study's Apply settings
preset ▸), or you write the same edit: the preset's shared settings on every solve
step, and for each solver it names, its block replacing the study's.

## Units

Settings are plain numbers, with no unit: a tolerance is relative, `output_every` and
`max_iterations` are counts. Time settings (`time_step`, `end_time`) are quantities,
`{"value": 0.1, "unit": "s"}`.

## Mistakes to avoid

- `"linear_solver": "direct"` on Code_Saturne or OpenFOAM: they are iterative only, and
  it is refused.
- Code_Aster `method` PETSC on a serial build: it stops ("PETSc n'est pas installé").
  PETSc needs a parallel build; MUMPS is the default.
- Setting `solver_settings.calculix.solver` when the study has no CalculiX block yet:
  set `solver_settings.calculix` as a whole object.
- Changing the shared settings on one step only in a multi-step study: set them on every
  solve step unless the user wants otherwise.
- Code_Aster cut-back settings with `fixed_increments`: refused (fixed never cuts).
  `line_search_iterations` without a `line_search`: refused.
- Load stepping on Elmer or FEniCSx: refused; they have no load increments.
- Using pass-through for what a setting covers: the setting is checked and labelled,
  the pass-through only placed.
- Pass-through `*STEP` or `*END STEP` cards on CalculiX: refused.
