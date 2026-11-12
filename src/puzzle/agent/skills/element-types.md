---
name: element-types
description: which element each solver writes for a body, the element library, and choosing another (incompatible modes, mixed u-p, Euler-Bernoulli, lumped heat)
adr: 0082
---
Read this when the user asks which elements a solver uses or offers, wants a different
element for a body (bending of a thin part meshed with bricks, rubber or plastic flow
near incompressibility, slender beams, a thermal shock that undershoots), or a solve
says a formulation isn't written.

## The library

`elements_list(solver=, family=, study=, written=)` lists every solver's elements:
- each element's native name, family (solid_3d, solid_2d, shell, beam, truss, discrete,
  thermal, electromagnetic, fluid), cells and formulation;
- the studies it supports, what it is for and not for;
- `written`, whether Puzzle writes it, and the manual's section.

Help → Element library and `docs/elements.md` show the same, grouped.

## Choosing one for a body

`element_choose(component, selection, formulation)`: `selection` a body selection or a
part. The formulations written so far:
- `incompatible_modes`: CalculiX C3D8I on linear bricks (hexahedral meshes, element
  order 1). It bends without the shear locking of C3D8: a 10 mm deep cantilever two
  bricks deep comes to 0.993 of Timoshenko's deflection, against 0.896 for C3D8.
- `mixed_up`: Code_Aster 3D_INCO_UPG on quadratic cells, for ν near 0.5. It is solved
  by STAT_NON_LINE, in one increment.
- `euler_bernoulli`: Code_Aster POU_D_E for slender members, with no shear deflection.
  Exact PL³/3EI.
- `lumped`: Code_Aster 3D_DIAG for transient heat. No node undershoots after a sudden
  temperature.
- `reduced`: CalculiX C3D20R and Code_Aster 3D_SI on 20-node bricks (0.9957 of
  Timoshenko's cantilever on both).
- `full`, `timoshenko`, `thin_shell`: the defaults (C3D10, 3D, POU_D_T, DKT...).

`formulation="none"` removes the choice. `solver_types={"calculix": "C3D20R"}` names a
solver's own type outright (expert use: it is written as given).

## Steps

1. `elements_list(study=..., written=True)` to see what is available for the study.
2. `element_choose(...)`.
3. `check_study(study)`: a solver with nothing written for that formulation says so.
   At the solve, a body whose cells don't take it (incompatible modes on tetrahedra) is
   refused, naming what the solver has for those cells.

## Large deflection (a static step's geometric_nonlinearity)

- CalculiX writes NLGEOM. Code_Aster writes Green–Lagrange for elastic bodies,
  logarithmic strains for yielding ones, small strain for point elements, and pressure
  as a follower load (TYPE_CHARGE='SUIV').
- The two agree to five digits on a pressed cantilever, and both match the elastica
  within 1 %.
- On Code_Aster, beams, beam bolts and shells are refused under large deflection so far:
  run those on CalculiX.
- A run warning `large_strain` means strains passed 5 % on an elastic material: the
  answer needs a hyperelastic model.
- Transient large deflection is refused on both solvers so far.

## Mistakes to avoid

- Incompatible modes on a tetrahedral mesh: set the mesh recipe's element_shape to
  hexahedra and element_order 1.
- Second-order bricks: set the mesh recipe's `element_shape` to "hexahedra",
  `element_order` 2, and `hexahedron_nodes` 20 (CalculiX, Code_Aster, Elmer) or 27
  (Code_Aster, Elmer, FEniCSx). CalculiX refuses 27-node bricks and FEniCSx 20-node
  ones. Reduced integration (C3D20R, 3D_SI) needs the 20-node ones.
- Thick shells (DST, S8R) and C3D8R: listed, not written yet.
- Choosing per body on PyNite, Elmer or FEniCSx: nothing to choose yet; they refuse
  it.
