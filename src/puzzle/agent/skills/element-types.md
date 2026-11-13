---
name: element-types
description: each solver's elements, the element library, choosing another per body (incompatible modes, mixed u-p, p-elements, quad shells...), large deflection
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
- `mixed_up`: Code_Aster 3D_INCO_UPG on quadratic cells, for ν near 0.5 (solved by
  STAT_NON_LINE), and FEniCSx Taylor-Hood P2-P1 on quadratic tetrahedra (static, every
  body must choose it: one space over the mesh). Use it for plastic flow at large
  strain on Code_Aster too: plain tetrahedra lock there and Newton stalls.
- `euler_bernoulli`: Code_Aster POU_D_E for slender members, with no shear deflection.
  Exact PL³/3EI.
- `lumped`: Code_Aster 3D_DIAG for transient heat. No node undershoots after a sudden
  temperature.
- `reduced`: CalculiX C3D20R and Code_Aster 3D_SI on 20-node bricks (0.9957 of
  Timoshenko's cantilever on both); CalculiX B32R on quadratic beams and S8R on 8-node
  quadrilateral shells. B31R and S4R are refused: their one-point bricks hourglass
  (6.3 and 3.6 times too soft measured).
- `higher_order`: Elmer p-elements (Element = "p:2") on a first-order mesh: linear
  tetrahedra that bend (0.995 of Timoshenko's against 0.503). Every body chooses it;
  `solver_types={"elmer": "p:3"}` for another degree. Static and modal elasticity.
- `thick_shell`: Code_Aster DST (Mindlin-Reissner) for thick plates and shells
  (span/thickness below about 20): it takes the shear deflection DKT leaves out.

## Quadrilateral shells

Sheets mesh into quadrilaterals with the mesh recipe's `element_shape="hexahedra"`:
- CalculiX writes S4 (order 1) and S8 (order 2); both are within 1.4 % of Mindlin on a
  thick strip, as its S6 triangles are.
- Code_Aster writes DKT/DST on 4-node quadrilaterals (DKQ, DSQ) and COQUE_3D, the
  curved thick shell, on 9-node ones (order 2 with `hexahedron_nodes` 27). COQUE_3D
  matched Mindlin to eight digits.
- PyNite writes its MITC4 quadrilaterals as plates (order 1 quadrilaterals only),
  with pressure on them; they share nodes with frame members. It matched Mindlin to
  0.03 %.
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
- On Code_Aster, beams become POU_D_T_GD (geometrically exact) under large deflection:
  the elastica to four digits; their section forces aren't read yet. Beam bolts and
  shells are refused there so far: run those on CalculiX.
- A run warning `large_strain` means strains passed 5 % on an elastic material: the
  answer needs a hyperelastic model.
- Transient steps take it too. CalculiX cuts the increment back under NLGEOM and uses
  HHT α -0.05 unless `hht_alpha` is set: undamped Newmark diverged on a swinging strip.
  Code_Aster's DYNA_NON_LINE takes Green–Lagrange, a follower pressure and the same
  HHT default, cutting a failed increment back. A damped strip settles on the static
  elastica on both to 0.4 %.
- A buckling step preloaded by a large-deflection static step: CalculiX's NLGEOM
  static step, Code_Aster STAT_NON_LINE in Green–Lagrange. K_G comes from the final
  stresses, and the initial-displacement matrix is left out (solids only on Code_Aster).
- Plasticity at large strain: CalculiX NLGEOM, and Code_Aster GDEF_LOG. Choose
  `mixed_up` on Code_Aster, or Newton stalls. A bar drawn to 1.3 matches the log-strain
  closed form within 0.2 % on both.

## Mistakes to avoid

- Incompatible modes on a tetrahedral mesh: set the mesh recipe's element_shape to
  hexahedra and element_order 1.
- Second-order bricks: set the mesh recipe's `element_shape` to "hexahedra",
  `element_order` 2, and `hexahedron_nodes` 20 (CalculiX, Code_Aster, Elmer) or 27
  (Code_Aster, Elmer, FEniCSx). CalculiX refuses 27-node bricks and FEniCSx 20-node
  ones. Reduced integration (C3D20R, 3D_SI) needs the 20-node ones.
- C3D8R (CalculiX, `reduced` on 8-node bricks) converges from above: give it four or
  more bricks through a bent part's depth (1.016 of Timoshenko's at four).
- Q4G on Code_Aster: `thick_shell` with `solver_types={"code_aster": "Q4G"}`.
- Code_Aster refuses shells and beams in transient large deflection (no shell mass or
  beam stiffness for it there): run those on CalculiX.
- Choosing per body on PyNite: nothing to choose (its quads are the plates). Elmer's
  and FEniCSx's choices are whole-model (p-elements, Taylor-Hood).
