---
title: Imported fields
section: guide
order: 120
keys: kind:field, section:imported fields
summary: Bring values at points from another tool or a test into a model as loads: CFD wall pressures, measured temperatures, forces, shapes.
adrs: 0044
---

An imported field is a file of points and values, kept in the project, that loads can
take their values from. Use one when a load varies over a face or body and comes from
somewhere else: a CFD pressure, a thermal survey, a test, another FE program.

Nothing is mapped when you import it. Each run maps the field onto the mesh, so a new
mesh or a replaced file just runs again.

## How to import a field

1. Project → Import field… (or right-click Imported fields in the tree).
2. **File**: choose the file. Puzzle guesses the format and each column's role (x, y,
   z, value, time or ignore) and unit. Headers like `x [mm]` and `p [MPa]` give the
   units outright.
3. **Alignment**: place the points on the model: a coordinate system, then a rotation
   (degrees about x, then y, then z) about a point, then a translation.
4. **Verification**: choose the target face or body. Puzzle meshes it and reports the
   coverage (the share of the target near a data point), the largest distance, points
   off the model and the size ratio.
5. **Name**, then Finish. The file is copied into the project's `fields/` folder in one
   command, so one undo removes it.

Low coverage, or a size ratio far from 1, means the unit or the placement is wrong. Fix
the length unit or the placement and check again.

## Formats

- CSV, TXT, DAT with any delimiter, including Ansys Mechanical's and Fluent's ASCII
  exports (their node number columns are ignored).
- VTU/VTK, Fluent profiles (.prof), Exodus II (.exo, .e), EnSight Gold (.case), and
  OpenFOAM case folders (ASCII fields only).
- Vectors and tensors as one column per component: `U_x`, `U_y`, `U_z`; `stress_xx`...
- Puzzle's own exports (point, wall and Lorentz clouds) read back in, with their area
  or volume columns for conservative mapping.

## How to use a field as a load

1. Right-click the load or support in the model tree, Take a value from an imported
   field….
2. Choose the value (a pressure's p, a convection's h...), the field and its column.
3. Set a scale and an offset if needed, and how it maps:
   - linear (the default): in the points' own span, nearest value outside it;
   - nearest, or inverse distance;
   - conservative: keeps a force-type total (pressure, traction, heat flux, body
     force). It needs a weight column of each point's area or volume.
4. Right-click the load, Back to uniform values, to return to its own number.

Values that take a field: [pressure](help:feature:pressure),
[heat flux](help:feature:heat_flux), [convection](help:feature:convection)'s h and
T_inf, a [body temperature](help:feature:body_temperature) (thermal load),
[body force](help:feature:body_force), [traction](help:feature:traction) and
[displacement](help:feature:displacement) on CalculiX, Code_Aster, Elmer and FEniCSx.
[Initial stress](help:feature:initial_stress), a shell's thickness and a graded
material (Young's modulus or conductivity) run on CalculiX and Code_Aster.

## Fields in time or frequency

A field may vary along time **or** frequency, never both.

- **Time**: several files with a time each, a source's own steps, or a CSV column of
  role time. A static step k takes the field at its end time (or at k), linearly
  between the field's times. A transient step follows the whole history on CalculiX,
  for pressure and heat flux only.
- **Frequency**: a column of role frequency (a `freq` header or a Hz unit), or files
  with a frequency each. It applies in frequency-domain steps only, to pressure on
  CalculiX, Code_Aster and FEniCSx.

## Options worth knowing

- **Max distance** and **beyond**: past that distance from the data, take the nearest
  value (the default), zero, or refuse.
- A use's own placement moves the field again for that load only.
- A vector that a placement turns must take every component from one field with one
  placement and mapping.
- In 2D models the points lie at z = 0. Tractions and body forces run on CalculiX and
  Code_Aster there, and conservative mapping is refused.

## Mistakes to avoid

- Millimetre points read as metres: the coverage check shows it. Set the length unit.
- A temperature given both by a transfer and a body temperature: refused, keep one.
- An offset adds a difference: 10 K and 10 °C are the same offset.
- A check with no target says nothing about coverage: always give the face or body.
- A field in time with no time to take it at (a static use outside a study): refused.
- A frequency field on a load active in a static or transient step, or beside an
  amplitude: refused ([field_axis](help:problem:field_axis)). Limit the load to the
  harmonic steps.
- Binary OpenFOAM fields are refused: rerun with `writeFormat ascii`.

## For agents and scripts

The agent imports with [field_import](help:mcp:field_import) and checks with
[field_check](help:mcp:field_check). In a terminal: `puzzle import-field PROJECT FILE`
([cli:import-field](help:cli:import-field)) and `puzzle field-check PROJECT NAME --on
Component/Part/Selection` ([cli:field-check](help:cli:field-check)).

## See also

- [Loads and supports](help:guides/loads-and-supports)
- [Studies and solvers](help:guides/studies-and-solvers)
- [Results](help:guides/results)
