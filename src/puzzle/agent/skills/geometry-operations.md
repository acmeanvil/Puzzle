---
name: geometry-operations
description: history steps from ADR 0065: spline paths and curved beams, sweep, pipe and loft, mirror and scale, and Measure's curvature, sharp edges, topology and oriented box
adr: 0065
---
A geometry's history (ADR 0014) takes these steps like any other: a `submit` that edits
the geometry's `history` list, or the Geometry tab's Sweep and Modify groups. Lengths
are in metres, and any number may name a parameter (a Slider drives it).

## Steps

- `spline`: `points` [[x, y, z], ...], `closed`, and optionally `start_tangent` and
  `end_tangent`. A smooth line body: a path for a sweep, or a curved beam (it meshes
  into line elements, as a `polyline` does).
- `sweep`: a `profile` (as an extrusion's, with `plane` and `offset`) drawn square to
  the path where it starts. The path is `path` points (a spline through them if
  `smooth`, the default; else straight segments) or `along` an earlier spline or
  polyline step. `frame` is `corrected` (the default: no twist), `frenet` or `fixed`.
  Faces: `<step>/start`, `<step>/end`, and the sides after the profile's edges.
- `pipe`: `r`, and `inner` for a tube, along `path` or `along`. Faces: `wall`, `bore`,
  `start`, `end`. Tubing, wires, and weld beads (a small pipe along an edge chain's
  points).
- `loft`: `sections`, each `{"plane", "offset", "profile"}`, two or more; `ruled` for
  straight sides. Faces: `start`, `end`, and the first profile's sides.
- `mirror`: a plane through `x, y, z` with `normal` (or `axis`). `keep` (default) fuses
  the copy, making a whole part from its half: the copy's faces are `<name>~m`, and
  faces on the plane disappear inside. `keep: false` gives the other hand, its names
  kept.
- `scale`: `factor`, or `fx`, `fy`, `fz` about `x, y, z`. A non-uniform scale turns
  cylinders into general surfaces, so radii stop being exact. To fix an import's units,
  set the import's unit instead.

## Measure (ADR 0042, with ADR 0065's queries)

`geometry_measure` and the Measure operator also give:
- a face's least radius of curvature (its sharpest bend: a fillet's radius, which sets
  the mesh size there);
- a body's topology (solids, shells, faces, edges, free edges: more than 0 free edges
  means a hole in the skin);
- its sharp edges (faces meeting at a corner, not tangent);
- its oriented box (length, width, thickness in its own directions).

The Measure operator's `measurement` takes `least_radius` and `sharp_edges` too.

## Mistakes

- A sweep's profile must sit where the path starts, square to it. Draw it on the plane
  whose normal is the path's first direction (`yz` for a path along x).
- A pipe's radius larger than the path's tightest bend makes it cross itself, and the
  step fails. Use a smaller radius or a gentler path.
- Lofted profiles should have the same number of edges, starting at matching corners,
  or the loft twists.
- After `mirror` with `keep`, selections on the mirror plane's faces have nothing left
  to select: pick the copy's `~m` faces or the outer ones.
