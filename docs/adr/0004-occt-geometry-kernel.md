# 0004. OCCT via OCP and build123d as the geometry kernel

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-17

## Context

Puzzle must create, import (STEP, IGES, BREP) and modify geometry, and keep selections attached to the right faces when the geometry changes. That persistence is what makes COMSOL's parametric workflow pleasant to use.

FreeCAD offers a full feature tree, sketcher and importers on the same kernel (OpenCASCADE), but it is a complete Qt application that is awkward to embed or run headless.

## Decision

- Use OpenCASCADE (OCCT) directly through the OCP bindings, with build123d for parametric construction.
- Support FreeCAD as an optional plugin for users who want its sketcher and PartDesign.
- Define named selections by persistent identity, not by face index:
  1. Tag faces with attributes when Puzzle creates them (OCAF labels and history from boolean and feature operations).
  2. For imported geometry, match faces after a change by a geometric signature (surface type, area, centroid, normal, neighbours), and flag ambiguous matches to the user.

### First implementation (Phase 1)

STEP import uses Gmsh's built-in OpenCASCADE kernel, the same kernel without a second binding. Entities are named `body-N`, `face-N`, `edge-N`, `vertex-N` in the order the file yields them, and each carries a signature (kind, measure, centre, bounding box). Signatures find entities again after placement and fragmenting; matching them across edits of the source file is the next step for persistent naming. OCP and build123d join when Puzzle creates and edits geometry itself.

## Consequences

- Clean embedding and headless use; geometry operations are scriptable.
- Puzzle must build its own sketch and feature-tree UI.
- Persistent naming is the single largest technical risk. It is prototyped in Phase 0 and tested against geometry edits in Phase 1.

## Alternatives considered

- FreeCAD as the engine: rich features, but heavy coupling to its application architecture.
- pythonocc-core: equivalent kernel access; OCP is chosen because build123d and CadQuery build on it.
- A mesh-only workflow (import meshes, no CAD): fails the "geometry creation and modification" goal.
