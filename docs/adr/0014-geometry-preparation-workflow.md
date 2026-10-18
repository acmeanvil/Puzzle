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
5. **Sketching** uses python-solvespace (GPL-3.0) for 2D constraints, feeding build123d sketches. It is used for profiles and for 2D/axisymmetric models.
6. **FreeCAD** stays an optional importer for `.FCStd` files, not the engine (ADR 0004).

## Consequences

- One history covers both CAD preparation and light modelling, and geometry parameters can sit in a sweep like any other parameter.
- Persistent naming is still the largest risk. The first prototype is a bracket with holes and fillets, driven through edits that renumber its faces: resize, remove a fillet, split, and re-import a changed STEP. It measures how many selections each tier resolves.
- Bulk reattachment and selection rules need GUI work, but they reduce the cost of a detachment on a large model to one review.
- Heavy direct-edit work (SpaceClaim's pull on arbitrary B-rep) will not match the commercial tool. OCCT's local operations cover offsets, face removal and simple moves.

## Alternatives considered

- Direct modelling without history (SpaceClaim as is): geometry could not be parametrised or swept.
- History-free re-import plus signature matching only: works for unchanged faces, but every real edit detaches selections.
- FreeCAD PartDesign as the history: mature, but brings FreeCAD's application, Python and Qt versions along (ADR 0004).
