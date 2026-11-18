---
name: shells
description: thin walls as shells: sheet and curved sheet geometry, a thickness and offset per part or face selection, element order and shape per solver, pressure's sign
adr: 0027
---
Read this before modelling a thin plate, panel, tank wall or duct as a shell (a surface
with a thickness) instead of a meshed solid, or when a solve refuses a sheet body.

A shell is a sheet body (a surface) with a `ShellSection`: its thickness, and where the
mid-surface sits. CalculiX, Code_Aster and PyNite (plates) write shells; Elmer and
FEniCSx refuse them. Stresses are the shell's, at its faces.

## Steps

1. The geometry: a history step that makes a sheet (lengths in m):
   - `{"kind": "sheet", "name": "Plate", "args": {"points": [[0, 0, 0], [1, 0, 0],
     [1, 1, 0], [0, 1, 0]]}}`: a flat face through corners in one plane.
   - `{"kind": "curved_sheet", "name": "Roof", "args": {"r": 25, "length": 50,
     "angle": 80, "x0": 0, "y0": 0, "z0": 0}}`: part of a cylinder about x, the angle
     (degrees) centred on +z.
   Name the edges you will hold (entity `edge`) and the faces you will load (entity
   `face`, the sheet itself for a pressure) as selections (skill `structural-loads`).
2. The thickness, one edit of the component: `{"op": "append", "path":
   "shell_sections", "value": {"selection": "Plate", "thickness": {"value": 10, "unit":
   "mm"}}}`. `selection` is the part, or `Part/Selection` of some of its faces (a
   different thickness there). `offset` moves the mid-surface, in thicknesses: 0 is
   centred, +0.5 puts the sheet on the shell's bottom face (against the face's normal),
   −0.5 on its top. A thickness may come from an imported field (`fields`, skill
   `imported-fields`).
3. The mesh recipe: triangles by default; quadrilaterals with `element_shape`
   "hexahedra" on the recipe. The order matters per solver:
   - CalculiX: second order (`element_order` 2): S6 triangles, S8 quadrilaterals (S3
     is refused: it locks). Expanded into solids inside CalculiX, so it reads back
     displacements and stresses at the shell's nodes.
   - Code_Aster: first order: DKT triangles, DKQ quadrilaterals (Kirchhoff); DST/DSQ
     for thick shells and COQUE_3D on nine-node quadrilaterals by the element choice
     (skill `element-types`, `element_choose` with `thick_shell`).
   - PyNite: MITC4 plates on quadrilaterals.
4. Run it as any structural study. `check_study` names what a solver refuses.

## Signs and units

- A positive `pressure` on a sheet pushes against the face's normal (the side the
  normal points away from goes in). If a plate bends the wrong way, the face's normal
  points the other way: negate the pressure.
- Thickness and offset are lengths and fractions; the shell's stress is in Pa, read
  with `result_stats(study, "stress")` as for solids.

## Benchmarks (tests/integration/test_shells.py)

A 1 m square steel plate 10 mm thick under 1 kPa, simply supported and clamped, against
Timoshenko's α q a⁴ / D: CalculiX S6 and Code_Aster DKT within 1.5 %. The
Scordelis-Lo roof (MacNeal and Harder): the free edge's mid-point within 2 % on both.

## Mistakes

- A shell on Elmer or FEniCSx: refused ("does not write shells"); use CalculiX or
  Code_Aster.
- CalculiX with a first-order mesh: S3 is refused; set `element_order` 2.
- Code_Aster with second-order shells: its DKT/DKQ are first order; set `element_order`
  1 (COQUE_3D on nine-node quadrilaterals is the exception).
- A 2D planar model's thickness is a `shell_sections` entry too, but it is a plane-stress
  thickness there, not a shell (skill `planar-models`).
