---
title: Symmetry
section: theory
order: 140
keys: theory:symmetry
summary: Model half, a quarter or one sector of a symmetric part and get the whole part's answer, with symmetry and antisymmetry planes, cyclic symmetry and linear periodicity.
adrs: 0081
---

When a part and its loads mirror themselves across a plane, or repeat round an axis or
along a line, the solution does too. Puzzle can then solve only the piece that repeats
and hold its cut faces so they behave as if the rest were there. The model is smaller
and the solve faster, and the answer is the same.

## Symmetry and antisymmetry planes

Cut a symmetric model on its plane of symmetry. A point on the plane has a mirror image
that is itself, so it cannot move through the plane: the displacement normal to the
plane is zero, and the in-plane components are free.

$$\mathbf{u} \cdot \mathbf{n} = 0$$

An **antisymmetric** load (one that reverses in the mirror image, as a torque on a
shaft cut through its axis) gives a solution that reverses too. Then the plane's
points move only through it: the in-plane components are zero and the normal one is
free.

| Plane | Held | Free |
| --- | --- | --- |
| Symmetry | the normal component | the two in-plane ones |
| Antisymmetry | the two in-plane components | the normal one |

In heat transfer and electrostatics symmetry means no flux across the plane, which is
already the natural condition of an unloaded boundary, so nothing is written. In
magnetic fields and fluid flow, symmetry is a feature of its own
([magnetic symmetry](help:feature:magnetic_symmetry),
[fluid symmetry](help:feature:fluid_flow:symmetry)) that each adapter writes.

## How a plane is held

`symmetry.py` fits a plane to the face's nodes (in 2D, a line to the edge's nodes) and
refuses a face that is curved or bent. Then:

- **A plane across x, y or z** holds that global component (symmetry), or the other
  two (antisymmetry), as a plain displacement support. Every FE solver writes it.
- **Any other plane** is held in a Cartesian system made along its normal. CalculiX
  writes it as one linear equation per node, $n_x u_x + n_y u_y + n_z u_z = 0$
  (`*EQUATION`), with the components plain supports already hold dropped and the
  equations reduced so each dependent unknown appears once; unlike a `*TRANSFORM`,
  this combines with other planes and supports at shared edges. Code_Aster writes
  `LIAISON_OBLIQUE`, Elmer Normal-Tangential Displacement (which it refuses where two
  oblique planes meet, since it averages its normals at shared nodes). FEniCSx holds
  planes across x, y or z only.

The planes count as supports, so a model held only by them is not
[unconstrained](help:problem:unconstrained) once they stop every rigid motion.

## Cyclic symmetry

A part made of $N$ identical sectors round an axis (a flange, a bladed disc, a ring)
under loads repeated in every sector deforms the same way in each. Model one sector,
with a **source** cut face and a **target** cut face, the source turned by
$\theta = 2\pi/N$ about the axis. Each target node must move as its source node does,
turned:

$$\mathbf{u}_t = \mathbf{R} \mathbf{u}_s$$

where $\mathbf{R}$ is the rotation by $\theta$ (right-handed about the axis). In heat
transfer the temperatures are equal, $T_t = T_s$. Because the cut faces need not be
planes, cyclic symmetry joins sectors whose cuts bend, where no symmetry plane could.

The mesher meshes the target face as the source turned (Gmsh's periodic meshing), so
every target node lands on a turned source node; `cyclic.pairs` finds them and refuses
the pair if any is off by more than $10^{-6}$ of the model's size. `cyclic.rows` turns
each pair into equations, leaving out components a support already holds at zero.
CalculiX writes them as `*EQUATION` (solid mechanics and heat), Code_Aster as
`LIAISON_DDL` (solid mechanics). Loads and supports are the sector's: the whole part
carries $N$ times them.

## Linear periodicity

A part that repeats along a line (a slice of a long slab, a cell of a lattice) is the
same idea with no turn: the target face is the source moved by a translation, and

$$\mathbf{u}_t = \mathbf{u}_s$$

so the two ends move together and there is no mean strain along the line.

## Only the symmetric solution

A sector's equations find the solution that is the same in every sector, which is
the whole answer when the loads repeat in every sector: statics, heat and transients.
A cyclic part's modes and buckling shapes are mostly not like that: they vary from
sector to sector (other nodal diameters), and one sector with these equations would
miss them. So `cyclic.check` refuses modal and buckling steps with cyclic symmetry:
model the whole part for those.

## Drawing the whole

With Settings > Results > Symmetric models set to the whole, `post/expand.py` draws the
results as copies of the solved piece: mirrored across each symmetry plane (every
combination of up to three planes), mirrored and reversed across an antisymmetry plane,
turned into every sector, or repeated a few times along a periodic line. Vectors and
tensors are turned or mirrored with the copy. The copies are for the view only:
probes, picks and extremes read the solved piece.

## How Puzzle does it

- Model → Symmetry: Symmetry plane… and Antisymmetry plane… put a plane on a face in
  every physics that takes one; cyclic and periodic symmetry name the two cut faces and
  the axis and sector count, or the translation. The agent uses
  [symmetry_plane](help:mcp:symmetry_plane) and
  [symmetry_repeat](help:mcp:symmetry_repeat).
- The planes become supports after meshing, in `run_study`, so the project keeps the
  intent and the solvers get ordinary supports or equations.
- Benchmarks (`tests/integration/test_symmetry_solved.py`): an eighth of a bar in
  tension and the same bar turned 30°, to $10^{-4}$; a ring sector under internal
  pressure, on planes and as one of twelve cyclic sectors (with straight and kinked
  cuts), against Lamé to 0.2 %; half a shaft in torsion by antisymmetry, to 0.5 %; a
  periodic slice of a slab, to $10^{-4}$.

## Limits

- The geometry, the materials (with their directions), the supports and the loads must
  all be symmetric. A symmetric part under an unsymmetric load needs the whole model,
  or the load split into a symmetric and an antisymmetric part solved apart.
- A symmetry plane must be flat; curved faces are refused.
- Cyclic symmetry gives no modes or buckling of a sector; linear periodicity holds no
  mean strain along its line.
- Cyclic and periodic symmetry are written by CalculiX (solid, heat) and Code_Aster
  (solid) only.

## Sources

- R. D. Cook, D. S. Malkus, M. E. Plesha and R. J. Witt, *Concepts and Applications of
  Finite Element Analysis*, 4th ed., Wiley, 2002: symmetry and antisymmetry conditions,
  constraint equations.
- D. L. Thomas, "Dynamics of rotationally periodic structures", *International Journal
  for Numerical Methods in Engineering* 14 (1979) 81-102: cyclic symmetry and nodal
  diameters.
- G. Dhondt, *CalculiX CrunchiX User's Manual*: `*EQUATION`.
- EDF, Code_Aster documentation, `AFFE_CHAR_MECA`: `LIAISON_OBLIQUE`, `LIAISON_DDL`.
- S. P. Timoshenko and J. N. Goodier, *Theory of Elasticity*, 3rd ed., McGraw-Hill,
  1970: Lamé's thick cylinder, the ring benchmarks.
