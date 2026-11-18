# 0072. Keep one list of further development

- Status: Accepted (a living record: items leave it as they are built)
- Date: 2026-11-08

## Context

Earlier ADRs set many things aside: "not yet" lists, "later" phases, "out of scope for
now", alternatives that "may come later". They are scattered over 71 records, some were
built since under other ADRs, and some ADRs' own status lines have drifted. Nothing showed
what is still open in one place.

Moving development to a Windows machine (2026-11-08) added a few more: container runs on
Windows (FEniCSx, Elmer and Code_Aster now prefer their images there; `CONTAINER_FIRST` in
`jobs/runtime.py`) start a fresh container per solve, which costs about 0.55-0.6 s each
(measured: `docker run --rm` of every image) against 0.1 s for a `docker exec` into a
running one. CalculiX stays native because its image was about 1.4x slower on small models,
partly start-up and partly files crossing the mounted Windows folder. The maintainer's
concern is that start-up overhead; image size doesn't matter to them.

Every item below was checked against the later ADRs, CLAUDE.md and the code on
2026-11-08; items found built since were left out (about 60 of them, e.g. selection probes
(0042), I and channel sections (0055), the bolt table (0050), cancelling from the status
bar (0045), Netgen (0019), MMG (0066), project archives (0061)).

Checked again on 2026-11-13, after ADRs 0073-0082 were built: items built since are
marked **Built** or **Partly** with the date, and what those ADRs left open is copied in
(sections 3, 3a, 3b and 5). Work built since that no item named (frames, welds, symmetry,
weak springs, element types, modelled bolts, the tree's status, the solver log window,
PyNite in the installer) is recorded in its own ADR, not here.

## Decision

This record is the one list of further development. Each item names the ADR it came from;
"Partly" says what is left. Picking an item up means a new ADR, or a step of the ADR it came
from, and removing the line here in the same change. New deferrals are added here as well
as in their own ADR. The order inside each section is not a plan, except where marked;
the order of work is the Plan to finish below.

**No new ADRs after 0085 (maintainer, 2026-11-13)** until everything through 0085 is
built and tested and Puzzle could be run as semi-production software. ADR 0084 (remote
model providers for the agent) and ADR 0085 (OpenCourant, explicit dynamics) are the
last new records for now; one may still be written later if something is found to need
it. Picking an item up means a step of the ADR it came from, amended in place; a gap
found while building goes on this list, not into a new record. A decision that can't be
made without one is put to the maintainer first.

The order of work (maintainer, 2026-11-13): this record's Plan to finish, then ADR 0084
in full, then ADR 0085 planned and built, then, once all of it is stable, ADR 0075's
worked examples and ADR 0083's help and documentation to round the application out.
Once it is stable, and before 0075 and 0083 are called done, the whole application gets
a full review, adversarial, by a Fable 5.2 model (maintainer, 2026-11-13): its findings
fixed or answered before the freeze lifts. **A hold point comes before that review:** work stops when
the application is stable and the maintainer lays down the review's groundwork and goals;
the review starts only on their word.

### 1. Solver runtimes and containers

Maintainer's priority, first:

- **Warm containers to cut start-up overhead (new).** For a batch of solves (a sweep, an
  optimisation, a convergence or adaptivity series, a test run) keep one container per
  solver running and `docker exec` each solve into it, instead of `docker run --rm` per
  solve: about 0.5 s saved per solve, more for small models. The job runner (0067) is the
  natural owner: it already owns every solve and outlives Puzzle. Needs: a mount that covers
  every run folder of the batch (the project's `.puzzle/runs`), the thread variables per
  exec, cancel by killing the exec'd process, the container stopped when the batch ends or
  goes idle, and the FEniCSx form cache kept as now. Measure CalculiX again with it: if the
  overhead goes, CalculiX's image may beat native on Windows too.
  **Built (2026-11-10), `jobs/warm.py`:** inside `warm.batch(root)` (every sweep,
  optimisation, convergence and adaptivity run) a container solve is `docker exec`'d
  into one running container per image, `root` mounted at /batch, in its own folder,
  with its thread variables and the image's entrypoint in front; the container stops
  itself after 300 s untouched and is removed at the batch's end; a cancel kills the
  exec'd solve inside it by its pid (the job runner's cancel calls it too). Measured on
  Windows 11 (Docker Desktop, Code_Aster's image): the cantilever over four materials
  took 14.7 s warm against 15.3 s cold, the same answers to every digit: about 0.15 s a
  point, less than the 0.5 s guessed, since Code_Aster's own start-up inside the
  container dominates. `tests/integration/test_warm_containers.py`.
- Then measure where the rest of CalculiX's container penalty goes (files over the
  Windows mount vs start-up), with warm containers now in place; copying a case into the
  container's own file system may help.

Other items:

- One shared base for the conda-based images (FEniCSx, Code_Aster, CalculiX) and a
  slimmer FEniCSx image (5.1 GB, mostly its compiler and PETSc) (new; low priority).
