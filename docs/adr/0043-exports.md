# 0043. Export the deformed shape as CAD and results in neutral formats

- Status: Accepted (2026-10-25, the maintainer); steps 1 and 2 built (2026-10-25)
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
