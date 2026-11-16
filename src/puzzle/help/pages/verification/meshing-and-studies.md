---
title: Meshing, convergence and studies
section: verification
order: 90
keys: verification:meshing-and-studies
summary: Mesh convergence, error estimates and adaptive refinement, mesh and material sweeps, optimisation, and the result tools, each against a closed form or an exact field.
adrs: 0018, 0019, 0042, 0046, 0048, 0063, 0065, 0066
---

These benchmarks check the tools around a solve rather than a solver: that a
convergence study reports what the mesh really does, that an error estimate tracks the
true error, that an optimiser lands on the optimum worked by hand, and that a probe,
a reaction or a linearized stress reads back beam theory from a solved run. Most run on
CalculiX, the reference solver for these tools; the error estimates and optimisers
also have unit tests on exactly known fields.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Convergence, quadratic tets | Timoshenko tip deflection | CalculiX | extrapolated 1.5 %, GCI below 0.2 % | 3.5013 mm (−0.8 %), GCI 0.03 % |
| Convergence, linear tets | "not converged" | CalculiX | GCI above 10 %, extrapolation nearer | GCI 26 % |
| ZZ estimate on a cantilever | falls with h, at the root | CalculiX | finest 1-4 %, falling | 3.17, 2.23, 2.12 % |
| ZZ estimate on exact fields | exact energy-norm error | none (unit test) | effectivity 0.7-1.3; orders ±0.2, ±0.35 | effectivity 1.01; orders 1.0, 1.96 |
| Adaptive refinement, Gmsh | converged tip deflection | CalculiX | 0.2 %, error 1.5 %, below 3037 elements | 1.24 % in 1273 elements |
| Adaptive refinement, Netgen | falling error | CalculiX | below 2.5 % in three passes | 3.94 → 1.71 % |
| Adaptive refinement, plate with a hole | Heywood K_t 3.03; a uniform mesh as fine | CalculiX | 0.5 % error, K_t 5 % | 0.46 % in 3305 elements, K_t +3.7 %; uniform 0.89 % in 3998 |
| A stress at a singularity | Williams' corner exponent | none (unit test) | reported diverging | diverging |
| Anisotropic adaptation, MMG | erfc heat layer | CalculiX | 0.7 K, half the isotropic error | 0.053 % vs 0.14 %, within 0.6 K |
| Mesh shoot-out | Gmsh against Netgen | CalculiX | 1 % on peak displacement | 0.005 % |
| Material sweep | tip deflection ∝ 1/E | CalculiX | 1e-5 | passes |
| Design of experiments | 4FL³/(Ewt³), best t 8 mm | CalculiX | 3 % per point | best 8 mm |
| Bayesian and response surface | thinnest t on the 2 mm limit | CalculiX | t 4 %, deflection 1.96-2 mm | 7.24 and 7.25 mm |
| Robust design | t + 2.326σ at 99 % | CalculiX | ±0.04 mm, 99 ± 0.5 % | 7.48 mm, 99.21 % |
| Thickness by morphing | 4FL³/(Ewt³) | CalculiX | 3 % per point | passes |
| Optimisers on closed forms | hand optimum, Rosenbrock | none (unit test) | 0.2 % to 0.5 % | passes |
| Reaction force and moment | P, PL | CalculiX | 1e-5 | 100.0003 N, 20.000006 N·m |
| Mean tip deflection, path | PL³/3EI, beam curve | CalculiX | 3 % | −0.20 %, within 0.57 % |
| Stress linearization | M c / I = 60 MPa | CalculiX | 2 % | 59.9, 60.1 MPa |
| Section force on a plane | V = 100 N, M = 10 N·m | CalculiX | 2 % | passes |
| Axisymmetric section of a tube | Lamé | CalculiX, Code_Aster, FEniCSx, Elmer | 0.5 % | passes |

