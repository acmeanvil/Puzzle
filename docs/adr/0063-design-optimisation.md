# 0063. Design optimisation and design of experiments: a study that chooses its own points

- Status: Accepted (2026-11-02); phase 1 built (2026-11-03)
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
     optuna only proposes them.
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
