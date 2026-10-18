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
| [0008](0008-results-storage.md) | Delta Lake catalog, Parquet fields, VTKHDF view cache | Proposed |
| [0009](0009-project-model.md) | One model tree, references for sharing, transfers as the only schematic edges | Proposed |
| [0010](0010-mvp-solvers.md) | MVP solvers are CalculiX and Elmer; Elmer is the core multiphysics and EM solver | Proposed |
| [0011](0011-solver-shootout.md) | Solver shoot-out: one study, several solvers, compared | Proposed |
| [0012](0012-solver-lineup.md) | Solver line-up: CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne | Proposed |
| [0013](0013-packaging-and-platforms.md) | Platforms and packaging: pixi, native solvers first, containers for the gaps | Accepted |
| [0014](0014-geometry-preparation-workflow.md) | Geometry is imported CAD prepared through a parametric history; bulk reattachment | Accepted |
| [0015](0015-ai-agent-infrastructure.md) | AI-aware: one command layer for people and agents, MCP from the start, pluggable providers, autonomy levels | Accepted |
