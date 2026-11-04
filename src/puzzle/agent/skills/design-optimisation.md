---
name: design-optimisation
description: design of experiments and optimisers (COBYLA, Nelder–Mead, differential evolution): a study run per point over parameters, objective and constraints, Pareto front, best point applied on request
adr: 0063
---
An optimisation (kind `optimisation`) runs one study once per point it chooses, as a
sweep runs once per listed point. Each point's run is the study's own run: it is
stored with the point's labels (`t: 8 mm`), cached by its run key, and opens like any
other run. A point already run is never solved again, so a stopped optimisation
resumes, and a bigger plan reuses the points it shares with a smaller one.

A design of experiments plans its points up front (full factorial, Latin hypercube,
Sobol), the same every time for one seed: for seeing the design space and the
sensitivities first. An optimiser chooses each point from the last:

- `cobyla`: local, with the constraints as constraints; a few continuous or integer
  variables and a smooth response (most sizing). It starts from the variables' current
  values and usually needs the fewest solves.
- `nelder_mead`: local, the constraints as a penalty; for a noisy response.
- `differential_evolution`: global, for choices, integers or several optima;
  `population` points per generation for up to `generations`, seeded. It costs
  population × (generations + 1) solves at most.
- `bayesian`: for expensive models, where every solve counts. A Gaussian process of
  each output chooses the point with the most expected improvement that is likely
  feasible. It starts from `samples` Latin hypercube points (4 to 2 × the variables
  + 4 is plenty) and stops at `samples` + 20 or when little improvement is left. It
  was the most accurate in the fewest solves on the cantilever benchmark.
- `response_surface`: a surface (`surface` `rbf` or `quadratic`) through the points,
  its best point solved to check it, refitted, until that point is already solved.
  The cheapest when the response is smooth. Read `surface_fit` (leave-one-out error
  per output, a share of its range): above about 10 %, don't trust the surface's
  optimum without the check solve (which it always makes).

- `robust`: design under scatter. A variable of kind `random` (`{"name": "F", "kind":
  "random", "scatter": {"distribution": "normal", "spread": {"value": 10, "unit":
  "N"}}}`, or `"cv": 0.05` for 5 % of its value) stays at its current value and
  scatters. A chosen variable may have a `scatter` too (a tolerance). A constraint's
  `probability` (0.99) holds it over the scatter; the objective's `statistic` is
  `nominal`, `mean` or `mean_plus_sd` (with `sigmas`). It starts from `samples` Latin
  hypercube points, fits a surface, runs Monte Carlo on it (`monte_carlo` samples), and
  solves the chosen design and the sample deciding each probability to check it. With
  no chosen variables it is an uncertainty analysis at the current values. Read
  `robust` in `optimisation_points`: each output's mean, sd and ±3σ (SI), each
  constraint's `holds` against `wanted`, and `checks` (the surface's miss at each
  checked solve; above a few per cent, add `samples`).
- The `random` operator makes seeded samples (a list); a sweep over it is Monte Carlo
  with real solves, for checking robust design's surface by brute force.

Optimisers round each continuous variable to its `resolution` (default a round
thousandth of its range), so the same point is the same run. They are replayed over
the stored runs, so a stopped one resumes where it was.

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
     points, `seed`), or `cobyla`, `nelder_mead`, `differential_evolution`
     (`population`, `generations`, `seed`). COBYLA and Nelder–Mead refuse choice
     variables.
   - `tolerance` (default 0.001): an optimiser stops when its steps or the
     objective change by less than this share.
   - A constraint has `upper`, `lower`, or both.
   - Budgets: `max_solves` (the most points; 50 for a local optimiser when unset) and
     `max_minutes`.
   - A shoot-out study runs every solver per point. `reads` is `primary` (the default)
     or `worst` (a constraint held on every solver).

## Run and read

- `optimisation_run(name, budget)` needs autonomy "act", as runs do. `budget` is
  the most new points solved this call (stored ones are free), so a long optimisation
  can go in slices. It returns the points. The CLI is
  `puzzle optimise PROJECT NAME [--budget N]`.
- `optimisation_points(name)` solves nothing and reads the stored runs. It gives each
  point's `values` (SI), `labels`, `runs` (by solver), `objective`, `constraints`,
  `feasible` or the `reason` it isn't ("Tip deflection above 2 mm", or "mesh_failed:
  ..."), and `seconds`. It also gives the `best` feasible point, the `suggestion` text,
  and `sensitivities` (each variable's correlation r with each output: a first look at
  what matters, not a gradient). `ended` says how an optimiser stopped ("converged",
  "reached its most points", "not run yet" when it stopped part way). `pareto` lists
  the indexes of the feasible points that no other beats on every output (the
  objective and each constraint, each away from its limit): the trade-off when a
  second objective was made a constraint. Each point's `origin` says what chose it
  ("initial plan", "expected improvement", "surface optimum").
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
  response. For a shape change, a study's `morph` (skill `geometry-operations`)
  moves the same mesh instead. Look at the sensitivities and the table before trusting a narrow
  difference.
- Changing the plan (levels, samples, seed, bounds), the method's settings, or the
  model changes the points. Points already stored are reused only where a new point
  equals an old one with the same run key.
- A local optimiser's best point sits on an active constraint, within a grid step or
  two of the resolution. Where the mesh changes with the variable, the response is a
  little noisy, so do not read more into the last digit.
- A region of failed points (mesh refused) tells an optimiser nothing; it can get
  stuck there. Start from values that solve.
- Robust design: a random variable must be a parameter the model reads (a feature's
  value can't name one yet; a history argument can). Probabilities near 1 (0.9999)
  need `monte_carlo` large enough to see the tail: at least 100 / (1 − p) samples.
