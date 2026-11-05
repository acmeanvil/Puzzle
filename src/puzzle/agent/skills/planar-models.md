---
name: planar-models
description: 2D models (plane stress, plane strain, axisymmetric about Y): shapes, walls and sections at z = 0; loads, whole-part totals; structures, heat, dynamics, magnetics, CFD
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
   - shapes, each side named (region `<step>/face`), all taking `mode`, `plane`, `offset`:
     `rectangle` (dx, dy, corner x0, y0: sides `-x`, `+x`, `-y`, `+y`), `circle` (r, x, y:
     `outer`), `annulus` (r_inner, r_outer, x, y: `inner`, `outer`), `polygon` (sides, r to
     the corners, x, y, angle: `side-1`…), `slot` (length overall, width, x, y, angle:
     `side-1`, `end-1`, `side-2`, `end-2`).
   - a wall along a line, the quick way to draw a vessel's section: `{"kind": "offset",
     "name": "W", "args": {"line": [[0.1, 0], [0.1, 0.2]], "t": 0.01, "side": "right"}}`.
     `side` is `left`, `right` (as you walk along the line) or `both` (half each side);
     `closed: true` makes a ring. Edges `W/left`, `W/right`, `W/start`, `W/end`. Here the
     line goes up the bore at r = 0.1, so `right` puts the wall outward.
   - corners: `fillet` (r) or `chamfer` (d) on a part made of faces rounds its corners;
     `rule` `{"all": true}`, `{"outer": true}`, `{"between": ["W/left", "W/start"]}` or
     `{"near": [x, y]}`. New edges `<step>/edge-1`…
   - `mirror` and `pattern` work on faces as in 3D; `fragment` splits touching faces into
     regions sharing their edges.
   - imports: `{"kind": "import", "args": {"file": "plate.step", "flat": true}}` lays flat
     faces on XY (edges `edge-N` in file order). A DXF profile with several closed outlines
     makes several regions.
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
- Reaction and section totals are the **whole part's** on CalculiX and Code_Aster: the
  full 360° axisymmetric, per metre of depth in plane strain, through the thickness in
  plane stress. `probes()` says which as `totals`; quote it with the number.
- A section tool on a 2D model cuts a line, which stands for the ring or the depth. A
  linearization runs in the XY plane; its hoop is the out-of-plane stress.
- Axisymmetric results show revolved in the GUI (Settings > Results); the numbers are the
  section's either way.

## Solvers

CalculiX CPS/CPE/CAX (heat on CPS, axisymmetric heat DCAX), Code_Aster C_PLAN/D_PLAN/
AXIS (heat PLAN/AXIS), Elmer Cartesian 2D or Axi Symmetric, FEniCSx. Stress has six
components on all four (`zz` the hoop stress axisymmetric).

- Dynamics: modal, transient and frequency_domain steps on all four, as in 3D (skill
  `dynamics`). On CalculiX a 2D harmonic or modal transient takes edge forces square to
  the edge (or pressures) only, and an end load needs many modes (`modes` 100–200).
- Body loads: planar gravity/acceleration in XY and rotation about z; axisymmetric along Y
  and rotation about Y through x = 0. Temperature transfers work on all four.
- Magnetics (Elmer, FEniCSx; static and frequency-domain): axisymmetric as ADR 0047, or
  planar with currents along z: `coil_current` on a solid bar region, or `stranded_coil`
  with turns; a return conductor takes a negative current or phase 180. Put
  `magnetic_insulation` on the outer edge of an air region well beyond the conductors.
  Planar totals are per metre: joule_loss W/m, magnetic_energy J/m, resistance Ω/m,
  inductance H/m.
- CFD (OpenFOAM, Code_Saturne): a 2D component is solved one cell thick, nothing to do
  differently. Inlet, outlet, wall and symmetry on edges. Don't put an inlet or outlet on
  the axis edge (refused); a symmetry there is dropped. First-order meshes; quadrilaterals
  give the cleanest profiles. No swirl. Code_Saturne time-marches an axisymmetric case to
  steady: give it enough iterations (about 1000 for a pipe at Re 10).

## Mistakes to avoid

- A part off z = 0 is refused (`not_planar`); an axisymmetric region at x < 0 is
  refused (`negative_radius`). Draw on the XY plane.
- A plane-stress part with no thickness section is refused.
- 2D models refuse beams, bolts (fasteners), remote loads, pretension and contacts other
  than conformal bonds (shared nodes). Code_Aster also refuses elastic and
  compression-only supports in 2D; Elmer and FEniCSx loads in local axes.
- Filleting a corner two regions share is refused. An `offset` step's `offset` is the
  plane's position; the wall's thickness is `t`.
- `edge-N` names of an import are positional (file order): reselect after replacing the
  file. Select by side names (`Ring/inner`, `W/left`) where you can.
- Several regions in one geometry (two materials): a later `face` step with
  `"mode": "add"` fuses them into one region. Use `"mode": "separate"`: they share
  their edges and stay regions, each with its own selection and material.
- Axisymmetric means the loads are round too: a side load on a shaft needs 3D.
