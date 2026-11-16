---
title: Stress results
section: theory
order: 10
keys: theory:stress-results
summary: How a solve's stresses reach the nodes, what von Mises, principal, maximum shear and equivalent strain mean, and how load cases combine.
adrs: 0025
---

A finite-element solve finds displacements at the nodes. Stress is not solved for: it
comes afterwards from how the displacement changes inside each element. That is why a
peak stress depends more on the mesh than a deflection does, and why two solvers can
give the same deflection but different stresses at a sharp corner. This note says where
the stresses Puzzle shows come from and what each listed stress result means.

## From displacements to stress

Inside an element the displacement is a polynomial through its nodes (linear in a
four-node tetrahedron, quadratic in a ten-node one). Its gradient gives the strain, and
the material law gives the stress:

$$\varepsilon = \frac{1}{2}\left(\nabla u + \nabla u^T\right)$$

$$\sigma = \mathbf{D} \varepsilon$$

The strain is one polynomial order lower than the displacement, and it jumps from one
element to the next. The stress is most accurate at the element's integration points.
To draw a smooth picture, each solver moves it to the nodes in its own way:

- **CalculiX** (`*EL FILE`): extrapolates the integration-point values to each element's
  nodes, then averages the elements meeting at a node.
- **Code_Aster** (`SIGM_NOEU`): the stress at each element's nodes, averaged over the
  elements at a node.
- **FEniCSx**: an L2 projection of the stress onto the displacement's own nodal space,
  over the whole model at once (`_stress_projector` in its `study.py`).
- **Elmer**: StressSolve's own nodal stresses (`Calculate Stresses`).

Where the stress is smooth these agree closely. Where it changes fast (a re-entrant
corner, the edge of a bore, a material interface, the edge of a contact), the averaging
smears the peak, and each solver smears it differently. Puzzle has measured nodal
stresses differing by 30 % between solvers at a bore's corners while the displacements
agreed. So compare solvers by displacements, reactions and forces across sections, and
judge a corner's peak with a [mesh convergence check](help:theory/mesh-convergence).

## What Puzzle stores and lists

Every solver's stress is stored as six components in the order xx, yy, zz, xy, yz, zx
(a 2D model's zz is its out-of-plane stress, the hoop stress when axisymmetric). Strain
is stored the same way, as tensor strain: the shear components are half the
engineering shear strain. `post/quantities.py` lists every result a run can show from
these fields and computes each one at the nodes when it is drawn.

The equivalent (von Mises) stress:

$$\sigma_{vM} = \sqrt{\frac{1}{2}\left[(\sigma_{xx}-\sigma_{yy})^2 + (\sigma_{yy}-\sigma_{zz})^2 + (\sigma_{zz}-\sigma_{xx})^2\right] + 3\left(\tau_{xy}^2 + \tau_{yz}^2 + \tau_{zx}^2\right)}$$

It is the uniaxial stress that would store the same distortion energy, so it is
compared with a ductile metal's yield strength.

The principal stresses $\sigma_1 \geq \sigma_2 \geq \sigma_3$ are the eigenvalues of the
3 × 3 stress tensor (listed as maximum, middle and minimum principal). The maximum shear
stress is half their largest difference:

$$\tau_{max} = \frac{\sigma_1 - \sigma_3}{2}$$

The equivalent strain (of strain or plastic strain) is computed from the deviatoric
strain $e = \varepsilon - \frac{1}{3}\mathrm{tr}(\varepsilon) \mathbf{I}$:

$$\varepsilon_{eq} = \sqrt{\frac{2}{3} e : e}$$

For a plastic strain, which keeps volume, this equals the strain of a bar pulled the
same amount. For an elastic strain it does not: in a bar under uniaxial stress it gives
$\frac{2}{3}(1+\nu)\varepsilon$, so 0.87 ε at ν = 0.3. Puzzle uses no effective Poisson's
ratio, as some codes do.

Results shown in a coordinate system's axes are the same tensors rotated per node
(`post/axes.py`); the equivalent and principal values do not change with the axes.

## Combining load cases

A derived result (`post/derived.py`, ADR 0025) combines stored runs without solving
again. Each case is a study's run and step, with a coefficient $c_i$. Nodal fields of
cases on other meshes are mapped onto the first case's mesh (as transfers are); element
fields must be on the same elements, or they are left out with a warning.

- **Linear**: $\sigma = \sum_i c_i \sigma_i$, component by component.
- **SRSS**: each component is $\sqrt{\sum_i (c_i \sigma_i)^2}$.
- **Envelope**: one chosen result (von Mises, a component...) computed for each scaled
  case, the largest (or smallest) kept at each node, with the case that gave it.
- **Range and mean**: with two cases, the signed difference $c_1 \sigma_1 - c_2 \sigma_2$
  and the mean, component by component (the stress range fatigue needs); with more,
  each component's largest minus its smallest, and their mean.

Linear, SRSS and range combine the components first. Von Mises, the principal stresses
and the rest are then computed from the combined tensor, as Ansys does: the von Mises
stress of two cases added is not the sum of their von Mises stresses.

Combinations are set up from Results → Combinations and fatigue: see
[Results](help:guides/results) and [derived_result](help:mcp:derived_result).

## Limits

- Superposition holds only for linear analyses. Puzzle warns when a linear combination
  reads a plastic result, but not for contact or large-deflection runs, which are just
  as nonlinear: combine those by solving the loads together.
- SRSS works on the components, so their signs are lost (every component comes out
  positive). The von Mises stress of an SRSS tensor is not the SRSS of the cases' von
  Mises stresses.
- A range over more than two cases takes each component's extremes on its own, so the
  range tensor may not belong to any one pair of cases.
- Nodal stresses at singular points (sharp re-entrant corners, point loads, the edge of
  a fixed face) grow without limit as the mesh is refined. They are a property of the
  model, not a value to design to: see [Mesh convergence](help:theory/mesh-convergence).
- Equivalent strain is a von Mises measure; for elastic strain it is not the uniaxial
  strain.

## Sources

- R. D. Cook, D. S. Malkus, M. E. Plesha and R. J. Witt, *Concepts and Applications of
  Finite Element Analysis*, 4th edition, Wiley, 2002: stress recovery and smoothing.
- G. Dhondt, *CalculiX CrunchiX User's Manual*: `*EL FILE` (nodal values extrapolated
  from the integration points and averaged).
- Code_Aster documentation: `CALC_CHAMP`, options `SIGM_ELNO` and `SIGM_NOEU`.
- Ansys Mechanical User's Guide: Solution Combination (component-wise combination, as
  ADR 0025 follows).
