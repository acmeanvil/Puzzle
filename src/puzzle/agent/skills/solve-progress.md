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
2. Act on the diagnosis, then run again with `submit({"command": "run_study", "study":
   "<study>"})`:
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
3. `run_progress("<study>", run="<run id>")` reads a stored run's history instead (run
   ids from `runs`).

## Follow a result while it solves (trackers)

A probe with `track: true` whose `source` is the study is followed while it solves and
plotted in the Solution panel (and in `run_progress` as `trackers_last`):

```
submit({"command": "create", "kind": "probe", "data": {"name": "Tip deflection",
  "source": "<study id>", "type": "selection", "selection": "Part/Tip",
  "quantity": "displacement:y", "reduction": "min", "track": true}})
```

- Types: `point` (needs `point`, metres), `selection` (max, min or mean), `reaction`
  (the force a selection takes). Quantities: `displacement` (`:total`, `:x`, `:y`,
  `:z`), `temperature:value`, reactions.
- CalculiX follows all of them; Code_Aster follows displacement components over a
  selection (max, min, mean) in nonlinear static solves. Others are noted as not
  tracked and still work on the finished result (`probes`).
- A linear solve has one increment: nothing to follow while solving.

## Mistakes to avoid

- Don't read the solver log to find out why a run failed: `run_progress` has it in
  English, with the diagnosis.
- A stall (no progress for minutes) is not a failure: a big factorisation can be quiet.
  Check `now` again before suggesting a stop; only the user stops a run (the Solution
  panel's Stop).
