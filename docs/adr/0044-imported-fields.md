# 0044. Imported fields: a wizard to bring them in, and features that take them

- Status: Accepted (2026-10-25, with the maintainer's answers)
- Date: 2026-10-25

## Context

A model often needs a load or a starting state that another tool computed or a test measured:
- the temperatures of a CFD or thermal run;
- wall pressures and heat transfer coefficients from CFD;
- Lorentz forces from an electromagnetic solve;
- residual stresses from welding;
- a scanned shape;
- thermocouple readings.

Ansys calls this External Data, COMSOL interpolation functions, and Abaqus mapped analytical fields. All three read point clouds (x, y, z and values), place them, map them onto the model's mesh, and use them as loads.

Puzzle already maps fields between its own studies:
- transfers (ADR 0009, `transfer.py`) map a result onto another study's mesh by copying, linear interpolation in the source's tetrahedra, or nearest node;
- adapters take the mapped field as an `Input` (role load, initial condition or prestress) and refuse what they can't apply;
- ADR 0043 exports point clouds for other tools.

What's missing is the way in from outside: reading foreign files, placing them, checking them, and applying them to any load.

The maintainer's answers (2026-10-25):
- **a wizard** that collects the file and handles alignment and verification;
- **how and where the field is applied, including scaling and a three-axis translation and rotation;**
- **every quantity** in scope (temperature, pressure, heat transfer coefficient, body forces and the rest), in stages;
- **features take a field** rather than a dedicated imported load per quantity;
- **sources:** Ansys exports, OpenFOAM and Code_Saturne results, test and measurement data, Exodus and other FE tools, beside generic CSV and VTU.

## Decision

1. **An Imported field is a project object** (kind `field`, an "Imported fields" section in the tree). It holds:
   - the file, copied into the project, and its content hash;
   - how it is read: the format, the rows, and each column's role and unit;
   - the source frame: units, and placement in the source's own axes;
   - time points when it varies in time.

   It stores no mapped values: like a transfer, it is mapped onto the target mesh when a study runs, and it is part of the run key. A changed file or setting makes the studies that use it out of date.

2. **The wizard** (File → Import field…, and the tree's section menu) makes one, in four pages. Nothing changes in the project until Finish, which is one command (one undo).
   1. **File:**
      - **Formats:**
        - generic delimited text (CSV, TXT, DAT) with header and comment rows;
        - Ansys External Data and Fluent profile exports;
        - VTU/VTK with point or cell data;
        - Exodus II;
        - OpenFOAM and Code_Saturne wall and volume fields (from Puzzle's own runs, or a case folder);
        - Puzzle's own point-cloud exports (ADR 0043).
      - **Columns:** each column is a coordinate, a scalar, or a vector's or tensor's component, with its unit.
      - **Varying in time or frequency:** several files, or a time or frequency column.
      - Test data with only a few sensor points is allowed, and flagged as sparse.
   2. **Alignment:** place the source frame on the model:
      - the length unit (a file in millimetres against a model in metres is the classic mistake);
      - a three-axis translation and three rotations (about x, y and z, about a chosen point), or one of the project's coordinate systems (ADR 0037);
      - helpers: three picked point pairs, the bounding boxes matched, or a best fit of the points onto the target faces;
      - the points are drawn over the model in the view as they move.
   3. **Verification**, on the target the field will be used on:
      - point count, range and mean per column;
      - coverage: the share of the target within a tolerance of the data;
      - a histogram of each target node's distance to the nearest data point;
      - outliers well off the model;
      - the field mapped and coloured on the target, with extrapolated areas marked.
      - Plain-word warnings ("12 % of Bar/Top is more than 2 mm from any data point"); a mapping that misses most of the target is flagged before it can be used.
   4. **Name and Finish.**

3. **Features take a field in place of a value.** Any feature value can be a reference instead of a number: `{"field": <Imported field id>, "column": "p"}`. The same pressure, temperature, convection, force density or displacement feature takes a uniform number or an imported field, and applying a field means editing a value.

   Each use carries its own application settings, so one imported field can be reused in several places and steps:
   - **scaling:** a factor and an offset on the values (a pressure ×1.2, a temperature +20 K), unit-aware;
   - **placement on the model:** another three-axis translation and three rotations (about x, y and z, about a chosen point), or a coordinate system, on top of the field's own alignment. Vectors and tensors rotate with the points;
   - **mapping:** nearest point; linear inside the source mesh where the file has one; inverse distance or radial basis for scattered points; or conservative, which keeps the total force of a force-type quantity. Beyond a set distance it takes the nearest value, uses zero, or is refused, as chosen;
   - **where and when:** the feature's own selection (faces or bodies) and steps (`Feature.steps`); a field that varies in time follows the step's time, as an amplitude does.

4. **Quantities**, by stage:

   | Stage | Quantity | Applied as |
   | --- | --- | --- |
   | 1 | Temperature | a thermal load on bodies (thermal strain), or a transient heat study's starting state |
   | 1 | Pressure | a face load |
   | 1 | Heat transfer coefficient (+ bulk temperature) | convection on faces |
   | 2 | Body force density (vector) | a body load, e.g. electromagnetic Lorentz forces |
   | 2 | Traction (vector) | a face load, e.g. CFD wall shear |
   | 2 | Heat flux | a face load |
   | 2 | Displacement | imposed on faces (a measured shape, a submodel's boundary) |
   | 3 | Initial or residual stress | prestress on bodies |
   | 3 | Thickness and material property fields | shells, graded materials |

5. **Solvers apply them in their own terms.** For example, CalculiX takes nodal temperatures, per-element face pressures, and nodal forces from an integrated traction. Each adapter declares the field-driven quantities it writes (`Capabilities.field_features`) and refuses the others with the reason, as it does for unsupported features today. CalculiX and Code_Aster come first, then Elmer and FEniCSx.

6. **Checks before claims:**
   - a linear temperature field round-trips through the import and mapping exactly;
   - a plate under an imported uniform pressure matches the closed form, and a translated and rotated copy of the same field gives the same answer;
   - thermal stress from an imported temperature matches the same case run through a transfer;
   - a Code_Saturne or OpenFOAM wall pressure brought into CalculiX keeps its total force (conservative mapping);
   - sparse test points are interpolated within their stated tolerance.

7. **Twins:**
   - `puzzle import-field PROJECT FILE` with the same options, and `puzzle field-check` for the verification report;
   - for agents, MCP `field_import` (made as a proposal) and `field_check`;
   - the outline lists each imported field and where it is used.

## Consequences

- Loads from other tools, tests and Puzzle's own CFD reach the structural and thermal solvers through one mechanism, with the same mapping code as transfers.
- Feature values become "a number or a field". The properties panel, the symbols in the view (ADR 0035), the outline and every adapter's feature writer must handle both. That is the main cost, and stage 1 keeps it to three quantities.
- Run keys cover the field's file hash and settings, so a re-import makes its users out of date, never silently stale.
- Mapping can extrapolate. The verification page, the distance limits and the marked areas are the guard, and nothing is applied without the user seeing the coverage.

## Alternatives considered

- **A dedicated imported load per quantity** ("imported pressure", "imported temperature"): explicit in the tree, but a parallel set of loads to write, show and test. The maintainer chose field-driven values.
- **Mapping once at import and storing the values:** fast, but it binds the field to one mesh; mapping per run follows mesh changes, as transfers do.
- **Only our own result files:** simpler readers, but they leave out the main reason to import, other tools and tests.
