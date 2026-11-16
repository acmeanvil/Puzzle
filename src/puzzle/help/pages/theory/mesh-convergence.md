---
title: Mesh convergence
section: theory
order: 30
keys: theory:mesh-convergence
summary: From one quantity on three meshes, the observed order of convergence, the extrapolated value and Roache's grid convergence index, with a verdict.
adrs: 0066
---

A finite-element answer changes as the mesh gets finer, and stops changing once the
mesh is fine enough. A mesh convergence check solves the same study on a sequence of
finer meshes and asks three questions of each quantity read: is it settling down, how
fast, and how far is the finest mesh's value from the one an infinitely fine mesh would
give? Richardson extrapolation answers the last two, and Roache's grid convergence index
(GCI) turns the answer into an uncertainty band.

## The idea

When the mesh is fine enough to be in the asymptotic range, the error of a quantity $f$
falls as a power of the element size $h$:

$$f_h \approx f_0 + C h^p$$

$p$ is the order of convergence. Three meshes give three equations for the three
unknowns $f_0$, $C$ and $p$, so the order can be measured instead of assumed, and $f_0$
estimated.

## The equations

Number the three finest meshes 1 (finest), 2 and 3 (coarsest), with $N$ elements each.
Puzzle takes the refinement ratios from the element counts, with $d$ the mesh's
dimension (3 for solids, 2 for a 2D model):

$$r_{21} = \left(\frac{N_1}{N_2}\right)^{1/d}$$

and $r_{32}$ the same from $N_2$ and $N_3$. The changes between meshes are
$\varepsilon_{21} = f_2 - f_1$ and $\varepsilon_{32} = f_3 - f_2$. With unequal ratios
the observed order solves

$$p = \frac{1}{\ln r_{21}} \left| \ln \left| \frac{\varepsilon_{32}}{\varepsilon_{21}} \right| + q(p) \right|$$

$$q(p) = \ln \left( \frac{r_{21}^p - 1}{r_{32}^p - 1} \right)$$

by fixed-point iteration from $q = 0$ (to 1e-8, at most 50 steps). The extrapolated
value is

$$f_{ext} = \frac{r_{21}^p f_1 - f_2}{r_{21}^p - 1}$$

and the GCI of the finest mesh, as a share of its value, with Roache's safety factor
1.25 for three or more meshes:

$$\mathrm{GCI} = \frac{1.25}{r_{21}^p - 1} \left| \frac{f_1 - f_2}{f_1} \right|$$

A GCI of 0.3 % says: the converged value is within about 0.3 % of the finest mesh's.
(When $f_1$ is zero, the absolute difference is used instead of the relative one.)

## The verdicts

`convergence.verdicts` gives each quantity one state:

- **converged**: the GCI is at or under the target (default 2 %), with an order from
  0.5 to 6; also when the two finest meshes give exactly the same value.
- **not converged**: a sensible order, but a GCI above the target. Refine.
- **oscillating**: $\varepsilon_{32}$ and $\varepsilon_{21}$ have opposite signs, so the
  value goes up and down between meshes. No order or GCI is given: the meshes are not
  in the asymptotic range.
- **not in the asymptotic range**: the observed order is below 0.5 or above 6. The
  meshes are too coarse for the power law to hold, or something else (a singularity,
  a contact switching) changes with the mesh.
- **too few levels**: fewer than three meshes ran for that quantity.

## How Puzzle does it

`convergence.py` plans the levels from the study's mesh recipe: level k divides every
size by $\mathrm{ratio}^k$ (the global size, each control's size; transfinite counts are
multiplied instead; a Gmsh recipe without a global size gets Gmsh's size factor). The
defaults are three levels and a ratio of 1.5. Each level is the study's own stored run,
labelled `mesh: level k: …` and found again by its run key, so a stopped check resumes.

The quantities are operators or result tools (a tip deflection, a reaction, a
frequency, a peak stress); with none, the largest value of the study's first result.
The verdicts use the three finest levels that ran.

Set it from a study's Check mesh convergence…, or a mesh study object: see
[Optimisation and mesh convergence](help:guides/optimisation-and-convergence),
[mesh_convergence](help:mcp:mesh_convergence) and
[puzzle convergence](help:cli:convergence).

Checked on a CalculiX cantilever (ADR 0066): second-order tets from 8 mm converge with
order 4.1 and GCI 0.03 %; first-order tets lock and are reported not converged (GCI 26 %).

## Limits

- The ratios come from element counts, which assumes the mesh is refined evenly
  everywhere. Local controls that refine one region faster than the rest make $r$ an
  average, and the observed order with it.
- Three meshes are the least the method needs. More levels do not enter the verdict
  (only the three finest do), but they show the trend in the plot.
- Uniform refinement must really be uniform: a recipe with fixed transfinite counts or
  sizes that do not scale breaks the assumed $h$.
- A stress at a singularity (a re-entrant corner, a point load, the edge of a fixed
  face) never converges: it is reported not converged or not in the asymptotic range
  however fine the mesh. That is the model, not the mesh: read the stress a short
  distance away, round the corner, or judge it by linearization
  ([Stress linearization](help:theory/linearization)).
- Locking elements (first-order tetrahedra in bending, nearly incompressible materials)
  converge slowly and may look converged early. Use second-order elements.
- The GCI estimates discretisation error only, not modelling error (supports, material
  data, geometry).

For one run's error without solving again, see
[Error estimates](help:theory/error-estimates).

## Sources

- P. J. Roache, "Perspective: A Method for Uniform Reporting of Grid Refinement
  Studies", *Journal of Fluids Engineering* 116(3), 405-413, 1994.
- P. J. Roache, *Verification and Validation in Computational Science and Engineering*,
  Hermosa Publishers, 1998.
- I. B. Celik, U. Ghia, P. J. Roache, C. J. Freitas, H. Coleman and P. E. Raad,
  "Procedure for Estimation and Reporting of Uncertainty Due to Discretization in CFD
  Applications", *Journal of Fluids Engineering* 130(7), 078001, 2008: the iteration for
  $p$ with unequal ratios, and the extrapolated value.
