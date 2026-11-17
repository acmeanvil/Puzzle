---
title: Flow (CFD)
section: theory
order: 160
keys: theory:cfd
summary: How Puzzle solves steady incompressible flow, laminar or turbulent, with heat carried by the flow, on OpenFOAM and Code_Saturne, and how far to trust the result.
adrs: 0012, 0016, 0048
---

Puzzle solves steady incompressible flow: a fluid of constant density and viscosity
entering through inlets, leaving through outlets and held by walls, laminar or
turbulent, optionally carrying heat. It runs on two finite-volume codes, OpenFOAM
(simpleFoam) and Code_Saturne, which take the same model, so one can check the other.

## The equations

For an incompressible fluid of density $\rho$ and viscosity $\mu$, steady flow
satisfies mass and momentum conservation:

$$\nabla \cdot \mathbf{u} = 0$$

$$\rho (\mathbf{u} \cdot \nabla) \mathbf{u} = -\nabla p + \nabla \cdot \left( (\mu + \mu_t) (\nabla \mathbf{u} + \nabla \mathbf{u}^T) \right)$$

where $\mu_t$ is the turbulent viscosity (zero for laminar flow). The Reynolds number
$Re = \rho U D / \mu$ ($U$ a mean speed, $D$ a size such as a channel's height) says
which applies: the benchmarks are laminar at $Re = 10$ and turbulent at $Re = 10^5$.

## SIMPLE: solving for pressure and velocity

Pressure has no equation of its own; it is whatever keeps the velocity
divergence-free. The SIMPLE algorithm (Patankar and Spalding) iterates: solve momentum
with the last pressure, correct the pressure so the mass fluxes balance, correct the
velocities, and repeat with under-relaxation until nothing changes. Each iteration
counts as one step towards the step's `iterations` setting.

- **OpenFOAM:** simpleFoam with SIMPLEC (`consistent`), velocity under-relaxed by 0.9,
  second-order upwind convection (`linearUpwind`) for velocity and temperature,
  `limitedLinear` for k, ε and ω; at most 2000 iterations by default, stopping once
  every residual is below its `residualControl` entry ($10^{-6}$ for pressure,
  $10^{-7}$ for velocity, $10^{-6}$ for the turbulence fields, $10^{-8}$ for
  temperature). The step's `tolerance` sets pressure's and velocity's.
- **Code_Saturne:** its steady algorithm for 500 iterations by default, with no
  stopping test; Puzzle calls it converged when every residual has fallen three
  decades from its largest, and warns otherwise. A `pseudo_time_step` marches in time
  with that step instead, which reaches steady state much faster when a slow diffusive
  mode remains (axial conduction in a heated channel).

Puzzle follows each run's residuals as it goes (ADR 0054); a run that ends without
meeting its criteria is flagged as not converged, and its answer should be checked.

## Laminar and turbulent flow

Turbulent flow is solved as its time average (RANS): the turbulence adds a viscosity
$\mu_t$ computed from two transported quantities. The physics' `turbulence_model` is
`laminar` (default), `k-epsilon` or `k-omega-SST`.

- **k-ε** transports the turbulent kinetic energy $k$ and its dissipation $\varepsilon$,
  with $\mu_t = \rho C_\mu k^2 / \varepsilon$, $C_\mu = 0.09$ (Code_Saturne's
  linear-production variant).
- **k-ω SST** (Menter) transports $k$ and the specific dissipation $\omega$, using k-ω
  near walls and k-ε away from them.

Neither resolves the thin layer next to a wall; **wall functions** bridge it with the
log law. They expect the first cell's centre in the log layer, at
$y^+ = \rho u_\tau y / \mu$ of roughly 30 or more ($u_\tau = \sqrt{\tau_w/\rho}$);
the turbulent benchmark puts it near 200. OpenFOAM's walls use `nutkWallFunction`,
`kqRWallFunction` and `epsilonWallFunction`/`omegaWallFunction`; the wall export
reports $y^+$ per face so you can check.

## Inlet turbulence

A turbulent inlet takes an **intensity** $I$ (a fraction, default 0.05) and a
**hydraulic diameter** $D_h$ (default $4A/P$ of the inlet face). On OpenFOAM, with
$\ell = 0.07 D_h$ the mixing length and $U$ the inlet speed:

$$k = \frac{3}{2} (U I)^2$$

$$\varepsilon = C_\mu^{3/4} \frac{k^{3/2}}{\ell}, \quad \omega = \frac{k^{1/2}}{C_\mu^{1/4} \ell}$$

which also start the whole domain. Code_Saturne takes the intensity (in percent) and
$D_h$ directly and works its inlet values out itself.

## Heat carried by the flow

With heat transfer, temperature is carried by the computed flow and conducted through
it, with inlet or wall temperatures, wall heat flux (positive into the fluid) and, on
Code_Saturne only, an exchange coefficient. Properties are constant, so the heat does
not change the flow: it is one-way coupled. OpenFOAM solves temperature as a passive
scalar (`scalarTransport`, diffusivity $\nu/Pr + \nu_t/Pr_t$, `turbulent_prandtl`
default 0.85); Code_Saturne as its thermal scalar. Under wall functions $\nu_t$ is not
zero on a wall face, so OpenFOAM's wall heat flux is imposed as the gradient
$q / (k + \rho c_p \nu_t / Pr_t)$, each face's own: then exactly $q$ goes in (a
gradient of $q/k$ put in 1.5 to 2.4 times as much). Buoyancy and temperature-dependent
properties are not modelled.

