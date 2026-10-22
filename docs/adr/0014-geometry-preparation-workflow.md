# 0014. Geometry is imported CAD prepared through a parametric history

- Status: Accepted
- Date: 2026-10-18

## Context

Most Puzzle models start as CAD from elsewhere (STEP, IGES, BREP, FreeCAD files) that must be cleaned up and simplified for analysis, with some light modelling: enclosures and air boxes, splits for loads and mesh control, simple added features. The reference tool for that work is SpaceClaim/Discovery, but with one difference: every operation lives in a history, with parameters, so that a change replays.

Geometry parameters should drive parametric sweeps. When a geometry change detaches selections, the user can reattach them, but on large models one-by-one reattachment becomes tedious, so detachment must be rare and repair must be quick.

ADR 0004 chose the kernel: OpenCASCADE through OCP, with build123d for construction. Gmsh stays the mesher and takes OCP shapes directly (`occ.importShapesNativePointer`, or `.brep` without loss).

## Decision

1. **A geometry is a feature history.** A `Geometry` holds an ordered list of operations, each with parameters that may reference a `ParameterSet`:
   - import;
   - primitive;
   - sketch → extrude, revolve or sweep;
   - boolean;
   - fillet and chamfer;
   - transform and pattern;
   - preparation operations (below).

   Regenerating the history produces the shape. A sweep point that changes a geometry parameter regenerates, then meshes.
2. **Preparation operations on the same history**, in the spirit of SpaceClaim:
   - heal (sew, fix small edges and gaps);
   - remove faces (fillets, holes, rounds, by size threshold or pick);
   - split body or face (by plane, face or sketch);
   - imprint;
   - share topology versus separate parts (conformal versus nonconformal, ADR 0009);
   - enclosure.

   Direct face moves or pulls are recorded as operations too, where OCCT supports them.
   Mid-surface extraction has no good open-source implementation. It is deferred; Puzzle would have to write its own.
3. **Selections survive regeneration in three tiers:**
   1. OCCT operation history (generated, modified or deleted per face) carries each `face-N` through every operation.
   2. Our existing geometric signatures re-find faces the history cannot account for, such as those from a re-imported source file.
   3. Faces neither tier resolves are flagged as detached, never silently guessed.
4. **Reattachment works in bulk.** Detached selections are listed together, each with ranked candidates (signature distance) and the members it kept. The user accepts all, accepts per selection, or picks. A reattachment is stored as a rule (for example "faces on plane x = 0" or "the cylindrical faces of radius 4 mm") where possible, so the next regeneration resolves it unaided. Rules are the same selection helpers scripts use today.
5. **Sketching** uses SolveSpace's constraint solver (GPL-3.0) for 2D constraints, through the py-slvs binding (amended 2026-10-22: python-solvespace has had no release since 2022 and no Python 3.12 build; py-slvs binds the same solver with wheels for every platform Puzzle supports). Sketches are profiles for extrude and revolve, and later for 2D/axisymmetric models.
6. **FreeCAD** stays an optional importer for `.FCStd` files, not the engine (ADR 0004).

## Prototype results (2026-10-19)

`puzzle.geometry.history` regenerates a history (box, hole, fillet, split) with OCP and names faces by construction. Names are carried by OCCT's Modified/Generated/IsDeleted history; fillet edges are picked by rules. `Geometry.history` is meshed by writing a BREP and matching names to its faces by signature (`tests/test_naming.py`, `tests/integration/test_history_geometry.py`).

| Edit to a bracket (4 selections) | Tier 1, history | Detached |
| --- | --- | --- |
| Hole radius 5 → 6 mm | all four, with the new geometry | none |
| Hole moved 5 mm | all four | none |
| Corner rounds 4 → 2 mm | all four | none |
| Rounds removed | three | "Rounds", correctly |
| Split at mid-length | all four; "Top" follows onto both halves | none |

| Re-import without history | Tier 2, signatures |
| --- | --- |
| Hole moved 0.5 mm | every face re-found |
| Rounds 4 → 2 mm | holes and top re-found; rounds and fixed end (area changed 10–75%) flagged |

End to end, a parameter edit re-meshes and re-solves with every selection on the right faces and no warnings. The edit also changes the study's content hash, so the stored result is not reused. Gmsh and OCP share conda-forge's OCCT (7.9.3), so shapes pass between them through BREP without loss.

## G1: parametric creation (2026-10-21)

