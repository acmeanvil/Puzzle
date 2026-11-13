---
name: symmetry
description: modelling part of a symmetric whole: symmetry and antisymmetry planes, cyclic sectors, periodic slices, and 2D planar or axisymmetric models
adr: 0081
---
Read this when the user wants to model only part of a symmetric part or assembly
(a half, a quarter, an eighth), mentions a symmetry or mirror plane, or asks for a
2D, plane stress, plane strain or axisymmetric model.

## What to use

- **Symmetry plane**: the model continues mirrored across a flat cut face, and the
  loads mirror too. Nothing moves through the plane, and no heat or field crosses it.
- **Antisymmetry plane**: the mirrored half carries the opposite load (a beam's central
  moment, a twist). Nothing moves along the plane. Solid mechanics only.
- **2D or axisymmetric**: a component's `dimension` ("2d_planar" with `planar_model`
  "plane_stress" or "plane_strain", or "2d_axisymmetric"; Y the axis, X the radius).
  The parts must be faces at z = 0 (see the `planar-models` skill).
- **Cyclic symmetry**: one sector of a disc, wheel, flange or bolt circle; the two cut
  faces needn't be flat. Statics, transients and heat, on CalculiX (solid and heat) and
  Code_Aster (solid). Modes and buckling are refused: the sector's solution misses the
  other nodal diameters.
- **Periodic symmetry**: one slice of something that repeats along a line, with no
  stretch along it overall.

## Steps

1. Find the cut face's selection: `get_object(name="<geometry>")` lists selections;
   make one from the face if there is none (`selections_check`, the geometry skill).
2. `symmetry_plane(component="<component>", selection="Part/Cut", kind="symmetry")`.
   This puts the plane on every physics that takes one, in one edit: solid, heat and
   electrostatic `symmetry`, fluid `symmetry`, magnetic `magnetic_symmetry`. Use
   `kind="antisymmetry"` for the opposite load.
   For a sector: `symmetry_repeat(component="<c>", kind="cyclic", source="Part/Cut0",
   target="Part/Cut1", sectors=12, axis=[0, 0, 1], origin=[0, 0, 0])`. The target is the
   source turned 360/sectors degrees, right-handed about the axis. For a slice:
   `kind="periodic", translation=[0.2, 0, 0]` in metres. `kind="none"` removes both.
3. Halve what crosses the plane: a load on the cut face, or a total force split by the
   plane, is half (a quarter for two planes). Reactions and totals read from the model
   are its share.
4. The component still needs a support for every rigid motion the planes leave free.
   Three planes at right angles hold a block; one plane holds only its normal.
5. `check_study(study)`. A curved face is refused ("needs a flat face"). A plane across
   no global axis runs on CalculiX and Code_Aster; Elmer takes one unless two such
   planes meet at an edge; FEniCSx refuses it.
6. A sector or slice is free to spin or slide along its repeat: hold one point (a vertex
   selection) in that direction, as you would the whole.
7. Results draw as the whole model by default (Settings > Results > Symmetric models).
   Totals and reactions read from the run are the part modelled.

## Units

Planes take no values.

## Mistakes to avoid

- A symmetry plane on a face that is not on the plane of symmetry (an outer wall): it
  holds the part there.
- Forgetting to halve a load that the plane cuts.
- Expecting modal results of a half model to include the antisymmetric modes: a
  symmetry plane suppresses them. Run the whole part or an antisymmetric half too.
- Putting `antisymmetry` on heat transfer: it is refused.
- A cyclic target that isn't the source turned right-handed: the mesher refuses it. Swap
  source and target, or flip the axis.
- Meshing a cyclic or periodic model with Netgen: refused, since the cut faces must be
  meshed together.