- Native Code_Aster on Windows shows no progress until it ends: its `run_aster.bat`
  sends the run to `fort.6` with `>>` where Linux's uses `tee`, so the Solution panel
  stays empty for minutes and a long solve looks hung (found 2026-11-08 on an
  882,309-equation model). The container streams; for the native fallback, follow
  `fort.6` in run_aster's temporary folder, or run Code_Aster's Python directly (new).
  **Built (2026-11-10):** natively on Windows, `CodeAsterProgress.poll` follows the
  fort.6 of the newest `%TEMP%\run_aster__*` folder made since its job started (claimed,
  so two jobs in one Puzzle don't share one) and ignores stdout while it does, so no
  line counts twice. A plastic bar natively: its increments and iterations reach
  progress.jsonl during the solve (2.1-2.8 s into a 3.5 s solve), not at its end.
- Native Windows FEniCSx without Docker: use `pypardiso` (MKL PARDISO, on conda-forge
  win-64) in `study.py`'s SciPy path instead of SuperLU (new; the fallback when Docker is off).
  **Built (2026-11-10):** `study.py`'s `_spsolve` and `_Factor` use pypardiso where it is
  installed (the fenicsx environment on win-64), SuperLU otherwise and for complex
  systems; `PUZZLE_FENICSX_LU=superlu` forces SuperLU; the solve prints its linear
  solver. NAFEMS LE1 natively on Windows 11: 9.8 s against 30.8 s on SuperLU, the same
  answer. Left: FEniCSx still runs in its image first on Windows (`CONTAINER_FIRST`);
  whether native now beats it needs the 3D magnetics cases timed both ways.
- Publish the images (ghcr.io); none are published yet (0007, 0013).
- Record the solver version and image digest with each run; images write
  `puzzle-solver-version` but the store keeps neither (0007).
  **Built (2026-11-10):** `jobs/versions.py` names the solver each run used (a container's image id and the version its image writes; a native run's package version from conda-meta; a PARDISO build as built here; the machine's source image), kept in the runs table (`solver_version`, `image_digest`, merged into older stores) and shown in the run's Properties and `puzzle runs`.
- A Code_Aster image for arm64 (needs a source build) (0013).
- A CalculiX image with PARDISO (0051); PARDISO or PaStiX on Apple silicon (MKL has no
  arm64 build) (0051). **Built on Windows (2026-11-10):** `solver_builds` compiles it
  with conda-forge's MinGW gcc/gfortran and `mkl_rt` (ADR 0068 step 4).
- The job runner on Windows: it runs solves in-process when the OS isn't POSIX
  (`service.py`) (0067).
- Progress parsed where the solver runs, so the whole log needn't cross (0067).
- LSF: a fake scheduler and tests (0067). Partly: GPUs passed to PSI/J but not limited in
  the runner's own queue (0067). Cancel and dismiss in the Jobs panel as history entries (0067).
- Code_Aster's MPI-build check on remote hosts (`mpi_build()` looks at the local runtime
  only) (0021). A test against a real Slurm (0021). Cloud batch, its own ADR (0021). Site
  modules on hosts (0021).
- **Solve helpers on other computers (maintainer, 2026-11-08).** A small package installed
  on other PCs or Macs on the network, so the main Puzzle can hand them solves and run
  several at once: not an HPC cluster, just spare machines. It could be the job runner
  (0067) with the solver environments and no GUI, or another full Puzzle install that
  accepts work, found and reached like a remote host (0021), with a queue spanning them.
  Needs: discovery or a simple pairing step, the transport (SSH as today, or the runner
  listening on the network with authentication), and moving cases and results efficiently.
- Partly: ranks for OpenFOAM (`decomposePar`), Code_Saturne and FEniCSx; threads, Code_Aster
  MPI and Elmer magnetics MPI are done (0021).
- GPU: FEniCSx with a CUDA PETSc as a preset, benchmarked on a large LE1; CalculiX with GPU
  PaStiX; recheck Code_Saturne offload, OpenFOAM's GPU add-ons, cuDSS (0052).
- A remote run cancelled before its driver has written `puzzle-run.pgid` finds nothing
  there to stop, and the run goes on (0021; seen on CI, 2026-11-18, where the test now
  cancels once the solver has started). The two beam-bolt thread benchmarks take 24 and
  49 minutes on GitHub's Linux machine against about a minute with PARDISO here: marked
  `heavy`; why is not known (0076).

### 2. Installers and packaging (0068 steps 3-5 and others)

- Puzzle's own Linux machine (WSL2 import on Windows, Lima on macOS) with the non-native
  solvers, through the runner, started on demand and stopped when idle; a one-prompt WSL
  enablement on Windows (0068, 0013).
  **Begun (2026-11-10):** Windows' WSL 2 machine and its runtime (ADR 0068 step 5:
  `jobs/machine.py`, `puzzle machine`; `test_machine_solve.py` passed 2026-11-11);
  2026-11-13: staging (opt-in), WSL turned on from Settings with one prompt, on demand
  and idle by WSL itself. Left: the root file systems fetched from the registry on first
  use without Docker (the maintainer, 2026-11-14: fetched, not shipped; phase 5) and
  offering WSL itself, and Lima on macOS (phase 6: a Mac).
- CalculiX PARDISO built on the user's machine after install (0068 step 4, 0051).
  **Built (2026-11-10)** (ADR 0068 step 4).
- A Solvers page in Settings with Repair (0068). Side-by-side releases, a launcher, a
  release-feed check (0068). Licences in `licenses/`, Help > Licences, the GPL source offer (0068).
  **Built (2026-11-10):** the Solvers page and Repair (ADR 0068); side-by-side releases and the launcher still to do.
- Partly: signed and notarized installers; the workflow is in place, certificates pending (0068).
- The same solver environments on remote hosts via pixi (0068). A web installer, AppImage
  (0068). Faster installs (13 min on an Intel Mac) (0068).
- Native ports: Code_Aster on macOS; Elmer, Code_Saturne and OpenFOAM on macOS and Windows
  (0068). Partly: elmerfem on conda-forge (recipe builds locally on macOS, not submitted;
  Windows and MPI variants to follow) (0013, 0068).
- Open questions: Lima inside a notarized app (fallbacks Podman, Apple's `container`), the
  VM's idle time, the order of native ports (0068).

### 3. Solvers and physics

- Left by ADR 0090 package 8: outlets at different pressures on Code_Saturne; the direct
  analysis method under ASD (the 1.6 factor); `explicit.estimate`'s added mass (low by a
  third); an un-ramped displacement in an explicit step (refused, could be ramped).
- Refused by ADR 0090 package 7 until built: a free modal study on Code_Aster (a shift
  scaled to the model, or rigid modes named); planar heat across parts of different
  thickness on Elmer and Code_Aster (properties and fluxes scaled per part).
- Found by ADR 0090's written-or-refused matrix, refused until built: thermal expansion
  of beam bolts, pretension sections, threaded contacts and line welds under a temperature
  load (0037, 0076, 0078); couplings (`thermal_expansion`, `joule_heating`,
  `non_isothermal_flow`) as one study outside CalculiX, and Code_Saturne's mass-flow inlet
  (0009, 0016); material directions on CalculiX's 2D regions, shells and beams (0049);
  magnetics on hexahedra and quadrilaterals (0047); custom PDEs in modal and harmonic
  steps; OpenCourant's frictional contact, rigid-wall friction and mass scaling, written
  but with no benchmark (0085).
- Static current conduction (StatCurrentSolve), circuits (CircuitsAndDynamics), waves
  (VectorHelmholtz), rigid motion in magnetodynamics on Elmer (0010); moving parts and
  nonlinear B-H in time-harmonic analysis (0047).
- Elmer's slide, friction and stick contact behind an opt-in: the `experimental` flag exists,
  nothing writes them (0010).
- Magnetics benchmarks never run: TEAM 7 at 200 Hz, a step current into a half-space
  (erfc), a coil switched on (L/R rise) (0047). BDF2 for Elmer magnetic transients (0047).
  R and L from transient results (0047).
- OpenFOAM: convection walls, transient runs, mass-flow inlets; then multiphase,
  compressible, dynamic meshes (0016).
- FreeFEM, GetDP, Palace (0012). MYSTRAN: the Nastran-export test harness, packaging,
  adapter or test tool, licence and OP2 (0060, 0012). Nastran deck import (0012).
  Fluid-structure coupling Code_Saturne-Code_Aster, and two-way coupling as one study
  (preCICE) (0012, 0009).
- Dynamics: Code_Aster's modal method (0041); modal superposition on Elmer and FEniCSx
  (0041); HHT on Elmer (0041); base excitation (0041); stresses in harmonic steps (0041);
  the NAFEMS forced-vibration benchmarks (0041); frequency points clustered at the modes,
  damping per mode and per material, the real part at a chosen phase (0041). Partly:
  frequency-dependent loads (only an imported pressure with a frequency axis) (0041, 0044).
  Explicit dynamics: ADR 0085 (OpenCourant; built 2026-11-14/15: solids, shells and
  beams, plastic and Johnson–Cook materials, failure and erosion, contacts and
  self-contact, rigid walls, native, MPI, Apptainer; left: beam forces as results, Help
  with ADR 0083). **Built
  (2026-11-12):** large-deflection
  transients of solids on CalculiX and Code_Aster, and of shells and beams on CalculiX
  (ADR 0082).