`puzzle.geometry.history` now builds parts from primitives (box, cylinder, cone, sphere), profiles extruded or revolved (points, circles or a DXF drawing via ezdxf, with holes), booleans by `mode` (new, add, cut, intersect; a fuse merges the faces it leaves split along the seam), holes along any axis, fillets and chamfers by edge rules (axis, outer corners, the edges of a named face, the edges between two named faces, curved, all), move, rotate, linear and circular patterns of an earlier feature, splits by any plane, and an enclosure (an air box sharing its interface with the part, as its own named body). Every value may name a parameter. Faces are named by construction (`L/side-3`, `Bolt/wall`, `Pin/end`, `Air/+x`) and bodies too (`Body`, `Air`); the mesher maps both onto the meshed file by signature. Measured in `tests/test_geometry_ops.py` (areas and volumes against closed forms) and end to end in `tests/integration/test_history_geometry.py`: an angle bracket from an L profile, a bolt hole and a corner fillet, thickened from 8 to 12 mm through its parameter, keeps every selection with no warnings (toe deflection 40.7 to 11.2 um).

Files: STEP parts keep their assembly names (one body selection per part on import), IGES and BREP import through the same path, DXF drawings are profiles. IFC is dropped; STL, OBJ and 3MF come with G2 as mesh-only geometry.

## G2: preparing imported CAD (2026-10-21)

