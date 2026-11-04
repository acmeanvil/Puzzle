# 0063. Design optimisation and design of experiments: a study that chooses its own points

- Status: Accepted (2026-11-02); phases 1, 2 and 3 built (2026-11-03)
- Date: 2026-11-02

## Context

ADR 0062 lets numbers, sliders and expressions drive geometry and settings, and lets
result operators turn runs into numbers and verdicts (a maximum stress, a mass, a
pass/fail against an allowable). It allows no wire from results back into the model.
The loop "change the design until the result is good enough", Grasshopper's Galapagos
and Ansys's DesignXplorer, is this ADR's job.

What exists to build on:

- **Sweeps** (ADR 0018): a study runs once per point, each run stored with its point
  (`RunRecord.point`) and cached by a run key that covers the point, so a point already
  run is never solved twice. A sweep sharing one mesh is cheaper when only materials
  change.
- **Run keys and versions** (ADR 0033, 0059): every run says exactly what it was
  computed from.
- **Progress** (ADR 0054): typed events and the Solution panel, a run's convergence and
  its stall warning.
- **Cores and parallel processes** (ADR 0053): how many solves run at once.
- **Agent budgets** (ADR 0015): runs and solver seconds an agent may spend.
- **Compare** (ADR 0018): a swept study's points side by side, by difference and in a
  summary table.

What an optimisation adds is that the next point depends on the results of the last.
So the points can't be listed in advance, which is what a sweep needs.

## Decision

1. **An optimisation is a project object** (kind `optimisation`, under Studies in the
   tree), not a study step. It names the **study it drives** and runs that study once
   per point it chooses, as a sweep does. Its runs are the study's own runs, stored and
   cached by their points' run keys, so a stopped optimisation resumes where it was and
   a later one reuses every point already solved. The study it drives stays an ordinary
   study, runnable alone with the model's current values.

2. **Design variables** are project parameters (ADR 0062's Numbers and Sliders, or a
   parameter set's values), each with bounds and a kind: continuous, integer (a hole
   count), or a list of choices (thicknesses from a catalogue, or materials at a
   location as in ADR 0018). A variable starts from its current value.

3. **Objectives and constraints** are post-solve outputs (ADR 0062 item 6): a Reduce
   (maximum von Mises, a reaction, a mass), a derived result's value, a Check's
   margin, a Python snippet's number. The objective is to minimise or maximise one of
   them. A constraint is an output kept at or below, at or above, or between limits
   (stress ≤ allowable, first frequency ≥ 30 Hz). A run that fails (mesh refused,
   solver diverged) is an infeasible point with its reason, never a crash of the
   optimisation.