## 2D flow

OpenFOAM and Code_Saturne solve only in 3D, so a 2D study's mesh is extruded one cell
thick (`mesh/layer.py`). A planar model's front and back faces are OpenFOAM `empty`
(Code_Saturne symmetry); an axisymmetric model is turned into a 5° wedge
(OpenFOAM `wedge`, Code_Saturne symmetry, which there means axisymmetry with no swirl).
Code_Saturne's steady algorithm runs away on a wedge, so Puzzle marches it in time at
half a cell's crossing time unless a pseudo time step is set. Results come back to the
2D elements.

## How Puzzle does it

- The fluid is a material with density and dynamic viscosity (and conductivity and
  specific heat for heat); features are [velocity inlet](help:feature:velocity_inlet),
  [pressure outlet](help:feature:pressure_outlet), [wall](help:feature:wall) and
  [symmetry](help:feature:fluid_flow:symmetry); boundary faces in no selection are walls.
- The study is one stationary step; the solvers are set as in
  [Studies and solvers](help:guides/studies-and-solvers).
- Results are per cell: velocity, pressure (in Pa: OpenFOAM's kinematic pressure is
  multiplied by $\rho$), temperature, and $k$, $\varepsilon$ or $\omega$ and the
  turbulent viscosity.

## Limits and accuracy

Measured by the benchmarks:

- **Laminar channel** (plane Poiseuille, $Re = 10$; `benchmarks/channel_flow`): on
  Code_Saturne the peak velocity −0.23 %, the profile 0.21 %, $dp/dx$ −0.95 %. The
  two codes agree on velocity to 0.16 % in L2.
- **Heated channel** (uniform wall flux): the bulk temperature's rise within 1 % of the
  energy balance and the developed profile within 2 % of $Nu = 8.235$'s in the test.
- **Turbulent channel** at $Re = 10^5$ against Dean's correlations,
  $C_f = 0.073 Re^{-1/4}$ and $U_c/U = 1.28 Re^{-0.0116}$: friction from −3.2 % to
  −8.2 % depending on code and model, the centreline ratio within 2.6 %. The test
  allows 10 % and 3 %.
- **Wall functions need a developed log layer.** At $Re = 13300$ ($Re_\tau \approx
  390$) both codes under-predicted friction by 13-16 % on two meshes. Below
  $Re \approx 10^5$ treat RANS wall friction with care.
- RANS gives the mean flow only: no unsteadiness, separation bubbles that shed, or
  transition. Only steady flow is solved.
- Check convergence (the residuals) and the mesh (a finer one should not change the
  answer you need).

## Sources

- S. V. Patankar, *Numerical Heat Transfer and Fluid Flow*, Hemisphere, 1980: the
  finite-volume method and SIMPLE.
- H. K. Versteeg and W. Malalasekera, *An Introduction to Computational Fluid Dynamics:
  The Finite Volume Method*, 2nd ed., Pearson, 2007: SIMPLE, RANS models, wall
  functions, inlet turbulence.
- B. E. Launder and D. B. Spalding, "The numerical computation of turbulent flows",
  *Computer Methods in Applied Mechanics and Engineering* 3 (1974) 269-289: k-ε and
  wall functions.
- F. R. Menter, "Two-equation eddy-viscosity turbulence models for engineering
  applications", *AIAA Journal* 32 (1994) 1598-1605: k-ω SST.
- R. B. Dean, "Reynolds number dependence of skin friction and other bulk flow
  variables in two-dimensional rectangular duct flow", *Journal of Fluids Engineering*
  100 (1978) 215-223: the turbulent channel benchmark.
- R. K. Shah and A. L. London, *Laminar Flow Forced Convection in Ducts*, Academic
  Press, 1978: $Nu = 8.235$ between plates with uniform heat flux.
