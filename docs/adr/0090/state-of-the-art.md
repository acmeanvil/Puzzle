# Puzzle design review: gap analysis against the state of the art

Reviewed 2026-11-16 on `development` at da26cde.

## Basis and limits

- Puzzle's side comes from CLAUDE.md, the ADR index, ADR 0072's open list, the adapters' capability matrices and keyword searches of `src/puzzle` (for example: no file mentions random vibration, acoustics, radiation, topology optimisation, rotor dynamics, piezoelectricity, multiphase flow, preCICE, element birth and death, superelements or arc-length control).
- The commercial side and the open solvers' abilities are from my own knowledge of those products, not from a search made for this review. Product statements are kept general for that reason; check a specific release before quoting one. Open-solver keywords named below should each be confirmed against the solver version Puzzle pins before work starts.
- Effort: S is days, M one to three weeks, L one to two months, XL a quarter or more, for one adapter with its benchmark, skill and help page.
- Priority: now, next, later, or not for Puzzle.
- ADR 0072 freezes new ADRs until everything through 0085 is built. Most items below would need a new record, so "now" means "first after the freeze, or as an amendment to the ADR named".

Target users, as CLAUDE.md states them: small engineering teams, non-experts relying on the agent, and experts who want open solvers.

---

## 1. Physics and analysis types

### Structural materials and nonlinearity

| ID | State of the art, and who has it | Puzzle today | Value to Puzzle's users | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-1 | Hyperelasticity (Neo-Hooke, Mooney-Rivlin, Ogden, Yeoh) with curve fitting from test data: Ansys, Abaqus, COMSOL, Marc; PrePoMax exposes CalculiX's | `Material.models.hyperelasticity` is in the schema and refused by every adapter (`solve.py`, registry); on 0072 phase 3 | High: seals, gaskets, mounts and soft parts are everyday work; a refusal reads as "can't do rubber" | CalculiX `*HYPERELASTIC` (first), Code_Aster `ELAS_HYPER`, FEniCSx natively; mixed u-p elements already written (ADR 0082) | M per solver; fitting S | now |
| SOTA-2 | Creep and viscoplasticity (Norton, time hardening), viscoelasticity (Prony series): all commercial codes | Absent (one schema mention) | Medium: hot bolting, plastics, solder; relaxation of the preloads Puzzle already models well | CalculiX `*CREEP`, `*VISCO`; Code_Aster's creep laws and MFront | M | next |
| SOTA-3 | Kinematic and combined hardening, cyclic plasticity (Chaboche), temperature-dependent plasticity in the GUI | Isotropic tabulated plasticity on CalculiX and Code_Aster; Johnson-Cook on OpenCourant; hardening per temperature is in the schema but not the builder (ADR 0020) | Medium: ratcheting and low-cycle fatigue need it | CalculiX `*PLASTIC, HARDENING=KINEMATIC/COMBINED`; Code_Aster `VMIS_CINE_LINE`, `VMIS_CIN1_CHAB` | S-M | next |
| SOTA-4 | Composites: layered shells and solids, ply stacks, failure criteria (Tsai-Wu, Hashin, Puck), draping: Ansys ACP, Abaqus, Simcenter, HyperWorks | Absent; orthotropic elasticity on solids only | Medium-high for small teams in sport, marine, drones; a wide commercial moat | Code_Aster `DEFI_COMPOSITE` with DKT/DST shells; CalculiX composite S8R sections | L | next |
| SOTA-5 | Nonlinear buckling and post-buckling: arc length (Riks), imperfections seeded from modes | Linear buckling with preload; nonlinear buckling and imperfections on 0072 phase 3; Code_Aster `PILOTAGE` noted "not yet" (ADR 0023) | High for thin-walled work; linear factors alone are unconservative | Code_Aster `PILOTAGE`; CalculiX by a stabilised static step from a perturbed mesh (`mesh/morph.py` can apply a mode shape) | M | next |
| SOTA-6 | Fracture: J-integral and K extraction, XFEM, cohesive zones, crack growth: Abaqus, Ansys SMART, Code_Aster | Absent | Low-medium: specialist, but pressure-vessel users ask | Code_Aster `CALC_G`, XFEM, `CZM_*` laws; CalculiX crack propagation | L | later |
| SOTA-7 | Gaskets, element birth and death, model change between steps: Ansys, Abaqus | Absent; `Feature.steps` switches loads only | Medium: weld sequence, staged assembly, bolt-up sequences | CalculiX `*MODEL CHANGE`, `*GASKET` | M | later |
| SOTA-8 | Restart and continuation from a converged step | Absent; steps on single-step solvers are independent analyses joined by transfers | Medium: long contact and transient runs fail late; rerunning from zero is costly | CalculiX `*RESTART`; Code_Aster `POURSUITE`; Elmer restart files (already used for fields) | M | next |

