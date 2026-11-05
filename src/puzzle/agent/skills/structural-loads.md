---
name: structural-loads
description: supports and loads on a structure: fixed, displacement, force, pressure, remote loads, springs, gravity and spin, pretension, local axes, loads in some steps or over time
adr: 0037
---
Read this before adding or changing a support or load in a solid-mechanics physics.
Features live in `physics.<i>.features` of the component. Each has a `type`, a
`selection` ("Part/Selection", or a component selection's bare name) and `values`
(quantities: `{"value": 5, "unit": "kN"}`; any unit of the right kind). Give only the
values you mean: a missing displacement component stays free.

## The features (value names, default units)

| type | on | values | solvers |
| --- | --- | --- | --- |
| `fixed` | faces | none | all four |
| `displacement` | faces | x, y, z (mm); leave out free ones | all four |
| `force` | faces | x, y, z (N): the total over the face | all four |
| `pressure` | faces | p (MPa), positive pushes into the face | all four |
| `traction` | faces | tx, ty, tz (Pa) | all four |
| `remote_force` | faces | Fx, Fy, Fz (N), Mx, My, Mz (N*m); px, py, pz the point (default the centroid) | all four |
| `remote_force_rigid` | faces | as above, the face kept rigid | CalculiX, Code_Aster |
| `remote_displacement` | faces | ux, uy, uz (mm), rx, ry, rz (deg); rigid face | all four |
| `elastic_support` | faces | kn, kt (N/mm^3): stiffness per area | CalculiX, Code_Aster, Elmer, FEniCSx |
| `compression_only_support` | faces | kn (N/mm^3); may lift off (nonlinear) | CalculiX, Code_Aster |
| `bolt_pretension` | a section face in a solid bolt | F (kN) | CalculiX, Code_Aster |
| `bolt_adjustment` | the same | d (mm), a shortening | CalculiX, Code_Aster |
| `gravity` | bodies | gx, gy, gz (m/s^2), default gz −9.81 | all four |
| `acceleration` | bodies | ax, ay, az (m/s^2): the inertia −a | all four |
| `rotation` | bodies | omega (rpm), x0, y0, z0 (mm) a point on the axis, nx, ny, nz | all four |
| `body_force` | bodies | fx, fy, fz (N/m^3) | all four |
| `body_temperature` | bodies | T (degC): thermal strain | all four |
| `initial_stress` | bodies | sxx … szx (MPa) | CalculiX, Code_Aster |

`check_study` gives the real answer for one study and solver; `capabilities(solver)`
lists the feature types it takes.

## Steps

1. `outline` with `ids: true`, then `get_object(component)`: the physics' index and its
   features, the part and selection names.
2. A face or body selection that doesn't exist yet: an `edit` of the geometry appending
   to `selections` (`{"name": "Top", "entity": "face", "members": ["Plate/+z"]}`) in the
   same batch.
3. Add the feature (one `submit`):
   `{"command": "edit", "object": "<component>", "ops": [{"op": "append", "path":
   "physics.0.features", "value": {"type": "pressure", "selection": "Plate/Top",
   "values": {"p": {"value": 2, "unit": "MPa"}}}}]}`.
   Change a value: `{"op": "set", "path": "physics.0.features.2.values.p", "value":
   {"value": 3, "unit": "MPa"}}`.
4. **Local axes**: create a coordinate system (skill `coordinate-systems`) and set the
   feature's `axes` to its id. Then x, y, z mean the system's x, y, z, or r, θ, z for
   a cylindrical one. Taken by: `force` and `displacement` (Cartesian, everywhere),
   `displacement` cylindrical (all four with all three components; only some
   components: CalculiX only), `gravity`, `acceleration` and `rotation` (a rotation in a
   system turns about its z through its origin).
5. **Some steps only**: `"steps": [2, 3]` applies a feature in those solve steps (1 is
   the first); empty means every step. A pretension acts in its first step and is held
   at its length after.
6. **Over time**: `"amplitude": "<amplitude id>"` makes `force` or `pressure` follow a
   time table in transient steps (skill `dynamics`).
7. Run with `submit({"command": "run_study", "study": "<name>"})`. Check reactions with
   `result_total(study, "reaction_force", "<Part/Support>")`.

Bolts as beams between holes, with sizes and preloads: skill `bolts`.

## Mistakes to avoid

- No `fixed`, `displacement`, `remote_displacement`, elastic or compression-only support
  and no grounded bolt: a static or transient study is `unconstrained` and refused.
- Body loads need a density in every material (`incomplete_material`). On Elmer and
  FEniCSx put them on a material's body selection.
- `force` is the total on the face, not per node or per area; use `traction` or
  `pressure` for per area.
- A pretension needs a section face: the geometry's `split` step makes one
  (`<split>/section`). Keep the section clear of other parts. Elmer and FEniCSx refuse
  it, and so does a study with more than one structural step on Code_Aster.
- A compression-only support makes the step nonlinear (CalculiX adds NLGEOM).
- Spherical axes on a load are refused; a node on two faces with different axes is
  refused on CalculiX.
- 2D models refuse remote loads and pretension (skill `planar-models`).
