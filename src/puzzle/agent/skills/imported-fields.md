---
name: imported-fields
description: values from another tool or a test as loads: importing a file (CSV, VTU, Fluent, Exodus, EnSight, OpenFOAM), checking it, fields varying in time, and a feature taking a value from it
adr: 0044
---
An **imported field** (kind `field`) is a file of points and values in the project
(see Formats), how to read it (`columns`: each `name` with a `role` x, y, z, value,
time or ignore and a `unit`; `length_unit` of the points), and a `placement` onto the
model: `axes` (a coordinate system's id: the points are in its axes), then `rotation` (degrees about x, then y, then z) about `about` (m),
then `translation` (m). Nothing is mapped when it is made: each run maps it onto the
mesh, so a remesh or a changed file just reruns.

## Import one

1. Put the file inside the project folder (an agent reads no files outside it).
2. `field_import(file="fields/cfd.csv", name="CFD walls")`: the guess (`read`) and the
   `create` command. Headers like `x [mm]`, `p [MPa]` give units outright; check the
   roles and the `length_unit` (mm points read as m is the classic mistake).
3. `submit(create)` (edit its `data` first if the guess is wrong).
4. `field_check(field="CFD walls", on="Bracket/Plate/Top")`: `coverage` (share of the
   target within `tolerance` of a data point), `largest_distance`, `off_model`,
   `size_ratio`, and `warnings` in plain words. Low coverage or a size ratio far from 1
   means the unit or placement is wrong: fix `length_unit` or `placement` with an
   `edit` and check again.

## Formats

`format` is sniffed from the name or the contents:
- `csv`: CSV/TXT/DAT, any delimiter; Ansys Mechanical's exports ("Node Number", "X
  Location (mm)") and Fluent's ASCII export ("nodenumber", "x-coordinate") are found
  as such, the number columns ignored.
- `vtu`: VTU/VTK point or cell data.
- `fluent_profile` (.prof): point profiles; `region` one profile by name, else all.
- `exodus` (.exo, .e; netCDF-3 or netCDF-4): nodal variables at the nodes, element
  variables at the elements' corner means; `region` an element block or node set.
- `ensight` (.case, C Binary or ASCII; Code_Saturne, Puzzle's exports): per node or per
  element; default the volume parts, `region` one part (a boundary, `Part/Selection` in
  Puzzle's exports). Its geometry and variable files come into the project with it.
- `openfoam`: the case folder (`file` is the folder): ASCII vol fields at the cell
  centres (the `C` field when written, else OpenFOAM's own centres from polyMesh), or
  `region` a patch at its face centres (its `value`, else the cells beside it). Binary
  fields are refused: rerun with `writeFormat ascii` or `foamFormatConvert`.
- Vectors and tensors are one column per component: `U_x`, `U_y`, `U_z`; `stress_xx`,
  `_yy`, `_zz`, `_xy`, `_yz`, `_zx`. A field reads node values or element values, not
  both. `field_import` returns `read.steps` (time, index) and `read.regions`.
- `index` reads one step of a multi-step source (an Exodus/EnSight step number from 1,
  an OpenFOAM time directory's name); default the last.

## Varying in time

- Several files of one layout: `field_import(file=..., files=[...], times=[0, 5, 10])`
  (s, one per file, the first file's first); a source's steps: `every_step=True` (their
  own times, or `times` one per step); or a CSV column with role `time` (rows grouped
  by its value, its unit converted to s). The field then lists `times`.
- Solve step k takes it at its time: the step's `end_time` setting if it has one, else
  k (1, 2, ...), linearly between the field's times and held at the ends, as an
  Amplitude is. So two static steps on a field at 0 and 2 s get its values at 1 and 2 s.
- A transient step follows the whole history against its step time: CalculiX for
  pressure p and heat_flux q (an amplitude per face, `Capabilities.field_histories`);
  everything else is refused there, as is a field varying in time beside an amplitude.

## Use it

A feature takes a value from a field through `fields` (value name → use):
`{"command": "edit", "object": "<component>", "ops": [{"op": "set",
"path": "physics.0.features.2.fields.p", "value": {"field": "<field id>", "column": "p",
"scale": 1.0, "offset": {"value": 0, "unit": "MPa"}, "mapping": "linear"}}]}`.
- `mapping`: linear (in the points' own span: scattered 3D, a plane, a line; outside it
  the nearest value), nearest, or inverse_distance.
- `max_distance` (a quantity) with `beyond`: nearest (default), zero, or refuse.
- A use's own `placement` moves the field again for this use only.
- Remove the use (`"op": "remove"` on that path) to go back to the feature's number.

Which values take a field, by solver (`Capabilities.field_features`; the rest are
refused by name, never ignored):
- **body_temperature T** (a structural feature on a body: "Temperature (thermal
  load)"; thermal strain from the physics' `reference_temperature`): every FE solver.
- **pressure p, heat_flux q, convection h and T_inf** (on face selections): CalculiX,
  Code_Aster, Elmer and FEniCSx, steady or transient, a flux or pressure also times
  its amplitude. CalculiX takes one value per face (the mean of its nodes), which
  smears a field that varies within a few elements (a linear flux's spread across a
  10 mm face came out 4.6 % short on 2.5 mm tets); the other three integrate the
  field's own finite-element interpolant (Code_Aster: pressure as consistent nodal
  forces, flux and films as FORMULEs over the faces; Elmer: restart variables read by
  `Equals`; FEniCSx: Lagrange functions). Code_Aster refuses flux and film fields on 2D
  models so far.

## Mistakes

- A temperature given both by a transfer and a body temperature: refused, keep one.
- Offsets add a difference: `offset` 10 K and 10 °C are the same step.
- The check on the data alone (no `on`) says nothing about coverage: give the target.
- A field varying in time with no time given (a static use outside a study): refused;
  `field_check` reports on its first time and lists `times`.
- CLI twins: `puzzle import-field PROJECT FILE [FILE...] [--times s...] [--every-step]
  [--region NAME] [--step INDEX] [--unit mm] [--column p:value:MPa]
  [--translate x y z] [--rotate rx ry rz] [--about x y z]` and
  `puzzle field-check PROJECT NAME [--on Component/Part/Selection] [--json]`.
