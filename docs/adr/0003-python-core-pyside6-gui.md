# 0003. Python core, PySide6 GUI with a VTK viewport

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-17

## Context

Most libraries Puzzle needs have first-class Python bindings: OCP and build123d (geometry), gmsh, meshio, deltalake, pyarrow, pyvista, and FEniCSx (dolfinx). The GUI needs a 3D viewport with picking of faces, edges and bodies, a model tree, property panels and a node graph.

## Decision

- Core in Python (3.11+), typed, with Pydantic models for the project schema.
- Desktop GUI in PySide6 (Qt for Python, LGPL) with a VTK viewport. (First through pyvistaqt; since 2026-10-21 VTK renders off screen and the image is painted in a Qt widget with Puzzle's own mouse handling, because on-screen OpenGL in Qt left unrefreshed rows on an Intel-graphics Mac.)
- Performance-critical paths (result mapping between meshes, large array transforms) may move to Rust (PyO3) or C++ once profiled.
- A web front end, if needed later, uses trame and reuses the same core.

## First implementation (2026-10-20)

`puzzle.gui` (`puzzle gui [PROJECT]`) is the baseline to build on: a model tree, a properties panel generated from each object's data (every edit is one `Edit` command through `Session`, so it is undoable and logged), a pyvistaqt viewport (mesh with the selected face selection highlighted, results with field, step and deformation controls), output, history and problems, and the agent panel (ADR 0015). Meshing and solving run on Qt's thread pool. The window watches `history.jsonl`, so changes made by the CLI or an outside agent appear without a reload. Added the same day: right-click menus that build a model from nothing (STEP import, mesh recipe, component, physics, features with defaults from `puzzle.model.catalog`, material assignment, studies and steps, ParaView export), each entry one command; and face picking (double-click in the view; the mesher reports which part and geometry face each Gmsh entity is) to create or extend named selections, highlighted at once without remeshing. A test builds and solves a cantilever entirely through these menus (tip deflection within 5% of beam theory). The geometry history (ADR 0014 G4) is edited in the tree with a step dialog, and the preview follows each edit. Later the same week: a selection filter in the pick bar (faces, edges, bodies, points). Edges are picked within 2% of the model's size of the click, points likewise, and bodies through any of their faces; edges are measured by length and two points by distance. On 2026-10-22, user-friendliness became a standing rule: properties with a fixed set of values are dropdowns of readable names (objects by name, "Solid mechanics", "CalculiX", "Fixed support"), the stored value being the id or key; ids are never shown, property names read as words, list items by their names; the tree, menus, history, Problems, the agent panel and result fields use the same labels; and a quantity in an unknown unit is refused where it is typed. Not there yet: box selection, the schematic (ADR 0009), sweeps and shoot-out reports.

## Consequences

- Fast iteration and direct use of the ecosystem above.
- GUI code stays separate from the core (`puzzle.gui` imports core; never the reverse), so the core can run headless and from scripts.
- Python start-up time and packaging (Qt, VTK and OCCT are large) need care; plan conda-forge and Pixi-based distribution.

## Alternatives considered

- C++ with Qt: fastest, but slower to build and fewer contributors.
- Electron or a web UI first: easier to distribute, but CAD-grade picking and large meshes are harder in the browser.
- Rust GUI toolkits: no mature equivalent of Qt plus VTK.
