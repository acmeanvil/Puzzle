---
title: Optimisation and mesh convergence
section: guide
order: 140
keys: kind:optimisation, section:optimisations, kind:mesh_study, section:mesh studies, panel:optimisation, panel:convergence
summary: Run a study over many designs to find the best one, and check that the mesh is fine enough to trust a result.
adrs: 0063, 0066
---

An optimisation runs one study once per design point and finds the best feasible one.
A mesh convergence check runs it on finer and finer meshes and says whether the mesh
is fine enough. Both store every run, so a stopped one resumes where it was.

## How to set up an optimisation

1. Make the design variables parameters the model reads: a parameter set's value
   (Project → Add parameter…), or a Number or Slider operator, named in a geometry
   step's argument or a load's value.
2. Make the outputs: a Result summary or Check operator, or a result tool, for the
   objective and each limit ([Schematic and operators](help:guides/schematic-and-operators)).
3. Results → Optimise and check the mesh → New optimisation…, or a study's right-click
   New optimisation…. One form asks for everything:
   - **Design variables**, one a line: `t: 4 mm .. 8 mm`; `n: 2 .. 6 integer`;
     `t: 4 mm, 6 mm, 8 mm` for choices;
   - **Objective** and **Best is** (smallest or largest), or none for constraints only;
   - **Constraints**, one a line by an output's name: `Tip <= 1 mm`;
   - **Method** and **Points** (default Latin hypercube, 10 points).
4. Right-click the optimisation under Optimisations in the tree, Run. Points appear in
   the Optimisation panel (Window → Optimisation, Ctrl+0) as each finishes, with a
   Pareto plot.

## Choosing a method

- **Design of experiments** (Full factorial, Latin hypercube, Sobol sequence): plans
  every point up front, the same for one seed. Use it first, to see the design space
  and which variable matters.
- **COBYLA**: local, with constraints as constraints; most sizing problems, the fewest
  solves. It starts from the variables' current values.
- **Nelder–Mead**: local, constraints as a penalty; for a noisy response.
- **Differential evolution**: global, for integers, choices or several optima; costs up
  to population × (generations + 1) solves.
- **Bayesian (Gaussian process)**: for expensive models; it was the most accurate in the
  fewest solves on the cantilever benchmark.
- **Response surface**: a surface through the points, its optimum solved to check it.
  The cheapest on a smooth response.
- **Robust design**: variables with scatter (`F: random ± 10 N`) and
  constraints that must hold with a probability (`Tip <= 1 mm at 99 %`).

## How to apply the best point

The best point is never applied by itself. The Advisor's Optimisation step suggests it.
Right-click the optimisation, Apply best (or the panel's Apply best): one change, one
undo, with a version saved first.

## How to check mesh convergence

1. Right-click the study, Check mesh convergence… (or Results → Optimise and check the
   mesh → Check mesh convergence…).
2. Choose **Meshes** (default 3), **Each finer by** 1.3, 1.5 or 2 (default 1.5: every
   size of the recipe divided once more), and **Converged within (%)** (default 2).
3. The Convergence panel (Window → Convergence, Ctrl+Shift+0) shows each mesh and the
   verdict: converged, not converged, oscillating, or not in the asymptotic range.
4. Each quantity gets its observed order, its extrapolated value and its GCI (the grid
   convergence index, Roache's, safety factor 1.25). Quote the GCI with the value.

By default it judges the largest value of the study's first result. To judge something
else, make a Result summary or a result tool first. Each finer mesh costs about 3.4
times the last at ratio 1.5, so start from a coarse recipe.

## How to estimate the error of one run

Right-click a study, New error estimate…: the Zienkiewicz–Zhu energy-norm error of its
newest run, drawn per element, with where it is largest. It needs no new solve; use it
to see where to put a size control.

## How to adapt the mesh

Right-click the study, Adapt the mesh…: it meshes again where the error is, pass after
pass, until the target. Choose the **Target (%)**, **Judged by** (energy-norm error, or
the quantities' change between passes), **Elements** (isotropic, or anisotropic with
MMG for thin layers such as a heat front), and the **Budget** in passes (default 5).

## Options worth knowing

- An optimiser rounds each variable to its resolution (a thousandth of its range), so
  one point is one run, reused next time.
- A failed point is infeasible with its reason; the rest still run.
- Anisotropic adaptation helps layers (a heated bar reached 0.05 % where isotropic
  reached 0.14 %) and brings nothing on smooth structural fields.

## Mistakes to avoid

- A variable nothing reads: every point solves the same model.
- A peak stress at a sharp corner never converges. Judge a stress away from it, or a
  linearised or integrated one.
- Two meshes can't give an order: the verdict stays "too few levels".
- First-order tetrahedra in bending lock and won't converge: use second order.
- Points that change the geometry remesh, which adds noise; don't read the last digit.

## For agents and scripts

The agent runs these with [optimisation_run](help:mcp:optimisation_run),
[optimisation_points](help:mcp:optimisation_points),
[optimisation_apply](help:mcp:optimisation_apply),
[mesh_convergence](help:mcp:mesh_convergence), [error_estimate](help:mcp:error_estimate)
and [mesh_adapt](help:mcp:mesh_adapt); a Mesh studies object (several checks of one
study) is made through [submit](help:mcp:submit). In a terminal:
[puzzle optimise](help:cli:optimise), [puzzle convergence](help:cli:convergence),
[puzzle error-estimate](help:cli:error-estimate) and [puzzle adapt](help:cli:adapt).

## See also

- Theory: [Mesh convergence and the GCI](help:theory/mesh-convergence), [Error estimates and adaptive meshing](help:theory/error-estimates), [Optimisation and surrogates](help:theory/optimisation).
- [Schematic and operators](help:guides/schematic-and-operators)
- [Meshing](help:guides/meshing)
- [Studies and solvers](help:guides/studies-and-solvers)
- [Results](help:guides/results)
