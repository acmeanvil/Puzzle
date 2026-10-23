# Architecture decision records

Each significant design decision gets one short record here, numbered in order and never renumbered. A record that is replaced stays in place with status "Superseded by NNNN".

New record: copy [template.md](template.md) to `NNNN-short-title.md`, set status to Proposed, and open a pull request. It moves to Accepted when merged after discussion.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted |
| [0002](0002-gpl-3-or-later.md) | License Puzzle under GPL-3.0-or-later | Accepted |
| [0003](0003-python-core-pyside6-gui.md) | Python core, PySide6 GUI with a VTK viewport | Proposed |
| [0004](0004-occt-geometry-kernel.md) | OCCT via OCP and build123d as the geometry kernel | Proposed |
| [0005](0005-meshing-layer.md) | A separate meshing layer, Gmsh first | Proposed |
| [0006](0006-solver-neutral-model-ir.md) | Solver-neutral model IR with a capability matrix | Proposed |
| [0007](0007-solvers-out-of-process.md) | Solvers run out of process, in containers | Proposed |
| [0008](0008-results-storage.md) | Results in Delta Lake, one array per field and step (format 2); VTKHDF for export | Accepted |
| [0009](0009-project-model.md) | One model tree, references for sharing, transfers as the only schematic edges | Proposed |
| [0010](0010-mvp-solvers.md) | MVP solvers are CalculiX and Elmer; Elmer is the core multiphysics and EM solver | Proposed |
| [0011](0011-solver-shootout.md) | Solver shoot-out: one study, several solvers, compared | Proposed |
| [0012](0012-solver-lineup.md) | Solver line-up: CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne | Proposed |
| [0013](0013-packaging-and-platforms.md) | Platforms and packaging: pixi, native solvers first, containers for the gaps | Accepted |
| [0014](0014-geometry-preparation-workflow.md) | Geometry is imported CAD prepared through a parametric history; bulk reattachment | Accepted |
| [0015](0015-ai-agent-infrastructure.md) | AI-aware: one command layer for people and agents, MCP from the start, pluggable providers, autonomy levels | Accepted |
| [0016](0016-openfoam-second-cfd-solver.md) | Add OpenFOAM as the second CFD solver, paired with Code_Saturne in shoot-outs | Proposed |
| [0017](0017-geometry-workbench.md) | Edit geometry in a workbench: sketches as steps, tools inline with live preview, picks in the view | Accepted |
| [0018](0018-materials-as-sweep-choices.md) | A study's materials are a table: one material, or several run side by side and compared | Accepted |
| [0019](0019-mesh-workbench.md) | Mesh in a workbench with the full breadth of Gmsh and Netgen | Accepted |
| [0020](0020-material-builder.md) | A material builder for the global library, complete for every solver | Accepted |
| [0021](0021-remote-solvers.md) | Run solvers remotely: SSH hosts, HPC schedulers, later cloud batch | Accepted |
| [0022](0022-local-expert-ai.md) | Choose the "local expert" model by measurement on the user's machine | Accepted |
| [0023](0023-solver-settings.md) | Full access to each solver's settings: typed, with an expert pass-through | Accepted |
| [0024](0024-application-settings.md) | One application Settings dialog for per-user preferences, outside projects | Accepted |
| [0025](0025-derived-results.md) | Derived results: solution combinations (linear, envelope, SRSS, range) and custom equations | Accepted |
| [0026](0026-fatigue.md) | Fatigue: data in the material builder, a life tool in results | Accepted |
| [0027](0027-beams-shells-and-fasteners.md) | Beams and shells beside solids, starting with beam fasteners (Ansys/Nastran/Algor style) | Proposed |
| [0028](0028-buckling.md) | Buckling: a linear eigenvalue step, with or without a preload | Proposed |
| [0029](0029-context-and-names.md) | Show only what applies, by name: panels follow their context, and no raw value reaches the screen | Accepted |
| [0030](0030-project-unit-system.md) | A unit system per project, chosen when it is made | Accepted |
| [0031](0031-workspace-layout.md) | Give the model the room: collapsible docks, a view toolbar, an icon toolbar | Accepted |
| [0032](0032-result-probes.md) | Read results in the view: extreme markers, probes, and the range in Properties | Accepted |
| [0033](0033-tree-status.md) | The tree says what is missing, stale or done | Accepted |
| [0034](0034-selection-tools.md) | Selection tools: by tangency and angle, by box, by likeness, with modifier keys | Proposed |
| [0035](0035-load-symbols.md) | Draw supports and loads in the view | Proposed |
| [0036](0036-property-grid-dialogs.md) | Long editors as property grids with sections, a description box and errors on top | Accepted |
| [0037](0037-structural-loads-and-constraints.md) | Structural loads and constraints: body loads, remote loads, springs, couplings, local axes, amplitudes | Proposed |
| [0038](0038-results-cuts-animation-plots.md) | Results tools: section cuts, animation, and plots over steps | Proposed |
| [0039](0039-contact-search.md) | Find contacts between parts automatically | Proposed |
| [0040](0040-advisor.md) | An Advisor: the next step, from the project's state | Proposed |
| [0041](0041-structural-dynamics.md) | Structural dynamics: transient and harmonic response | Proposed |