`puzzle.geometry.prep` adds history operations for CAD from elsewhere: `import` (STEP through OCCT's document reader, so assembly part names become named bodies; IGES; BREP; millimetres converted, a `scale` for files that declare the wrong unit), `heal` (sew faces within a tolerance, fix wires and faces, close shells into solids; face names carried through the sewing and fixing history), `remove` (OCCT defeaturing of fillets or holes below a radius, faces below an area, or named faces; the neighbours extend to close the gap and keep their names), and `fragment` (touching bodies split along their contact so they share faces, for a conformal mesh). A geometry check (`puzzle check-geometry`, MCP `geometry_check`) reports invalid shapes, surfaces that are not solids, gaps (free edges), short edges, tiny and sliver faces, overlapping bodies, and bodies that touch without sharing faces.

Faceted files (STL, OBJ, 3MF; `geometry/meshfile.py`) are read as triangles in metres (3MF states its unit; STL and OBJ default to mm) and handed to Gmsh, which groups them into faces by feature angle (`facet_angle`, default 40 degrees) and closes a volume. The faces users select come from that grouping alone; meshing re-classifies with reparametrization, which may cut a face into patches Gmsh can re-mesh, and maps each patch back to its face. A faceted part must be its component's only part, unplaced, for now; preparation (heal, remove) does not apply to it.

## G4: the history in the GUI (2026-10-21)

A geometry's steps are listed under it in the model tree ("2. hole Bore", "(off)" when suppressed). Right-click a geometry to add a step of any kind, convert a file geometry into a history whose first step imports the file (its face selections become `<step>/face-N`), check it (findings join the Problems tab), or start a new parametric part; right-click a step to edit, insert after, turn off or on, move up or down, or delete it. Each is one command (undoable, logged). The step dialog comes from `puzzle.geometry.steps`: lengths typed in millimetres, stored in metres, and any number may be a parameter's name; parameters are added from the tree. The preview rebuilds whenever the shape changes (its cache key covers the history and the parameters, not the selections); a failing step is reported by name. Selecting a step highlights the faces it made, a geometry no component uses is shown on its own, and the pick bar measures the picked faces' area and, for two, the distance between their centres. Its filter picks edges, points or bodies instead. Bodies of a history keep their persistent names ("Body"); edges and points were first stored by their file names (`edge-N`, `vertex-N`), and since G3 by the faces that meet there.

## G3: robust naming (2026-10-22)

`puzzle.geometry.naming` resolves every selection member on the regenerated shape, and the mesher, the GUI, the agent (`selections_check`) and the CLI (`puzzle selections`) all use the same path (`resolve_geometry`).

- **Edges and points are named by the faces that meet there.** The edge between "Plate/+x" and "Plate/+z" is "Plate/+x|Plate/+z", and a corner is "A|B|C"; a seam has its one face on both sides, and repeats get "#2" in midpoint order. These names last as long as the face names do: a resized hole keeps "Bore/wall|Plate/+z". Names are unique per entity type only (a seam edge and its face share one), so anchors are keyed by type too. Old file-order members (`edge-9`) still resolve, through the file's names.
- **Anchors** (`NamedSelection.anchors`: kind, measure, centre) record where each member was when it was picked. A name that is gone is re-found when exactly one entity of the same kind lies within 0.1% in measure and 1e-5 of the model size in position. Names that only count positions (`face-7` of an imported file) are checked against their anchor, since a replaced file can put another face there.
- **Rules** (`NamedSelection.rule`) describe members instead of listing them: a plane (`{"plane": {"axis": "x", "at": 0}}`), a radius (cylindrical faces, circular edges), a kind. A selection with a rule is evaluated on every regeneration and cannot detach.
- **Reattachment is one review.** Problems lists detached members as soon as a preview or a mesh is made, and the selections are re-checked in the background whenever they change. The preview cache now also notices a replaced file, by its size and time. "Reattach selections…" (right-click the geometry) shows every re-found or detached member with its candidates, nearest first. Where a simple rule picks exactly the new members it is offered, and checked by default. Accept applies everything as one undoable command; the CLI's `--accept-all` and the agent's `accept_all` command do the same.

Measured in `tests/test_selection_naming.py` and `tests/test_gui_geometry.py`:

| Case | Result |
| --- | --- |
| Hole radius 5 → 7 mm, edge selections on the rim and a top edge | both kept, by name |
| Vertical corners filleted | the rounded-away corner edge is detached; the top edges are kept, now 8 mm shorter |
| File replaced by an export from another tool (faces renumbered) | the end face and the rim edge are re-found under their new numbers; the bore, still `face-7`, is confirmed by its anchor |
| The same, with the bore moved 10 mm | the bore and its rim are detached, never guessed; the nearest candidate is the moved bore; Accept keeps the end as "on the plane x = 0" and the bore as "radius 5 mm" |

## G5: sketches (2026-10-22)

`puzzle.geometry.sketch` holds a sketch as data inside an extrude or revolve profile (`{"sketch": {...}}`). It contains named points, lines, arcs and circles, construction edges, and constraints (fix, coincident, horizontal, vertical, length, distance and its horizontal and vertical parts, parallel, perpendicular, equal, angle, radius, diameter, point on, midpoint, tangent, symmetric). Any dimension may name a parameter.

- **Solving.** SolveSpace solves the sketch on every regeneration, starting from the stored positions, so a parameter change moves it along the same branch. It reports the degrees of freedom left and the conflicting constraints. A sketch that does not solve, or whose outline is open, refuses to regenerate and names the cause ("Constraints 11 conflict", "not closed at points A, D").
- **Naming.** The extruded or revolved faces take the sketch edges' names, so selections read "Plate/right" and "Plate/bore" rather than "side-2".
- **The editor.** The GUI's editor (`gui/sketcher.py`, from "Sketch…" on a profile field) draws lines, rectangles, circles and arcs by clicks, reusing points within reach. Constraint buttons turn on for what is selected, and Dimension measures it, in mm or degrees or as a parameter's name. Constraints are listed in words, and the status line shows the degrees of freedom left (green once fully constrained, red with the conflicting constraints). Free points can be dragged, while fixed and dimensioned ones hold. Edges can be renamed, since their names become face names. A typed polygon opens in the editor as lines ready to constrain.
- **Agents.** The MCP tool `sketch_check` solves a sketch with the project's parameters before an agent submits it.

Measured in `tests/test_sketch.py`: a dimensioned plate solves to its dimensions; an obround of two lines and two tangent arcs extrudes to (40 x 10 + pi 5^2) x 2 mm^3 to 1e-9; a revolved rectangle gives 2 pi R A; a sketched plate meshes with "Plate/right" on x = W for W = 60 and 80 mm, with no warnings. `tests/test_gui_sketch.py` drives the editor by clicks and dimensions.

## Consequences

- One history covers both CAD preparation and light modelling, and geometry parameters can sit in a sweep like any other parameter.
- Persistent naming is still the largest risk. The first prototype is a bracket with holes and fillets, driven through edits that renumber its faces: resize, remove a fillet, split, and re-import a changed STEP. It measures how many selections each tier resolves.
- Bulk reattachment and selection rules need GUI work, but they reduce the cost of a detachment on a large model to one review.
- Heavy direct-edit work (SpaceClaim's pull on arbitrary B-rep) will not match the commercial tool. OCCT's local operations cover offsets, face removal and simple moves.

## Alternatives considered

- Direct modelling without history (SpaceClaim as is): geometry could not be parametrised or swept.
- History-free re-import plus signature matching only: works for unchanged faces, but every real edit detaches selections.
- FreeCAD PartDesign as the history: mature, but brings FreeCAD's application, Python and Qt versions along (ADR 0004).
