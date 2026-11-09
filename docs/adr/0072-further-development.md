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

## Decision

This record is the one list of further development. Each item names the ADR it came from;
"Partly" says what is left. Picking an item up means a new ADR, or a step of the ADR it came
from, and removing the line here in the same change. New deferrals are added here as well
as in their own ADR. The order inside each section is not a plan, except where marked.

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
- Then measure where the rest of CalculiX's container penalty goes (files over the
  Windows mount vs start-up); copying a case into the container's own file system may help.

Other items:

- One shared base for the conda-based images (FEniCSx, Code_Aster, CalculiX) and a
  slimmer FEniCSx image (5.1 GB, mostly its compiler and PETSc) (new; low priority).
- Native Code_Aster on Windows shows no progress until it ends: its `run_aster.bat`
  sends the run to `fort.6` with `>>` where Linux's uses `tee`, so the Solution panel
  stays empty for minutes and a long solve looks hung (found 2026-11-08 on an
  882,309-equation model). The container streams; for the native fallback, follow
  `fort.6` in run_aster's temporary folder, or run Code_Aster's Python directly (new).
- Native Windows FEniCSx without Docker: use `pypardiso` (MKL PARDISO, on conda-forge
  win-64) in `study.py`'s SciPy path instead of SuperLU (new; the fallback when Docker is off).
- Publish the images (ghcr.io); none are published yet (0007, 0013).
- Record the solver version and image digest with each run; images write
  `puzzle-solver-version` but the store keeps neither (0007).
- A Code_Aster image for arm64 (needs a source build) (0013).
- A CalculiX image with PARDISO (0051); PARDISO or PaStiX on Apple silicon and Windows (0051).
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

### 2. Installers and packaging (0068 steps 3-5 and others)

- Puzzle's own Linux machine (WSL2 import on Windows, Lima on macOS) with the non-native
  solvers, through the runner, started on demand and stopped when idle; a one-prompt WSL
  enablement on Windows (0068, 0013).
- CalculiX PARDISO built on the user's machine after install (0068 step 4, 0051).
- A Solvers page in Settings with Repair (0068). Side-by-side releases, a launcher, a
  release-feed check (0068). Licences in `licenses/`, Help > Licences, the GPL source offer (0068).
- Partly: signed and notarized installers; the workflow is in place, certificates pending (0068).
- The same solver environments on remote hosts via pixi (0068). A web installer, AppImage
  (0068). Faster installs (13 min on an Intel Mac) (0068).
- Native ports: Code_Aster on macOS; Elmer, Code_Saturne and OpenFOAM on macOS and Windows
  (0068). Partly: elmerfem on conda-forge (recipe builds locally on macOS, not submitted;
  Windows and MPI variants to follow) (0013, 0068).