### Dynamics and vibration

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-9 | Random vibration (PSD in, RMS and 3-sigma stress out), with vibration fatigue (Steinberg, Dirlik): Ansys, Nastran, Abaqus | Absent | High: electronics, aerospace brackets, transport qualification are all PSD-driven | Puzzle's own modal post-processing over stored modes and participation (it already does CQC in `puzzle/spectra.py`); Code_Aster `DYNA_ALEA_MODAL` as the cross-check | M | now |
| SOTA-10 | Response spectrum on any model, base excitation, harmonic stresses, modal superposition everywhere | Spectrum by CQC on frames and on CalculiX/Code_Aster modes; base excitation, harmonic stresses and superposition on Elmer/FEniCSx on 0072 phase 3 | High: completes ADR 0041 | Existing adapters | M | now |
| SOTA-11 | Prestressed and cyclic-symmetry modal, Campbell diagrams, rotor dynamics with gyroscopic terms: Ansys, Simcenter, Code_Aster | Cyclic symmetry for static and transient only (`cyclic.check` refuses modal); prestress on CalculiX | Medium: turbomachinery and any spinning shaft | CalculiX cyclic `*FREQUENCY` and `*COMPLEX FREQUENCY, CORIOLIS`; Code_Aster's rotor tools | M-L | later |
| SOTA-12 | Substructuring, component mode synthesis and superelements: Nastran, Ansys, Abaqus | Absent | Low for small teams; matters for large assemblies and supplier models | Code_Aster `MACR_ELEM_DYNA`; CalculiX `*SUBSTRUCTURE GENERATE` | L | later |
| SOTA-13 | Submodelling: cut-boundary displacements from a coarse run onto a fine local model | Half there: imported fields map displacements (ADR 0044) and the Abaqus export writes a submodel's displacements; no guided workflow | High: cheap accuracy on a laptop, and Puzzle already has the mapping | Puzzle's own transfer with a new role, plus a wizard; CalculiX `*SUBMODEL` as a cross-check | S-M | now |

### Thermal, fluids and coupling

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-14 | Surface-to-surface radiation with view factors, radiation to ambient: every thermal code | Absent; convection and flux only | High: any hot or vacuum thermal problem is wrong without it | Radiation to ambient: all four FE solvers (S). Cavity radiation: CalculiX `*RADIATE` with view factors, Elmer's diffuse-gray solver | S then M | now (ambient), next (cavity) |
| SOTA-15 | Conjugate heat transfer: Fluent, STAR-CCM+, COMSOL, SimScale | Fluid temperature is a passive scalar; no solid region; wall clouds export h and T for a second study (ADR 0043) | High: electronics cooling is a top use for small teams | OpenFOAM `chtMultiRegionFoam`; Code_Saturne's internal solid coupling | L | next |
| SOTA-16 | Transient, compressible, multiphase and free-surface flow, rotating machinery (MRF, sliding mesh), LES | Steady incompressible RANS on both; transient on Code_Saturne only; OpenFOAM transient, convection and mass-flow inlets on 0072 phase 5 | Medium: each is a user class of its own; transient and MRF first | OpenFOAM `pimpleFoam`, `rhoSimpleFoam`/`rhoPimpleFoam`, `interFoam`, MRF | M each | next (transient, MRF), later (rest) |
| SOTA-17 | CFD meshing for real parts: hex-dominant cut-cell with prism layers (snappyHexMesh, cfMesh; Fluent Meshing, STAR-CCM+) | Gmsh tets with Netgen boundary layers; Gmsh's 3D layers do not work on OCC geometry (ADR 0019) | High for CFD users: external aerodynamics and manifolds are impractical today | `snappyHexMesh` or cfMesh inside the OpenFOAM image, from Puzzle's faceted export | L | next |
| SOTA-18 | Two-way coupling: FSI, thermal FSI, electro-thermal-mechanical in one run: Ansys System Coupling, COMSOL, Simcenter | One-way transfers by design (ADR 0009); preCICE is "not planned unless asked" on 0072 | Medium: one-way covers most small-team work; two-way is where COMSOL is chosen | preCICE with its OpenFOAM, CalculiX and Code_Aster adapters; Elmer monolithically | XL | later |
| SOTA-19 | Acoustics and vibro-acoustics (Helmholtz, radiated sound power, PML): COMSOL, Actran, Ansys | Absent | Medium: NVH and enclosures; small teams rarely afford Actran | Elmer's Helmholtz solver; FEniCSx (complex PETSc build); Code_Aster acoustic elements | L | later |
| SOTA-20 | Electric currents with Joule heating, piezoelectricity, electromagnetic waves, circuit coupling | Electrostatics, magnetostatics, eddy currents (strong, TEAM 7 benchmarked); `electric_currents` and `electromagnetic_waves` are planned physics; the `joule_heating` coupling is declared and unwritten (conformance ADR-37) | High for electric currents (busbars, heaters, fuses): small step, common need | Elmer `StatCurrentSolve`, piezo module and `VectorHelmholtz`; FEniCSx | S (currents), M (piezo), L (waves) | now (currents), later (rest) |