Run one with `pixi run -e dev pytest tests/integration/test_mesh_convergence.py`, and
so on. Tests that need CalculiX, Netgen or MMG skip without them. The methods are in
[Mesh convergence](help:theory/mesh-convergence),
[Error estimates](help:theory/error-estimates) and
[Optimisation](help:theory/optimisation); the workflow in
[Optimisation and convergence](help:guides/optimisation-and-convergence).

## The cantilever behind most of these

The convergence, error-estimate, adaptivity and optimisation benchmarks share one
model (`tests/optimise_beam.py`): a steel cantilever 200 × 20 × t mm, t = 6 mm unless
an optimiser moves it, E = 210 GPa and ν = 0.3 (the generic structural steel),
clamped at one end with 100 N at the other. Its tip deflection with shear, the
reference for convergence, is Timoshenko's:

$$\delta = \frac{F L^3}{3 E I} + \frac{F L}{\kappa G A}, \quad \kappa = \frac{5}{6}$$

3.5297 mm. The beam is wide (20 mm against 6 mm deep), so the plate's Poisson
stiffening puts the true 3D answer a little below it.

## Mesh convergence

`tests/integration/test_mesh_convergence.py` (CalculiX) solves the cantilever on three
meshes 1.5 apart and applies Roache's grid convergence index to the tip deflection.

- `test_second_order_tets_converge_to_the_beam`: second-order tets from 8 mm. The
  verdict must be "converged" with a GCI below 0.2 %, the extrapolated value within
  1.5 % of Timoshenko's and within 0.1 % of the finest mesh. Measured (the test's
  docstring and ADR 0066, CalculiX 2.22): 3.4944, 3.4984 and 3.5004 mm, observed order
  4.1, GCI 0.03 %, extrapolated 3.5013 mm, 0.8 % under 3.5297 mm.
- `test_first_order_tets_are_not_converged`: first-order tets from 4 mm lock in
  bending. The verdict must be "not converged" with a GCI above 10 %, and the
  extrapolated value nearer the answer than the finest mesh, as Richardson
  extrapolation should be. Measured: 1.04, 2.18, 2.74 mm, GCI 26 %, extrapolated
  3.31 mm.

Both also check that a second run reads the stored levels back instead of solving
again.

## Error estimates

`tests/test_estimates.py` checks the Zienkiewicz–Zhu estimate on a unit cube cut into
n³ cubes of six tetrahedra, with fields interpolated exactly at the nodes, so the true
error is known:

- a linear displacement or temperature has constant stress or flux: the estimate and
  the jumps must be zero (below 1e-9 of the energy);
- u = (x², 0, 0) on linear tets, n = 4 and 8: the estimate against the exact
  energy-norm error

$$\|e\|^2 = \int (\sigma - \sigma_h)^T \mathbf{D}^{-1} (\sigma - \sigma_h) dV$$

(σ the exact stress, D the elasticity matrix) must give an effectivity, the estimate
over this, between 0.7 and 1.3;
- the observed order under halving h must be 1 ± 0.2 on linear tets and 2 ± 0.35 on
  quadratic tets (a cubic temperature);
- a jump in conductivity with a continuous flux gives no false error at the interface,
  since recovery is per material.

Measured (ADR 0066): effectivity 1.01, orders 1.0 and 1.96.

`tests/integration/test_error_estimates.py`
`test_the_estimate_falls_and_points_at_the_root` (CalculiX) estimates the cantilever
on three meshes. On quadratic tets the relative error must fall from mesh to mesh and
end between 1 and 4 %; on linear tets it must stay above 30 % and fall; the ten
largest element errors must lie within 20 mm of the clamped root. Measured: 3.17,
2.23 and 2.12 % on quadratic tets, flattening because the clamped root's edges are
singular; 64, 54 and 43 % on linear tets, which lock.

## Adaptive refinement

`tests/integration/test_adapt_solves.py` (CalculiX), the cantilever from 8 mm
second-order tets, adapted to a 1.5 % energy-norm error:

