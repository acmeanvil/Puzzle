---
name: solve-progress
description: checking how a solve is going or why one failed, and following a result (a tip deflection, a reaction) while it solves
adr: 0054
---
Every solve keeps its progress: steps, increments (load or time steps) with their
cut-backs, each equilibrium iteration's residuals against their criteria, the solver's
warnings and errors (Code_Aster's French put in English), equations and memory. A
failed or stopped solve keeps it too, in its run folder.

## Why did a run fail, or how is it going

1. `run_progress("<study>")`: the newest attempt (a failed, stopped or running one
   included). Read `status`, `now` (one line: step, time, increment, iteration,
   residual), `cutbacks`, `failed`, `messages` and above all `diagnoses`: each has a
   `code` and plain advice.
2. Act on the diagnosis, then run again with
   `submit(command={"command": "run_study", "study": "<study>"})`:
   - `friction_singular`: friction multipliers went singular. Check the contact's source
     face lies within its target, no source node is also held or a bolt end; try
     `type: "frictionless"` on the contact first, then friction.
   - `redundant_constraints`: a node held twice (contact plus a tie or a support).
   - `rigid_body`: something is not held; add a support, or a bond for parts held only
     by contact.
   - `cutbacks`: the steps are too large; raise the step's increments (stationary steps
     with contact or plasticity take `increments`), or hold the model before contact.
   - `chattering`: contact opening and closing in turn; more increments, or a softer
     contact.
   - `imprecise_solve` (Code_Aster `FACTOR_57`): MUMPS's answer failed the accuracy
     check on a large model. Set the study's `solver_settings.code_aster.refinement` to
     `FORCE` (Puzzle's default), or loosen it with the expert setting
     `MECA_STATIQUE.SOLVEUR.RESI_RELA = 1e-5`.
   - `not_converged` (CFD, OpenFOAM and Code_Saturne): a steady flow solve used all its
     `iterations` with a residual above its criterion. If it says still falling, it
     estimates how many more iterations: raise the step's `iterations` past that; if
     stalled, look at the mesh near walls and corners (`mesh_quality`) before anything
     else. The results exist but may be wrong.
   - `bad_setting`: the solver refused its input before solving; look at the study's
     `expert_settings` for that solver first.
3. `run_progress("<study>", run="<run id>")` reads a stored run's history instead (run
   ids from `runs`).

## CFD runs (OpenFOAM, Code_Saturne)

- A steady flow solve is one increment of many iterations: `now` reads "iteration 170
  of 500" and the residual furthest from its criterion; progress is the iteration over
  the step's `iterations` setting.
- OpenFOAM's residuals are each field's initial residual (`Ux`, `Uy`, `Uz`, `p`, `k`,
  `epsilon`, `omega`, `T`) against its `residualControl` criterion (the step's
  `tolerance` sets p's and U's); it stops early once all are below (`Converged`).
  `continuity` has no criterion.
- Code_Saturne's are each variable's time residual (`velocity`, `pressure`, `k`,
  `epsilon`, `omega`, `temperature`); it always runs all its `iterations` and has no
  criterion, so Puzzle counts it converged when every residual fell three decades from
  its largest. A heat-transfer channel converges much faster with `pseudo_time_step`.

## Follow a result while it solves (trackers)

A probe with `track: true` whose `source` is the study is followed while it solves and
plotted in the Solution panel (and in `run_progress` as `trackers_last`):

```
submit(command={"command": "create", "kind": "probe", "data": {"name": "Tip deflection",
  "source": "<study id>", "type": "selection", "selection": "Part/Tip",
  "quantity": "displacement:y", "reduction": "min", "track": true}})
```

- Types: `point` (needs `point`, metres), `selection` (max, min or mean), `reaction`
  (the force a selection takes). Quantities: `displacement` (`:total`, `:x`, `:y`,
  `:z`), `temperature` (or `temperature:value`), reactions.
- CalculiX follows all of them; Code_Aster, in nonlinear static solves, follows up to
  four columns: a displacement component over a selection (max, min, mean), a point's
  displacement, a reaction (a magnitude takes three columns, one per component), in
  the probes' order; FEniCSx and Elmer follow
  displacement and temperature (point and selection probes, not reactions) at every
  time step. Others are noted as not tracked and still work on the finished result
  (`probes`).
- Progress comes from CalculiX, Code_Aster, FEniCSx and Elmer: time steps (or harmonic
  frequencies, counted k of n) and nonlinear iterations (Picard or Newton on FEniCSx,
  `ComputeChange` on Elmer, as a relative change against the tolerance).
- A linear solve has one increment: nothing to follow while solving.

## The solver's own words

When `run_progress` doesn't explain it (no diagnosis, or the user asks what the solver
said), read the logs with `run_log`:

- `run_log(study=..., problems=true)`: the error and warning lines of the newest
  attempt, by each solver's markers (CalculiX `*ERROR`/`*WARNING`, Code_Aster's message
  codes `<F>`, `<E>`, `<S>` and alarms `<A>`, Elmer `ERROR::`, OpenFOAM `FOAM FATAL`),
  with `counts` over all of them.
- `pattern` (a regular expression, any case) with `context` lines around, e.g.
  `pattern="MODELISA8_14", context=8` for a Code_Aster message's whole box.
- `run=<id prefix>` reads a stored run's folder instead.
- No logs: the run folder was cleared, or the run was reused from an identical one.

The user reads the same logs in a window: a run's right-click Solver output…, Results →
View → Solver output…, or Open in window beside the message tabs (the Output tab as it
grows, or on the Solution tab the run's logs); search, errors and warnings coloured,
Next problem. In a terminal: `puzzle log PROJECT --study S --problems`.

## Several studies

Run them one at a time (`run_study`, never in a batch), a study that another reads
through a transfer first (ADR 0073: the GUI's Run all and `puzzle run --all` use the
same order); if one fails, read its `run_progress`, then go on with the rest.

## Mistakes to avoid

- Don't start with the solver log to find out why a run failed: `run_progress` has it
  in English, with the diagnosis; `run_log` is the next step, not the first.
- A stall (no progress for minutes) is not a failure: a big factorisation can be quiet.
  Check `now` again before suggesting a stop; only the user stops a run (the Solution
  panel's Stop).
