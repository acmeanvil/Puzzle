---
name: coordinate-systems
description: coordinate systems (Cartesian, cylindrical, spherical): making one, from a face, edge or part, and using it for loads, material directions, beams, mesh boxes and results
adr: 0049
---
Read this before giving a load, a material or a result in local axes: radial or hoop
directions, a turned frame, a fibre direction.

A coordinate system is a project object of kind `axes`. Its `type` is `cartesian`
(x, y, z), `cylindrical` (r, θ, z: z the axis, θ anticlockwise about it from x) or
`spherical` (r, θ the polar angle from z, φ the azimuth about z). Lengths are in
metres, angles in degrees. Every input that names one has an `axes` field holding its
id; results start in global axes.

## Steps

1. `outline` with `ids: true`: the systems there, and the ids of geometries,
   components and parts you may derive one from.
2. Create one (one `submit`), choosing one way to orient it:
   - directions: `{"command": "create", "kind": "axes", "data": {"name": "Shaft",
     "type": "cylindrical", "origin": [0, 0, 0], "axis": [0, 0, 1], "x_axis": [1, 0,
     0]}}`; Cartesian takes `x_axis` and `xy_direction` (a direction in its xy plane);
   - rotations: `"angles": [0, 0, 30]` (degrees about x, then the turned y, then the
     turned z, from global);
   - three points: `"points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]` (origin, a point on x,
     a point in the xy plane);
   - from geometry: `"geometry": "<geometry id>", "entity": "face", "entity_name":
     "Shaft/wall"` (a planar face: its centroid, z its normal; a cylinder or cone: its
     axis; a sphere: its centre), or `"entity": "edge"` with `"along": 0.5` (x along
     the tangent there; a circle's axis as z);
   - attached to a part: `"component": "<component id>", "part": "Shaft"`: it moves
     with the part's placement;
   - relative: `"relative_to": "<axes id>"`: origin and axes given in that system.
   Give your own `"id"` (a new UUID) to use it in the same batch.
3. Use it:
   - a load or support: set the feature's `axes`
     (`{"op": "set", "path": "physics.0.features.1.axes", "value": "<axes id>"}`);
     values x, y, z then mean the system's axes (r, θ, z cylindrical). Skill
     `structural-loads` says which features take which type;
   - material directions: `materials.<i>.axes` on the component. Its x, y, z (or r, θ,
     z) are the material's 1, 2, 3 for orthotropic elasticity and anisotropic
     conductivity (CalculiX, Code_Aster; 3D);
   - a beam section: `"orient": "axes", "axes": "<id>", "axes_axis": "y"` (skill
     `beam-sections`);
   - a mesh box control: `"axes": "<id>"` on the box (Cartesian), corners in its axes;
   - an imported field's `placement.axes`.
4. Results in axes: `export(study, format, path, axes="<name>")` writes vectors and
   tensors rotated into the system per node (formats `paraview`, `point_cloud`; skill
   `exports`). In a cylindrical system the stress components are xx radial, yy hoop
   (θθ), zz axial, the displacement's x radial. Von Mises, principal stresses and
   magnitudes don't change. The GUI's results bar has an In axes dropdown.
5. Check a system with `get_object(name)`; a derived one is resolved at each run.

## Mistakes to avoid

- `result_stats`, `result_value_at` and the other result tools give global components
  only. For hoop or radial values use `export` with `axes`, or ask the user to pick
  the system under In axes. (In a 2D axisymmetric model the hoop stress is the global
  `zz` component already.)
- A spherical system on a load or a material is refused; Elmer and FEniCSx refuse
  orthotropic materials.
- A displacement in cylindrical axes with only some components given is CalculiX only.
- A system from a face or edge names the geometry's own entity names (as selections'
  members), not a selection's name. If that face goes away the run fails naming it:
  reattach or redefine.
- Relative systems may not loop back on themselves.
- Cartesian `x_axis` and `xy_direction` must not be parallel.
