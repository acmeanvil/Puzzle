# 0044. Imported fields: a wizard to bring them in, and features that take them

- Status: Accepted (2026-10-25, with the maintainer's answers); built 2026-11-01 (stages 1 to 3; what is left is listed under "Still open")
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
- **sources:** Ansys exports, OpenFOAM and Code_Saturne results, test and measurement data, Exodus and other FE tools, beside generic CSV and VTU;
- **seeing the connections** concisely and informatively: a schematic, the tree and Properties, and the 3D view, each switchable in the preferences (point 8).

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

8. **The connections are shown three ways**, each switched on or off on its own in Settings > Connections (all on by default), so a user keeps what helps and hides the rest:
   - **a Connections schematic**, the overview (`connections.schematic`; Window → Connections): a read-only map drawn from the model, never edited by hand:
     - left to right: **sources** (imported fields, and studies whose results feed others) → **what they feed** (the features that take a field, the studies a transfer feeds) → **results**;
     - only links that carry data: imported fields into features, and transfers between studies. Ordinary references (a component's geometry and mesh) stay in the tree, so the map stays small;
     - each box is coloured by its state (ADR 0033: up to date, out of date, missing input, not run, failed);
     - each link is labelled with what it carries and how ("T, +10 mm", "p ×1.2", "temperature, interpolate"), and an imported field's box with its coverage, flagged when low;
     - a click on a box selects it in the tree; hovering a link shows its mapping, scaling, placement and coverage in full;
     - laid out automatically in columns, as Ansys Workbench's project schematic is, but generated from the model, so it can't drift from it;
   - **in the tree and Properties** (`connections.tree`):
     - a field-fed feature names its source in the tree ("Pressure on Top ← CFD walls ×1.2");
     - an imported field lists "Used by" with each use's scaling and placement;
     - Properties shows the whole chain from the file to the solver;
     - transfers show on the studies they feed;
   - **in the 3D view** (`connections.view`): selecting a field-fed feature or an imported field draws the mapped values on its faces with the legend (ADR 0038), extrapolated areas hatched, and optionally the source points, so you see where the data lands and where it doesn't.

## Built: stage 1 (2026-11-01)

- **The object:** `ImportedField` (kind `field`, the tree's "Imported fields" section). It holds:
  - the file, copied into the project's `fields/` folder, and its SHA-256, so a changed file stales its users;
  - how the file is read: the format (csv, vtu), delimiter, rows to skip, header, and each column's role (x, y, z, value, ignore) and unit;
  - the points' length unit;
  - a `FieldPlacement`: a coordinate system's axes, then a rotation in degrees about x, then y, then z, about a point, then a move.
- **Reading** (`puzzle.fields`): `sniff` guesses the delimiter (comma, semicolon, tab or whitespace), comment and header rows, and roles and units. A `name [unit]` header names both, which is how Puzzle's own point clouds (ADR 0043) and Ansys's External Data write them. VTU point or cell data is read with pyvista.
- **Features take a field** through `Feature.fields`, a value name mapped to a `FieldUse`. A use holds the column, scale, offset (a temperature offset adds a difference), its own placement, the mapping and `max_distance` with `beyond` (nearest, zero, refuse).
  - Linear mapping works in the points' own span: Delaunay cells for scattered 3D points, triangles for a wall's points, segments for a line. Outside it a target takes the nearest value. Nearest and inverse distance are the other mappings.
  - An unused `fields` is left out of content hashes, so older projects keep theirs.
- **Verification** (`fields.check`):
  - point count, and each column's range and mean in SI;
  - with a target: coverage within a tolerance, the distance histogram, points off the model, and the data's size against the target's;
  - warnings: sparse data, an uncovered share, "misses most of the target", and "spans 1e+03 times the target's size: check its length unit" (millimetres read as metres).
  - The default tolerance is 2 % of the target, or 1.5 × the data's median point spacing when that is larger, so a coarse grid that does cover the target isn't called uncovered.
- **In studies:** `run_study` maps each use onto the mesh (`fields.field_inputs`).
  - A new structural feature, **body temperature** ("Temperature (thermal load)", on a body, uniform or from a field), becomes a temperature load, which all four FE solvers apply. A temperature from both a transfer and a body temperature is refused.
  - Other field-driven values reach the adapter as `feature` inputs named `type:selection:value`. That name survives the per-step copies split studies get.
  - CalculiX writes pressure, heat flux and films per face, using the mean of the face's nodes.
  - Code_Aster writes a varying pressure as consistent nodal forces (`common.pressure_forces`, ∫N(−p n)dA with outward normals) on point groups, since its PRES_REP takes one value per group.
  - `Capabilities.field_features` lists what each adapter takes; the rest is refused by name.
- **GUI:**
  - Project (and the section's menu) → Import field… opens a four-page wizard: File (roles and units per column), Alignment, Verification (the target meshed in the background, the report shown) and Name. Finish copies the file in and adds the field, as one command.
  - A feature's menu → Take a value from an imported field… is one form, and Back to uniform values removes the uses.
  - The tree names a field-fed feature's source ("Convection on Tip ← CFD walls ×1.5"), and the outline lists each field and where it's used.
- **Twins:** `puzzle import-field` and `puzzle field-check`; MCP `field_import`, which reads files inside the project only and returns the Create command to submit, and `field_check`; the skill `imported-fields`.
- **Benchmarks** (`tests/integration/test_imported_fields.py`, `tests/test_fields.py`):
  - a linear field maps exactly;
  - an imported linear temperature on the steel bar against its free expansion: +0.3 % on CalculiX, Code_Aster, FEniCSx and Elmer alike;
  - an imported uniform pressure on the tip equals the uniform feature (−0.18 % from −pL/E, the mesh's own error). A copy written turned 90° and moved 1 m, then placed back, gives the same digits, on CalculiX and Code_Aster;
  - an imported film coefficient equals the uniform one (CalculiX).

## Built: the rest (2026-11-01)

- **Readers** (`puzzle.field_readers`): Ansys Fluent point profiles (`.prof`; a `region` picks one), Exodus II (netCDF-3 through SciPy, netCDF-4 through h5py), EnSight Gold (C Binary or ASCII, its files copied in with the `.case`) and OpenFOAM case folders (ASCII vol fields at OpenFOAM's own cell centres, or the written `C`; a patch at its face centres). Vectors and tensors become one column per component (`U_x`, `stress_xx`, ...); element values sit at element centres; `region` and `index` pick a part and a step. Ansys Mechanical's and Fluent's other text exports are delimited text whose number and location headers `sniff` now recognises. Round trips from Puzzle's own Exodus and EnSight writers, a hand-written profile, and a two-cell OpenFOAM case (its prism's centroid exact to 1e-14) give back the values put in.
- **Fields varying in time:** `ImportedField.times` (a file each, or a source's steps) or a column with the role `time`. Solve step k takes the field at its `end_time` setting, else at k, linearly between the field's times and held outside them, as an Amplitude is; each job of a split study gets its own step's values (`Input.step`). Two static steps on a 0 → 20 MPa ramp equal uniform 10 and 20 MPa to 1e-6 on CalculiX and Code_Aster. Through a transient step, CalculiX follows a pressure or heat flux history (an `*AMPLITUDE` per face history: equal to a uniform flux on an Amplitude to 1e-9); `Capabilities.field_histories` refuses the rest.
- **Vectors (stage 2):** two new structural features, **body force density** (`body_force`, fx, fy, fz in N/m³: per volume, no density, e.g. Lorentz forces) and **traction** (`traction`, tx, ty, tz in Pa), uniform on all four FE solvers or from fields on CalculiX and Code_Aster; a **displacement** from a field, imposed per node. A vector's components are one `FieldUse` each (`fields.VECTORS`); when a placement turns the points or the mapping is conservative they must come from one field and placement, and are mapped and turned as one vector. CalculiX and Code_Aster write consistent nodal forces (∫N t dA, ∫N f dV; Code_Aster's `FORCE_FACE` and `FORCE_INTERNE` when uniform). A body force equal to ρg gives gravity's tip displacement to 9 digits and an inward traction the pressure's, on all four solvers; a shear traction is −0.47 % from Timoshenko; a turned and moved copy of a traction field placed back gives the uniform answer to every digit; a linear displacement field is exact at the nodes.
- **Conservative mapping** (pressure, traction, heat flux, body force): linear, then corrected by v + |v| k so that ∫ v over the target equals the source's Σ w v, w each point's area or volume from the use's `weight` column (Puzzle's wall cloud now writes `area`, its Lorentz cloud `dV`); refused without one. Coarse Lorentz and wall-pressure clouds keep their totals, confirmed by the root reactions (Code_Aster exactly; CalculiX within 7e-7, its reactions leaving out the body load applied straight to held nodes).
- **Every FE solver takes the scalar face values** (pressure, heat flux, film coefficient, ambient temperature), steady or transient, with temperature tables too:
  - CalculiX: pressure and heat flux as consistent nodal loads (`*CLOAD`, `*CFLUX`), films one value per face;
  - Code_Aster: pressure as consistent nodal forces, flux and films as their own `AFFE_CHAR_THER_F` charges whose values are FORMULEs over a small interpolator written into the `.comm` (the faces' shape functions at Code_Aster's Gauss points: `ECHANGE`/`FLUX_REP` take one value per group, `EVOL_CHAR` one per element);
  - Elmer: nodal variables in its restart file, read by the boundary conditions with `Equals` (an amplitude in MATC);
  - FEniCSx: Lagrange functions of the nodal values.
  A linear tip flux gives the energy-balance mean 775.023 K and the series' spread across the tip, 10.177 K, to 10.175–10.176 K on all four (`test_imported_fields_parity.py`); uniform imported h and T_inf give the 1D answer 351.61134 K with or without temperature tables.
- **Stage 3** (CalculiX and Code_Aster; Elmer and FEniCSx refuse by name):
  - **initial stress** (`initial_stress`, six components, uniform or from a field, the tensor turned R σ Rᵀ with the placements): CalculiX `*INITIAL CONDITIONS, TYPE=STRESS` at its integration points; Code_Aster `ETAT_INIT=_F(SIGM=...)` in STAT_NON_LINE (solids only). A free bar relaxes by −σ0 L / E within 1 %, with and without large deflection; a field, and a turned copy placed back, equal the uniform feature; a self-equilibrated residual stress barely moves it;
  - **shell thickness from a field**: CalculiX `*NODAL THICKNESS`, Code_Aster DKT elements binned by thickness within 0.5 %; a tapered strip's tip deflection matches ∫ M x / (E I) dx within 1.5 %;
  - **graded materials**: Young's modulus and conductivity from fields, elements binned into materials within 0.5 % (`adapters/graded.py`); a bar with E from 200 to 100 GPa matches ∫ F / (E A) dx, and graded conduction T0 + q ∫ dx / k, within 1 %.
- **2D models** (ADR 0048; `tests/integration/test_imported_fields_2d.py`): tractions on edges and body forces on the region, uniform or from fields, on CalculiX and Code_Aster, as consistent nodal forces ∫N t dl and ∫N f dA times `common.planar_depth`: the thickness (plane stress), 1 m (plane strain) or 2πr (axisymmetric) for CalculiX's whole forces, per metre of depth or per radian for Code_Aster's FORCE_NODALE (its uniform ones FORCE_CONTOUR and FORCE_INTERNE); x and y only. Code_Aster's flux and film fields interpolate along the edges (`OnEdges`, FORMULEs of X and Y). Against closed forms:
  - a radial body force field f = c r spins a ring as ρω² = c does: rim displacement within 1e-5 and bore hoop stress within 0.12 % on both, plane stress (2 mm thick), plane strain and axisymmetric;
  - a traction field on an axisymmetric bore equals Lamé's pressure and the uniform traction (rim −0.010 % CalculiX, 0.000 % Code_Aster);
  - a parabolic shear traction field on a plane-stress cantilever: Timoshenko's tip deflection −0.27 % (CalculiX), −0.14 % (Code_Aster), the uniform shear −0.31 % and −0.18 %;
  - a linear heat flux field on a planar slab: the energy balance's mean to 1e-5 on all four FE solvers and the series' spread across the edge, 20.354 K, to 4 digits on Code_Aster, Elmer and FEniCSx and −0.5 % on CalculiX (one value per edge); imported films and an axisymmetric wall's inner flux and outer film give the 1D answers to 7 digits on all four.
  Conservative mapping is refused in 2D models.
- **Alignment helpers** (`puzzle.field_align`): three or more point pairs fitted by Kabsch's SVD with the residual (a known placement recovered to 1e-14); bounding boxes matched centre to centre, optionally turned to line up the longest sides; a best fit by rigid ICP onto the target's faces (point to plane) from the current placement, with the RMS before and after (a misplaced bar from 0.89 mm to 3e-17). Each gives a `FieldPlacement`. The wizard is modeless: the Alignment page has them as buttons, pairs typed or picked in the view, and draws the data's points as they move; the Verification page colours the field on the target with the legend and marks faces past the tolerance.
- **Connections** (`gui/field_view.py`, `puzzle.field_display`): selecting a field-fed feature or an imported field maps it in the background and draws the values on its faces or bodies, extrapolated faces marked and the source points optional. Properties lists an imported field's uses in words and a feature's chain from the file to the solvers that take it. Settings > Connections switches the tree and Properties (`connections.tree`) and the view (`connections.view`) apart, and sets the points and the extrapolated colour. The Connections schematic is ADR 0058's.
- **Twins:** `puzzle import-field` takes several files, `--times`, `--every-step`, `--region`, `--step`; MCP `field_import` the same; the `imported-fields` skill covers formats, time, vectors, conservative mapping and stage 3.

## Still open

- Frequency as a field's axis (time only); films and body temperatures following a history through a transient step; vectors varying in time through a transient step.
- Fortran-binary and structured EnSight, binary OpenFOAM, Fluent transient profiles; a field from one of the project's own runs (transfers cover that inside Puzzle).
- Vector fields on Elmer and FEniCSx (and their vector loads in 2D models), conservative mapping in 2D models, a pressure field on 2D edges on Code_Aster, CalculiX's 2D flux fields as consistent nodal flows (one value per edge today), weights taken from a VTU file's cells, all three components chosen at once in the GUI's form.
- Code_Aster initial stress on beams and shells.
- The alignment helpers over MCP.

## Consequences

- Loads from other tools, tests and Puzzle's own CFD reach the structural and thermal solvers through one mechanism, with the same mapping code as transfers.
- Feature values become "a number or a field". The properties panel, the symbols in the view (ADR 0035), the outline and every adapter's feature writer must handle both. That is the main cost, and stage 1 keeps it to three quantities.
- Run keys cover the field's file hash and settings, so a re-import makes its users out of date, never silently stale.
- Mapping can extrapolate. The verification page, the distance limits and the marked areas are the guard, and nothing is applied without the user seeing the coverage.

## Alternatives considered

- **A dedicated imported load per quantity** ("imported pressure", "imported temperature"): explicit in the tree, but a parallel set of loads to write, show and test. The maintainer chose field-driven values.
- **Mapping once at import and storing the values:** fast, but it binds the field to one mesh; mapping per run follows mesh changes, as transfers do.
- **Only our own result files:** simpler readers, but they leave out the main reason to import, other tools and tests.
