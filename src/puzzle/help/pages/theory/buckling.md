---
title: Linear buckling
section: theory
order: 120
keys: theory:buckling
summary: How a buckling step finds the multiple of the applied load at which a structure loses stability, and when that number overestimates the real capacity.
adrs: 0028, 0082
---

A slender column, a thin plate or a shell under compression can fail long before its
material yields: past a certain load the straight or flat shape stops being stable and
the structure snaps sideways. A buckling step answers "by what factor can I scale
these loads before that happens?" It returns that load factor and the shape the
structure buckles into, for each of the lowest few modes.

## The idea

A compressive stress makes a structure softer against sideways motion: think of a
column whose axial load pushes it further in whatever direction it bends. Tension does
the opposite. This effect is the geometric (stress) stiffness $\mathbf{K}_G$, built
from the stresses of a static solve under the step's loads. It scales with the load,
so at $\lambda$ times the load the total stiffness is $\mathbf{K} + \lambda\mathbf{K}_G$.
Buckling is where that stiffness becomes singular: a shape $\phi$ exists that the
structure can take with no added load,

$$\left( \mathbf{K} + \lambda\mathbf{K}_G \right) \phi = 0$$

Each eigenvalue $\lambda_i$ is a load factor and its eigenvector the mode shape. The
critical load is $\lambda_1$ times the applied load. A factor below 1 means the structure
buckles before the applied load is reached; a negative factor means it would buckle
with the load reversed (labelled "(load reversed)").

For a column fixed at its base and free at its top, the lowest factor on a 1 N load is
Euler's load

$$P_{cr} = \frac{\pi^2 E I}{(2L)^2}$$

## A preload

Some loads are not scaled: self weight, a bolt pretension, a pressure that is fixed. With
`preload` set, the static step just before the buckling step is solved first and its
stresses are held. Its geometric stiffness $\mathbf{K}_{G0}$ joins the stiffness, and only
the buckling step's own loads are scaled:

$$\left( \mathbf{K} + \mathbf{K}_{G0} + \lambda\mathbf{K}_{G1} \right) \phi = 0$$

For a column the factor then satisfies $P_0 + \lambda P_1 = P_{cr}$. Each feature's
`steps` puts the preload in the static step and the load to scale in the buckling step.

## How Puzzle does it

- **The step:** `buckling`, settings `modes` (default 4), `preload` (default off) and
  `shift` (search near a factor; Code_Aster only, CalculiX refuses it). See
  [Choosing an analysis](help:guides/analyses).
- **CalculiX:** `*BUCKLE` finds the smallest load factors. With a preload, the static
  step is written with NLGEOM and the buckling step as `*STEP, PERTURBATION`, since a
  plain `*BUCKLE` after `*STATIC` ignored the static stress. Its preloaded factor is
  0.02 % below Code_Aster's for that reason: its preload step is geometrically
  nonlinear.
- **Code_Aster:** a static solve (`MECA_STATIQUE`) under the step's loads, its stresses
  into `CALC_MATR_ELEM(OPTION='RIGI_GEOM')`, and `CALC_MODES(TYPE_RESU='MODE_FLAMB')`
  with K (plus the preload's $\mathbf{K}_{G0}$, added by `COMB_MATR_ASSE`) against
  $\mathbf{K}_{G1}$. Its `CHAR_CRIT` is the load factor. The modes are normalised to a
  largest translation of 1 (`NORM_MODE`, `NORME='TRAN'`).
- **Under `geometric_nonlinearity`** on the preload step (ADR 0082): CalculiX's NLGEOM
  static step, or Code_Aster's `STAT_NON_LINE` in Green-Lagrange strains over ten
  increments, $\mathbf{K}_{G0}$ from its final stresses. Code_Aster keeps $\mathbf{K}$
  the undeformed one (the initial-displacement matrix is left out), which matters only
  when the preload deforms the structure appreciably before it buckles.
- **Results:** one result step per mode, kind `buckling`, its value the load factor,
  labelled "Buckling mode 1: load factor 3.42". The mode shape is normalised: its size
  means nothing, only its form.
- Elmer and FEniCSx refuse buckling.

## Benchmarks

`tests/integration/test_buckling.py`: a 10 × 10 × 300 mm steel column fixed at its
base, 2.5 mm quadratic tetrahedra.

| Check | CalculiX | Code_Aster | Theory |
| --- | --- | --- | --- |
| Euler, fixed-free | +0.085 % | +0.085 % | 4797.7 N |
| Preload 2400 N, the factor on 1 N | +0.15 % | +0.17 % | P_cr − P_0 |
| Four modes, solver against solver | agree to 0.01 % | | |

The two lowest modes are equal (a square section bends either way), and the next pair is
well over five times higher: (3π/2)² / (π/2)² = 9 for a beam. The solid mesh is stiffer
than beam theory as a column gets stockier, so check slender members on a fine mesh, or
model them as beams.

## Limits

- **It is non-conservative for imperfection-sensitive structures.** The eigenvalue
  assumes a perfect shape and small deflections up to the instant of buckling. Real
  structures have imperfections, and for thin shells (cylinders under axial load,
  spheres under pressure) small imperfections drop the real collapse load far below the
  linear factor. Design rules for such shells apply knock-down factors to the linear
  value; never take a shell's linear factor as its capacity.
- **It assumes linear behaviour before buckling.** Plasticity before buckling (stocky
  columns), large deflection under the load itself (arches, shallow domes that snap
  through) and contact that changes with the load all make the linear factor wrong.
- **It gives no post-buckling behaviour:** whether the structure carries more load after
  buckling (a plate) or collapses (a shell) needs a nonlinear analysis.
- **The large-deflection alternative:** a static step with `geometric_nonlinearity`
  on CalculiX or Code_Aster, on a shape drawn with an imperfection, shows how the
  deflection grows as the load rises toward the limit; it cannot follow past the peak,
  since
  arc-length methods (CalculiX's RIKS, Code_Aster's `PILOTAGE`) and seeding an
  imperfection from a buckling mode are planned (ADR 0028, point 5), not built.
- The mesh must be fine enough to bend: solid elements through a thin wall need
  second order and several elements across.

## Sources

- S. P. Timoshenko, J. M. Gere, Theory of Elastic Stability, 2nd edition, McGraw-Hill
  1961: Euler's column and its end conditions.
- R. D. Cook, D. S. Malkus, M. E. Plesha, R. J. Witt, Concepts and Applications of
  Finite Element Analysis, 4th edition, Wiley 2002: the stress stiffness matrix and
  linear buckling as an eigenproblem.
- NASA SP-8007, Buckling of Thin-Walled Circular Cylinders, 1965 (revised 1968):
  imperfection sensitivity and knock-down factors.
- CalculiX CrunchiX User's Manual: `*BUCKLE`, `*STEP, PERTURBATION`.
- Code_Aster documentation: `CALC_MODES` with `TYPE_RESU='MODE_FLAMB'`,
  `CALC_MATR_ELEM` option `RIGI_GEOM`.
