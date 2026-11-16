---
title: Time integration for heat
section: theory
order: 100
keys: theory:time-integration
summary: How a transient heat step marches the temperature in time, what each time scheme costs in accuracy, and why backward Euler is the default.
adrs: 0023, 0037
---

A transient heat step answers how a temperature field changes after something happens:
a face is suddenly held hot, a heater switches on, a film cools a part. The solver
cannot solve all times at once, so it steps: from the temperatures at one time it
finds those a time step later. How it takes that step (the time scheme) decides how
accurate the history is and how it behaves right after a sudden change.

## The equation in time

After meshing, conduction is one equation per node:

$$\mathbf{C}\dot{\mathbf{T}} + \mathbf{K}\mathbf{T} = \mathbf{F}$$

$\mathbf{C}$ is the heat capacity (from density and specific heat, $\rho c_p$),
$\mathbf{K}$ the conduction and film terms, and $\mathbf{F}$ the heat fluxes, sources
and film temperatures. A held temperature is a constraint on $\mathbf{T}$.

## The schemes

**The theta method** weighs the two ends of a step:

$$\mathbf{C}\frac{\mathbf{T}_{n+1} - \mathbf{T}_n}{\Delta t} + \theta\mathbf{K}\mathbf{T}_{n+1} + (1-\theta)\mathbf{K}\mathbf{T}_n = \mathbf{F}$$

- $\theta = 1$ is **backward Euler** (`backward_euler`): first order in time, the error
  falling in proportion to $\Delta t$. Every mode of the solution decays, however large
  the step, and the fast ones are damped hardest, so it never oscillates.
- $\theta = 1/2$ is **Crank-Nicolson** (`crank_nicolson`): second order. It is stable
  for any step, but it barely damps the fastest modes: after a jump (a face suddenly
  held hot) they flip sign from step to step and the history oscillates near the jump.
- `theta` takes a `theta` setting between 0.5 and 1 (`common.time_scheme` refuses
  anything else: below 0.5 the scheme is only conditionally stable).

**BDF2** (`bdf2`) uses the two previous steps:

$$\mathbf{C}\frac{3\mathbf{T}_{n+1} - 4\mathbf{T}_n + \mathbf{T}_{n-1}}{2\Delta t} + \mathbf{K}\mathbf{T}_{n+1} = \mathbf{F}$$

It is second order and, like backward Euler, damps the fast modes, so it is accurate
without Crank-Nicolson's oscillation. Its first step is a backward Euler step, since
there is no earlier one.

On the transient conduction benchmark (a steel bar at 20 °C with one end held at
100 °C, 10 s steps to 600 s, against the series solution), backward Euler's largest
error is about 0.35 K of the 80 K rise; BDF2's is under 0.02 K
(`tests/integration/test_benchmarks.py`).

## Which solver offers which

| Scheme | CalculiX | Code_Aster | Elmer | FEniCSx |
| --- | --- | --- | --- | --- |
| Backward Euler | yes | yes | yes | yes |
| Crank-Nicolson | | yes | yes | yes |
| Theta | | yes | yes | yes |
| BDF2 | | | yes | yes |

A scheme a solver doesn't offer is refused, never replaced. CalculiX steps heat by
backward Euler only (`*HEAT TRANSFER, DIRECT`, the increment fixed). Code_Aster's theta
method is `PARM_THETA`; its own default is 0.57, Puzzle writes 1 unless asked
otherwise. Elmer's schemes are `BDF` (order 1 or 2), `Crank-Nicolson`, and `Newmark`
with `Newmark Beta` = theta, which on a first-order equation is the theta method.

## Why backward Euler is the default

The default is backward Euler on every solver, so a shoot-out compares like with like.
It is the one scheme all four FE solvers have. Under it, the four agree with the series
solution to within 0.34 % and with each other to about three decimals, so a difference
in a shoot-out means a difference in the model, not in the scheme. It is also robust:
it doesn't oscillate after a sudden load. Choose BDF2 for accuracy on Elmer or FEniCSx,
or a smaller time step with any scheme.

