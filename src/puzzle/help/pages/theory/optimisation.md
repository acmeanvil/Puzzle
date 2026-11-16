---
title: Design optimisation
section: theory
order: 190
keys: theory:optimisation
summary: How Puzzle chooses design points (designs of experiments, gradient-free and evolutionary optimisers, Gaussian-process and response-surface methods, robust design under scatter) and how it judges them.
adrs: 0063
---

An optimisation asks which values of a few design variables (a thickness, a count, a
material) give the best result while every limit holds. Each design point is one
solve, often minutes long, so the question is how to learn the most from the fewest
solves. Puzzle offers three answers. A design of experiments lays points out in
advance. An optimiser walks towards the best point. A surrogate method fits a cheap
model to the solves so far and asks it where to look next.

## What the methods see

The optimisers in `optimise.py` don't see the raw numbers. They see:

- **Variables scaled 0 to 1** from low to high, so a step means the same for every
  variable. Choices are counted by index.
- **The objective divided by its first value.** A maximised objective is negated, so
  every method minimises.
- **Each limit as a scaled margin**, positive when it holds:
  $g = (L - v) / |L|$ for an upper limit L and $g = (v - L) / |L|$ for a lower one.
- **Snapping.** Each continuous value is rounded to the variable's `resolution`, by
  default a round thousandth of its range (0.001 mm for 4 mm to 10 mm). The same point
  is then the same stored run and is never solved twice. Integers are rounded to whole
  numbers.
- **Failed points.** A point that failed counts as worse than every point so far and
  breaks every limit, so the optimiser moves away from it.

Every method is deterministic for a given project, settings and `seed`. So Puzzle can
rebuild the table by replaying the method over the stored runs, and a stopped run
resumes on the same path.

## Designs of experiments

- **Full factorial**: `levels` values per variable, evenly spaced, every combination.
  It needs levels^d points, which grows quickly.
- **Latin hypercube** (`scipy.stats.qmc.LatinHypercube`): `samples` points such that
  each variable's range, cut into `samples` equal strips, has one point in every strip.
- **Sobol** (`scipy.stats.qmc.Sobol`, scrambled): a low-discrepancy sequence that fills
  the space evenly. Puzzle draws a power of two and cuts it to `samples`.

A design of experiments gives the **sensitivities**. Each is Pearson's correlation r
between a variable and an output over the run points, from −1 to 1. It is a first look
at what matters and assumes a roughly linear effect.

## Optimisers

- **COBYLA** (scipy `minimize`): a local, gradient-free method that models the
  objective and constraints linearly over a simplex of points. It starts from the
  variables' current values with an initial step `rhobeg` of 0.25 of each range. It
  keeps a tenth of `tolerance` inside each limit, because it ends on an active limit
  and the grid's nearest point could be just past it. It suits smooth sizing problems.
- **Nelder–Mead**: a local simplex method with no constraints of its own, so Puzzle
  minimises a penalised objective $f_p$ (below). The first simplex steps a quarter of
  each range from the start.
- **Differential evolution** (scipy): a global, population-based method. Each
  generation has about `population` members (rounded up to a multiple of the number of
  variables). The first generation is a Latin hypercube that includes the current
  design. Integer and choice variables stay whole (`integrality`). Limits are scipy
  nonlinear constraints, updates are deferred, and there is no gradient polish. It
  takes many solves, but it handles catalogues of sizes and many local minima.

Nelder–Mead's penalised objective, 100 times each scaled breach:

$$f_p = f + 100 \sum_j \max(0, -g_j)$$

`tolerance` is COBYLA's final step and Nelder–Mead's change in the points and the
objective, in the scaled units above. Without `max_solves`, a local method runs at
most 50 points.

## Bayesian optimisation

`bayesian` starts from the Latin hypercube of `samples` points. It then fits a Gaussian
process to the objective and to each margin (`surrogates.py`, in numpy and scipy).

**The model.** The responses are standardised, and the correlation between points a
and b is Matérn 5/2 with a length scale $\ell_k$ per variable:

$$d = \sqrt{\sum_k \left( \frac{a_k - b_k}{\ell_k} \right)^2}$$

$$k(d) = \sigma_f^2 \left( 1 + \sqrt{5} d + \frac{5 d^2}{3} \right) e^{-\sqrt{5} d}$$

The length scales, signal $\sigma_f^2$ and noise come from maximising the marginal
likelihood (L-BFGS-B from two fixed starts, so the same data always give the same fit).
Length scales are bounded to 0.01 to 10 of the range, the signal to 0.05 to 20, and the
noise to $10^{-8}$ to 0.5. The noise term absorbs a remeshed point's scatter. The model
gives a mean μ and a standard deviation σ at any new point.

**The next point.** With $f^*$ the best feasible objective so far and
$z = (f^* - \mu) / \sigma$, the expected improvement is:

$$EI = \sigma \left( z \Phi(z) + \varphi(z) \right)$$

Here Φ and φ are the standard normal distribution and density. The probability that a
margin holds is $\Phi(\mu_g / \sigma_g)$. Puzzle picks the candidate with the largest EI
times the product of those probabilities. Before any point is feasible, it picks the
most likely feasible one. The candidates are 2048 Sobol points over the range plus 512
around the best point, snapped and not yet run. The search stops when the best score is
below `tolerance`, or at `samples` + 20 points.