- `test_gmsh_adapts_to_the_target_in_fewer_elements`: converged in at most four
  passes, the last at or below 1.5 % in fewer than 3037 elements (what uniform
  refinement needed to reach 2.12 %), the error falling every pass, and the tip
  deflection within 0.2 % of the convergence benchmark's extrapolated 3.5013 mm.
  Measured: 665 → 1227 → 1273 elements, 3.17 → 1.68 → 1.24 %, in three passes.
- `test_netgen_adapts_through_its_size_file`: three passes, the error falling each
  time and below 2.5 % at the end. Measured (ADR 0066): 3.94 → 1.71 %. Netgen's size
  file can only make elements smaller.
- `test_a_plate_with_a_hole_adapts_where_the_stress_concentrates`: a quarter of a
  200 × 200 × 5 mm steel plate with a 20 mm hole, pulled at 10 MPa, symmetric on two
  faces, from 12 mm tets adapted to 0.5 % (a plate in tension is mostly uniform stress,
  so 2 % comes on the first mesh). The peak σxx at the hole's top must be within 5 % of
  Heywood's finite-width $K_t = (2 + (1 - d/W)^3)/(1 - d/W) = 3.03$, and a uniform mesh
  with at least as many elements must stay above 0.5 %. Measured (2026-11-15): 802,
  2214, 2786 and 3305 elements at 1.55, 0.62, 0.50 and 0.46 %; the peak 3.27 on the
  first mesh by nodal overshoot and 3.14 adapted (+3.7 %: a 5 mm plate's mid-plane
  peak sits above plane stress, and this one is as long as it is wide); uniform at
  4.53 mm, 3998 elements, 0.89 %.
- `tests/test_convergence.py` `test_a_stress_at_a_singularity_is_reported_diverging`:
  a peak growing as $h^{-0.4555}$, Williams' exponent at a 270° re-entrant corner,
  is reported diverging, with no order or GCI.
- `test_mmg_follows_a_heat_layer_better_than_isotropic`: a steel bar 200 × 10 × 10 mm
  at 20 °C with one end held at 100 °C for 2 s, a thin layer of heat whose exact
  solution is

$$T = T_0 + \Delta T \, \mathrm{erfc}\left( \frac{x}{2\sqrt{\alpha t}} \right)$$

adapted to 0.2 % from 10 mm tets, once by MMG's anisotropic passes and once
isotropically by Gmsh. MMG's final error must be under half the isotropic one in at
most 1.1 times the elements, and the temperature within 0.7 K of the erfc solution
everywhere. Measured: both from 403 elements at 1.75 %; MMG 1647 elements at 0.053 %,
Gmsh 1710 at 0.14 %, each in two passes, within 0.6 K (the rest is backward Euler's
time error).

`tests/test_mmg.py` checks only the shape of MMG's elements in a tanh layer (thin
across, long along, the named face kept), with no reference value, so it is not listed
as a benchmark.

## Mesh shoot-out

`tests/integration/test_mesh_shootout.py` `test_gmsh_and_netgen_agree` (CalculiX)
meshes the example cantilever's recipe with Gmsh and with Netgen and compares the
peak displacement, which must agree within 1 %. A node-by-node norm must be refused,
since the meshes have different nodes. This is a cross-check between meshers, not
against a closed form. Measured (ADR 0019): 0.005 %.

## Material sweep

