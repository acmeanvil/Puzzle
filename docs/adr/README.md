# Architecture decision records

Each significant design decision gets one short record here, numbered in order and never renumbered. A record that is replaced stays in place with status "Superseded by NNNN".

New record: copy [template.md](template.md) to `NNNN-short-title.md`, set status to Proposed, and open a pull request. It moves to Accepted when merged after discussion.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted |
| [0002](0002-gpl-3-or-later.md) | License Puzzle under GPL-3.0-or-later | Accepted |
| [0003](0003-python-core-pyside6-gui.md) | Python core, PySide6 GUI with a VTK viewport | Accepted |
| [0004](0004-occt-geometry-kernel.md) | OCCT via OCP and build123d as the geometry kernel | Accepted |
| [0005](0005-meshing-layer.md) | A separate meshing layer, Gmsh first | Accepted |
| [0006](0006-solver-neutral-model-ir.md) | Solver-neutral model IR with a capability matrix | Accepted |
| [0007](0007-solvers-out-of-process.md) | Solvers run out of process, in containers | Accepted |
| [0008](0008-results-storage.md) | Results in Delta Lake, one array per field and step (format 2); VTKHDF for export | Accepted |
| [0009](0009-project-model.md) | One model tree, references for sharing, transfers as the only schematic edges | Accepted |
| [0010](0010-mvp-solvers.md) | MVP solvers are CalculiX and Elmer; Elmer is the core multiphysics and EM solver | Accepted |
| [0011](0011-solver-shootout.md) | Solver shoot-out: one study, several solvers, compared | Accepted |
| [0012](0012-solver-lineup.md) | Solver line-up: CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne | Accepted |
| [0013](0013-packaging-and-platforms.md) | Platforms and packaging: pixi, native solvers first, containers for the gaps | Accepted |
| [0014](0014-geometry-preparation-workflow.md) | Geometry is imported CAD prepared through a parametric history; bulk reattachment | Accepted |
| [0015](0015-ai-agent-infrastructure.md) | AI-aware: one command layer for people and agents, MCP from the start, pluggable providers, autonomy levels | Accepted |
| [0016](0016-openfoam-second-cfd-solver.md) | Add OpenFOAM as the second CFD solver, paired with Code_Saturne in shoot-outs | Accepted |
| [0017](0017-geometry-workbench.md) | Edit geometry in a workbench: sketches as steps, tools inline with live preview, picks in the view | Accepted |
| [0018](0018-materials-as-sweep-choices.md) | A study's materials are a table: one material, or several run side by side and compared | Accepted |
| [0019](0019-mesh-workbench.md) | Mesh in a workbench with the full breadth of Gmsh and Netgen | Accepted |
| [0020](0020-material-builder.md) | A material builder for the global library, complete for every solver | Accepted |
| [0021](0021-remote-solvers.md) | Run solvers remotely: SSH hosts, HPC schedulers, later cloud batch | Accepted |
| [0022](0022-local-expert-ai.md) | Choose the "local expert" model by measurement on the user's machine | Accepted |
| [0023](0023-solver-settings.md) | Full access to each solver's settings: typed, with an expert pass-through | Accepted; built for all six solvers, with presets |
| [0024](0024-application-settings.md) | One application Settings dialog for per-user preferences, outside projects | Accepted |
| [0025](0025-derived-results.md) | Derived results: solution combinations (linear, envelope, SRSS, range) and custom equations | Accepted |
| [0026](0026-fatigue.md) | Fatigue: data in the material builder, a life tool in results | Accepted |
| [0027](0027-beams-shells-and-fasteners.md) | Beams and shells beside solids, starting with beam fasteners (Ansys/Nastran/Algor style) | Accepted; fasteners, beams and shells built |
| [0028](0028-buckling.md) | Buckling: a linear eigenvalue step, with or without a preload | Accepted |
| [0029](0029-context-and-names.md) | Show only what applies, by name: panels follow their context, and no raw value reaches the screen | Accepted |
| [0030](0030-project-unit-system.md) | A unit system per project, chosen when it is made | Accepted |
| [0031](0031-workspace-layout.md) | Give the model the room: collapsible docks, a view toolbar, an icon toolbar | Accepted |
| [0032](0032-result-probes.md) | Read results in the view: extreme markers, probes, and the range in Properties | Accepted |
| [0033](0033-tree-status.md) | The tree says what is missing, stale or done | Accepted |
| [0034](0034-selection-tools.md) | Selection tools: by tangency and angle, by box, by likeness, with modifier keys | Accepted |
| [0035](0035-load-symbols.md) | Draw supports and loads in the view | Accepted |
| [0036](0036-property-grid-dialogs.md) | Long editors as property grids with sections, a description box and errors on top | Accepted |
| [0037](0037-structural-loads-and-constraints.md) | Structural loads and constraints: body loads, remote loads, springs, couplings, local axes, amplitudes | Accepted |
| [0038](0038-results-cuts-animation-plots.md) | Results tools: section cuts, animation, and plots over steps | Accepted |
| [0039](0039-contact-search.md) | Find contacts between parts automatically | Accepted |
| [0040](0040-advisor.md) | An Advisor: the next step, from the project's state | Accepted; built |
| [0041](0041-structural-dynamics.md) | Structural dynamics: transient and harmonic response | Accepted; built on CalculiX, Code_Aster, FEniCSx and Elmer |
| [0042](0042-result-tools-and-measuring.md) | Result tools (reactions, results over selections, paths) and exact measuring in the geometry | Accepted |
| [0043](0043-exports.md) | Export the deformed shape as CAD (faceted, morphed B-rep, compensated) and results in neutral formats by physics | Accepted; built: 17 formats (ParaView, VTKHDF, Exodus II, UNV, EnSight, CGNS, Tecplot, Nastran, Abaqus, glTF, point, wall and Lorentz clouds, faceted and morphed shapes) |
| [0044](0044-imported-fields.md) | Imported fields: a wizard to bring them in (file, alignment, verification), features that take them with scaling and placement, connections shown three ways | Accepted; built (stages 1 to 3; a few open items) |
| [0045](0045-look-and-feel.md) | Look and feel: one theme from tokens (light, dark, high contrast), an icon set, docking, start page, feedback, accessibility | Accepted; built (tokens for every colour, engineering icons, toasts and progress with Cancel, density, text size, F6 and Tab order, theme screenshots) |
| [0046](0046-construction-geometry.md) | Construction geometry: paths and planes for result tools (linearization, results on a plane, section forces) | Accepted |
| [0047](0047-magnetodynamics.md) | Magnetodynamics: time-harmonic eddy currents first (Elmer, FEniCSx), then transient; losses, impedance, skin depth, heat and force couplings | Accepted; built |
| [0048](0048-2d-geometry.md) | 2D models: planar and axisymmetric geometry (sketches, shapes, sections of 3D parts), meshing and solving on the four FE solvers | Accepted; round 1 built (four solvers, section of a part) |
| [0049](0049-coordinate-systems.md) | Coordinate systems everywhere: Cartesian, cylindrical, spherical; typed, three points, from geometry, relative or attached; used by inputs and results | Accepted; built (orthotropic on Code_Aster and result tools' own axes open) |
| [0050](0050-bolt-manager.md) | Bolts: types (standard sizes and grades, preload), an automatic generator (picks, sets, rules, the agent), a bolt table with results along each bolt | Accepted |
| [0051](0051-calculix-with-pardiso.md) | Build CalculiX with MKL PARDISO (a pixi env and build task) and prefer it: 2-4x faster factoring, identical answers | Accepted |
| [0052](0052-gpu-wishlist.md) | GPUs for meshing and solving: researched, a wishlist (FEniCSx PETSc GPU first, then CalculiX GPU PaStiX); no work now | Accepted |
| [0053](0053-solver-cores-and-code-aster-speed.md) | Cores per run (physical cores by default) and parallel processes in Settings; Code_Aster sized from them, MUMPS options, MPI builds, full-load contact with automatic cutting | Accepted |
| [0054](0054-solve-progress-and-convergence.md) | Every solve's progress and convergence: typed events from each solver, a Solution tab (progress, convergence and time plots, result trackers, messages, stall warning), history kept with the run, diagnoses in the Advisor | Accepted; phase 1 built (CalculiX, Code_Aster) |
| [0055](0055-structural-sections-and-beam-orientation.md) | Standard structural sections (AISC v16.0, EN 10365/10056-1) for beams, from a shipped library; beams oriented by a vector, a coordinate system, toward a point, a face normal or an edge; Code_Aster GENERALE (all shapes), CalculiX BOX/PIPE (HSS, pipe) and exact Timoshenko springs (open shapes) | Accepted |
| [0056](0056-contact-tool.md) | A contact tool (as Ansys's): each contact's setup and what the solver is given, its initial state from the mesh (status, gap, penetration, overhang) drawn on the faces, contact results (pressure, status, sliding) per run, interface treatment (adjust to touch, offset), checks before the run | Accepted; built (Hertz on Code_Aster refused, open) |
| [0057](0057-agent-skills.md) | A skill for agents with every finished feature: Markdown how-tos in `agent/skills/`, the catalogue in the built-in agent's prompt, `skills_list`/`skill_read` over MCP; first `bolts` and `beam-sections` | Accepted |
| [0058](0058-project-schematic.md) | A project schematic (as Ansys Workbench's, with Blender-like nodes and ports): files, resources, components, studies and results as nodes, generated from the model; data wires (transfers, imported fields) and use wires (shared geometry, meshes, materials), all shown by default; positions remembered per user; state, upstream and downstream; drawing wires makes transfers; a dock and a tab; CLI, MCP, SVG | Built |
| [0059](0059-project-versions-and-copies.md) | Project versions to roll back to (made by Puzzle before runs, after changes, before restores, and by you; known good marked by successful runs; filtered by who, type, date, good and words; restore is one undoable command) and Save as New Project (a full, independent copy, outside files brought in) | Accepted; built |
| [0060](0060-mystran-for-nastran-testing.md) | MYSTRAN (open-source, reads Nastran decks) to verify the Nastran export against a Nastran-style solver, packaged as the other solvers; later perhaps an adapter. Further research after the first release | Proposed |
| [0061](0061-project-archives.md) | A project archive (`.puzzle`, one zip with a manifest and hashes) to share a project and open it in another Puzzle: model always, materials, results, versions and history by choice; checked on import (format version, hashes, safe paths) | Proposed |
| [0062](0062-operators-in-the-schematic.md) | Operators in the schematic, as Grasshopper's: project objects with typed ports (number, list, table, points, geometry, field, result, verdict); data operators (sliders, series, expressions, tables, list ops, panels), geometry from data (driving history arguments, points, polylines, patterns), result operators (reduce, filter, combine, check, plot, export), Python snippets in their own environment (numpy, scipy, polars, plus what the user installs) behind a project trust gate; pre-solve ones hashed, post-solve ones on demand; loops only as an optimisation study (0063) | Accepted; built (phases 1 to 4: result operators, data operators, geometry from data, Python scripts behind a trust gate) |
| [0063](0063-design-optimisation.md) | Design optimisation and design of experiments: an `optimisation` object driving a study like a sweep, its runs the study's own (cached, resumable); design variables from parameters (continuous, integer, choices) with bounds; objective and constraints from result operators (0062); scipy DOE (factorial, Latin hypercube, Sobol), COBYLA, Nelder–Mead, differential evolution, Bayesian (optuna), response surfaces; repeated shoot-outs; points table, plots, Pareto front, Apply best (suggested, never automatic); robust design to close it | Built (phases 1 to 4: design of experiments; COBYLA, Nelder–Mead, differential evolution; Bayesian and response surfaces; robust design with scatter, probabilities and Monte Carlo on the surface) |
| [0064](0064-dataframe-operators.md) | Dataframe operators: results as polars tables with a unit per column; sources (a run's field at nodes or elements, a sweep's or optimisation's points, probe histories, contact, bolt and mode tables, files); columns, filter rows, sort, top N, new column, convert units, group and aggregate, join, stack, pivot, rolling; out to a number, list, plot, export or field; lazy, post-solve; a Table viewer | Built (phases 1 to 4: tables as polars frames, sources, table operators, both expression syntaxes, CSV/Parquet/Excel, lookup, measure, list statistics and modes, verdicts, the seven groups, aliases, the Table viewer; join, stack, pivot, fill, rolling; mesh, project, run, probe, mode, contact and bolt tables; nearest, section, divide, clearance, text, preview, bar charts; tables into studies as imported fields, toggles, choices, 2D sliders, points merged and thinned, harmonic parts, dates, tags; clusters, recipes, snapshots) |
| [0064A](0064A-grasshopper-review.md) | Review of ADR 0064: Grasshopper's 817 core components against Puzzle, 106 rows by job with a tick box each (yes, later, no, change) and a column for the change; how to present the operators | Reviewed (outcome: where each accepted row is built) |
| [0065](0065-geometry-operations.md) | Geometry operations from the Grasshopper review: mirror, scale, splines (paths and curved beams), sweep, pipe, loft, patch, thicken, regions on faces, intersection points and curves, booleans on faceted meshes (manifold3d), SubD, mesh morphing for shape optimisation and B-rep deformation, Measure's curvature and topology queries | Built (phases 1 to 4: spline, sweep, pipe, loft, mirror, scale; Measure's least radius, topology, sharp edges, oriented box; mesh morphing for optimisation; patch, thicken, regions, intersections, DXF and sketch splines; faceted booleans (manifold3d), SubD from a cage, deform) |
| [0066](0066-mesh-adaptivity.md) | Mesh convergence and adaptivity: convergence studies with Richardson extrapolation and GCI, solver-neutral error estimates (Zienkiewicz–Zhu, gradient jumps), sizes from tables or runs as Gmsh background fields, adaptive refinement passes as stored runs | Built |
