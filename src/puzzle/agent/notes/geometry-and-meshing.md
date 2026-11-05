# Geometry and meshing: what Puzzle has learned

Curated from Puzzle's own development notes: behaviour measured or decided while building the adapters. Read with the skills and ADRs.

## Gmsh is global state.

Always use `puzzle.geometry.gmsh_session()`; it holds a lock and sets OCC import units to metres. Don't nest sessions.

## OpenCASCADE is serialised

`geometry.session.occ_lock` (re-entrant) wraps `regenerate`, `read_cad`, `write_brep`, `resolve_geometry`, `check_geometry`, and every Gmsh session takes it before its own lock. Gmsh and OCP share one OCCT; concurrent GUI jobs (a preview and a selection check) failed inside it (`NCollection_IndexedMap::FindKey`, bodies lost while placing). New OCC work off the GUI thread must take it too.

## OCC pads bounding boxes

by ~1e-7 m. Plane tests need a tolerance of at least 1 µm.

## Entity names

(`face-3`) follow STEP file order; signatures (kind, measure, centre) re-find them after placement or fragmenting.

## Element node order

Gmsh differs from CalculiX, Elmer and VTK for tet10 (last two edge nodes swap). Adapters own the reordering; shared face lookups live in `adapters/common.py`.

## Custom PDE weak forms

are evaluated with `eval` and no builtins in `study.py` (`UFL_NAMES` plus the unknown, `v_<u>`, `<u>_n`, `dt`, coefficients, `x`, `dx`/`ds` taking selection names, `prop(...)`). That limits mistakes, not a determined attacker, so equations (and script operators) run only in a trusted project (`puzzle.trust`, ADR 0062 phase 4). dolfinx wants each cell tagged once, so the case mesh tags volumes by material selections (or parts) only.

## Code_Saturne's Gmsh reader

names groups by physical tag number, not name: zones select `"2"`, not `"Inlet"` (`write_med` returns the tags). Results are per cell (`location="element"`), matched to Puzzle elements by node-average centres (`match_points`, float32-tolerant).

## Local mesh sizes

are Gmsh `Constant` fields over the selection's entities (min-combined background field); point sizes alone barely refine curved bodies.

## History geometry

(`Geometry.history`, ADR 0014; operations listed in `geometry/history.py`'s docstring, profiles in `geometry/profile.py`) needs OCP (pixi env only; the `.venv` Python 3.14 has no OCP wheels): run `tests/test_naming.py` and `tests/integration/test_history_geometry.py` with `.pixi/envs/dev/bin/python -m pytest`. Lengths are SI; name box sides from geometry (OCCT's Left/Front/... faces don't follow the axes); use tight bounding boxes (`AddOptimal`); keep tool margins relative to the model size. OCCT's STEP/IGES writers assume millimetres (build test files in mm); `xstep.cascade.unit` is ignored in OCCT 8. Faces left split by a fuse are merged with `ShapeUpgrade_UnifySameDomain`, carrying names through its history. A cut removes a tool face lying on the part's surface (its name goes away).

## Selection names (G3)

edge and point names are joined face names (`A|B`, `A|B|C`) and are unique per entity type only (a seam edge is named after its one face), so key anything by (entity, name). File-order names (`face-7`) are positional: trust them only when their anchor agrees. Selections with a `rule` have members recomputed on every regeneration. The GUI preview key covers the files' size and time, so a replaced file is noticed.

## Sketches (py-slvs)

circle and arc constraints (`addCircle`, `addEqualRadius`, `addPointOnCircle`, `addDiameter`, `addArcLineTangent`) take no workplane argument; line and point ones do. `fix` (where-dragged) holds a point at its *stored* position, so the editor never drags a fixed point. SolveSpace distances are unsigned: which side a point stays on follows the starting positions, which is why solved positions are stored back. The SWIG binding's Python 3.12 deprecation warnings are filtered in pytest.

## Touching bodies of one part are joined when meshed

(`Geometry.share_faces`, on by default; `generate._share_faces`): STEP stores each solid with its own faces, so without it two touching bodies share no nodes (42 nodes on the Hertz cylinder's split, 21 positions). Overlapping bodies are refused. A face the join splits (a block's top under a smaller block) is re-found by its pieces (`generate._pieces`: same kind, inside its placed bounding box, measures adding up). Off: each body meshed apart.

## Part selections are matched within the part

`generate_mesh` finds each part's faces only among its own bodies' boundaries, since identical parts can have coincident faces (two halves end to end).

## Faceted geometry

(STL/OBJ/3MF): Gmsh's `classifySurfaces` with reparametrization cuts faces into re-meshable patches (a box's top into two); `load_facets(reparametrize=True)` maps patches back to the unreparametrized faces users select, and the mesher's name map holds lists. Discrete entities have no OCC mass/centre; `model._discrete_props` computes them from the triangles.

## Gmsh from threads

OpenCASCADE keeps per-thread state from its first user; if that thread exits and another thread uses Gmsh, the process segfaults. `gmsh_session` first initialises Gmsh on a resident daemon thread. MCP runs sync tools on worker threads, so this matters.
