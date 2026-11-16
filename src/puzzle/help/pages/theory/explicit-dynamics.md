---
title: Explicit dynamics
section: theory
order: 110
keys: theory:explicit-dynamics
summary: How an explicit run steps an impact or short pulse in time, what sets its time step, and what its energy checks tell you.
adrs: 0085
---

Impact, drops, crashes and short pulses happen over microseconds to milliseconds, with
large motions, contact switching on and off, and materials yielding or breaking. An
implicit solver would need a factorised stiffness and Newton iterations at every tiny
step. An explicit solver takes millions of cheap steps instead, none of them needing a
matrix solve. Puzzle runs these on OpenCourant, through an `explicit` step.

## Central differences

With a lumped (diagonal) mass matrix $\mathbf{M}$, the equation of motion gives each
node's acceleration directly from the forces at the current time:

$$\ddot{\mathbf{u}}_n = \mathbf{M}^{-1} \left( \mathbf{F}^{ext}_n - \mathbf{F}^{int}_n \right)$$

and the central difference scheme moves on by half steps:

$$\dot{\mathbf{u}}_{n+1/2} = \dot{\mathbf{u}}_{n-1/2} + \Delta t\,\ddot{\mathbf{u}}_n$$

$$\mathbf{u}_{n+1} = \mathbf{u}_n + \Delta t\,\dot{\mathbf{u}}_{n+1/2}$$

The internal forces $\mathbf{F}^{int}$ are summed element by element from the current
shape and the material law, so a large deformation, plasticity or a contact costs no
more per step than a linear spring. Nothing is inverted but a diagonal.

## The stable time step

The price is a limit on the step. Central differences are stable only while a stress
wave cannot cross an element in one step:

$$\Delta t \leq \frac{L}{c}$$

$L$ is the element's characteristic length and $c$ its wave speed. The ratio
$c\,\Delta t / L$ is the Courant number. The solver takes a share of the smallest
element's limit, `time_step_scale` (default 0.9), and that one element sets the step for
the whole model.

Puzzle's estimate (`puzzle/explicit.py` `estimate`) uses, per element:

| Element | Wave speed c | Length L |
| --- | --- | --- |
| Solid tetrahedron (tet4) | dilatational | its smallest height, 3V / A_max |
| Ten-node tetrahedron (tet10) | dilatational | 0.31 of its corner tetrahedron's height |
| Brick (hex8) | dilatational | V / A_max (its edge, for a cube) |
| Triangle shell | plate | its smallest height |
| Quadrilateral shell | plate | its area over its longest side |
| Beam | bar | its length |

The dilatational wave speed in a solid, the plate speed in a shell and the bar speed in
a beam are

$$c_{solid} = \sqrt{\frac{E(1-\nu)}{\rho(1+\nu)(1-2\nu)}}$$

$$c_{shell} = \sqrt{\frac{E}{\rho(1-\nu^2)}}$$

$$c_{beam} = \sqrt{\frac{E}{\rho}}$$

The ten-node factor `TET10_SHARE` = 0.31 is measured, not textbook: halving the corner
tetrahedron's height (the node spacing) gave a step 63 % longer than OpenCourant's own,
since the mid-edge nodes and the mass they carry shorten it further. Against
OpenCourant's own step the estimates came out hex8 +5.1 %, tet4 +5.1 %, tet10 +1.0 %,
quadrilateral shells +8.3 %, beams +1.4 %. The solver's own step decides; the estimate
is for planning.

The cost is the cycle count, end_time / (scale × Δt). Puzzle warns when a run would
pass 10⁷ cycles, and when one element's step is under a fifth of the tenth smallest's
(a sliver or a small feature: remeshing it speeds the whole run). MCP
[explicit_estimate](help:mcp:explicit_estimate) gives the same numbers.

## Mass scaling and added mass

Since $c = \sqrt{E/\rho}$ up to Poisson factors, an element's step grows with the
square root of its mass. Mass scaling (`mass_scaling_time_step`) adds nodal mass where
an element's step would be under the target, so no element is slower than that. Each
such element gains

$$\Delta m = m \left( \left( \frac{\Delta t_{target}}{\Delta t_e} \right)^2 - 1 \right)$$

When a run would pass 10⁷ cycles, Puzzle suggests the target that brings it to about
10⁶ and says the mass that would add. Added mass changes inertial results: it is
harmless in a few small elements of a slow event, wrong where the added mass moves
with the load. After the run, added mass over 5 % of the model's is a warning
(`added_mass`).

## The energy balance

An explicit run has no equilibrium check at each step, so its energy is the check.
What the model holds (kinetic, internal, rotational, contact and hourglass energy)
must equal what it started with plus the work done on it:

$$E_{kin} + E_{int} + E_{rot} + E_{contact} + E_{hg} = E_0 + W_{ext}$$

`explicit.run_checks` reads these from the time history and warns (`energy_error`)
when the largest difference over the run passes 5 % of the largest energy. Causes are a
time step scale too large, or a contact or wall making or losing energy. The Solution
panel plots the Engine's own energy error as the run goes.

## Hourglass energy

