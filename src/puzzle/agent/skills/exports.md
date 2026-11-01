---
name: exports
description: results and the deformed shape for other software: a format by who receives it (CAD, another FE or CFD tool, post-processors, test correlation, reports), its options and what it carries
adr: 0043
---
`export(study, format, path, step=-1, quantity=None, selection=None, scale=1.0,
compensate=False, axes=None)` writes a study's latest run for other software. `path` is
inside the project folder (`exports/bracket`); its suffix is set by the format. Every
export writes `<name>.txt` beside it: study, run, solver, step, units and the format's
notes (what it couldn't carry). Everything is SI unless the format says otherwise
(STEP in mm).

## Choose by who receives it

- **CAD** (clearances under load, compensation): `morphed_step` (real surfaces, one
  body per part; refused when the morph can't stay within 1 % of the largest
  displacement: then `deformed_step`, a mesh body) or `deformed_stl`. `scale`
  exaggerates; `compensate=True` writes −u (the shape that deforms into the design).
- **Another FE or CFD tool** mapping a field onto its own mesh: `point_cloud`
  (one `quantity`, optionally one `selection`), `wall_cloud` (CFD wall pressure, shear,
  y+, heat transfer coefficient), `lorentz_cloud` (J × B body forces).
- **A solver deck:** `abaqus_inp` (mesh, sets, materials, and a thermal step's
  temperatures as `*TEMPERATURE`), `abaqus_displacement` (displacements as `*BOUNDARY`,
  a submodel's boundary), `nastran` (bulk data and punch; its stresses are unchecked
  against a Nastran: say so if the user relies on them).
- **Post-processors:** `paraview`, `vtkhdf`, `exodus` (selections as named sets),
  `ensight` and `cgns` (CFD: boundaries named after selections), `tecplot`.
- **Test correlation:** `unv` (modes as normal modes with their frequencies).
- **Reports, the web:** `gltf` (a coloured, optionally deformed 3D picture).

## The formats

| Format | Name | Carries | Options |
| --- | --- | --- | --- |
| `paraview` | ParaView (.pvd + .vtu) | every field of every step | none (whole run) |
| `point_cloud` | Point cloud (.csv: x, y, z, values) | one result's values per node, for mapping onto another tool's mesh | quantity, selection, step, units |
| `deformed_stl` | Deformed shape (.stl) | the deformed outer surface | compensate, scale, step |
| `deformed_step` | Deformed shape (.step, AP242 tessellated) | the deformed outer surface as a mesh body, one per part | compensate, scale, step |
| `exodus` | Exodus II (.exo) | every field of every step; parts as element blocks, selections as node and side sets | none (whole run) |
| `vtkhdf` | VTKHDF (.vtkhdf) | every field of every step in one HDF5 file | none (whole run) |
| `unv` | I-DEAS universal file (.unv) | nodes, elements, groups and results per step (modes with their frequencies) | none (whole run) |
| `ensight` | EnSight Gold (.case) | every field of every step, per cell and per node; each face selection a part named after it | none (whole run) |
| `cgns` | CGNS (.cgns, HDF5) | the mesh, every step's flow solution (cell-centred and nodal) and each face selection as a boundary condition named after it | none (whole run) |
| `tecplot` | Tecplot ASCII (.dat) | every field of every step, cell-centred or nodal; a zone per part and face selection | none (whole run) |
| `wall_cloud` | Wall quantities (.csv: x, y, z, values) | wall pressure, shear stress, y+ and heat transfer coefficient (with its reference temperature) at a wall's face centres, for a structure or a conjugate heat transfer model elsewhere | selection, step, units |
| `nastran` | Nastran bulk data and punch results (.bdf + .pch) | the mesh, properties, materials and selections as sets (large-field, metres); displacements, mode eigenvalues and solid stresses per step as punch | none (whole run) |
| `abaqus_inp` | Abaqus/CalculiX mesh and temperatures (.inp) | the mesh, sets, materials and sections; a step's nodal temperatures as *TEMPERATURE data beside it | step |
| `abaqus_displacement` | Abaqus/CalculiX mesh and displacements as *BOUNDARY (.inp) | the mesh deck, and a step's nodal displacements as *BOUNDARY data (a submodel's boundary) | selection, step |
| `gltf` | glTF 3D picture (.glb) | each part's outer surface coloured by one result (the legend's colour map), deformed by the step's displacement times the scale | quantity, scale, step |
| `lorentz_cloud` | Lorentz force density J × B (.csv point cloud) | x, y, z and the body force per volume J × B (N/m^3) per node, for a structural model elsewhere | selection, step |
| `morphed_step` | Deformed shape (.step, morphed CAD) | each solid part's CAD faces moved by u: real surfaces CAD can edit, one body per part | compensate, scale, step |

Options a format doesn't list are ignored by it; `step` is the step index (0 first, −1
last); `quantity` a result key as in `result_stats` (`stress:von_mises`); `axes` a
coordinate system for vectors and tensors (not for shapes).

## Mistakes

- Expecting loads and supports in `nastran` or `abaqus_inp`: they carry the mesh, sets,
  materials and results only.
- A morphed shape across contact or a plastic hinge: refused; use `deformed_step`.
- A wall cloud on a RANS run read as the solver's own shear: it is a wall-function
  estimate from the wall cell.
- Writing outside the project folder: refused for agents.
