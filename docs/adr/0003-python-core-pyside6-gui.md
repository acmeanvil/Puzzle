# 0003. Python core, PySide6 GUI with a VTK viewport

- Status: Proposed
- Date: 2026-10-17

## Context

Most libraries Puzzle needs have first-class Python bindings: OCP and build123d (geometry), gmsh, meshio, deltalake, pyarrow, pyvista, and FEniCSx (dolfinx). The GUI needs a 3D viewport with picking of faces, edges and bodies, a model tree, property panels and a node graph.

## Decision

- Core in Python (3.11+), typed, with Pydantic models for the project schema.
- Desktop GUI in PySide6 (Qt for Python, LGPL) with a VTK viewport through pyvistaqt.
- Performance-critical paths (result mapping between meshes, large array transforms) may move to Rust (PyO3) or C++ once profiled.
- A web front end, if needed later, uses trame and reuses the same core.

## Consequences

- Fast iteration and direct use of the ecosystem above.
- GUI code stays separate from the core (`puzzle.gui` imports core; never the reverse), so the core can run headless and from scripts.
- Python start-up time and packaging (Qt, VTK and OCCT are large) need care; plan conda-forge and Pixi-based distribution.

## Alternatives considered

- C++ with Qt: fastest, but slower to build and fewer contributors.
- Electron or a web UI first: easier to distribute, but CAD-grade picking and large meshes are harder in the browser.
- Rust GUI toolkits: no mature equivalent of Qt plus VTK.
