---
title: Error estimates and adaptive meshing
section: theory
order: 40
keys: theory:error-estimates
summary: How far one run is from the exact answer, element by element (Zienkiewicz–Zhu), and how the next mesh is sized from it, isotropically or stretched by MMG.
adrs: 0066
---

A [mesh convergence check](help:theory/mesh-convergence) needs several solves. An error
estimate needs one: it compares the stress the elements give, which jumps from element
to element, with a smoothed version of it. Where the two differ, the mesh is too coarse.
The same comparison says how big each element of the next mesh should be, which is what
adaptive refinement does pass after pass.

## The Zienkiewicz–Zhu estimate

Each element's own stress $\sigma_h$ comes from the solved displacement's gradient and
the element's E and ν. The recovered stress $\sigma^*$ is smoother: at each node, the
average of the stresses the elements around it give there, weighted by their volumes,
and taken separately per material so that a real jump at an interface is not counted as
error. Inside each element $\sigma^*$ is interpolated from its nodes with the element's
shape functions. The error in the energy norm, per element, is

$$\|e\|_K^2 = \int_K (\sigma^* - \sigma_h)^T \mathbf{D}^{-1} (\sigma^* - \sigma_h) dV$$

with $\mathbf{D}^{-1}$ the material's compliance. The element's own energy norm is

$$\|u_h\|_K^2 = \int_K \sigma_h^T \mathbf{D}^{-1} \sigma_h dV$$

and the model's relative error is

$$\eta = \sqrt{\frac{\sum \|e\|_K^2}{\sum \|e\|_K^2 + \sum \|u_h\|_K^2}}$$

For heat the flux $q = -k \nabla T$ takes the stress's place and $1/k$ the compliance's.
The integrals use the 4-point Gauss rule on linear tetrahedra, and that rule on each of
the eight sub-tetrahedra (32 points) on quadratic ones.

On a cube with exact fields, Puzzle's estimate is zero for linear fields, its
effectivity (estimate over exact error) is 1.01 on linear tets, and it falls with
orders 1.0 and 1.96 under refinement of linear and quadratic tets, as it should.

## The gradient jump indicator

A second, cheaper measure looks at the faces between elements: the normal stress (or
flux) should be continuous across a face, and its jump $[\sigma n]$ shows the error.
Per element, over its faces shared with another element,

$$\eta_K^2 = \frac{1}{2} \sum_F h_F A_F \frac{|[\sigma n]|^2}{E}$$

with $h_F$ the face's longest edge and $A_F$ its area (heat: the flux jump over $k$).
Boundary faces are left out, since their loads are not known here. On any other nodal
field (a potential, a velocity) the same is done on each component's gradient with unit
weight, which gives a relative indicator only. On the cantilever benchmark it reads
about 4 times the exact error: it shows where, not how much.

## Sizes for a target error

Zienkiewicz and Zhu's refinement ratio compares each element's error with an equal
share of the allowed error for a target $\bar\eta$ over $N$ elements:

$$\xi_K = \frac{\|e\|_K}{\bar\eta \sqrt{\left(\sum \|u_h\|_K^2 + \sum \|e\|_K^2\right) / N}}$$

Above 1, the element is too big. Its new size, for an element order $p$, is

$$h_{new} = h \xi_K^{-1/p}$$

with $h$ the element's longest edge. Puzzle clamps it to at most 4 times finer and 2
times coarser in one pass and to the recipe's smallest and largest sizes, and gives each
node the smallest size of its elements.

## How Puzzle does it

- **The estimate** (`post/estimates.py`): from a run's stored displacement or
  temperature, the mesh and the component's material assignments (single values of E
  and ν, or k, not temperature tables). It is a derived result of method Error estimate,
  drawn as Error estimate (energy norm), Refinement ratio (above 1: refine) and Gradient
  jump indicator, with η as its note. Cell values of a CFD run are averaged to the nodes
  by volume first.
