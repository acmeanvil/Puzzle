---
name: planar-models
description: 2D models: plane stress with a thickness, plane strain, axisymmetric (Y the axis, X the radius); faces at z = 0 from a profile or a section of a 3D part; loads and totals
adr: 0048
---
Read this before building or solving a 2D model: a plate loaded in its plane, a long
section, or a part that is a solid of revolution (vessels, shafts, discs, flanges).

A component's `dimension` is `3d`, `2d_planar` or `2d_axisymmetric`. A 2D model lies in
the XY plane at z = 0. Axisymmetric: **Y is the axis, X the radius** (x ≥ 0). Planar:
`planar_model` is `plane_stress` (the default; a thickness per part) or `plane_strain`
(per metre of depth). Selections step down a dimension: a region (entity `face`) is a
body (materials, body loads), an edge (entity `edge`) is a face (supports, pressure,
convection), a point is a point.

## Steps

1. `outline` with `ids: true`: the mesh recipe's id, and a 3D geometry's id if the 2D
   part is its section.
2. The 2D geometry, a history (lengths in m), with its selections (one `submit`):
   - from a profile: `{"command": "create", "kind": "geometry", "data": {"name":
     "Wall", "history": [{"kind": "face", "name": "S", "args": {"profile":
     {"points": [[0.01, 0], [0.02, 0], [0.02, 0.1], [0.01, 0.1]]}}}], "selections":
     [{"name": "Region", "entity": "face", "members": ["S/face"]}, {"name": "Bore",
     "entity": "edge", "members": ["S/side-4"]}]}}`. Sides are `side-1`, `side-2`, …
     from the first point (here 1 bottom, 2 outer, 3 top, 4 bore); a sketch names them
     itself. `{"circle": [cx, cy, r]}` and `"holes": [...]` work too; another `face` step
     with `"mode": "cut"` (or `intersect`, `add`) combines regions.
   - a section of a 3D part: `{"kind": "section", "name": "Cut", "args": {"part":
     "<geometry id>", "plane": "xy", "offset": 0.0, "half": true}}`. The cut is laid in
     XY; `half` keeps u ≥ 0 (axisymmetric). Plane `xy` suits a part whose axis is Y,
     `xz` one whose axis is Z (x stays the radius). The region is `Cut/face`; each edge
     is named after the 3D face it cuts (`Cut/Bore/wall`), so it follows edits.
3. The component: `{"command": "create", "kind": "component", "data": {"name":
   "Vessel", "dimension": "2d_axisymmetric", "mesh": "<mesh id>", "parts": [{"name":
   "Wall", "geometry": "<geometry id>"}], "materials": [{"material": "<material id>",
   "selection": "Wall/Region"}], "physics": [{"type": "solid_mechanics", "features":
   []}]}}`.
4. Plane stress needs each part's thickness: `{"op": "append", "path":
   "shell_sections", "value": {"selection": "Wall", "thickness": {"value": 2, "unit":
   "mm"}}}`.
5. Features as in 3D (skill `structural-loads`), on edges and regions. `fixed` holds x
   and y. An axisymmetric model needs no condition on the axis, but must be held in Y
   somewhere (`displacement` with only `y`).
6. Mesh and run as usual. Quadrilaterals: the recipe's `element_shape: "hexahedra"`.
   Read the hoop stress of an axisymmetric model as `result_stats(study, "stress",
   component="zz")`.

## How loads and totals count

- Pressure and traction are per area, as in 3D.
- A `force` on an edge spreads over its area: length × thickness (plane stress),
  length × 1 m (plane strain), or 2π r dl (axisymmetric: the force is the **whole
  revolution's**).
- Reaction totals follow the same depths, but aren't labelled per 360° or per metre
  yet; check one against a hand total before quoting it.

## Solvers

CalculiX CPS/CPE/CAX (heat on CPS, axisymmetric heat DCAX), Code_Aster C_PLAN/D_PLAN/
AXIS (heat PLAN/AXIS), Elmer Cartesian 2D or Axi Symmetric, FEniCSx (2D statics and heat;
2D dynamics and body loads refused). Stress has six components on all four (`zz` the
hoop stress axisymmetric).

## Mistakes to avoid

- A part off z = 0 is refused (`not_planar`); an axisymmetric region at x < 0 is
  refused (`negative_radius`). Draw on the XY plane.
- A plane-stress part with no thickness section is refused.
- 2D models refuse beams, bolts (fasteners), remote loads, pretension and non-conformal
  contacts.
- Several regions in one geometry (two materials): a later `face` step with
  `"mode": "add"` fuses them into one region. Use `"mode": "separate"`: they share
  their edges and stay regions, each with its own selection and material.
- Axisymmetric means the loads are round too: a side load on a shaft needs 3D.