### Design checks and durability

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-21 | Fatigue from load histories: rainflow counting, multiaxial critical plane, weld fatigue by structural hot-spot stress (IIW, DNV, BS 7608, Eurocode 3-1-9), FKM: nCode, fe-safe, Simcenter | Stress-life and strain-life with Miner over blocks; weld fatigue by AISC Appendix 3 and ASME VIII-2; rainflow and critical plane on 0072 phase 3 | High: fatigue decides most real designs; IIW and Eurocode categories are what Europe uses | Puzzle's own post-processing (`post/fatigue_tool.py`, `weld_fields.py`) | M | now |
| SOTA-22 | European and pressure-vessel codes: Eurocode 3 members and joints, EN 13445, ASME VIII-2 Part 5 as a guided assessment, FKM static proof | AISC 360/370, ADM, ASCE 7-16, VDI 2230, ASME VIII-2 linearization and 5.5.5; nothing European for members or welds | High outside North America: half the likely users | Puzzle's own modules beside `frames/aisc360.py` | L per code | next |
| SOTA-23 | Reliability and tolerance analysis: Monte Carlo on surrogates, six-sigma (optiSLang, HEEDS) | Built (ADR 0063 phase 4: scatter, probabilities, common random numbers) | Already competitive | n/a | n/a | protect |

---

