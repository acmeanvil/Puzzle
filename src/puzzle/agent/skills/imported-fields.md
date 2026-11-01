---
name: imported-fields
description: loads from another tool or a test: importing CSV, VTU, Fluent, Exodus, EnSight or OpenFOAM data, checking it, time, vectors, conservative mapping
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

## Vectors (stage 2)

Three features are vectors, one use per component:
- **body_force** "Body force density" (on a body): `fx, fy, fz` in N/m^3, a force per
  volume with no density (Lorentz forces J × B from an EM run; unlike `gravity`, which
  is an acceleration times the material's density);
- **traction** "Traction (force per area)" (on faces): `tx, ty, tz` in Pa, e.g. CFD wall
  shear plus pressure. Inward normal traction equals a pressure: t = −p n;
- **displacement** `x, y, z` from a field: imposed per node (a measured shape, a
  submodel's boundary); a component neither given nor taken from a field stays free.

A body force's or traction's missing component is zero. When a placement (the field's
or the use's) turns the points, the vectors turn with them: then every component must
come from **the same field with the same placement and mapping** (columns may differ),
else it is refused. Scale and offset act on the file's components before the turn.
Don't give such a feature `axes`: place the field instead.

## Conservative mapping

`"mapping": "conservative"` keeps a force-type value's total (pressure, traction,
heat_flux on faces; body_force on bodies): the values are mapped linearly, then corrected
in proportion to the local magnitude (v + |v| k) so ∫ v over the target equals the
source's ∑ w v; zero stays zero. It needs `"weight": "<column>"`: each point's area (a
face load) or volume (a body load); a weight column without a unit is in the points'
length unit squared or cubed. Puzzle's own exports carry it: the wall cloud's `area`
column, the Lorentz cloud's `dV`. Without a weight, or on a temperature or a
displacement, it is refused. A vector's components are conserved as one vector, so all
three need the same conservative use settings.

Which values take a field, by solver (`Capabilities.field_features`; the rest are
refused by name, never ignored):
- **body_temperature T** (a structural feature on a body: "Temperature (thermal
  load)"; thermal strain from the physics' `reference_temperature`): every FE solver.
- **pressure p, heat_flux q, convection h and T_inf** (on face selections): CalculiX,
  Code_Aster, Elmer and FEniCSx, steady or transient, a flux or pressure also times
  its amplitude. All four integrate the field's own finite-element interpolant
  (CalculiX: pressure and flux as consistent nodal loads, films one value per face;
  Code_Aster: pressure as consistent nodal forces, flux and films as FORMULEs over
  the faces; Elmer: restart variables read by
  `Equals`; FEniCSx: Lagrange functions). Code_Aster refuses flux and film fields on 2D
  models so far.
- **body_force fx/fy/fz, traction tx/ty/tz, displacement x/y/z:** CalculiX and
  Code_Aster (consistent nodal forces ∫N t dA, ∫N f dV; displacements per node).
  Uniform body_force and traction: all four FE solvers (not 2D models yet).

## Stage 3: initial stress, shell thickness, graded materials (CalculiX, Code_Aster)

Elmer and FEniCSx refuse all three by name.
- **initial_stress** (a structural feature on a body, "Initial stress": `sxx`, `syy`,
  `szz`, `sxy`, `syz`, `szx` in MPa; uniform, or each from a field column): the stress
  the static step starts from. Components from a field are in the field's own axes and
  turn with its placement and the use's (R σ Rᵀ), so take them all from **one field
  with one placement**; components not taken from it are uniform in those axes. Static
  steps only (linear or `geometric_nonlinearity`), every step (`steps` empty), global
  axes (`axes` empty); solids only. CalculiX writes it per integration point, Code_Aster
  as `ETAT_INIT` of a STAT_NON_LINE (the static step then runs nonlinear).
  - Not self-equilibrated, it relaxes: a free bar from σxx = σ0 shortens σ0 L / E. A
    weld's residual stress (no net force or moment) barely moves a free part but stays
    in the stress results, which are the total.
- **Shell thickness:** `shell_sections.<i>.fields.thickness` (a use, as a feature's;
  values in a length unit). CalculiX: exact, per node (`*NODAL THICKNESS`); Code_Aster
  DKT: one thickness per element (its nodes' mean), binned within 0.5 %.
- **Graded material:** `materials.<i>.fields.youngs_modulus` or
  `.thermal_conductivity` (Pa, W/(m K)): each element takes its nodes' mean, binned
  into materials within 0.5 % of it on both solvers. Isotropic materials only (no
  plasticity or material axes); for conductivity set the heat physics'
  `temperature_dependent_materials` false (a table would be ignored, so it's refused).

## Mistakes

- A temperature given both by a transfer and a body temperature: refused, keep one.
- Offsets add a difference: `offset` 10 K and 10 °C are the same step.
- The check on the data alone (no `on`) says nothing about coverage: give the target.
- CalculiX's reaction forces leave out the share of a body load applied straight to the
  held nodes (a fraction of a per cent on a coarse mesh); Code_Aster's include it.
- A field varying in time with no time given (a static use outside a study): refused;
  `field_check` reports on its first time and lists `times`.
- CLI twins: `puzzle import-field PROJECT FILE [FILE...] [--times s...] [--every-step]
  [--region NAME] [--step INDEX] [--unit mm] [--column p:value:MPa]
  [--translate x y z] [--rotate rx ry rz] [--about x y z]` and
  `puzzle field-check PROJECT NAME [--on Component/Part/Selection] [--json]`.