Eight-node bricks integrated at one point have deformation modes that produce no strain
there: hourglass modes, which can grow unresisted. Puzzle writes bricks with the HEPH
formulation (`Isolid` 24), whose stabilisation is physical (a stiffness) rather than
viscous, and tetrahedra need none. The energy those stabilising forces store is
hourglass energy; when it passes 10 % of the internal energy the zero-energy modes are
taking part in the answer, and `hourglass` is warned: refine there or use tetrahedra.

## Johnson-Cook plasticity and failure

For metals under impact the flow stress rises with strain and strain rate. Johnson and
Cook's law (`Material.models.johnson_cook`, `/MAT/PLAS_JOHNS`) is

$$\sigma_y = \left( A + B\varepsilon_p^n \right) \left( 1 + C \ln\frac{\dot{\varepsilon}}{\dot{\varepsilon}_0} \right)$$

with `a`, `b`, `n`, `c` and `reference_strain_rate` $\dot{\varepsilon}_0$, an optional
`max_stress` cap, and no temperature term (an explicit run here doesn't heat). The rate
a part reads includes its ringing: unfiltered, a bar's axial ringing raised the flow
stress 4 % in the benchmark, because the swings above $\dot{\varepsilon}_0$ count and
those below don't. `rate_cutoff` (Hz) low-passes the rate the law reads; below the
part's ringing it reads the mean rate.

Failure (`Material.models.failure`, `/FAIL/JOHNSON`) is Johnson and Cook's strain to
fracture:

$$\varepsilon_f = \left( D_1 + D_2 \exp(D_3\sigma^*) \right) \left( 1 + D_4 \ln\frac{\dot{\varepsilon}}{\dot{\varepsilon}_0} \right)$$

$\sigma^*$ is the triaxiality, mean stress over von Mises. Damage adds
$\Delta\varepsilon_p/\varepsilon_f$ each step; an element whose damage reaches 1 is
deleted (eroded). D1 alone is a constant failure strain. The temperature term (D5) is
written as 0. Failure needs plasticity (a table or Johnson-Cook). Results: `eroded` per
element and the global `eroded_elements`; nodal stresses average the elements left.

## Contacts and walls

An explicit code finds contact by searching for nodes that come within a gap of the
other side's faces, and pushes them back with a penalty force; contact switching on
and off needs no iterations, unlike Newton's in an implicit solve. Puzzle writes:

- bonded across meshes that don't match as `/INTER/TYPE2` (the source's nodes tied to
  the target's faces); a conformal bond shares nodes;
- frictionless and frictional as `/INTER/TYPE7` both ways, with INACTI 6, so parts that
  touch at the start aren't pushed apart: the gap shrinks for those nodes instead;
- self-contact (a selection against itself) as one `/INTER/TYPE7`; where faces start
  closer than Radioss's own gap (a tenth of the smallest edge), the gap is set to half
  that distance (`deck.self_gap`), since starting inside the gap blew one run up;
- a [rigid wall](help:feature:rigid_wall) as `/RWALL/PLANE`: a kinematic condition that
  stops the nodes that reach it, so a little momentum goes each impact (a bar leaving at
  0.964 of its speed on 40 elements, 0.976 on 80).

## How Puzzle does it

- The step's settings: `end_time`, `frames` or `frame_interval`, `history_interval`,
  `time_step_scale`, `mass_scaling_time_step`, `precision`; see
  [Choosing an analysis](help:guides/analyses).
- `adapters/opencourant/deck.py` writes the Starter and Engine decks; the run goes two
  steps past the end time so the last frame is written. Results are one step per frame,
  with the energies as global fields.
- Loads may follow an amplitude; [initial velocity](help:feature:initial_velocity) and
  [velocity](help:feature:velocity) are explicit-only features.
- Benchmarks (`tests/integration/test_explicit.py`): the wave behind a front
  σ = ρ c v₀ within 0.27 % (hex8), a suddenly loaded cantilever within 0.3 % of
  CalculiX's implicit transient, a bar pulled past yield −1.6 %, Johnson-Cook hardening
  +0.05 %.

## Limits

- Anything slower than a few periods of the lowest mode belongs in an implicit
  transient ([structural dynamics](help:theory/structural-dynamics)).
- Second-order bricks are refused; pressure only on first-order faces.
- No heat, bolts, welds, threads, symmetry equations, or beam forces as results.
- Hourglass control and mass scaling change the answer: read the checks after every
  run.

## Sources

- T. Belytschko, W. K. Liu, B. Moran, Nonlinear Finite Elements for Continua and
  Structures, Wiley 2000: central differences, the stable time step, hourglass control.
- G. R. Johnson, W. H. Cook, "A constitutive model and data for metals subjected to
  large strains, high strain rates and high temperatures", Proceedings of the 7th
  International Symposium on Ballistics, The Hague, 1983.
- G. R. Johnson, W. H. Cook, "Fracture characteristics of three metals subjected to
  various strains, strain rates, temperatures and pressures", Engineering Fracture
  Mechanics 21 (1), 1985, 31-48.
- OpenCourant (the community continuation of OpenRadioss, AGPL-3.0), release
  latest-20261011: its card layouts (`hm_cfg_files`) for `/MAT/PLAS_JOHNS`,
  `/FAIL/JOHNSON`, `/INTER/TYPE7` and `/DT/NODA/CST`.