## 2. Geometry and meshing

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-24 | Mid-surface extraction with automatic thickness and shell-to-shell joining: MSC Apex, HyperMesh, SpaceClaim | Deferred since ADR 0014 ("no good open-source implementation"); on 0072 | High: sheet-metal and fabricated frames are small teams' core work, and shells are built | Own code on OCCT: face pairing by offset, then `sheet` parts with `shell_sections`; even a constant-thickness-only version covers most parts | L | next |
| SOTA-25 | Automatic defeaturing: find and remove small fillets, holes, logos, slivers by size, with preview: SpaceClaim, Discovery, Apex, SimSolid sidesteps it | `prep.py` heals and removes picked faces; no detection | High for non-experts: most meshing failures on imported CAD are small features | OCCT `BRepAlgoAPI_Defeaturing` (already Puzzle's kernel) driven by `measure.py`'s radii and areas; the agent proposes the list | M | now |
| SOTA-26 | Live CAD links: geometry updates with selections kept (Workbench CAD interfaces, Simcenter, Onshape, Fusion) | STEP re-import with signature matching and bulk reattachment (ADR 0014); a Fusion 360 link and FreeCAD on 0072 phase 5 | High: design iteration is where time goes | A watched STEP file with automatic re-import and a change report first (S); then Onshape's REST API or FreeCAD headless | S then L | now (watched file), later (links) |
| SOTA-27 | Robust general hex-dominant meshing; shell and solid mesh in one part with automatic coupling | Hex by blocks for revolutions, prisms, bends and cuttable bodies; mixed hex and tet tied; shell-to-solid joins without shared nodes on 0072 | Medium: Puzzle is already ahead of most open tools | Gmsh recombination improvements; own block decomposition (continue) | L | later |
| SOTA-28 | Error estimates and adaptivity on every element type; goal-oriented adaptivity (COMSOL, Ansys) | ZZ and adaptivity on tetrahedra only, while 2D defaults to quadrilaterals (conformance ADR-24) | High: it underpins the "built-in expert" promise | `post/estimates.py` | M | now |
| SOTA-29 | Parallel and out-of-core meshing for 10-100 M elements | Gmsh in-process under one global lock | Low for target users | Gmsh's own threads; Netgen's parallel build | M | later |
| SOTA-30 | Meshless or isogeometric analysis on raw assemblies in seconds: Altair SimSolid; Discovery's live GPU solver | Absent | High in demos, and attractive to non-experts, but no open solver matches it | None mature | XL | not for Puzzle |

---

## 3. Solver technology and scale

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-31 | Iterative solvers with algebraic multigrid chosen automatically by model size; distributed memory; 100 M dof routine | Direct solvers by default (PARDISO for CalculiX, MUMPS); MPI on Elmer, Code_Aster and OpenCourant; "ranks for OpenFOAM/Code_Saturne/FEniCSx" on 0072 phase 5 | Medium: a workstation runs out of memory near 2-5 M dof with a direct solver | FEniCSx with PETSc GAMG or Hypre; Code_Aster `PETSC`; a size-based default per adapter with the reason stated | M | next |
| SOTA-32 | GPU solvers: Fluent native GPU, Discovery, Ansys Mechanical's GPU acceleration, COMSOL's recent GPU support | Researched wishlist only (ADR 0052) | Medium: mostly CFD and explicit; hardware is not in hand | PETSc CUDA for FEniCSx; cuDSS; AmgX with OpenFOAM | L | later |
| SOTA-33 | Model order reduction and exported reduced-order models (FMU, digital twins): Ansys Twin Builder, COMSOL, Simcenter | Response surfaces and Gaussian processes over design points (ADR 0063); no modal ROM, no FMU | Medium: controls and system engineers want an FMU | Own: scalar surrogates exported as FMU or ONNX from the existing `surrogates.py` (S-M); modal ROMs later | M | later |
| SOTA-34 | Cloud bursting with pay-per-run and a browser client: SimScale, Onshape, Ansys Cloud | SSH hosts, Slurm and PBS, a job runner that outlives the app (ADR 0021, 0067); cloud batch deferred | Medium: small teams lack clusters, but SSH to a rented machine already works | A documented recipe for a rented VM plus `puzzle hosts add`; AWS or Azure Batch later | S then L | next (recipe), later (batch) |

---

## 4. Workflow and user experience

| ID | State of the art, and who has it | Puzzle today | Value | Open route | Effort | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| SOTA-35 | Solver comparison as a product feature | Unique in concept, but `run_shootout` is unreachable from the GUI, CLI and MCP, with no difference field (conformance ADR-7) | Very high: this is Puzzle's reason to exist | Own | M | now |
| SOTA-36 | Analysis templates and wizards per task (bracket static, bolted flange, modal survey, electronics cooling): Discovery, SimScale, Fusion, Onshape | The Advisor says the next step; `recipes` and clusters insert operator blocks; no whole-study templates; 2 examples of 46 planned tutorials | Very high for non-experts: the first hour decides adoption | Own: `.puzzle` archives as templates with a parameter form; the tutorials (ADR 0083) double as templates | M | now |
| SOTA-37 | Automatic report generation to Word, PDF and PowerPoint with model data, pictures, convergence, checks and a sign-off sheet: Ansys, SimScale, Simcenter | The Report operator writes HTML from checks, numbers, curves and tables (ADR 0071); no setup tables, pictures, mesh statistics or solver provenance | High: a report is the deliverable for consultants | Own: extend `operators/report.py` with the outline, the version, the solver version and image digest (already stored per run), mesh convergence verdicts and renders; PDF through the help system's Qt path | M | now |
| SOTA-38 | Simulation data and process management: Ansys Minerva, 3DEXPERIENCE, Teamcenter Simulation, COMSOL Model Manager | Versions with known-good marks, archives, history with actors, run keys (ADR 0059, 0061); one-TOML-per-object diffs well in git | Medium: Puzzle's file design is a strength; a small team needs git guidance, not a server | Own: a documented git workflow, a `.gitignore` and merge checks; comparison of two versions as a diff view | S | next |
| SOTA-39 | Real-time collaboration and review in a browser: Onshape, SimScale | Single user with a project lock; MCP over the network on 0072 | Low-medium | A read-only shared results viewer (the static site machinery plus glTF export) before any multi-user editing | M | later |
| SOTA-40 | Scripting with a recorder: Ansys Mechanical's ACT and PyMechanical, Abaqus's replay file, COMSOL's Java and MATLAB APIs | Every change is a command in `history.jsonl`; `Session` is the Python API; no "export this history as a script", and no published API reference beyond the schema | High for experts: reproducible scripts are why they pick open tools | Own: `puzzle history --as-script` writing a Python file of `Session.submit` calls; publish the command reference (the help generator already lists MCP tools and CLI) | S-M | now |
| SOTA-41 | Application builder: a locked-down app around one model for non-experts (COMSOL Application Builder and Compiler, Ansys custom apps) | Sliders, panels and shared blocks in the schematic; no stand-alone app | Medium: the agent partly fills this role | Own: a "panel mode" showing only a project's sliders, Run and the Report | L | later |
| SOTA-42 | Materials data management: Granta MI and Selector, temperature-dependent curves with provenance, test-data fitting | A registry of properties per solver with completeness checks, a builder, S-N and strain-life data, 42 fastener grades, filler metals; a small generic library; no fitting | Medium-high: an open library with sources is something users would cite | Own: import from open sets (for example MIL-HDBK-5-era public data, NIST cryogenic data) with a source per value; fitting for SOTA-1 | M | next |
| SOTA-43 | Automatic contact with one-click bolted joints, plus bolt and weld libraries | Strong: contact search, the contact tool, bolt generator with VDI 2230, threads, modelled bolts, welds with code checks | Ahead of every open tool and level with commercial ones | n/a | n/a | protect |
| SOTA-44 | Topology optimisation and generative design, with manufacturing constraints and smoothed CAD output: Ansys, OptiStruct, Fusion, nTop, COMSOL | Absent; parametric optimisation and RBF mesh morphing only | High in perception: it is a headline feature in every competitor | FEniCSx SIMP with a density filter (own `study.py` mode); CalculiX with a BESO-style outer loop; output through `meshfile` and SubD smoothing (ADR 0065) | L | next |
| SOTA-45 | Adjoint shape optimisation and sensitivities | Finite differences through the DOE and optimisers | Medium | CalculiX `*SENSITIVITY`; OpenFOAM's adjoint solvers | L | later |
| SOTA-46 | AI assistance: in-product copilots (Ansys, Siemens, Autodesk), physics prediction from past runs (Ansys SimAI, Altair PhysicsAI, COMSOL surrogate models with neural networks), AI in SimScale | Ahead on the copilot: an agent that acts through the same command layer, with autonomy levels, budgets, proposals, skills, any provider or a local model; scalar surrogates only | High: protect the lead; field-level surrogates are the next step | Own: train on the Delta store's runs across a sweep (the store already keeps one mesh for many runs); start with POD plus GP for fields on a fixed mesh | L | next |
| SOTA-47 | Guided verification for the user's own model: automatic convergence, energy balance, reaction checks and a quality score | Mesh convergence with GCI, ZZ estimates, contact checks, explicit energy checks, the Advisor; reaction-against-load balance is not a standard check | High: this is the "built-in expert" | Own: a "Trust this result?" panel combining reaction balance, GCI, error estimate, contact status, weak-spring share and solver warnings into one verdict; MCP twin | M | now |
| SOTA-48 | Large-assembly handling: 500+ parts, instancing, lazy loading, section and explode views, part filters | Hide, suppress, colours, groups, section cuts; scale not measured; Gmsh and OCC behind one lock | Medium: the first real assembly a team loads decides whether they stay | Measure first: a 200-part STEP through import, contact search, meshing and display, with timings on 0072 | M | next |
| SOTA-49 | Units, expressions and parameters everywhere, tied to CAD | Strong: a unit per quantity, project unit systems, parameters and expressions feeding any quantity | Level | n/a | n/a | protect |
| SOTA-50 | Published V&V with reproducible cases: Ansys and Abaqus verification manuals, NAFEMS sets | Verification pages generated beside the tests; NAFEMS LE1 only from the standard set; most solver benchmarks never run on CI (conformance section f) | High for trust: experts check this first | Own: the NAFEMS linear statics, free vibration and thermal sets, MacNeal-Harder (cited already) as cross-solver benchmarks with a public results table per release | M | now |

---

## Ranked top fifteen

Ranked by how much each would raise Puzzle's standing with practising engineers per unit of effort, given what is already built.

1. **SOTA-35: make the solver shoot-out a first-class, visible feature** (run it from the GUI, CLI and MCP; a difference field; a stored report). It is the one thing no commercial tool can offer.
2. **SOTA-1: hyperelasticity** on CalculiX, then Code_Aster and FEniCSx, with curve fitting. It removes the most visible refusal.
3. **SOTA-36: study templates and the planned tutorials.** They decide the first hour for non-experts.
4. **SOTA-37: a full report** (setup, mesh, convergence, checks, provenance) as PDF and HTML.
5. **SOTA-47: one "can I trust this result?" verdict** from the checks Puzzle already computes, plus reaction balance.
6. **SOTA-9 and SOTA-10: random vibration and the rest of ADR 0041** (base excitation, harmonic stresses).
7. **SOTA-21: fatigue from histories** (rainflow, critical plane) and weld fatigue by IIW and Eurocode categories.
8. **SOTA-14: thermal radiation**, to ambient first, then cavity radiation with view factors.
9. **SOTA-25 and SOTA-26: defeaturing by detection, and a watched CAD file** that re-imports and reports what changed.
10. **SOTA-13: a submodelling wizard** on the existing field mapping.
11. **SOTA-28: error estimates and adaptivity on quadrilaterals and hexahedra.**
12. **SOTA-50 with conformance section (f): the NAFEMS sets across solvers, run on CI,** with a public table per release.
13. **SOTA-24: mid-surface extraction** for constant-thickness sheet parts.
14. **SOTA-44: topology optimisation** on FEniCSx or CalculiX with smoothed geometry out.
15. **SOTA-15 and SOTA-17: conjugate heat transfer with a CFD-grade mesher,** which together open electronics cooling.

Next in line: SOTA-20's electric currents with Joule heating (small, and it makes an existing capability claim true), SOTA-40's history-to-script export, SOTA-22's Eurocode 3, SOTA-5's nonlinear buckling.

---

## What Puzzle already does that commercial tools do not

Protect these in future work.

- **One model, several solvers, compared.** A solver-neutral model with a capability matrix and refusals by name (ADR 0006, 0011). No vendor will compare its solver with another's. It needs finishing (SOTA-35), not rethinking.
- **An agent that acts through the same command layer as the user,** with autonomy levels, budgets, proposals, undo and attribution in the project log (ADR 0015), on any provider or a local model chosen by measurement (ADR 0022, 0084). Commercial copilots mostly answer questions; this one edits and runs, under control.
- **Everything is a typed, logged, undoable command,** and a project is one text file per object (ADR 0009, 0015): diffable, scriptable and reviewable in git. Commercial project files are opaque binaries.
- **Results as an open, queryable store** (Delta Lake, ADR 0008) with dataframe operators over runs (ADR 0064), instead of a proprietary result file.
- **Solver inputs that are real, complete cases** an expert can read, edit and rerun, with typed settings and a checked pass-through (ADR 0023).
- **Verification that travels with the code:** benchmarks as tests with stated tolerances, verification pages generated beside them, and the rule that a capability is claimed only once a benchmark passes.
- **Joint engineering in the pre-processor:** bolt generator with VDI 2230, thread load distribution as a contact correction, modelled bolts from standard tables, welds with AISC, ADM and ASME checks, weak springs with their load reported (ADR 0050, 0076-0080). Commercial tools sell much of this as add-ons or leave it to spreadsheets.
- **Frames and continuum in one tool,** with member checks by AISC 360/370 and the ADM and ASCE 7 seismic (ADR 0079), cross-checked by beam elements in two FE solvers.
- **A dataflow schematic with operators, design of experiments, robust design and reports** on results (ADR 0058, 0062-0064, 0071): Workbench's project page and Grasshopper's graph in one, open.
- **Honest refusals.** An adapter refuses what it cannot write instead of ignoring it, and problems carry codes an agent can act on. Keep the rule strict; the conformance review found a few places where it slipped.
- **No licence tokens:** every solver runs on any number of cores or machines, locally, in containers or on a cluster, from an installer that brings them all (ADR 0067, 0068).