`tests/integration/test_material_sweep.py` `test_a_study_runs_each_material_on_one_mesh`
(CalculiX) runs the example cantilever's tip load on steel and on 6061-T6, given the
steel's Poisson's ratio. With ν the same, the deflection scales exactly as 1/E, so the
ratio of the two runs' tip deflections must be the ratio of the moduli within 1e-5
(CalculiX's results keep five digits). Both points must share one mesh, and a second
run must reuse both. Measured: passes.

## Optimisation

`tests/integration/test_optimise_cantilever.py` (CalculiX) asks for the thinnest
cantilever whose tip deflection stays at most 2 mm. By Euler-Bernoulli

$$\delta = \frac{4 F L^3}{E w t^3}$$

- `test_thinnest_cantilever_by_design_of_experiments`: a full factorial of t from 4 to
  10 mm in 1 mm steps. Every point's deflection must be within 3 % of the formula, its
  feasibility as the formula says, and the best point 8 mm; a second run must reuse
  every stored point. Measured: the best is 8 mm.
- `test_thinnest_cantilever_by_a_model_of_the_outputs` (Bayesian and response surface,
  from four Latin hypercube points): the best t within 4 % of the hand value, its
  deflection between 98 % and 100 % of the limit, in at most 12 solves. Measured
  (ADR 0063): Bayesian 7.24 mm, 1.994 mm, 7 solves; response surface 7.25 mm,
  1.986 mm, 6 solves; COBYLA, for comparison, 7.26 mm in 5.
- `test_thinnest_cantilever_under_a_thickness_tolerance` (robust design): t made to a
  normal tolerance of σ = 0.1 mm and the limit held at 99 %. Since the deflection
  falls as t grows, the limit holds at 99 % when t's first percentile is the
  deterministic optimum on this mesh, so the answer is 7.25 mm + 2.326 σ. The test
  holds t to ±0.04 mm and the probability to 99 ± 0.5 %, in at most 26 solves.
  Measured (ADR 0063): 7.48 mm in 8 solves, mean deflection 1.810 mm, standard
  deviation 0.072 mm, the limit holding at 99.21 %.
- `test_thickness_by_morphing_the_mesh` (ADR 0065): the beam meshed once at 6 mm and
  morphed thicker by dt, so every point has the same nodes. Each deflection must be
  within 3 % of the formula at t = 6 mm + dt and fall at every step. Measured: passes.

`tests/test_optimisers.py` runs every optimiser on stand-in studies whose answer is
known exactly. The lightest section w t with 4FL³/(Ewt³) ≤ 1 mm, w from 10 to 30 mm and
t from 4 to 8 mm, has w at its lower bound and

$$t = \left( \frac{2 \times 10^{-9}}{0.01} \right)^{1/3} = 5.848 \, \mathrm{mm}$$

COBYLA and Nelder-Mead must find it within 0.2 %, the Bayesian and response-surface
methods within 0.5 % of 58.48 mm² in at most 14 points, differential evolution over a
catalogue of whole millimetres the 6 mm, 60 mm² design within 1 %; Nelder-Mead must
find Rosenbrock's minimum at (1, 1) within 0.02 and 0.04. The robust tests check the
mean and spread of a deflection under a normally scattered load (P = Φ(1) = 0.8413
within 0.01) and a design t = 6.272 mm within 0.2 % that holds a limit at 99 %.

## Result tools on a solved cantilever

`tests/integration/test_result_tools.py`, `test_linearization.py` and
`test_construction.py` share a steel bar 200 × 10 × 10 mm, clamped at x = 0, with
100 N down at the tip, on CalculiX quadratic tets. See
[Stress linearization](help:theory/linearization) and
[Results](help:guides/results).

- **Reaction** (`test_reaction_balances_the_load`): the clamp's reaction must equal
  the load within 1e-5, and its moment about the face's centroid PL = 20 N·m within
  1e-5; about the tip, the moment must vanish. Measured (ADR 0042): 100.0003 N and
  20.000006 N·m, the .frd's printed digits.
- **Over a selection** (`test_results_over_a_selection`): the tip face's mean
  deflection against PL³/3EI within 3 %, its integral the mean times the area.
  Measured: −0.20 %.
- **Path** (`test_path_follows_the_deflection_curve`): the deflection along the axis
  against the beam's curve beyond the first quarter, within 3 %:

$$v(x) = \frac{P x^2 (3L - x)}{6 E I}$$

Measured: within 0.57 %.

- **Linearization** (`test_linearization.py`
  `test_bending_across_the_depth_matches_beam_theory`): through the depth at mid-span,
  the linearized bending stress at each face must be

$$\sigma_b = \frac{M h/2}{I} = 60 \, \mathrm{MPa}$$

within 2 % (compression below, tension above), the normal membrane stress below 1 %
of it, and the membrane shear V/A = 1 MPa within 10 %. Measured (ADR 0042): 59.9 and
60.1 MPa.

- **Construction geometry** (`test_construction.py`): a plane across mid-span must pass
  on 100 N and 10 N·m within 2 %, with the cut area exact
  (`test_section_force_across_a_plane`, also from a coordinate system); the largest
  normal stress on the plane 60 MPa within 3 % on the tension side and its mean below
  1 % of it (`test_result_over_a_plane`); a construction path linearizes to 60 MPa
  within 2 %. Measured: passes.

## A tube's section solved axisymmetrically

`tests/integration/test_section_solve.py` `test_a_tube_section_under_pressure_is_lames`
(`over("calculix", "code_aster", "fenicsx", "elmer")`): a steel tube, bore 10 mm,
outside 20 mm, cut in half by a `section` history step and solved as a 2D
axisymmetric model with its edges named after the tube's faces, 100 MPa in the bore and
the ends held axially. With A = p a²/(b² − a²) and B = p a² b²/(b² − a²), Lamé's
radial displacement at the outside, the ends held, is

$$u(b) = \frac{1 + \nu}{E} \left( (1 - 2\nu) A b + \frac{B}{b} \right)$$

and the mean over the outer edge must match it within 0.5 % on all four solvers.
Measured: passes. The 2D benchmarks of ADR 0048 are on
[Structural statics](help:verification/structural).

## Sources

- P. J. Roache, "Perspective: A Method for Uniform Reporting of Grid Refinement
  Studies", Journal of Fluids Engineering 116(3), 405-413, 1994.
- I. B. Celik, U. Ghia, P. J. Roache, C. J. Freitas, H. Coleman and P. E. Raad,
  "Procedure for Estimation and Reporting of Uncertainty Due to Discretization in CFD
  Applications", Journal of Fluids Engineering 130(7), 078001, 2008.
- O. C. Zienkiewicz and J. Z. Zhu, "A simple error estimator and adaptive procedure for
  practical engineering analysis", International Journal for Numerical Methods in
  Engineering 24, 337-357, 1987.
- C. Dapogny, C. Dobrzynski and P. Frey, "Three-dimensional adaptive domain remeshing,
  implicit domain meshing, and applications to free and moving boundary problems",
  Journal of Computational Physics 262, 358-378, 2014 (MMG).
- S. P. Timoshenko and J. N. Goodier, Theory of Elasticity, 3rd edition, McGraw-Hill,
  1970: the cantilever with shear, and Lamé's thick cylinder.
- H. S. Carslaw and J. C. Jaeger, Conduction of Heat in Solids, 2nd edition, Oxford,
  1959: the semi-infinite solid with a step in surface temperature (erfc).
- ASME Boiler and Pressure Vessel Code, Section VIII, Division 2, Annex 5-A: stress
  linearization.
- M. J. D. Powell, "A direct search optimization method that models the objective and
  constraint functions by linear interpolation", in Advances in Optimization and
  Numerical Analysis, Kluwer, 1994 (COBYLA).
- J. A. Nelder and R. Mead, "A simplex method for function minimization", The Computer
  Journal 7, 1965.
- H. H. Rosenbrock, "An automatic method for finding the greatest or least value of a
  function", The Computer Journal 3, 1960.
- D. R. Jones, M. Schonlau and W. J. Welch, "Efficient global optimization of expensive
  black-box functions", Journal of Global Optimization 13, 1998.
