# 0043. Export the deformed shape as CAD and results in neutral formats

- Status: Accepted (2026-10-25, the maintainer); steps 1 and 2 built (2026-10-25); steps 3–7 built (2026-11-01)
- Date: 2026-10-25

## Context

Results leave Puzzle today only as ParaView files (`.vtu`/`.pvd`, `puzzle export`), images and GIFs, and CSV from probes and plots. The maintainer asked (2026-10-25) for:

- the deformed shape as a STEP file;
- structural, thermal, electromagnetic and fluid results in neutral formats that other software reads.

Who receives an export, and why:

| Receiver | Why | What it reads |
| --- | --- | --- |
| CAD (SolidWorks, Fusion, FreeCAD, Onshape) | check clearances under load or heat; build a pre-distorted ("compensated") part for welding, casting or printing; show the deflected assembly | STEP (B-rep or AP242 tessellated), STL/3MF |
| Another FE or CFD tool (Ansys, Abaqus, Nastran, COMSOL, STAR-CCM+, Fluent) | its own analysis on our results: temperatures into a stress model, wall pressures or heat transfer coefficients into a structure, EM forces into a structure | point clouds (x, y, z, value) such as Ansys External Data and COMSOL interpolation functions; Nastran and Abaqus decks for the mesh |
| Post-processors (ParaView, EnSight, Tecplot, VisIt) | their own plots, reports and comparisons | VTU/PVD, XDMF+HDF5, Exodus II, EnSight Gold, CGNS, Tecplot |
| Test-analysis correlation (Simcenter Testlab, ME'scope, FEMtools) | compare mode shapes with measured ones; place sensors | UNV universal file (datasets 2411, 2412, 2414 and 55/58 for modes) |
| Reports, the web, AR | a 3D picture anyone can open | glTF 2.0 with colours by result, PNG, GIF |
| Scripts and spreadsheets | their own checks | CSV tables, and the results store (Delta Lake) as it is |

The neutral-format landscape, briefly:

- **VTK XML** (`.vtu`, `.pvd`, `.vtkhdf`): ParaView and VisIt's own. It holds every element type and field we have. VTKHDF is one HDF5 file, transient included, and scales better than many `.vtu` files.
- **XDMF + HDF5:** light XML over HDF5 arrays. It suits FEniCS, ParaView and VisIt, and large or transient data.
- **Exodus II** (netCDF): Sandia's format, used by MOOSE, Cubit, Sierra, ParaView and EnSight. It carries element blocks, node and side sets (our selections) and time steps.
- **EnSight Gold:** the long-standing CFD exchange format (Fluent, STAR-CCM+ and OpenFOAM can write it; EnSight and ParaView read it). Code_Saturne already writes it and we read it.
- **CGNS** (HDF5): the CFD standard for meshes and flow solutions, with boundary conditions by zone. Fluent, CFX, STAR-CCM+, SU2 and ParaView read it.
- **MED** (HDF5): Salome and Code_Aster's format. We already write its meshes for Code_Aster and Code_Saturne.
- **UNV (I-DEAS universal file):** ASCII, old and still the common language of test-analysis correlation and many FE tools. Dataset 2414 holds nodal and element results per step or mode.
- **Nastran:** bulk data (`.bdf`) is the most portable FE mesh and model deck. Its results are OP2 (binary, complex to write) or punch (`.pch`, ASCII).
- **Point clouds (CSV/TXT):** columns x, y, z, value(s). This is how Ansys Mechanical's External Data, COMSOL's interpolation functions, Abaqus analytical mapped fields and many others bring in a field from elsewhere, mapped onto their own mesh.

What the environment has (pixi `dev`, 2026-10-25):
- VTK 9.7 writers: XML, VTKHDF, Exodus II, EnSight, XDMF, glTF, STL, PLY, OBJ;
- OCCT 8: STEP B-rep, AP242 tessellated shells (`write.step.tessellated`), B-spline conversion and sewing;
- not installed: meshio, h5py and netCDF4.

## Decision

1. **One export command, many writers.** Each export is a `post/exports/<format>.py` writer: `write(run, mesh, results, options, path) -> list[Path]`. It is listed in an `EXPORTS` table with its name, extensions, the physics it carries, and what it needs. The GUI is Results → Export… (one form: the run, the format, the fields, the steps, the selections, the units, the path). Alongside it come `puzzle export PROJECT --study S --format F` and MCP `export`. Every export writes SI unless the form asks for display units, says so in the file where the format allows, and writes a short `.txt` beside it naming the source study, the run, the solver and the units.

2. **The deformed shape as CAD**, three ways, in this order:
   1. **Faceted, exact to the mesh:** the deformed outer surface, from the mesh's boundary with each node moved by u. It is written as STL or 3MF, or as a STEP AP242 tessellated shell, which most CAD imports as a mesh body. Quadratic faces are split into four triangles each. This is always available, for any part, mode or step.
   2. **B-rep by morphing:** each face of the part's CAD is converted to B-splines (`BRepBuilderAPI_NurbsConvert`), each control point is moved by the displacement there, and the faces are sewn back into a solid. The displacement comes from the nearest point of the mesh for poles off the body. The result is a STEP with real surfaces, which CAD can edit, measure and mate. It is accepted when the moved part's volume and its faces' deviation from the deformed mesh stay within a tolerance (default 1 % of the largest displacement); otherwise the export says so and offers the faceted way.
   3. **Compensated:** the same with −u, for manufacturing compensation (the shape that deforms *into* the design). It is offered for thermal distortion and residual stress results, where it is used.
   Options: a scale (1 = true; larger exaggerates and is marked in the file name), the step or mode, and the parts. A multi-part component exports as an assembly, one body per part, with the part names.

3. **Results by physics** (all writers take any field, but these are the ones each physics is exported for and benchmarked with):
   - **Structural:**
     - displacement, stress and strain tensors, von Mises and principal stresses, reaction forces, beam section forces and fastener forces;
     - per step or mode, with frequencies and buckling factors as step values;
     - formats: VTU/PVD, VTKHDF, Exodus II, UNV 2414 (modes for correlation), Nastran BDF mesh with punch results, and point clouds of displacement or stress.
   - **Thermal:**
     - temperature, heat flux and its magnitude, per time step;
     - for thermal stress elsewhere: a point cloud (x, y, z, T), as Ansys External Data and COMSOL take;
     - for Abaqus and CalculiX: nodal temperatures with the mesh as `.inp` (`*TEMPERATURE`);
     - Exodus II and VTU for post-processing.
   - **Electromagnetic:**
     - electric potential and field, current density, magnetic flux density, and stored energy (a global value in the `.txt` and CSV);
     - Lorentz force density J × B as a point cloud of body forces for a structural model elsewhere;
     - VTU and XDMF.
   - **Fluid:**
     - velocity, pressure, temperature and turbulence quantities per cell;
     - wall quantities on each wall selection: pressure, shear stress, y+ and heat transfer coefficient;
     - EnSight Gold and CGNS (zones from our selections, so boundary names survive), VTU, Tecplot ASCII;
     - wall pressure and heat transfer coefficient (with its reference temperature) as point clouds, for a structure or a conjugate heat transfer model elsewhere.
4. **Selections travel:** every format that can name sets (Exodus side and node sets, CGNS boundary zones, MED groups, Nastran SETs, UNV groups) gets our selections by their names, so the receiver's loads land on the same faces.
5. **What's checked:**
   - every writer round-trips: read back by VTK, or by the receiving solver where we have it (Code_Aster for MED, CalculiX for `.inp`), with the same node values to the format's digits;
   - the faceted deformed shape matches the mesh exactly;
   - the morphed STEP keeps the part's volume to the deformation's first order (a cantilever's volume barely changes; a heated block grows by 3 α ΔT V);
   - a point cloud mapped back with `transfer.map_field` returns the field within the interpolation error.
6. **Order of work:**
   1. Export… form and CLI/MCP twins, with the VTU/VTKHDF and point-cloud writers;
   2. the faceted deformed shape (STL, 3MF, AP242 tessellated STEP);
   3. Exodus II and UNV;
   4. EnSight Gold and CGNS for fluids;
   5. the morphed B-rep STEP and compensation;
   6. Nastran BDF/punch and Abaqus `.inp` with temperatures;
   7. glTF.
   CGNS and Exodus need HDF5 or netCDF in the pixi environment (`h5py`, `netCDF4`, or VTK's own Exodus writer). That is checked on all three platforms before those steps.

## Built: the Export form, point clouds and the faceted deformed shape (2026-10-25)

- **One module** (`post/exports.py`): `EXPORTS` lists the formats with what each carries; `export(project, store, run_id, format, path, …)` writes one and returns the files. Every export writes `<name>.txt` beside it: the study, run, solver, step, units, and for shapes the exaggeration and whether it is compensated.
- **Formats so far:**
  - `paraview`: every field of every step (`.pvd` + `.vtu`);
  - `point_cloud`: CSV of x, y, z and one listed result per node (a component, a magnitude, von Mises...; a field's every component when no single value is asked), on the whole model or one selection, SI or display units, each column's unit in its header;
  - `deformed_stl`: the deformed outer surface as binary STL in metres, quadratic faces split in four;
  - `deformed_step`: the same as STEP AP242 with each part a tessellated face (`TRIANGULATED_SURFACE_SET`), in millimetres. OCCT writes it once its STEP controller is initialised; its working unit is set to metres so it converts (Gmsh leaves it so in this process: scaling the points ourselves wrote them a thousand times too large).
  - `scale` exaggerates (said in the file name's .txt), `compensate` moves by −u.
- **GUI:** Results → Export… and Export… on each run: one form (results, format by name, step, then only what the format needs), then where to save (`window.ask_save_path`).
- **Twins:** `puzzle export PROJECT --study S OUT --format F [--step --quantity --selection --scale --compensate --units]`; MCP `export`, which writes inside the project folder only.
- **Checks** (`tests/integration/test_exports.py`): ParaView read back by VTK; the point cloud's tip value equals the stored deflection, and a selection's cloud has its nodes only; the STL's lowest point is the tip deflection (×10 when exaggerated, mirrored when compensated); the STEP declares millimetres and its coordinates span the bar's 200 mm with the tip moved down. Puzzle's own importer doesn't convert a tessellated STEP's units on reading, so the test reads the file's units and coordinates directly.
- **Next (order of ADR 0043):** Exodus II and UNV; EnSight Gold and CGNS for fluids; the morphed B-rep STEP; Nastran and Abaqus decks; glTF.

## Built: every format of the plan (2026-11-01)

- **One writer per format:** `post/exports/` is a package; `EXPORTS` names each format's
  writer (`module:function`), the options it takes (`step`, `quantity`, `selection`,
  `units`, `scale`, `compensate`; the Export form, CLI and MCP offer only those) and its
  physics. A writer takes a `Job` (project, run, mesh, results, chosen step, path,
  options, `extra` for its own) and returns its files and the sidecar's notes. 17
  formats now. `h5py` joined the environment for CGNS (HDF5 1.14 on every platform).
- **Exodus II, VTKHDF, UNV (step 3):**
  - `exodus`: written through SciPy's netCDF-3 writer (VTK's `vtkIOSSWriter` renumbers
    elements 1..n and writes no info records; `vtkExodusIIWriter` names no sets).
    Every step a time step (time, frequency in Hz, or load factor; step numbers when
    values repeat), nodal, element and global variables, one block per part, every
    selection a node set and face selections side sets by name, Puzzle's node and
    element numbers in the maps, SI said in the info records.
  - `vtkhdf`: every step in one HDF5 file (`vtkHDFWriter`); stored fields only, no sets.
  - `unv`: datasets 164, 2411, 2412 (I-DEAS node orders), 2467 (parts and selections as
    groups) and 2414 (I-DEAS tensor order; modes as "normal mode" with their number
    and frequency, so correlation tools read them). Global values aren't carried;
    modal mass and damping are 0 (not stored).
  - Checks (`tests/test_exports_formats_a.py`, `integration/test_exports_formats_a_calculix.py`):
    Exodus read raw and by VTK's Exodus and IOSS readers (coordinates and fields
    bit-identical, side sets drawn by VTK on the selections' faces corner for corner);
    VTKHDF by `vtkHDFReader` at every step; UNV parsed from its fixed-width records
    (coordinates to 17 digits, values to 6, CalculiX's four modes with their frequencies).
- **Fluids (step 4):**
  - `ensight`: EnSight Gold, C binary (float32): every step a time set, part 1 the
    volume and each face selection a part named after it.
  - `cgns`: CGNS 4.4 over HDF5 by h5py (the SIDS file mapping, no CGNS library): one
    unstructured zone, each face selection a FamilySpecified `BC_t` whose family holds
    the type from its fluid feature; names `Part_Selection` (CGNS allows no "/"), the
    exact name in a Descriptor; per step cell-centred and vertex solutions in
    `BaseIterativeData`/`ZoneIterativeData` (`FlowSolutionVertexPointers` is an
    addition VTK reads); SI by `DimensionalUnits`.
  - `tecplot`: ASCII FE zones per part and step, cell values via VARLOCATION, geometry
    shared across steps; quadratic meshes by their corners.
  - `wall_cloud`: wall pressure, wall shear stress, y+, and the heat transfer
    coefficient with its reference temperature at each wall's face centres. The CFD
    adapters store no wall fields, so they are computed from the wall cell: τ = μ u_t/d
    (the solvers' own one-cell gradient), a standard wall function in RANS log-layer
    cells (an estimate, not the solver's value), q = k (T_w − T_cell)/d. The reference
    temperature is `export(..., reference_temperature=)` from Python, else the inlet's.
  - Checks (`tests/test_exports_fluid.py`, `integration/test_exports_fluid_runs.py`):
    EnSight read back by VTK and the Code_Saturne reader, CGNS by `vtkCGNSReader`
    (patch names, values exact), Tecplot by a parser, signed volumes and tet10 order on
    every cell type. On real runs: laminar channel τ −0.45 % against 6μU/h (the run's
    own dp/dx error), heated channel h +0.16 % against Nu = 8.235, turbulent k-ε wall
    function τ −2.4 % (Code_Saturne) and +0.2 % (OpenFOAM) against the force balance.
- **The morphed B-rep and compensation (step 5, `morphed_step`):**
  - each solid part's placed CAD is converted to B-splines and its geometry moved in
    place (faces stay shared, nothing is sewn): every surface and edge curve refined
    to degree 3 with poles about the mesh spacing apart (at most 64 spans), each pole
    moved by u × scale (−u compensated) interpolated in the part's mesh (off the body,
    the nearest cell plus the gradient), then three corrections towards interpolating
    x + u at the Greville points; vertices moved by u; `SameParameter`, a validity
    check, `ShapeFix` only when needed;
  - the export compares every deformed surface node with the morphed face at the same
    parameters, and the volume change with the mesh's; outside 1 % of the largest
    displacement (`tolerance` in extra) it refuses (`export_failed`, the numbers, and
    the faceted `deformed_step` suggested); written as AP214 in mm, a named body per
    part, an assembly for several;
  - not followed (refused by the checks): kinks and jumps finer than the poles (contact,
    plastic hinges, two materials in one part), folding exaggerations; shells, beams,
    2D and faceted parts are refused outright;
  - checks (`tests/test_morph.py`, `integration/test_exports_morphed.py`): cantilever tip
    within 1e-4 of the stored deflection, deviation 3e-5 of it at any scale up to 500,
    volume change equal to the mesh's; heated plate with a hole: volume (1 + αΔT)³ to
    1e-4, edges and hole radius × (1 + αΔT); split-bar assembly with a body per part.
- **Decks, glTF and the Lorentz force (steps 6 and 7):**
  - `nastran`: `.bdf` bulk data, large-field (GRID in metres; CTETRA, CHEXA and CPENTA in
    both orders, CTRIA, CQUAD, CBAR; PSOLID, PSHELL or PBAR and a MAT1 per assignment;
    each selection a SET1, plus SET3 for element selections, with a table of numbers to
    names) and a `.pch` punch per step (displacements, eigenvalues as (2πf)² or the
    buckling factor, solid stresses in OES1 word order: **not checked against a
    Nastran**, and pyNastran has no punch reader). Loads and supports aren't written;
    2D models are refused.
  - `abaqus_inp`: the mesh, sets named as the CalculiX decks name them, materials and
    sections, and a step's nodal temperatures beside it as `*TEMPERATURE` data;
    `abaqus_displacement`: the same with a step's displacements as `*BOUNDARY` data (a
    submodel's boundary). Material tables aren't written, only room-temperature values.
  - `gltf`: binary glTF 2.0 written by hand: each part's surface coloured (linear RGB)
    by the legend's colour map, deformed by displacement × scale, the legend in
    `extras`, values in `_VALUE`; the range is the data's (no custom range yet).
  - `lorentz_cloud`: J × B (N/m³) per node where both are stored.
  - Checks (`tests/test_exports_formats_d.py`, `integration/test_exports_formats_d_runs.py`):
    CalculiX solves decks including the exported mesh (the cantilever's tip equal to
    the stored run's to the `.frd`'s six digits; exported tip displacements reproduce
    the bar's middle deflection; exported temperatures grow a free bar by α ΔT L to
    1e-6); pyNastran reads the bulk data with every volume positive and the total
    exact; VTK's glTF reader reads the same points and triangles, the extremes the
    colour map's ends; in the wire J × B points inward with μ0 J² r / 2 (FEniCSx median
    1.001, Elmer 1.04 at 0.25 mm: its direct solve fails at 0.15 mm, out of memory).
- **Probes** (found here): VTK's default probe tolerance extrapolated a neighbouring
  cell at nodes; `post/probes.sample` probes tightly first (ADR 0032's tools use it).
- **Not yet:** Nastran stresses checked in a Nastran; the wall cloud's single quantity
  and reference temperature in the Export form; a custom legend range in glTF; sweep
  points' materials in the decks; MED results.

## Consequences

- Results can go to the tools people already use for reports, test correlation and downstream analyses, without ParaView in between.
- A deformed shape a CAD tool can open closes the loop for clearance checks and manufacturing compensation.
- Many formats are many writers to maintain. The `EXPORTS` table and round-trip tests keep each honest, and formats with no tester here (OP2, Fluent's own) stay out.
- The morphed B-rep is approximate where the deformation is large or local (contact, plasticity). It must say so and fall back to the faceted shape rather than produce a wrong solid.

## Alternatives considered

- **meshio for every format:** one dependency covers many formats, but its CGNS and Exodus writers don't carry sets and steps the way the receivers need, and VTK already gives most of them. It may still be used for Nastran and Abaqus decks.
- **Only VTU, and let ParaView convert:** it leaves CAD, test correlation and other solvers' mapped loads out, which are the main reasons to export.
- **A deformed STEP rebuilt from the mesh by surface fitting:** better surfaces than morphing for large deformations, but fitting and sewing are fragile. It is kept for later, if morphing proves too limited.
- **OP2 for Nastran results:** the format most Nastran tools read, but it is binary, versioned and undocumented in places. Punch (ASCII) is enough to start.