## Response surfaces

`response_surface` starts from the same Latin hypercube and fits a surface to the
objective and each margin (`surface`):

- **rbf**: scipy's thin-plate `RBFInterpolator` with a linear part, through every point.
- **quadratic**: a full quadratic by least squares. It has
  $(d + 1)(d + 2) / 2$ coefficients, so it needs at least that many points.

It searches the surface (Sobol candidates, then COBYLA on the surface) for the best
feasible point. It solves that point to check it and refits. It stops when the
surface's best point is already solved, or at `samples` + 10 points.

**Fit quality** is the leave-one-out error. Each point is predicted by a surface fitted
to the others, and the RMS of those errors is divided by the output's range (0.02 is
2 %). A large value means the surface is guessing.

## Robust design

`robust` designs for scatter. A random variable (a load, a modulus) stays at its value
but scatters: normal, log-normal (with the value as its mean) or uniform, with a spread
or a coefficient of variation. A chosen variable may scatter too, as a manufacturing
tolerance. The method works in these steps:

1. Solve a Latin hypercube over the chosen ranges, widened by their scatter, and each
   random variable's ±3σ (a log-normal by its quantiles, a uniform by its half-width).
2. Fit a response surface through the solved points.
3. Judge candidate designs (256 Sobol points, plus as many around the last choice) by
   Monte Carlo on the surface, 2000 samples each. The objective's `statistic` is its
   nominal value, its mean, or mean + `sigmas` × standard deviation. A constraint with
   `probability` p must hold in at least that share of the samples.
4. Solve the chosen design, and the sample at each constraint's 1 − p margin quantile,
   then refit. Stop when the design moves less than the tolerance.

The same seeded draws are used for every candidate (common random numbers). The noise
then cancels when two designs are compared. The final summary uses `monte_carlo`
samples (20 000 by default) and reports each output's mean, standard deviation, the
0.135 % and 99.865 % points (±3σ for a normal), and each probability of holding.

## The Pareto front

When an objective has been turned into constraints, `pareto` lists the feasible points
that no other feasible point beats on every output. The objective counts by its sense,
and a constraint is better further from its limit. The front is the trade-off to choose
from.

## How Puzzle does it

Each point is applied to a copy of the project (`sweep.apply`). The study is run with
the point's labels, stored and cached by its run key. A shoot-out study runs every
solver per point, and `reads` takes the primary solver's outputs or the worst. Applying
the best point is one batch of edits and one undo, never automatic. Set an optimisation
up as in [Optimisation and mesh convergence](help:guides/optimisation-and-convergence),
and run it with the CLI's [optimise](help:cli:optimise) or MCP
[optimisation_run](help:mcp:optimisation_run).

## Limits

- Each point is a real solve; the methods trade solves for certainty. Surrogate methods
  are only as good as their fit, so check the fit quality, and trust a surrogate's
  answer only once the point it chose has been solved, which Puzzle always does.
- Snapping to the resolution grid limits how close any method gets to a limit, and
  COBYLA stalls in narrow curved valleys on that grid.
- Mesh changes between points add noise to the outputs. The Gaussian process allows
  for it; the local methods do not, so keep the mesh's sizes steady across the range.
- The probability of feasibility multiplies each margin's probability, as if the
  constraints were independent.
- Correlation sensitivities miss effects that rise then fall, and interactions between
  variables.
- Robust design's probabilities come from the surface, not from solves. A probability
  near 1 depends on the surface's tails, which the probability checks test only at one
  sample each.

## Sources

- M. J. D. Powell, "A direct search optimization method that models the objective and
  constraint functions by linear interpolation," in Advances in Optimization and
  Numerical Analysis, Kluwer, 1994 (COBYLA).
- J. A. Nelder and R. Mead, "A simplex method for function minimization," The Computer
  Journal 7, 1965.
- R. Storn and K. Price, "Differential evolution: a simple and efficient heuristic for
  global optimization over continuous spaces," Journal of Global Optimization 11, 1997.
- M. D. McKay, R. J. Beckman and W. J. Conover, "A comparison of three methods for
  selecting values of input variables in the analysis of output from a computer code,"
  Technometrics 21, 1979 (Latin hypercube).
- I. M. Sobol', "On the distribution of points in a cube and the approximate evaluation
  of integrals," USSR Computational Mathematics and Mathematical Physics 7, 1967.
- C. E. Rasmussen and C. K. I. Williams, Gaussian Processes for Machine Learning, MIT
  Press, 2006 (the Matérn class, the marginal likelihood).
- D. R. Jones, M. Schonlau and W. J. Welch, "Efficient global optimization of expensive
  black-box functions," Journal of Global Optimization 13, 1998 (expected improvement).
- A. I. J. Forrester, A. Sóbester and A. J. Keane, Engineering Design via Surrogate
  Modelling, Wiley, 2008 (response surfaces, radial basis functions, constrained
  expected improvement).
- SciPy's `scipy.optimize` and `scipy.stats.qmc` documentation, for the
  implementations Puzzle calls.