- **Sizes from data** (`mesh/size_field.py`): a mesh control Size field takes those
  sizes (or a table's x, y, z and size) as a Gmsh background field, combined with the
  recipe's other controls by taking the smallest; Netgen gets them as its mesh-size
  file, which can only refine.
- **Adaptive passes** (`adapt.py`): pass 1 is the study on its own recipe; each later
  pass adds a size field from the pass before's run. It stops when the criterion holds
  (energy: η at or under the target, default 2 %; quantity: each chosen quantity
  changed by at most the target since the last pass, relative to the new value), or at
  the budget (5 passes by default, or a number of elements). Each pass is a stored run
  labelled `adapt: pass k`, so a stopped run resumes.

See [Optimisation and mesh convergence](help:guides/optimisation-and-convergence),
[error_estimate](help:mcp:error_estimate), [mesh_adapt](help:mcp:mesh_adapt) and
[puzzle adapt](help:cli:adapt).

## Anisotropic adaptation with MMG

A thin layer (a boundary layer, a thermal skin) needs small elements across it but not
along it. With Elements: anisotropic, each pass after the first remeshes the last mesh
with MMG (`mesh/mmg.py`) along a metric from the Hessian $H$ of a field: the run's
displacement, velocity or temperature (a vector's magnitude), or the field named. $H$
is the gradient recovered at the nodes, differentiated again the same way, and made
symmetric. With its eigenvalues $\mu_i$ and an allowed interpolation error
$\epsilon$ = target × the field's range,

$$\lambda_i = \frac{c |\mu_i|}{\epsilon}$$

with Frey and Alauzet's $c = 9/32$ for tetrahedra ($2/9$ in 2D). Each $\lambda_i$ is clamped between $1/h_{max}^2$ and
$1/h_{min}^2$ and to an aspect ratio of at most the stretch setting (50 by default);
the element size along each eigenvector is $1/\sqrt{\lambda_i}$. $h_{max}$ is the
recipe's global size (else a tenth of the model's diagonal) and $h_{min}$ is
$h_{max} / (4 \cdot \mathrm{finer}^2)$. MMG runs with a gradation of 1.3 and a surface
tolerance of 1e-3 of the diagonal. Gmsh's bodies, faces, curves and points go through
as MMG's references, so named selections, loads and supports find their faces again.

On a bar heated at one end, MMG reached 0.053 % error in 1647 elements where isotropic
Gmsh reached 0.14 % in 1710. On a cantilever it brings nothing: displacement has no
layer to stretch along, so structural studies should stay isotropic.

## Limits

- Tetrahedra only. Other elements are left out of the estimate with a note; MMG
  refuses meshes with them (keep a boundary layer as its own control).
- Isotropic linear elasticity (or conduction) with each material's single values: the
  energy norm of a plastic, orthotropic or temperature-dependent run is not computed
  from its real law.
- The estimate measures the error of the energy norm over the model. A local peak
  stress can still be wrong where η is small; check a quantity of interest with a
  convergence check or the quantity criterion.
- Singularities (re-entrant corners, fixed edges, point loads) keep the error from
  falling: refinement piles up there. The cantilever's η flattened at about 2 % for
  this reason.
- With a named field the estimate is relative only (η is not given), so the energy
  criterion cannot hold: an adaptive run with it stops after its first pass and says
  so. Use the quantity criterion.
- MMG's second-order elements get straight edges (midpoints added after it remeshes),
  so curved surfaces are faceted at the new mesh's size.

## Sources

- O. C. Zienkiewicz and J. Z. Zhu, "A simple error estimator and adaptive procedure for
  practical engineering analysis", *International Journal for Numerical Methods in
  Engineering* 24, 337-357, 1987.
- O. C. Zienkiewicz, R. L. Taylor and J. Z. Zhu, *The Finite Element Method: Its Basis
  and Fundamentals*, 7th edition, Butterworth-Heinemann, 2013: error estimation and
  adaptive refinement.
- D. W. Kelly, J. P. de S. R. Gago, O. C. Zienkiewicz and I. Babuška, "A posteriori
  error analysis and adaptive processes in the finite element method: Part I, Error
  analysis", *International Journal for Numerical Methods in Engineering* 19,
  1593-1619, 1983: residual and jump indicators.
- C. Dapogny, C. Dobrzynski and P. Frey, "Three-dimensional adaptive domain remeshing,
  implicit domain meshing, and applications to free and moving boundary problems",
  *Journal of Computational Physics* 262, 358-378, 2014: MMG.
- P. J. Frey and F. Alauzet, "Anisotropic mesh adaptation for CFD computations",
  *Computer Methods in Applied Mechanics and Engineering* 194, 5068-5082, 2005: metrics
  from the Hessian.
