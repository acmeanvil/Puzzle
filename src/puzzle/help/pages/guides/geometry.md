---
title: Geometry
section: guide
order: 20
keys: panel:geometry, kind:geometry, section:geometry, tree:selection, kind:construction, section:construction geometry
summary: Import CAD or build a part from steps, name the faces you will load as selections, measure it exactly, and place paths and planes for results.
adrs: 0014, 0017, 0042, 0046
---

A geometry is a part's shape: an imported CAD file, or a history of steps you can edit
later. Loads, supports, materials and contacts all point at its **selections**, named
groups of faces, edges, bodies or points, so name those before you build the model.

## How to import a CAD file

1. Geometry → Import geometry… (or right-click the Geometry heading). STEP, IGES, BREP,
   STL, OBJ and 3MF are read.
2. The file is copied into the project and converted to metres. STL and OBJ files
   carry no unit: they are read as millimetres unless you change the geometry's source
   unit in Properties. A different file with the name of one already there comes in
   under a free name (`bracket-2.step`), so no geometry's file is ever replaced; a
   file that can't be read says so on the status bar and adds nothing.
   What a geometry's file holds is part of what its studies depend on: replace the
   file in the project folder and those studies read "out of date" (their results
   are kept, and stay readable, until you run them again).
3. A body selection called Body holds every body. A STEP assembly also gets one body
   selection per named part.
4. Its faces are named `face-1`, `face-2`, … in file order. Make selections of the ones
   you will use.

To add steps to an imported part, right-click it, Edit as history (keep the file as its
first step). Faceted parts (STL, OBJ, 3MF) take Combine with another faceted part….

<!-- key: form:new_part -->
## How to build a part from steps

1. Geometry → New part… (or the Geometry heading's right-click, or the Geometry tab's
   New part… button): one form choosing how it starts: **a sketch** (the first choice,
   on a plane you choose; the part is made when the sketch is finished), **a box**, **a
   cylinder**, or **a file** to import.
2. Choose a tool from the ribbon above the view, or from the Geometry menu's groups:
   **Create** (box, cylinder, extrude, revolve, hole, polyline, sheet…), **2D**
   (rectangle, circle, annulus, slot, Wall along a line, Section of a part…),
   **Sweep** (spline, Sweep along a path, Pipe along a path, Loft through profiles…),
   **Modify** (fillet, chamfer, move, rotate, mirror, pattern, split, Split face by
   sketch…) and **Prepare** (Heal, Remove features, Share faces, Check).
3. Its options open in the Properties panel and the view previews them as they change.
   Lengths are in the units shown; any number may be a parameter's name.
4. Apply adds the step. Nothing is written to the project before that.

Each step names the faces it makes: `<step>/<side>`, such as `Base/+z` or `Hole1/wall`.
Those names follow later edits, so selections built on them survive.

### Editing the history

The geometry's History folder in the tree lists its steps. Right-click a step: Edit…,
Insert step after, Turn off (suppress), Move up, Move down or Delete step.

## How to draw a sketch

1. Geometry → New sketch…: on a plane, or on a flat face you pick.
2. Draw with Line, Rectangle, Circle and Arc. Clicking near a point reuses it, so
   outlines join.
3. Constrain with Horizontal, Vertical, Parallel, Perpendicular, Equal, Coincident, Fix,
   Tangent, On and Midpoint. Dimension sets a length, distance, radius or angle, typed
   in the units shown or as a parameter's name.
4. The status line says how many degrees of freedom are left, or which constraints
   conflict. Finish sketch keeps it as a history step.

Rename the sketch's edges to what they are: their names become face names after an
extrusion (`Plate/bottom`).

## How to make selections by picking

1. Turn on Pick (P, or Geometry → Pick) and choose the filter in Geometry → Pick filter:
   faces, edges, bodies or points (regions in a 2D model).
2. Double-click entities in the view. B switches to box select.
3. Geometry → Selections → New from picks… names them as a selection.
4. To change one, right-click it in the tree: Edit in the view, Replace with picked,
   Add picked or Remove picked. Remove selection deletes it.