## The start after a sudden load

A face held at a new temperature from t = 0 is a jump: the starting field and the
boundary value disagree. Backward Euler and BDF2 agree across the solvers from the
first step. With $\theta < 1$ the starting state matters, and solvers treat it
differently:

- Code_Aster and FEniCSx start from the given initial field.
- Elmer appears to apply the boundary values to the starting state first, and its
  Crank-Nicolson damps the initial jump.
- Plain Crank-Nicolson from a stepped start oscillates (18.7 K in a FEniCSx
  experiment).

So theta-scheme results differ between Elmer and the other two after a sudden load.
Prefer backward Euler or BDF2 there.

## Temperature-dependent materials

By default a heat study uses each material's temperature tables (conductivity,
specific heat, density), interpolated linearly between their points. Then
$\mathbf{C}$ and $\mathbf{K}$ depend on $\mathbf{T}$ and every step is a nonlinear
solve. The physics setting `temperature_dependent_materials = false` uses the single
values instead, and the problem is linear again.

- **FEniCSx** iterates by Picard: the properties are taken at the last iterate and a
  linear system solved, until the largest change falls below the tolerance (1e-10 of
  the largest temperature by default, up to 100 iterations).
- **Elmer** uses `Variable Temperature` tables with nonlinear iterations.
- **CalculiX** takes value/temperature lines, with tight `*CONTROLS, PARAMETERS=FIELD`
  (its defaults stopped 0.2 K short).
- **Code_Aster** runs `THER_NON_LINE` with the conductivity as a function and the heat
  capacity as an enthalpy function, stepping the enthalpy:
  $(H(T_{n+1}) - H(T_n))/\Delta t$ in place of $\rho c_p \dot{T}$.

The two forms differ by a term of order $\Delta t$ where $c_p$ varies steeply. In the
Eurocode steel shoot-out (heating to 700 °C, $c_p$ from 606 to 1008 J/(kg K)),
Code_Aster was 0.7 K warmer at 5 s steps and 0.15 K at 1 s, the closer of the two to
the converged answer (`tests/integration/test_nonlinear_conduction.py`). The steady
case is checked against the Kirchhoff transform, which is exact for a table
interpolated linearly: all four solvers came within 0.002 K of it (the test allows
0.05 K).

## How Puzzle does it

- A transient step takes `time_step`, `end_time` and `time_scheme` (plus `theta`), and
  heat physics takes `initial_temperature`; see
  [Choosing an analysis](help:guides/analyses).
- `common.time_scheme` checks the scheme; each adapter writes it, and the capability
  matrix's `time_schemes` says which a solver takes
  ([capabilities](help:reference/capabilities)).
- Results come back as one step per output time, the step's value the time.

## Limits

- Backward Euler's error is first order: halving the step halves it. Check a history
  by running it again at half the step.
- Stable is not accurate: a long step stays bounded but smears a sudden change.
- In heat steps only heat flux follows an amplitude so far; held temperatures and the
  other loads act at full value from t = 0.
- Structural transients use Newmark instead; see
  [Structural dynamics](help:theory/structural-dynamics).

## Sources

- J. Crank, P. Nicolson, "A practical method for numerical evaluation of solutions of
  partial differential equations of the heat-conduction type", Proceedings of the
  Cambridge Philosophical Society 43, 1947, 50-67.
- T. J. R. Hughes, The Finite Element Method: Linear Static and Dynamic Finite Element
  Analysis, Prentice-Hall 1987 (Dover 2000): the generalised trapezoidal (theta) family
  for parabolic problems, its stability and accuracy.
- H. S. Carslaw, J. C. Jaeger, Conduction of Heat in Solids, 2nd edition, Oxford 1959:
  the series solution of the benchmark bar.
- EN 1993-1-2, Eurocode 3, Design of steel structures, Part 1-2, structural fire
  design: the steel tables of the nonlinear benchmark.