- Buckling: the critical load in the label for a single force; a pinned-column benchmark
  (the hinge is now possible); nonlinear buckling (RIKS, PILOTAGE); a mode shape as an
  imperfection; beam and shell buckling benchmarked; buckling on FEniCSx and Elmer (0028).
  **Built (2026-11-12):** buckling after a large-deflection preload, solids, on CalculiX
  and Code_Aster (ADR 0082).
- Loads and constraints: amplitudes on temperatures, displacements, body loads, and in
  static steps for load-deflection curves (0037); rigid remote force, bolt pretension and
  compression-only supports on Elmer and FEniCSx (0037). Partly: pretension locked across
  steps on one-job-per-step solvers, **built (2026-11-11)** on Code_Aster for studies of
  static steps (the preload step, steps chained into one STAT_NON_LINE, ADR 0037
  amended); left: studies mixing modal, transient or thermal steps with them, and Elmer
  and FEniCSx (0037). Partly: partial local-axes displacements beyond CalculiX (Code_Aster
  Cartesian ones by LIAISON_OBLIQUE, 2026-11-11, ADR 0081); left: Elmer and FEniCSx, and
  partial cylindrical ones anywhere (0037).
  Partly: Elastic support on Elmer only on axis-aligned faces (0037).
- Materials: hyperelastic models written by any solver; creep as a typed model; orthotropic
  and anisotropic models on Elmer and FEniCSx, and in 2D on Code_Aster; Code_Aster's
  orthotropic plasticity and anisotropic conductivity with tables (0020, 0049). (Large-
  strain plasticity was built 2026-11-12, ADR 0082.)
- Coordinate systems: spherical displacements and forces as functions of position;
  spherical material axes; the sphere checks (0049).
- Beams, shells, fasteners: a fastener pinball radius; thermal expansion of a beam bolt;
  beams joined to solids or shells where nodes aren't shared; section stresses on beams;
  shells from a solid's mid-surface, joined where nodes aren't shared, section results,
  normals across several faces, the pinched-cylinder benchmark; curved panels about other
  axes (partly); a spring connector with given stiffnesses; fasteners, beams and shells on
  Elmer and FEniCSx (0027). Fitted middle holes in a bolt stack (0050; stacks of several
  layers were built 2026-11-09). Spring beams: rotary inertia, centrifugal loads, a body
  load's share within a segment (0055). (Quadrilateral and thick shells were built
  2026-11-12, ADR 0082.)
- Contact: Hertz-type non-conforming curved contact on Code_Aster (refused now) (0056).
  Weak springs released once a contact closes (CalculiX `*MODEL CHANGE`, Code_Aster a
  windowed multiplier), when a benchmark shows the residual matters (0080).
- Fatigue: critical-plane multiaxial fatigue; rainflow counting (0026).
- Symmetry: cyclic modes by nodal diameter (CalculiX `*CYCLIC SYMMETRY MODEL`); cyclic
  heat on Code_Aster; cyclic and periodic symmetry on Elmer (`Periodic BC`), the CFD
  solvers and FEniCSx (`dolfinx_mpc`) (0081).
- Element types: Code_Aster's shells and beams in transient large deflection stay
  refused, measured (0082). (Section forces of POU_D_T_GD: built 2026-11-13.)
- Found while checking: Elmer's capabilities and docstring list `electric_currents`
  (StatCurrentSolve) and `electromagnetic_waves` (VectorHelmholtz), but `elmer/sif.py`
  writes neither (see "Found while collecting") (0010).
- Threads: see 3a; welds and frames: see 3b.

### 3a. Threads (0076)

**Built (2026-11-09 to 2026-11-10)** as ADR 0076, which now holds the research that was
here (Ansys, COMSOL, Abaqus, Sopwith and Yamamoto, VDI 2230) and its design: any thread
form on smooth cylinders, 2D axisymmetric and 3D on CalculiX and Code_Aster, 2D on
FEniCSx, threaded beam-bolt ends, and the modelled bolts of ADR 0077. Left:

- threads on Elmer (its SIF has no node-pair springs or directional constraints);
  FEniCSx in 3D, until benchmarked;
- friction on the flanks (refused at first).

### 3b. Welds and frames (0078, 0079)

- Welds: a weld's own fatigue code with AWS D1.2,
  D1.6 and D1.9's curves (AWS D1.1 and AISC Appendix 3 share the categories built);
  weld fatigue through the fatigue tool's duty (0026); light welds on Elmer; welds in 2D
  (refused) (0078).