4. **Methods**, from scipy and optuna (ADR 0062's script environment is not needed: the
   optimiser runs in Puzzle's own Python and calls `run_study`):
   - **Design of experiments**: full factorial, Latin hypercube and Sobol
     (`scipy.stats.qmc`), a fixed number of points chosen up front, run like a sweep,
     for seeing the design space and the sensitivities before optimising.
   - **Local, without gradients**: COBYLA and Nelder–Mead, for a few continuous
     variables and smooth responses (most structural sizing).
   - **Global, evolutionary**: differential evolution (Galapagos's kind), for integer
     and choice variables and responses with several optima, with a population per
     generation run in parallel (ADR 0053).
   - **On a response surface**: fit a surface (quadratic, or radial basis functions,
     `scipy.interpolate.RBFInterpolator`) to a DOE's runs, optimise on the surface, and
     verify the best point with a real solve; the surface's fit (leave-one-out error)
     is shown, so a poor fit is seen.
   - **Bayesian**: a Gaussian-process model of each output, the next point chosen by
     expected improvement under the constraints, for expensive models where every
     solve counts (tens of solves, not hundreds). From optuna (MIT, pure Python, on
     conda-forge; its GP sampler for continuous and integer variables, TPE where
     choices are many), added to Puzzle's environment; the points stay Puzzle's runs,
     optuna only proposes them. *Changed in phase 3: optuna's GP sampler needs
     PyTorch, so Puzzle fits its own Gaussian process in scipy (`puzzle.surrogates`).*
   A budget bounds every method: the most solves, solver time or wall time, and a
   tolerance on the objective. Agents spend their own budget too (ADR 0015).

5. **Several objectives** (minimise mass and deflection): the first version makes one
   the objective and the others constraints. A DOE or an evolutionary run then shows
   the Pareto front of its feasible points (the points no other point beats on every
   objective) as a plot to pick from. A dedicated multi-objective method (NSGA-II) is
   left for later.

6. **Results**:
   - a table of every point (iteration, the variables, the objective and constraints,
     feasible or why not, the run), filterable and exported as CSV;
   - plots of the objective against iteration, each variable against the objective,
     the Pareto front, and the sensitivities (the DOE's correlation per variable and
     output);
   - the best feasible point marked. Apply best puts its values into the model's
     parameters as one command (undoable, a version made first, ADR 0059), only when
     the user asks. When an optimisation finishes, the best point is suggested: a
     notice, the Advisor's next step ("Apply the best point: mass −12 %, stress within
     its limit") and, for an agent, a proposal to accept (ADR 0015). Nothing changes
     the model by itself, so an optimisation never edits the project behind the user's
     back.
   Any point's run opens like any run, and Compare (ADR 0018) takes several.

7. **Progress and stopping** (ADR 0054): each point is an `Iteration` event (its
   values and outputs), each solve's own progress beneath it. The Solution panel shows
   the objective's history live. Stop finishes the running solves and keeps every
   point. Run again resumes from the stored points (scipy methods get their history
   back; a DOE skips the points it has).

8. **Shoot-outs** (ADR 0011): an optimisation of a study with a shoot-out is a
   repeated shoot-out. Every point runs every solver on one mesh, and the shoot-out's
   metrics (the solvers' differences) are kept per point, so where the solvers part
   company across the design space is seen. The objective and constraints read the
   primary solver's results by default, or the worst of the solvers (a constraint held
   on every solver), set per optimisation. A point where only some solvers fail is
   feasible on the others, marked so.

9. **In the schematic** (ADR 0058, 0062): the optimisation is a node right of the
   study it drives. Its inputs are wired from the design variables (their bounds) and
   from the objective and constraint outputs. It writes nothing into them, so the
   graph has no cycle and the layout stays left to right. A dashed "drives" mark joins
   it to its study without being a wire. The node shows the best value so far and the
   point count.

10. **Twins**: `puzzle optimise PROJECT NAME [--resume] [--budget N]`, MCP
   `optimisation_run`, `optimisation_points` and `optimisation_apply` (agents need
   "act" to run it and to apply a point, as for runs), the outline's line per
   optimisation, and a skill `design-optimisation`.

11. **Robust design**: variables with scatter (a distribution each: normal, uniform,
    log-normal, from a tolerance or a material's spread), results as distributions
    (mean, standard deviation, the probability of breaking a constraint) by Monte Carlo
    on a response surface, checked with real solves at the extremes; a constraint may
    be held at a probability ("stress ≤ allowable at 99.9 %"). Its own phase, after
    the optimisers, and needed to close this ADR.

12. **Phases**: 1, the object, DOE (factorial, Latin hypercube, Sobol), the points
    table and plots, Apply best and its suggestion, shoot-outs per point; 2, COBYLA,
    Nelder–Mead and differential evolution with constraints, resume, the Pareto plot;
    3, Bayesian optimisation (optuna) and response surfaces; 4, robust design (item 11).
    NSGA-II comes later and doesn't hold the ADR open.

## Consequences

- Sizing a part ("thinnest plate that keeps stress below 200 MPa") and exploring a
  design space become a few objects in one project, with every point a stored,
  reusable run.
- An optimisation is only as fast as its solves. Points that change geometry remesh
  each time. The budget, parallel runs and response surfaces are how it stays usable,
  and the points table says what each point cost.
- Mesh changes between points add noise to a response (a remeshed fillet moves a
  stress peak). Local methods can stall on it; the ADR's DOE first and response surfaces
  are the defence, and the mesh convergence check (Advisor) the honest answer.
- The store grows by one run per point; deleting an optimisation's runs uses the
  existing Delete results.
- Tests: each method on closed-form problems (a cantilever's thickness against a
  deflection limit, solved by hand; Rosenbrock for the optimisers through a cheap
  stand-in study), resume after stop, infeasible points kept, Apply best undoable.

## Alternatives considered

- **An optimisation as a study step** (like `parametric_sweep`): close to the sweep
  code, but a study would then hold a loop over itself, and the schematic would draw a
  cycle.
- **Wires from results back to parameters** (Grasshopper's loops through Galapagos):
  refused by ADR 0062: the layout and the hashes need a graph without cycles.
- **An outside optimiser** (Dakota, OpenMDAO, optuna): Dakota is a large C++ toolkit
  to package on five platforms; OpenMDAO and optuna are good Python options, but scipy
  covers the first phases with nothing new to install. Revisit for NSGA-II or Bayesian
  optimisation.
- **Gradient-based methods with adjoint sensitivities**: the fastest for many
  variables, but no adapter gives design sensitivities; finite differences cost a
  solve per variable per step.

## Review (2026-11-02)

The maintainer's answers:

1. scipy's methods, and Bayesian optimisation too (item 4: optuna's samplers).
2. One objective plus constraints first, Pareto plots from DOE and evolution, NSGA-II
   later (item 5).
3. Apply best only by the user's command, though it is suggested when an optimisation
   finishes (item 6).
4. Shoot-outs for sure: an optimisation of a shoot-out study is a repeated shoot-out
   (item 8).
5. Robust design comes later, but it is needed to close this ADR (item 11, phase 4).

## Phase 1 built (2026-11-03)

- **The object**: `model.schema.Optimisation` (kind `optimisation`, its own tree
  section).
  - `study` is the study it drives.
  - `variables` are `DesignVariable`s: a parameter by name (a parameter set's value,
    or a Number or Slider operator, ADR 0062), continuous or integer between `low` and
    `high`, or a `choice` of quantities.
  - The objective and `constraints` are `DesignOutput`s: an operator's or a result
    tool's output by id and port; `sense` for the objective, `lower`/`upper` for a
    constraint. A Check read as an output gives its margin.
  - Also `method`, `levels`, `samples`, `seed`, `max_solves`, `max_minutes`, and
    `reads` (primary or worst, for a shoot-out).
- **Running** (`puzzle.optimise`):
  - `plan` makes the design of experiments' points (SI) with
    `scipy.stats.qmc.LatinHypercube` and `Sobol` (a power-of-two base, cut to size)
    or a full factorial. They are seeded, so the same every time.
  - `applied` sets a point on a project copy through `sweep.apply`, which now also
    sets a Number or Slider swept by name.
  - `run` solves each point with `run_study(..., _point=(run key, labels))`, so it is
    stored with its labels and cached by its key, and a resume reuses every stored
    point. A shoot-out runs every solver per point on one mesh, and the point keeps
    each solver's outputs (`by_solver`). A failed point is infeasible with its reason
    and the rest run. `cancel` stops after the running point.
  - `points` rebuilds the table from the stored runs without solving. The operators
    read each point's run by its title, and by solver: `Context.solver` and
    `derived.run_for(..., solver)`. Before this, a shoot-out solver's run could be
    read as the study's.
  - Also `best`, `apply_commands` (one batch), `suggestion` and `sensitivities`
    (Pearson's r per variable and output).
- **Applied only when asked**:
  - The Advisor's Optimisation step suggests the best point; its button applies it.
  - The finished notice says the best point and to use Apply best.
  - `optimisation_apply` makes a proposal at "propose".
  - Apply best (panel, menu, CLI `--apply`) saves a version first, then submits one
    batch: one undo.
- **GUI**:
  - New optimisation… (tree section, a study's menu, Results menu) asks everything in
    one form: variables one a line ("t: 4 mm .. 8 mm", "n: 2 .. 6 integer", "t: 4 mm,
    6 mm, 8 mm") and constraints by name ("Tip deflection <= 2 mm").
  - The Optimisation dock (Window → Optimisation, Ctrl+0) has Run, Stop and Apply
    best; the points table (variables, objective, constraints, feasible or why not,
    time; the best in bold; a double-click opens a point's run); a plot of the
    objective or a constraint by point, or the objective against a variable; and the
    sensitivities.
- **Schematic**: the node sits right of its study. Inputs are wired from the
  variables' parameters and from the objective and constraint outputs. A dashed
  "drives" mark joins it to its study; the upstream and downstream walks skip it, so
  there is no loop. The node shows the point count and the best value.
- **Twins**: `puzzle optimise PROJECT NAME [--points] [--budget N] [--apply] [--json]`,
  MCP `optimisation_run` (at act), `optimisation_points` and `optimisation_apply`, an
  Optimisation line in the outline, and the skill `design-optimisation`.
- **Tests**:
  - `tests/test_optimise.py`: a closed-form stand-in study (δ = 4FL³/(Ewt³)), the grid
    best found by hand, reuse, failed points, stop and budget, seeded plans in bounds,
    sensitivities, shoot-out solvers.
  - `tests/integration/test_optimise_cantilever.py`: CalculiX, t from 4 to 10 mm, every
    deflection within 3 % of the closed form, 8 mm the best, the second run all
    reused.
  - `tests/test_optimise_gui.py`.

Not done in phase 1:
- The objective's history is not yet a live plot in the Solution panel as points
  finish: each point's solve progress shows as any run's does, and the table fills
  when the run ends.
- No Pareto plot yet (phase 2).

## Phase 2 built (2026-11-03)

- **Methods** (`Optimisation.method`, from scipy):
  - `cobyla`: `minimize(method="COBYLA")` with the constraints as constraints.
  - `nelder_mead`: the constraints as a penalty (100 times each scaled breach).
  - `differential_evolution`, with scipy's `NonlinearConstraint`, `integrality` for
    integer and choice variables (a choice by its index), Latin hypercube start,
    deferred updating, no gradient polish.
  - Settings: `tolerance`, `population` (per generation), `generations`, `seed`;
    `max_solves` caps every method (a local one's default is 50).
  - COBYLA and Nelder–Mead refuse choice variables (code `invalid`).
- **What an optimiser sees**:
  - The local methods move each variable from 0 to 1 over its range, so a step means
    the same for every variable. They start from the variables' current values (item
    2), as does differential evolution's first member.
  - The objective is scaled by its first value. Each limit is a margin scaled by the
    limit, positive when it holds.
  - COBYLA keeps a tenth of the tolerance inside each limit: it ends on an active
    limit, and the grid's nearest point to it was otherwise just past.
  - A failed point is worse than every point so far and breaks every limit.
  - Each continuous variable is rounded to `DesignVariable.resolution` (default a round
    thousandth of its range), so the same point is the same run, and labels stay short.
- **Resume by replay**:
  - Every method is deterministic for one project, its settings and seed.
  - `points` replays the optimiser over the stored runs, reading each point it asks
    for, and stops at the first point not run. `run` replays the same way and solves
    from there, so a stopped or budgeted optimisation goes on along the same path. The
    test checks the path matches an uninterrupted run's, with each point solved once.
  - Stored runs are now found by the point's run key, not its title, so a point of a
    changed model is not read from an old run.
  - `run(..., budget=N)` solves at most N new points this time (CLI `--budget`, MCP
    `optimisation_run`'s `budget`); `max_solves` stays the optimisation's own cap.
  - `Points.ended` says how it stopped: converged, reached its most points, past the
    budget, stopped, or (reading) not run yet.
- **The Pareto front** (item 5): `pareto(opt, points, keys)`, the feasible points no
  other feasible point beats on every output. The objective counts by its sense; a
  constraint counts as better further from its limit (`better`).
  - Panel: Plot → "Pareto front: <objective> against <constraint>", the front as a line
    over the feasible points as dots (`Curve.set_series(..., dots=)`).
  - MCP `optimisation_points` gives `pareto` and `ended`; the CLI prints the front.
- **Live table**: `run`'s `on_point` goes to the GUI as a task event. The panel adds
  each point as it finishes (`OptimisationPanel.add_point`), not only at the end.
- **Elsewhere**:
  - New optimisation… offers every method, its Points field being a sampled plan's
    points or an optimiser's most.
  - The outline says "COBYLA of Beam (up to 50 points)".
  - The schematic node's badge counts an optimiser's points ("12 points").
- **Tests**:
  - `tests/test_optimisers.py`: COBYLA and Nelder–Mead find the lightest beam by hand
    (w at its bound, t = 5.848 mm, within 0.2 %). Nelder–Mead on Rosenbrock reaches
    (1, 1) within 0.02. Differential evolution on Rosenbrock, and with a catalogue of
    thicknesses (t 6 mm, w 10 mm, 60 mm²). Also: resume after a budget, stop, failed
    points, choices refused, snapping, the outline, and a DOE's Pareto front checked
    against a brute-force count.
  - `tests/test_optimise_gui.py`: COBYLA on the CalculiX beam fills the panel point by
    point and ends on the deflection limit (t 7.26 mm, deflection 1.977 mm against 2 mm,
    five solves). The Pareto plot is offered and drawn.

Not done in phase 2:
- A generation's points run one after another; running them in parallel (ADR 0053)
  needs concurrent `run_study` calls on one store, not tried yet.
- Points are not yet `Iteration` progress events (item 7); the panel's table is the
  live view.
- COBYLA on Rosenbrock's valley stalls on the 0.001 grid (Nelder–Mead and differential
  evolution reach the minimum); COBYLA is for smooth sizing, as item 4 says.

## Phase 3 built (2026-11-03)

- **Bayesian optimisation without optuna** (a change to item 4, the maintainer's choice):
  optuna's Gaussian-process sampler needs PyTorch, several hundred megabytes on every
  install for one method. `puzzle.surrogates` has the model instead, in numpy and scipy:
  - A Gaussian process with a Matérn 5/2 kernel: a length scale per variable, signal
    and noise by maximum marginal likelihood (L-BFGS-B from two fixed starts, so the
    same data give the same fit). The noise term takes a remeshed point's scatter.
  - Expected improvement, and the probability that a margin holds.
- **`bayesian`**:
  - It starts from `samples` Latin hypercube points: the same points as a Latin
    hypercube design of that seed and size, so their runs are shared.
  - It models the objective and each limit's margin with a Gaussian process.
  - The next point is the most expected improvement times the probability that every
    limit holds; before any point is feasible, the most likely feasible point.
  - The search covers 2048 Sobol candidates over the range plus 512 round the best
    point, snapped to the variables' grid, so no point is asked for twice.
  - It stops when the best expected improvement is below `tolerance` (a share of the
    objective's first value), or at `samples` + 20 points.
  - Choices are allowed, by their index.
- **`response_surface`**:
  - It starts from the same Latin hypercube.
  - It fits a surface (`surface`: `rbf`, scipy's thin-plate `RBFInterpolator` with a
    linear part through every point; or `quadratic` by least squares) to the objective
    and to each margin.
  - It optimises on the surface (a Sobol search, then COBYLA on the surface, a little
    inside each limit) and solves that point to check it, then refits with it. It
    stops when the surface's best point is already solved, or at `samples` + 10.
  - A quadratic surface needs as many starting points as it has coefficients, (d + 1)
    (d + 2) / 2, or it is refused.
  - `fit_quality` is each output's leave-one-out error as a share of its range. It is
    shown in the panel's summary, MCP `optimisation_points` (`surface_fit`) and the
    CLI, so a poor fit is seen.
- **Points say what chose them** (`Point.origin`): "initial plan", "expected
  improvement", "likely feasible", "surface optimum". This is a "Chosen by" column in
  the panel for these two methods, and in the CLI and MCP output.
- **Speed**:
  - Each step converts the variables' bounds to SI once (`_spec`): a model's search
    converts thousands of points, and pint conversions cost 0.4 ms each.
  - The replayed table is cached until the optimisation, the study's run keys, the
    operators, result tools or derived results, or the store's runs change. A Bayesian
    replay refits its models, and the schematic, Advisor and panel all read the table.
- **Tests**:
  - `tests/test_optimisers.py`: both methods find the lightest beam (within 0.5 % of
    58.48 mm², at most 14 points from 8). The start is the Latin hypercube design, and
    the replay matches. A Bayesian run resumes on its path. A quadratic fits the area
    exactly (leave-one-out error below 1e-9) and the deflection not (1 to 20 %). Also
    the surrogates on known functions.
  - `tests/integration/test_optimise_cantilever.py` on CalculiX, from four points:
    - Bayesian: t = 7.24 mm, deflection 1.994 mm against the 2 mm limit, 7 solves.
    - Response surface (RBF): t = 7.25 mm, 1.986 mm, 6 solves.
    - For comparison, COBYLA ended at 7.26 mm in 5 solves.
  - A test fix: the stand-in store's run ids are unique now. Reused ids ("r3" in
    every test) hit the derived-result cache, which is keyed by run id, so one test
    read another's results.

Not done in phase 3:
- A plot of a response surface itself (the panel shows its fit as numbers).
- TPE for many choices (the ADR's other optuna sampler): the Gaussian process takes
  choices by index, and differential evolution remains the method for many of them.