- Open questions: Lima inside a notarized app (fallbacks Podman, Apple's `container`), the
  VM's idle time, the order of native ports (0068).

### 3. Solvers and physics

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
  Explicit and nonlinear dynamics stay out of scope (0041).
- Buckling: the critical load in the label for a single force; a pinned-column benchmark
  (the hinge is now possible); nonlinear buckling (RIKS, PILOTAGE); a mode shape as an
  imperfection; beam and shell buckling benchmarked; buckling on FEniCSx and Elmer (0028).
- Loads and constraints: amplitudes on temperatures, displacements, body loads, and in
  static steps for load-deflection curves (0037); rigid remote force, bolt pretension and
  compression-only supports on Elmer and FEniCSx (0037); pretension locked across steps on
  one-job-per-step solvers (0037); partial local-axes displacements beyond CalculiX (0037).
  Partly: Elastic support on Elmer only on axis-aligned faces (0037).
- Materials: hyperelastic models written by any solver; creep as a typed model; orthotropic
  and anisotropic models on Elmer and FEniCSx, and in 2D on Code_Aster; Code_Aster's
  orthotropic plasticity and anisotropic conductivity with tables (0020, 0049).
- Coordinate systems: spherical displacements and forces as functions of position;
  spherical material axes; the sphere checks (0049).
- Beams, shells, fasteners: a fastener pinball radius; thermal expansion of a beam bolt;
  beams joined to solids or shells where nodes aren't shared; section stresses on beams;
  shells from a solid's mid-surface, joined where nodes aren't shared, section results,
  normals across several faces, the pinched-cylinder benchmark; curved panels about other
  axes (partly); a spring connector with given stiffnesses; fasteners, beams and shells on
  Elmer and FEniCSx (0027). Fitted middle holes in a bolt stack (0050). Spring beams:
  rotary inertia, centrifugal loads, a body load's share within a segment (0055).
- Contact: Hertz-type non-conforming curved contact on Code_Aster (refused now) (0056).
- Fatigue: critical-plane multiaxial fatigue; rainflow counting (0026).
- Threads without meshing them: see 3a.

### 3a. Threaded connections as smooth cylinders (researched 2026-11-08)

Goal: a threaded engagement (bolt in a nut or tapped hole) modelled on smooth cylinders.
It should still give the per-thread load distribution, the radial wedge force and the
lead's torque. It should work for any thread form: ISO/UNC 60°, ACME, trapezoidal,
buttress, square and tapered pipe threads. Today's bolts (0050) bond the tapped thread,
which loses the thread effects.

**What others do (established):**

- **Ansys:** the Bolt Thread geometry correction (`SECTYPE,,CONTACT,BOLT`;
  `SECDATA, Dm, P, ALPHA, N, axis points`).
  - Inputs: the pitch diameter, pitch, half-thread angle, starts, and the hand by the
    angle's sign.
  - Both sides are meshed at the major diameter. Only the contact normal and detection
    change, not the geometry. Elements should be smaller than p/4.
  - Modes: a "general" mode (both flanks) and a "unidirectional" one (one flank, the axis
    tip→head).
  - Limits: straight symmetric threads only (no buttress, no taper), small strain.
  - Its M120 example: shank stress within 0.2 % of a meshed thread with 5-16× fewer
    elements. Bonded MPCs lose the thread-region stresses.
    [ansyshelp ctecgeomcor](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_ctec/ctecgeomcor.html)
- **COMSOL:** Bolt Thread Contact (3D and 2D axisymmetric).
  - Inputs: half-angle, lead and hand, contact orientation Both/Up/Down, friction.
  - Its default penalty is deliberately low (2 E/r) to stand in for the teeth bending: a
    stiffer one crowds the load onto the first thread.
  - [doc.comsol.com sme_ug_solid.07.141](https://doc.comsol.com/6.4/doc/com.comsol.help.sme/sme_ug_solid.07.141.html)
- **Abaqus:** `*CLEARANCE, BOLT` on small-sliding pairs.
  - Inputs: half-angle, pitch, major and mean diameter, axis.
  - Single start only.
- **The open-source solvers** have nothing for threads. CalculiX's `*CONTACT PAIR` normals
  can't be overridden, and its `*CLEARANCE` was 3× off here (0056). Code_Aster's
  `DEFI_CONTACT` has no thread option, nor does Elmer.
- **Their usable building blocks:**
  - CalculiX: `*EQUATION`, directional GAPUNI, SPRING2.
  - Code_Aster: `LIAISON_DDL`, oriented `DIS_CHOC`/`DIS_T`.
  - FEniCSx: a penalty or Nitsche term in the weak form.
- **Published checks:**
  - First engaged thread about 33 %, then 27, 20, 12, 8 % for a standard nut (Sopwith,
    Yamamoto). Yamamoto's spring model gives the tooth compliance.
  - Axisymmetric thread models match 3D helical ones for axial load sharing (Fukuoka; Acta
    Polytechnica Hungarica 12(6) 2015).
  - VDI 2230 gives the thread and nut compliance.

**Design (proposed):**

1. **A `ThreadForm`** (with a library of standard forms beside `bolts.SIZES`) holds:
   - `pitch`, `starts` (lead = starts × pitch), `hand`;
   - major, pitch and minor diameters;
   - a `taper` (NPT 1:16);
   - a half-angle **per flank**, measured from the radial plane:
     ISO/UNC 30/30, ACME 14.5/14.5, trapezoidal 15/15, buttress 7/45 or 3/45, square 0/0.

   Angles per flank and the taper are what make buttress and pipe threads possible where
   the commercial features refuse them. A `BoltType`'s size picks its form, and a bolt
   whose tapped end has a thread gets a thread engagement in place of a bond.
2. **Kinematics** (pure Python, solver-neutral, `threads.py`): each node on the bolt's
   thread face, in (r, θ, z) about the bolt axis, has a flank normal for flank k:

       n_k = (e_z − (s L / 2π r) e_θ ∓ tan α_k e_r) / |…|

   Each term carries one effect:
   - the e_z term carries the axial load;
   - the e_r term is the radial wedge (zero for square threads);
   - the e_θ term is the lead (the loosening torque, and which way friction acts).

   Pairing and areas:
   - Nodes pair with the nut face at the same (θ, z) by the KD-tree pairing
     `contact_check` already has.
   - The areas are tributary (`common.surface_area`, so curved second-order faces are
     right), each flank taking half × sec α_k.
   - The phase z/L − sθ/2π matters only for clearances (backlash) and starts.
3. **Which flank bears:** `bearing` = `auto` | `load` | `clearance`.
   - `auto`: both flanks unilateral, each with its own clearance, so the solve finds the
     bearing one, as Ansys's general mode does.
   - `load` or `clearance`: one flank only, cheaper and linear. Buttress threads need the
     steep flank facing the load, which `auto` finds and `load` checks.
4. **Thread compliance:** each pair gets a normal stiffness per area from the tooth's
   compliance: Yamamoto's bending, shear, root and radial terms over a pitch.
   - Also available: `rigid`, or a value the user gives.
   - Without the compliance, rigid flank ties over-load the first thread, which is why
     COMSOL lowers its penalty.
   - Tangentially: friction μ, stick or bonded.
5. **Per solver:**
   - **Linear, one flank:** CalculiX `*EQUATION` (u_nut − u_bolt)·n_k = 0, or SPRING2
     with a compliance. Code_Aster `LIAISON_DDL` or `DIS_T` on a two-node element along
     n_k.
   - **Unilateral:** CalculiX GAPUNI along n_k in an NLGEOM step, as the compression-only
     supports are. Code_Aster `DIS_CHOC` with its friction.
   - **FEniCSx:** the exact penalty integral with `ufl.conditional` on the flank gap.
   - **Elmer:** the bonded spring version only at first; the rest refused.
   - Helper nodes and their removal from results reuse the bolt machinery (0037, 0050).
   - **2D axisymmetric:** e_θ drops out. This is the cheapest benchmark and the first
     stage.
6. **Results:**
   - flank force per pair;
   - the load per thread (axial share summed per pitch), as a table and a Curve (0071);
   - the wedge's total radial force;
   - flank pressure.

   A skill `thread-engagement` and MCP arguments on bolts follow ADR 0057.

**Validation:**

- **2D axisymmetric, M16 bolt in a nut:** the true sawtooth profile meshed (sketch,
  0048) against the equivalent model, for each thread form.
  - Compare the per-thread shares, shank stress, nut hoop stress and axial stiffness.
  - A square thread must give no wedge.
- **The shares' shape** against Sopwith/Yamamoto (first thread about 30-35 %).
- **Compliance** against VDI 2230.
- **One 3D helical case:** circumferential pressure nearly uniform, and the tightening
  torque against T = F (d₂/2) tan(λ + ρ′) with ρ′ = atan(μ / cos α).

**Not yet confirmed:**

- how Ansys and Abaqus compute their internal gap from the helical phase;
- whether CalculiX `*TRANSFORM` applies to `*EQUATION` dofs;
- Code_Aster test V6.04.228;
- VDI 2230's and Yamamoto's exact coefficients, to be read from the sources before
  building.

The decision and its implementation strategy are ADR 0076.

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
  non-conformal contacts.
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
- Swept meshes; hex and tet together in one recipe; second-order hexahedra (0019).
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

### 8. Schematic and operators

- Wires routed around nodes (0058).
- A real script sandbox on Linux and Windows (bubblewrap, a job object); only macOS has one
  (0062).
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

### 9. Agent and MCP

- MCP over the network with authentication; per-change acceptance in the GUI (partly);
  streaming replies (0015).
- VRAM on discrete GPUs; English summaries of more of Code_Aster's French manual (partly);
  fine-tuning only if retrieval plateaus; routing by task family (0022).
- A `point` argument on `result_*` and point labels in `runs`; a time, frequency or mode
  within a step and `axes` on `result_*`; buckling factors labelled `frequency_hz` in
  `result_summary`; `capabilities` missing `axes_features`, `amplitude_features`,
  `time_schemes`, `material_models`; applying a settings preset by command; tools for a
  geometry's entity names and for edge seeds and meshing methods; a sweep counted as one run
  in the budget; a body with no material reported before a run (0057).
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
- Another docking library behind `gui/docking.py` (optional); menu icons; a focus-order
  review of every dialog; `operators.TYPE_COLOURS` as theme tokens (0045).
- 3D mouse: spacenavd on Linux; the macOS check beside 3DxWare and axis signs on real
  hardware; osx-arm64 hidapi in Puzzle's channel; the pivot under the view centre; the
  device's name (or why there is none) in Settings; per-axis invert (partly) (0069).
- Trackers: Code_Aster past SUIVI_DDL's four columns (OBSERVATION), in its linear and
  thermal solves; OpenFOAM and Code_Saturne probes as trackers (0054).

### 11. Project, storage and core

- Slow paths in Rust or C++ once profiled (0003).
- Results store: scheduled compaction (`compact()` exists, nothing calls it) (partly);
  deleting native solver files once imported (optional); byte-stream-split encoding on list
  columns (0008).
- Archives: older formats kept in `tests/data/archives`; export taking the session's lock
  and making an "archived" version first (0061).

### Found while collecting (fix soon, not deferrals)

- Elmer's capability matrix claims `bonded_nonconformal`, but `elmer/sif.py` writes no
  mortar tie and refuses it (it fails safe, with code `unsupported`). The claim, and
  CLAUDE.md's "mortar tie", should go until a benchmark passes.
- The ADR index had drifted: 0069 (steps 1-3 built) and 0071 (steps 1-4 built). Corrected
  with this record.

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