- Member checks: AISC 360 channels, tees and angles (E4-3/E4-4, E5, F9, F10),
  noncompact webs (F4, F5), stiffened webs and tension field action (G2.2), B1
  amplification (Appendix 8), torsion of I shapes (Design Guide 9); τb below 1 when
  αPr/Py > 0.5; notional loads only where C2.2b(4) needs them; welded aluminium members
  (the ADM's heat-affected zones), AISC 370's continuous strength method, ADM bridge
  factors, torsion of stainless and aluminium (0079).
- Frame analysis: envelopes over combinations (combinations, spectra and ± E on
  CalculiX and Code_Aster: built 2026-11-13, `puzzle.combine`, `puzzle.spectra`); seismic accidental torsion (12.8.4.2), the 100 %/30 %
  combination (12.5.3), E_v and ρ, the stability coefficient θ (12.8.7), drifts divided by
  ρ (12.12.1.1) (0079).
- Frame editing: members drawn node to node in the 3D view, members off the grid lines,
  a grid frame edited as a grid after it is made (0079).

### 4. 2D models (0048, 0044)

- Offset walls of arcs and splines. The new 2D shapes solved on Code_Aster and Elmer.
- Code_Aster's live 2D reaction trackers per radian; a node shared by plane-stress parts of
  different thickness; the revolved view's camera Y-up; derived results and Compare drawn
  revolved.
- 2D magnetic transients on FEniCSx, transient 2D CFD, swirl.
- CalculiX oblique edge forces in steps over modes; FEniCSx 2D displacements from fields and
  loads in local axes; FEniCSx temperature loads in 2D heat; Elmer local axes in 2D; Elmer
  plane strain with materials of different E·α.
- Still refused in 2D everywhere: beams and fasteners, remote loads, pretension,
  non-conformal contacts, welds (threaded contacts in axisymmetric models are the
  exception, ADR 0076).
- Imported fields in 2D: vector loads on Elmer and FEniCSx, conservative mapping, pressure
  fields on Code_Aster edges, CalculiX flux fields as nodal flows, weights from VTU cells,
  three components chosen at once in the form (0044).

### 5. Geometry and meshing

- **A live Fusion 360 link (maintainer, 2026-11-08; Windows first).** A Fusion add-in that
  sends the open design to Puzzle, starting Puzzle and a new project if it isn't running,
  and keeps it linked: geometry changed in Fusion updates the running Puzzle, pushed from
  Fusion or pulled from Puzzle. The geometry arrives as STEP (or BREP) into a geometry
  object whose selections survive the update by Puzzle's robust naming (G3); the add-in
  talks to Puzzle through its command layer (MCP or a local socket). Fusion's API runs on
  Windows and macOS, so macOS can follow.
- FreeCAD as a plugin or `.FCStd` importer (0004, 0014). Mid-surface extraction (0014).
  Direct face moves and pulls (0014). Sketches on any plane, not only xy/yz/xz (0017).
- Partly: a faceted part must be its component's only part, unplaced, without heal or
  remove (0014).
- Swept meshes; hex and tet together in one recipe (0019): a recipe with hexahedra
  meshes every body that can take them (blocks, revolutions) and refuses the rest. A
  hex body conformal with a tet one wants pyramids at the shared face, which CalculiX
  hasn't (Code_Aster has PYRAM5/13), so the way is a hex body meshed apart and tied, as
  the modelled bolts are (ADR 0077): a tie made automatically when a blocked body shares
  a face. **Built (2026-11-14):** a hexahedral recipe meshes the bodies that can't take
  hexahedra with tetrahedra (`generate._kinds` before any join), joins each kind apart
  (`_share_faces`, `_fragment_conformal`), makes only the hexahedral ones transfinite,
  and names the faces where the two touch as `<part>/(mesh tie k)` (the tetrahedra's)
  and `(mesh tie k target)` (`_mixed_ties`, after the blocks are cut), tied bonded and
  nonconformal at the solve (`with_mesh_ties`, from the groups' names, so a stored mesh
  ties too). Benchmark `tests/integration/test_hex_tet_mixed.py`: a block in hex20 bonded
  to a block with a blind hole in tet10, pulled: the reaction P exactly, the end's
  stretch −0.11 % from the whole bar in tetrahedra on CalculiX and Code_Aster alike.
  **Built (2026-11-14), sweeps:** a body of revolution with cones, tori or spheres
  (`blocks.meridian`: every face plane square to the axis or a surface whose u runs
  round it, one axis) has its meridian profile cut out (`blocks.revolve`), a strip along
  the axis one element across, meshed in quadrilaterals and revolved in four quarter
  turns with layers, as bolts are (hexahedra, prisms on the axis); a prism that isn't a
  block (`blocks.extrusion`: two flat faces that are translates of each other, every
  other face parallel to the direction and spanning it; a plate with holes, an
  extruded L) has its cross-section extruded in layers of the recipe's size
  (`blocks.sweep`). Both replace the body in Gmsh's model only and re-find its names on
  a copy (`_lies_on`), as the blocks do; their volumes are swept by their layers, not
  made transfinite, and take 20-node hexahedra and 15-node prisms. Benchmarks: a shaft
  tapered by a cone and one with a filleted shoulder, F / A in each segment within
  0.02 % (`test_hex_revolution.py`); a holed plate swept into hex20 alone, its stretch
  +0.18 % from tetrahedra (`test_hex_tet_mixed.py`). Partly (2026-11-10, ADR
  0077): modelled bolts, nuts and washers are swept by revolving and sit as hexahedra in a
  tet recipe, joined by nonconformal ties. **Built (2026-11-14), bends and cuts:** a
  body swept round an arc (`blocks.bend`: two flat ends holding one axis, one the
  other turned about it, every other face a surface of revolution about it or a plane
  square to it; an elbow, a bent tube or bar) has its end face revolved through the
  angle in layers (`blocks.bend_sweep`); a body that is blocks once cut
  (`blocks.decompose`: cut on the planes of its re-entrant flat faces, those with
  corners of the body on both sides; a bar stepped on four sides, a block with a
  pocket, a cross) is meshed as its blocks together, transfinite and conformal, when
  every piece is a block (tried on a copy first, `decomposes`). Swept and revolved
  profiles get an even count on every edge and Gmsh's quad algorithm, so no triangle
  is left to sweep into prisms. Benchmarks (`test_hex_bends_and_cuts.py`): a quarter
  bend of tube pushed out of its plane against Castigliano's curved cantilever with
  shear, +0.25 % at R = 20 r on CalculiX and Code_Aster alike (+1.08 % at R = 10 r:
  the formula leaves out the section's curvature, (r / R)²), and the stepped square
  bar's F / A in each segment within 0.05 %. Left by choice: sweeps
  along a free curve (a spline path has no axis to revolve about) and bodies whose
  cut pieces are prisms or revolutions rather than blocks; both get tetrahedra, saying
  why. **Built (2026-11-11):** second-order hexahedra, 20 and 27 nodes (ADR 0082).
- **Hex meshes of bodies of revolution (maintainer, 2026-11-08).** The use is cylindrical
  solids: idealised bolts (shank, head, nut), pins, shafts, tubes, bushings and rollers.
  - **Today:** Gmsh hexes only block-like bodies (six four-sided faces,
    `setTransfiniteAutomatic`), so a cylinder falls back to tetrahedra. 2D quadrilaterals
    are all-quad as of 2026-11-08 (`MeshRecipe.quadrilaterals`).
  - **Proposed, blocks cut at mesh time** (Ansys MultiZone's way), in the Gmsh model only,
    never in the geometry:
    - A solid cylinder gets a butterfly (O-grid): a square core of about 0.5 r and four
      planes from its corners out to the rim. Every block is then six-sided and four-edged,
      the hexes stay good on the axis, and there are no wedges there.
    - A tube needs only the four sector planes.
    - A stepped body (a bolt's head on its shank, a stepped shaft) is also cut by a coaxial
      cylinder at each radius present and a plane at each step. The core is sized from the
      smallest radius, so the blocks of the segments match.
    - `setTransfiniteAutomatic` needs a corner angle above 135° there (the core's
      corners).
    - The faces the cuts split are re-found by their pieces, as joined bodies' faces
      already are (`generate._pieces`).
  - **Which bodies:** all faces planes normal to one axis, coaxial cylinders, or cones
    (chamfers). Fillets (tori), threads and off-axis features fall back to tetrahedra,
    with the reason in the mesh bench, as today's block test does (`_method`: "revolvable
    about z").
  - **Second choice, sweep by revolving:** the meridian section meshed with quadrilaterals
    and turned about the axis in layers by Gmsh's extrude. Simpler, but it leaves wedges
    on the axis and needs a geometry Gmsh extrudes itself. Kept for tubes and rings
    (`hex_method: blocks | revolve`).
  - **With it:** second-order hexahedra (C3D20R, HEXA20, and Elmer and FEniCSx's 20-node
    bricks), since bolts and shafts in bending want them, and hex with tet in one recipe,
    so a bracket's tets meet a bolt's hexes (conformal where faces share, tied otherwise).
  - **Checks:**
    - a cylinder in tension, exact;
    - Lamé's thick tube under pressure within 0.5 % on 20-node hexes;
    - a cantilevered shaft in bending against beam theory;
    - element counts and the worst Jacobian against a tet mesh of the same size.
  - **Where it's used:** ADR 0076 step 3 was to mesh its 3D threaded benchmarks this
    way; they passed on tetrahedra instead.
  - **Partly built (2026-11-10, ADR 0077):** the second choice, for `bolt`, `nut` and
    `washer` steps only (`mesh/revolved.py`: four quarter turns in layers, hex20 and
    wedge15 on the axis).
  - **Built (2026-11-13), the first choice:** `mesh/blocks.py` and
    `generate._block_revolutions`. A hexahedral recipe's body that isn't block-like is
    cut at mesh time when its faces are planes square to one axis and coaxial
    cylinders:
    - a butterfly when solid on the axis (core 0.45 r_min, four planes, one on OCC's
      seam), four planes for a tube;
    - coaxial cylinders and planes at the steps;
    - blocks transfinite at 2.6 rad corners;
    - names re-found on a copy of the body (`_lies_on`).

    The mesh bench says "a body of revolution: cut into blocks when meshed". Checks
    (`tests/integration/test_hex_revolution.py`, hex20):
    - a stepped shaft's σzz over a cut is F/A to 0.02 % (CalculiX) and exact (Code_Aster);
    - Lamé's tube's bore u_r is −0.005 %;
    - every element a hex20; worst scaled Jacobian 0.66 solid, 0.89 tube.

    Found and fixed on the way:
    - `common.surface_area` measured 8-node quadrilaterals from their corners, so a
      force spread as F/A on curved hex faces came out 0.84 % high on Code_Aster;
    - an elastic support with no stiffness (or values not named kn, kt) made CalculiX
      hold a node set it never wrote; it is refused now.

    Left: cones (chamfers) and tori (fillets), which fall back to tetrahedra with the
    reason in the warnings; bodies sharing a face with another (fragmented or
    joined).
- Selections: the pick bar's "by size" filter; saving a "same as" pick as a rule (partly)
  (0034). Parts sharing faces after fragmenting reported as conformal in their own right (0039).
- Measure: radius of curvature at a point; G1/G2 tangency breaks and planarity (partly);
  sweep with twist and as a shell, loft as a shell; a SubD cage from 3DM (0065).
- Mesh adaptivity: the planned benchmarks (plate with a hole adapted to 2 %, an L-corner
  as a singularity); error estimates beyond tets; MMG with prism layers (partly) (0066).

### 6. Imported fields and exports

- Fields varying in time beyond CalculiX pressure and heat flux: films, body temperatures,
  vectors in time and with frequency, frequency fields on Elmer, other values with
  frequency (0044). Readers: Fortran-binary and structured EnSight, binary OpenFOAM, Fluent
  transient profiles (0044). Partly: a field from the project's own run (through a table
  today) (0044). Code_Aster initial stress on beams and shells; initial stress on Elmer and
  FEniCSx (0044). The field-alignment helpers over MCP (0044).
- Exports: Nastran stresses checked in a Nastran (waits on 0060); the wall cloud's
  quantity and reference temperature in the form; a legend range in glTF; a sweep point's
  materials in Nastran/Abaqus decks; MED results; XDMF+HDF5; 3MF for the deformed shape; a
  deformed STEP by surface fitting; morphed STEP for shells, beams, 2D, faceted; UNV modal
  mass and damping (0043).

### 7. Results and post-processing

- Shoot-out reports with the difference field and a pass/fail, and `run_shootout` reachable
  from the GUI, CLI and MCP (0003, 0011).
- Derived results cached on disk for large models (0025).
- Load symbols: value labels on tinted faces; larger cones on slender models; coil current
  along its path (partly) (0035).
- Several section cuts at once; MP4 animations; QtCharts (minor) (0038).
- Result tools: picking a path's ends (partly) and a moment point in the view; one tool
  over several selections; beam and spring probes (partly); reaction tools on Elmer and
  FEniCSx; the pick bar's read-out from the exact geometry (unconfirmed) (0042). Result tools
  with their own axes, and `axes` on MCP/CLI `result_stats` and probes; linearization's hoop
  from a cylindrical system (0049).
- Plane cuts and section forces limited to some bodies; section forces from element nodal
  forces on coarse meshes (0046).
- ParaView: `puzzle paraview`; Open in ParaView from a run's right-click; level 2,
  ParaView filters as Puzzle objects; client-server mode for large results (0070).
  **Built (2026-11-10)** (ADR 0070), but for client-server mode.

### 8. Schematic and operators

- Wires routed around nodes (0058).
- (The script sandbox on Linux and Windows: built 2026-11-13, bubblewrap and an
  AppContainer, ADR 0062.) Linux without bwrap installed still runs scripts unsandboxed:
  ship bwrap with the Linux installer (0062).
- Optimisation: NSGA-II; a generation's points in parallel; points as progress events with a
  live objective plot; a response surface over two variables; TPE for many choices (0063).
- Dataframes: reroute dots (A14) and Freeze (A15) (0064, 0064A); a 2D-slider pad on its
  node; filter by clicking a Table cell; a File operator's files in the content hash;
  running the upstream study when a study reads another's run table; recent operators
  across sessions; contact and bolt tables tested on real runs; the Section table's
  triangles (0064).
- Schematic builder: field operators; ParaView operators (with 0070 level 2); partly, user
  operators as one block node with exposed ports and sharing; partly, model building from
  the schematic (numbers wired into a feature's value, a study dragged out of a component);
  partly, live nodes ("evaluate up to here", a Report node); Plot of any number of series
  (four now) (0071).
  **Partly built (2026-11-10, ADR 0071 step 5):** field operators, ParaView's filters,
  user operators as folded blocks with their own sockets and shared as files, numbers
  wired into a feature's value, the Report node. The rest built 2026-11-13: a study
  dragged out of a component, "evaluate up to here", Plot of any number of curves.

### 9. Agent and MCP

- MCP over the network with authentication; per-change acceptance in the GUI (partly)
  (0015). Streaming replies: built with ADR 0084 (2026-11-14).
- Remote model providers beyond Anthropic and one OpenAI-compatible endpoint: ADR 0084
  (OpenAI, Google, Hugging Face, Thinking Machines, Meta, NVIDIA, any compatible
  endpoint). Left out of it: Vertex AI's and Bedrock's own authentication (OAuth, SigV4)
  (0084).
- VRAM on discrete GPUs; English summaries of more of Code_Aster's French manual (partly);
  fine-tuning only if retrieval plateaus; routing by task family (0022).
- A `point` argument on `result_*` and point labels in `runs`; a time, frequency or mode
  within a step and `axes` on `result_*`; applying a settings preset by command
  (`apply_preset` is GUI-only); tools for a geometry's entity names and for edge seeds and
  meshing methods (0057). (Fixed 2026-11-13: buckling factors in `result_summary`, every
  field in `capabilities`, a sweep's points in the budget, a body with no material
  before a run.)
- Translating more of Code_Aster's advice (`progress.translate`) (0054).

### 10. GUI and look

- A web front end (trame) if needed (0003). Partly: add menus filtered by the study's solver
  (0006).
- Settings: links to each solver's manual; results from a hand-edited case, marked not
  reproducible (0023). A per-quantity unit system for a project (0030).
- The material builder: CSV/XLSX import, export to a file, hardening curves per
  temperature (partly), material axes drawn on assignments (partly) (0020).
- The step dialog, tool panel and material builder on `FormPanel` (0036).
- Advisor text in tooltips and form descriptions; a guided tour once help exists (0040).
- The help and documentation system with worked examples, ADR 0083 (it supersedes
  0075). Built 2026-11-15: steps 1-3 (the Help window, F1, the generated reference,
  guides, MCP `help`), step 4 (nineteen theory notes), the verification pages and step 5
  (the static site and PDF); left: the tutorials (on `main`), where the site is
  published.
- Found writing the guides (2026-11-15), features an agent reaches but the GUI doesn't
  (each a form or menu item to add): making a weld (`weld_add`), a VDI 2230 check
  (`bolt_vdi2230`), weld fatigue (`weld_fatigue`), a parameter sweep step, a frame from a
  DXF (`frame_geometry`), a mesh study, a mesh shoot-out, selection rules (box, near,
  normal), and who may connect over MCP (`puzzle autonomy --mcp` only). Geometry → New
  part… starts from a sketch and the Geometry heading's New part… from a box: one
  behaviour. The maintainer (2026-11-15): agent-only is acceptable for these, but each
  needs a visible way in: a menu item that opens the agent window with the request
  preloaded, so the conversation starts there. Users offline without an assistant are
  the open case: a form for each in time, or the help page naming the MCP tool and its
  arguments meanwhile.
  **Planned (maintainer, 2026-11-15): a form for each, so nothing needs the agent.**
  Each form asks what the MCP tool asks, in one `window.ask_form` or `FormPanel` (no
  chain of popups), calls the same function the tool calls (so the two can't drift),
  submits one batch, and has its help key. Where the agent is set up, the form's
  **Ask the agent…** button opens the agent window preloaded with the form's values, the
  maintainer's menu item and the form being one way in. A test drives each form with
  `tests/forms.py` `fill` and compares its batch with the MCP tool's on the same
  arguments.
  1. **Add weld…** (Model → Fasteners, a component's Fasteners folder, the Welds panel):
     edges picked in the view with the Edges filter (or an edge selection), part, type
     (fillet, groove CJP/PJP), leg and second leg, pattern (continuous, intermittent with
     segment and pitch), name; previewed through `weld_generator.plan` (the seat face it
     found, each station) before OK; `weld_generator.commands`.
  2. **VDI 2230 check…** (a bolt type's or bolt's menu, the bolt table, Model →
     Fasteners): size, grade, head, tapped or nut, clamp length, shank, plate and nut
     moduli, friction (thread, head), interfaces, Rz, n, axial load, tightening factor;
     filled from the bolt and its joint where one is selected; `vdi2230.check` shown as a
     table of the steps' results with the governing check marked, saved as text or CSV.
     No project change, so no batch.
  3. **Weld fatigue…** (a weld's row in the Welds panel, a run's menu): study, weld,
     method (AISC category A-F and cycles; ASME VIII-2 with thickness, basis, material,
     side), steps; the per-station table and plot from `weld_fields.fatigue` or
     `structural_stress`, beside the Welds panel's strength results.
  4. **Add sweep step…** (a study's Steps menu): parameters from the project's
     (pre-solve operators by name) with values as a list, a range (start, end, count) or
     a series; material at a location from `sweep.material_table`; the point count
     shown live; the existing sweep step's edit reuses the form.
  5. **New frame from a DXF…** (Geometry, beside New frame from a grid…): the file,
     layer (from the file's layers), scale (from its units, editable), a preview of
     members and groups in `gui/frame_editor.py`'s view; `frames.geometry` → one Create.
  6. **New mesh study…** (Model → Studies, the Convergence panel): the study, levels,
     ratio, target, quantities (operators and result tools); `MeshStudy` created, Run
     from the Convergence panel as today's Check mesh convergence… does.
  7. **Mesh shoot-out…** (Mesh menu, a study's menu): study, recipes or meshers to
     compare, quantities, tolerance; runs in `MainWindow.background` and shows the
     comparison table as the solver shoot-out does.
  8. **Selection by rule…** (a geometry's Selections menu, the pick bar): rule kind
     (box, near a point, normal within an angle, area range, union, intersection,
     difference of selections), its values with a Pick for points and faces, the
     members shown highlighted as values change (`naming.resolve`); one Create.
  9. **Who may connect** (Settings > Agent): the MCP autonomy level and its double
     sign-off for act, as `puzzle autonomy --mcp`, through `Session` like the GUI's own
     autonomy control.
  10. **New part…** made one behaviour: both menus open one chooser (sketch, box,
      cylinder, import), the sketch first.
  **Built (2026-11-15):** all ten, each in the menu bar and the context menus named,
  with a help key (`form:<name>`, a guide's section) and **Ask the agent…** where an
  agent is set up (`gui/agent_prefill.py`: the request typed into the agent panel, not
  sent); `tests/test_gui_tool_forms.py` and `tests/test_gui_mesh_and_selection_forms.py`
  compare each form's batch or table with its tool's. Modules: `gui/weld_forms.py`,
  `gui/vdi2230_form.py` (read only, Save as CSV/text), `gui/sweep_form.py`
  (`sweep.with_sweep_step`), `frame_editor.new_frame_from_dxf`,
  `convergence_panel.new_mesh_study` (`convergence.mesh_study_command`),
  `gui/mesh_shootout.py` (`shootout.mesh_shootout_report`, which MCP `mesh_shootout`
  now calls), `gui/selection_rule.py`, Settings > Agent's This project box
  (`settings_dialog.set_autonomy`, the same SetAutonomy as `puzzle autonomy --mcp`),
  `menus.new_part`. As built, short of the plan: the shoot-out compares Gmsh with
  Netgen only (what `run_mesh_shootout` does) in a Messages tab; a rule's point and
  direction start from what was picked before the form opened (a modal form can't hand
  off to the view); the sweep form leaves materials to the study's Materials… dialog;
  the weld preview shows the part and faces, not stations (they need a mesh); the DXF
  preview is text. Agents make sweep steps, mesh studies and rules with `submit`; an
  MCP tool of their own each would match the forms.
- Another docking library behind `gui/docking.py` (optional); menu icons; a focus-order
  review of every dialog; `operators.TYPE_COLOURS` as theme tokens (0045).
- 3D mouse: the macOS check beside 3DxWare; axis signs on real hardware (Windows only so
  far, not under spacenavd); osx-arm64 hidapi built and published (the recipe is in
  `packaging/recipes/hidapi`) (0069). (Per-axis invert: built 2026-11-13, Settings >
  3D mouse, an entry per axis.) **Built
  (2026-11-10):** spacenavd on Linux (tested against a fake daemon), the pivot under the
  view centre, the device's name in Settings.
- Trackers: Code_Aster past SUIVI_DDL's four columns (OBSERVATION), in its linear and
  thermal solves; OpenFOAM and Code_Saturne probes as trackers (0054).
- Usability left by ADR 0090 package 11 (its Progress entry lists them): a command table
  behind menus, ribbons and the tree (UX-5, 16), Study and View menus and a workspace
  strip (UX-9), the view cube and the view's right-click menu, connection and run
  panels, solver settings as a form, a load's live preview, New analysis choosing among
  the solvers installed.

### 11. Project, storage and core

- Slow paths in Rust or C++ once profiled (0003).
- Results store: deleting native solver files once imported (optional); byte-stream-split encoding on list
  columns (0008).
- Archives: older formats kept in `tests/data/archives`; export taking the session's lock
  and making an "archived" version first (0061).

### Found while collecting (fix soon, not deferrals)

- Found writing the theory notes (ADR 0083 step 4, 2026-11-15). Fixed then: mesh
  convergence took a 2D model's refinement ratio as a cube root (`convergence.verdicts`
  now uses the meshes' dimension; its order had come out 1.5 times too high); adaptive
  passes counted no elements on a 2D mesh, and with a named field ran the energy
  criterion to the budget (it now stops at once and says to use the quantity
  criterion); VDI 2230 equation numbers in `vdi2230.py`; the contact summaries' "a
  tenth of the load" and "over 10 increments", and CLAUDE.md's K = 50 E/h (the default
  is 10) and Elmer magnetics solver. **Fixed (2026-11-15, the second round):**
  - fatigue: the load factor k_c (Shigley Eq. 6-26: 0.85 with the size basis axial,
    0.59 for a shear component, 1 otherwise; Example 6-10's S_e 33.9 kpsi checked
    against the 10th edition) and k_b held at its 254 mm value past Eq. 6-20's range.
  - threads: each flank stiffens by its own angle (`stiffness(..., angle=)`,
    `ThreadPairs.angles`; buttress's 7° and 45° flanks differ); `nodal_areas` removed.
  - spectra: the FE solvers' ELF scale takes the frame's levels as PyNite does
    (`spectra.elf_levels`); a model with no frame is one level.
  - direct analysis: 0.004 Y_i from the stainless members' and plates' loads, 0.002
    from carbon steel's (a shared node the length-weighted mean); plate pressures in Y_i.
  - FEniCSx theta schemes take an amplitude at θ g(t_n+1) + (1−θ) g(t_n) (a ramped flux
    under Crank-Nicolson had put 20 % too much heat in).
  - MMG: c = 9/32 in 3D, 2/9 in 2D (Frey and Alauzet 2005), tested on a quadratic field.
  - a linearization takes 47 points unless told (`ResultProbe`, any client); a linear
    combination warns for contact and large deflection as for plasticity, naming which.
  - `cyclic.check`: decided, a periodic cell refuses modal, buckling and harmonic steps
    as a sector does (its in-phase solution only).
  - the stale docstrings and ADR 0085's brick length.
  Left: the ASME P + Q ≤ 3S row uses one run's stresses where VIII-2 means the range
  (linearize a range combination); a mixed axial and bending load takes k_c = 1 (an
  explicit loading setting would need a schema field); `threads.flank_areas` is now
  used by tests only; the frame path's W for coarse member layouts puts half a column's
  weight at its top node only (12.7.2's levels, crude for one-member columns).

- Found writing the verification pages (ADR 0083, 2026-11-15). Fixed then: the
  optimisation benchmark's hand answer (7.37 mm for E = 200 GPa; 7.25 mm with the
  generic steel, computed in the test, held to 1 %); the plasticity docstring's 0.2611
  and 380.6 MPa (0.2605, 380.2: it left out 1 + H/E); the fastener test's "expands the
  beam into solids"; ADR 0037's Code_Aster elastic support "refused for now"; ADR 0078's
  and CLAUDE.md's −6.8 % weld line force (−6.3 % since the couplings fix); the time
  integration note quoting the 0.05 K tolerance as the 0.002 K measured. **Fixed
  (2026-11-15, the second round), each run on every solver it covers:**
  - tolerances tightened to two or three times what was measured: skin-effect R 1.5 %,
    the harmonic shoot-out 0.5 %, the transient eddy shoot-out 1e-4 (1.7e-6 measured),
    the step current 1.5 %, heat generation 0.05 % of the rise, TEAM 7 RMS 3.5 % (2.6 %
    Elmer, 2.3 % FEniCSx), the fastener F + P/2 1 %, P-Delta and DAM 0.1 %, FE spectra
    1e-6 (with the frame's levels), prestress 0.25 %, the tie's worst node 20 %,
    transient conduction 1 % of the rise (0.346 K on all four). HSS stays at 4 %: it
    is CalculiX's missing shear term (3 %), not slack.
  - built: Belleville springs stacked (series 0.2 %, parallel 3.5 % at light load from
    the nested faces' contact); 20 against 27 nodes (0.99959 on Code_Aster and Elmer);
    the plate with a hole adapted (0.46 % in 3305 elements, a uniform mesh of 3998 only
    0.89 %; K_t +3.7 % of Heywood's, a 5 mm plate above plane stress); a stress at a
    singularity reported **diverging** by `convergence.verdicts` (each finer mesh moving
    it as much as the last); weak springs sliding (friction holds 0.1 N sideways; 10 N
    lifted the interface's edge, so the load is small) and the factor (3.2e-6 between
    1e-6 and 1e-7).
  - records reconciled: transient heat (0.346 K, 0.43 %: CLAUDE.md's 0.34 % was the
    kelvin figure), ADR 0027's stretch, ADR 0077's fillet (−0.7 %), ADR 0044's Lorentz
    clouds and moved pressure, ADR 0078's refusals, the Netgen and AISC 370 docstrings.
  Left: TEAM 7 at 200 Hz (needs the published 200 Hz measurements in the test); a
  lift-off solved end to end on weak springs (CalculiX diverged; the warning is checked
  on results); and the pages' citations the repo names without details (Almen and
  László, Tipton, Sorem and Rolovic, Bisshopp and Drucker, Cowper, MacNeal and Harder,
  the textbooks), completed from the writers' knowledge: check each before the manual
  is published.

- Code_Saturne's capability matrix claims transient flow (`fluid_flow`: stationary,
  transient), but no benchmark runs one and its adapter and CLAUDE.md describe steady
  flow (found writing the help, 2026-11-15). The maintainer chose a benchmark
  (2026-11-15): a transient Code_Saturne case against a closed form. **Built
  (2026-11-15):** the writer refused transient steps (it failed safe), and nothing
  marched in time. It now does: a constant `time_step` to `end_time`, first order,
  results every `output_every` steps and at the start, one step per output time
  (`ensight.series`/`times`); the initial temperature is Code_Saturne's reference
  temperature (a formula is compiled into the solver, MEG, which the image can't link:
  no zlib or gfortran development files); `output_every` is a shared setting it takes.
  Benchmark `tests/integration/test_cfd_transient.py`: a temperature front in plug flow
  against Ogata and Banks, 0.53 % of the rise at 10 s and 0.50 % at 20 s (1 % allowed).
  OpenFOAM's adapter stays steady (simpleFoam).

- Elmer's capability matrix claimed `bonded_nonconformal`, but `elmer/sif.py` writes no
  mortar tie and refuses it (it failed safe, with code `unsupported`). Fixed 2026-11-09:
  the claim and CLAUDE.md's "mortar tie" are gone; the mortar tie stays in section 3
  until a benchmark passes.
- The ADR index had drifted: 0069 (steps 1-3 built) and 0071 (steps 1-4 built). Corrected
  with this record; 0068 (steps 1-3 built) corrected 2026-11-09.
- `build-packages --skip-existing` reused an older puzzle-fem of the same version, so a
  rebuilt installer could ship old code without a word (found 2026-11-09). Fixed: the
  build compares the package's recorded commit with the checkout's and rebuilds when
  they differ.
- This record and CLAUDE.md had `%TEMP%\run_aster__*\fort.6` with its `\r` and `\f`
  turned into control characters. Fixed 2026-11-13.
- Found 2026-11-13 and fixed the same day (phase 0 of the plan):
  - Elmer and FEniCSx listed `electric_currents` and `electromagnetic_waves`, which
    neither writes: they are now `Capabilities.planned_physics`, never offered as
    working, and a study using one is refused as planned.
  - MCP `capabilities` returns every field of the matrix (it left out `axes_features`,
    `amplitude_features`, `time_schemes`, `material_models` and others), and
    `result_summary` gives a buckling mode's `load_factor`, not `frequency_hz`, with
    each step's label.
  - Four integration tests shared their unit tests' module names, so collecting
    `tests/` whole (as `testpaths` does) failed: renamed `*_solved.py`.
  - Status lines of ADRs 0019, 0027, 0028, 0037 and 0041 brought up to date.
  - The results store compacts itself every 40 runs (`ResultStore.maybe_compact`:
    compaction, a log checkpoint, files past Delta's week removed).
  - A part with no material is the problem `no_material` before a run (it was only the
    Advisor's), component-wide selections counted.
  - A sweep spends a run of the agent's budget per point it solves, and one that would
    overrun the budget is refused before it starts (points already stored cost nothing).

## Plan to finish (2026-11-13)

Every item above lands in one phase. A phase is done when its items are built with the
benchmark or closed-form check the conventions ask for, or moved to "Not planned" with
the maintainer's word. Inside a phase the order is free; phases go in order, except that
phase 6's items are done whenever what they wait on arrives.

0. **Fix what's wrong now** (done 2026-11-13). The items found on 2026-11-13 above: claims an
   adapter doesn't back, the MCP gaps that mislead an agent, the test collection, ADR
   statuses. Also scheduled store compaction (11), and the agent's checks the GUI has
   that MCP lacks: a body with no material before a run, a sweep as one run in the
   budget (9).
1. **Finish what's partly built** (they are closest to done and each has its design):
   - pretension locked across mixed steps, partial local axes on the rest (3);
   - meshing (5) (done 2026-11-13: hex meshes of bodies of revolution by blocks;
     2026-11-14: hex and tet in one recipe, tied; cones and tori by revolved profiles;
     prisms swept; bends revolved and re-entrant bodies cut into blocks);
   - welds (3b) (done 2026-11-14: the light weld's lost couple, by per-station
     couplings, and the groove weld's cap as a solid; done 2026-11-13: spectra
     and ± E on CalculiX and Code_Aster, the plate-side ASME toe, combinations on the
     FE solvers, POU_D_T_GD section forces, the 3D mouse's per-axis invert, the thread
     profile drawn, the schematic's leftovers);
   - the script sandbox on Linux and Windows, WSL staging and enabling (2) (done
     2026-11-13; the root file systems moved to phase 5 by the maintainer's decision).
2. **The agent and help** (the overarching principle: every feature reachable and
   explained by the agent):
   - ADR 0084, remote providers;
   - section 9's MCP items (`point` and `axes` on `result_*`, a time or mode within a
     step, presets by command, entity names, edge seeds and meshing methods, Code_Aster's
     advice translated);
   - ADR 0083 (help) and 0075 (worked examples) come last of all, after 0084 and 0085,
     since their reference is generated from the code and their examples run on it.
3. **Breadth on the solvers Puzzle has** (sections 3, 3b, 4, 6): what one solver does and
   another refuses, in the order users meet it:
   - loads and supports (amplitudes everywhere, remote force, pretension and
     compression-only on Elmer and FEniCSx, Elmer's oblique elastic supports);
   - dynamics (Code_Aster modal, Elmer and FEniCSx modal superposition, HHT on Elmer,
     base excitation, harmonic stresses, the NAFEMS forced-vibration set) and buckling
     (critical load label, pinned column, beams and shells, FEniCSx and Elmer, nonlinear
     buckling, imperfections);
   - materials (hyperelasticity, creep, orthotropic on Elmer and FEniCSx and 2D Code_Aster,
     Code_Aster orthotropic plasticity), spherical coordinate systems;
   - beams, shells and fasteners (Elmer and FEniCSx, joins without shared nodes,
     mid-surfaces, section results, spring connectors, fitted middle holes, spring-beam
     inertia) and member-check clauses;
   - contact (Hertz on Code_Aster, weak springs released), fatigue (rainflow,
     critical plane), symmetry on the other solvers;
   - 2D's refusals and gaps, and imported fields' and exports' gaps;
   - the magnetics benchmarks never run, Elmer's circuits, static current and waves, BDF2.
4. **Post-processing, geometry, GUI and core** (sections 5, 7, 8, 10, 11): result tools,
   shoot-out reports, derived-result caching, symbols, cuts; sketches on any plane,
   selections, measure, adaptivity benchmarks, mid-surfaces; optimisation's NSGA-II and
   parallel generations, dataframe leftovers, wires routed; the material builder,
   FormPanel, Settings, trackers, focus order, theme tokens; archives and the store.
5. **Runtimes, packaging and wider reach** (sections 1, 2, 3, 5): CalculiX's container
   penalty measured, the shared image base, the job runner on Windows, progress parsed on
   the host, LSF, ranks for OpenFOAM/Code_Saturne/FEniCSx, side-by-side releases and the
   launcher, licences and the source offer, pixi on remote hosts, faster installs, native
   ports; the WSL machine's root file systems fetched on first use, not shipped (the
   maintainer, 2026-11-14: the six images are about 9 GB uncompressed, 5.1 GB of it
   FEniCSx's): a solver's image layers downloaded from the registry over HTTPS without
   Docker, each checked against a digest pinned in the release, stacked into a tarball,
   `wsl --import`ed and the tarball deleted, with a progress toast on the first solve;
   the same tarballs offered as optional downloads for offline sites
   (`machine.install_from_tarball` imports them); OpenFOAM's transient, convection and mass-flow inlets (then multiphase,
   compressible, dynamic meshes); solve helpers on other computers; the Fusion 360 link,
   FreeCAD; MCP over the network.
6. **Waiting on something outside the repository** (done when it arrives):
   - signing certificates (2); GitHub's container registry and conda-forge submissions,
     the maintainer's to start (1, 2);
   - real hardware: a GPU for FEniCSx/CUDA PETSc, PaStiX, cuDSS (1); a Mac with a 3D mouse
     (10); Apple silicon for PARDISO or PaStiX and the arm64 Code_Aster image (1);
   - a real Slurm (and LSF) cluster (1); a Nastran for checking exported stresses (6, 0060);
   - Lima inside a notarized app (2).

**Not planned unless asked again** (kept listed; each was conditional where it came from):
slow paths in Rust or C++ (only once profiled, 0003); a web front end (only if needed,
0003); fine-tuning local models (only if retrieval plateaus, 0022); another docking
library (optional, 0045); deleting native solver files after import (optional, 0008);
QtCharts (minor, 0038); new solvers (FreeFEM,
GetDP, Palace, MYSTRAN as an adapter) and two-way coupling (preCICE), each a decision of
its own that the freeze above holds back (0012, 0009, 0060). Explicit dynamics was
taken out of this group by the maintainer (2026-11-13): it becomes ADR 0085, OpenCourant
(the community continuation of OpenRadioss) as a solver with its adapter and GUI.

## Consequences

- One place answers "what's left?": for the maintainer, for an agent planning work, and
  for the shareable design doc.
- It stays true only if it is kept: building an item removes its line in the same change,
  and a new "not yet" in any ADR is copied here.
- Items stay owned by their source ADR for design detail; this list holds one line each.

## Alternatives considered

- Leave the deferrals in their ADRs: nothing shows them together, and their built state
  drifts (two index rows had).
- An issue tracker: better for discussion and assignment, but the project keeps its
  decisions in the repository, readable by the agent; issues can still be opened from here.
- One ADR per deferred item: too many records for one-line items; an item that grows gets
  its own ADR when it is picked up.
