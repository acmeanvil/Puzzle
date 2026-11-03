---
name: design-optimisation
description: design of experiments and optimisation: a study run once per point of a plan over parameters, an objective and constraints read at each point, the best point suggested and applied on request
adr: 0063
---
An optimisation (kind `optimisation`) runs one study once per point it chooses, as a
sweep runs once per listed point. Each point's run is the study's own run: it is
stored with the point's labels (`t: 8 mm`), cached by its run key, and opens like any
other run. A point already run is never solved again, so a stopped optimisation
resumes, and a bigger plan reuses the points it shares with a smaller one.

Phase 1 is design of experiments. The points are planned up front, full factorial,
Latin hypercube or Sobol, and are the same every time for one seed.

## Set one up

1. Design variables are parameters, by name: a parameter set's value, or a Number or
   Slider operator (skill `schematic-operators`). The geometry, loads or materials
   must read the parameter already, for example a history argument naming it.
2. The objective and constraints read post-solve outputs: an operator (a Reduce of a
   deflection or stress, a Check, an Expression) or a result tool, by id. A Check read
   as an output gives its margin (positive passes).
3. One `submit` (or `operator_add` first for the outputs):
   `{"command": "create", "kind": "optimisation", "data": {"name": "Thinnest",
   "study": "<study id>", "method": "full_factorial", "levels": 7,
   "variables": [{"name": "t", "low": {"value": 4, "unit": "mm"},
   "high": {"value": 10, "unit": "mm"}}],
   "objective": {"source": "<operator id>", "sense": "minimise"},
   "constraints": [{"source": "<Tip deflection id>", "upper": {"value": 2, "unit": "mm"}}]}}`
   - A variable's `kind` is `continuous` (the default), `integer`, or `choice` (with
     `choices`, a list of quantities).
   - `method` is `full_factorial` (`levels` per variable; an integer variable is
     rounded, a choice uses its choices), `latin_hypercube` or `sobol` (`samples`
     points, `seed`).
   - A constraint has `upper`, `lower`, or both.
   - Budgets: `max_solves` (the most points) and `max_minutes`.
   - A shoot-out study runs every solver per point. `reads` is `primary` (the default)
     or `worst` (a constraint held on every solver).

## Run and read

- `optimisation_run(name, budget)` needs autonomy "act", as runs do. It returns the
  points. The CLI is `puzzle optimise PROJECT NAME [--budget N]`.
- `optimisation_points(name)` solves nothing and reads the stored runs. It gives each
  point's `values` (SI), `labels`, `runs` (by solver), `objective`, `constraints`,
  `feasible` or the `reason` it isn't ("Tip deflection above 2 mm", or "mesh_failed:
  ..."), and `seconds`. It also gives the `best` feasible point, the `suggestion` text,
  and `sensitivities` (each variable's correlation r with each output: a first look at
  what matters, not a gradient).
- A failed point is infeasible with its reason, and the rest still run.

## The best point

The best point is never applied by itself. It is suggested: the Advisor's
Optimisation step, the finished notice, `suggestion`. When the user wants it:
`optimisation_apply(name)` (or `index` for another point). That is one batch, so one
undo, and a version is saved first at "act"; at "propose" it is a proposal for the
user to accept. The CLI is `puzzle optimise PROJECT NAME --apply`.

## Mistakes

- A variable must be a parameter the model reads. An optimisation over a parameter
  nothing uses runs the same model at every point.
- The objective must read results after solving, or be computed from the variables
  (an Expression over a parameter). An output in error makes the point infeasible
  with that error.
- Points that change geometry remesh each time, and the mesh adds noise to the
  response. Look at the sensitivities and the table before trusting a narrow
  difference.
- Changing the plan (levels, samples, seed, bounds) changes the points. Points already
  stored are reused only where a new point equals an old one.