When a step edit or a new file leaves a selection without its entities, Geometry →
Selections → Reattach… lists what was lost and lets you pick again.

<!-- key: form:selection_rule -->
## How to make a selection by rule

A rule describes the members instead of listing them, and is evaluated again every time
the part changes, so the selection never detaches.

1. Geometry → Selections → Selection by rule… (or the pick bar's Select menu, or the
   right-click of a geometry's Selections folder).
2. Choose the **Part**, what it holds (**Of**: faces, edges, points or bodies) and the
   **Rule**: inside a box, near a point (within a distance of an entity's centre),
   facing a direction (flat faces within an angle), area between (faces), or the
   union, intersection or difference of selections of the same kind.
3. Fill its values in the units shown. When something is picked on the part, the point
   starts at the first pick's centre and the direction at a picked flat face's normal.
4. The line above OK counts what the rule picks as you type, and the view lights it.
   OK makes the selection (one change; undo takes it back).

## How to measure

Geometry → Measure (Ctrl+Shift+M), or Measure in the pick bar, then pick in the view.
Values come from the CAD shape, so they are exact and don't depend on the mesh.

- One pick: a point's position; an edge's length (and radius for a circle); a face's
  area, centroid, normal or radius, and its tightest curvature (a fillet's radius); a
  body's volume, surface area, bounding and oriented box, and sharp edges.
- Two picks: the minimum distance between them, the distance between centres, and the
  angle between two planes or two straight edges.
- More picks: the summed length, area or volume.

## How to hide or suppress geometry

Geometry → Show and suppress, or right-click a geometry:

- **Hide or show:** not drawn, still solved. Hide all others, Show all and Swap hidden
  work the same way.
- **Suppress or unsuppress:** left out of meshing and solving, with the loads, contacts,
  bolts, sections and materials on it.
- **Hide picked bodies:** pick with the Bodies filter first. **Colour…** and **Colour
  picked bodies…** change how parts look in the view.

## How to place a path or plane for results

Project → New construction geometry → Path… or Plane… (or right-click the Construction
geometry heading). A panel opens in Properties with a Pick button per point. The next
double-click on the model gives that point, snapped to a corner when close.

- A **path** runs between two points, or along a straight edge. Curved and closed edges
  are refused.
- A **plane** goes through a point facing a direction, on a picked face (through its
  centroid along its normal), or through three points.
- Path and Linearized stress tools follow a path; Result on a plane and Section force
  read a plane. Edit… on its right-click menu reopens the panel.

## Options worth knowing

- **Share faces** (a geometry setting, on by default): touching bodies of one part are
  joined when meshed, so they share nodes. Off: each body is meshed apart.
- **Mirror** keeps the copy fused by default (its faces named `<name>~m`).
- **Scale** with three different factors turns cylinders into general surfaces, so
  radii stop being exact. To fix a faceted import's units, set its source unit instead.

## Mistakes to avoid

- Trusting `face-7` after replacing an imported file: file-order names are positional.
  Check the selections and reattach.
- A sweep's profile must sit where the path starts, square to it: draw it on the plane
  whose normal is the path's first direction.
- A pipe radius larger than the path's tightest bend makes it cross itself.
- After a mirror that keeps the copy, faces on the mirror plane are gone: select the
  copy's `~m` faces instead.
- Measuring a thickness between centres: use the minimum distance between the two
  opposite faces.
- Reading sizes off the mesh: measure the geometry, which is exact.

## For agents and scripts

[geometry_measure](help:mcp:geometry_measure) measures named entities,
[geometry_check](help:mcp:geometry_check) finds gaps and slivers,
[selections_check](help:mcp:selections_check) says how selections resolve, and
[sketch_check](help:mcp:sketch_check) solves a sketch. Steps and selections are added
with [submit](help:mcp:submit). On the command line: `puzzle measure`,
`puzzle check-geometry` and `puzzle selections`.

## See also

- [Components](help:guides/components)
- [Meshing](help:guides/meshing)
- [Results](help:guides/results)
- [Schematic and operators](help:guides/schematic-and-operators)
