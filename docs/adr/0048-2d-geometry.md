# 0048. 2D models: planar and axisymmetric geometry made, updated and solved like 3D

- Status: Accepted (2026-10-26, with the maintainer's answers); round 1 built 2026-10-31; rounds 2 to 5 built 2026-11-05
- Date: 2026-10-26

## Context

Many analyses are 2D, and cheaper and clearer for it:
- **axisymmetric parts:** pressure vessels, nozzles and flanges, shafts, discs, billets under an induction coil;
- **plane stress:** thin plates loaded in their plane;
- **plane strain:** long sections such as dams, pipes in the ground and extrusions;
- **2D heat, flow and magnetics:** sections of heat sinks, channels, inductors and busbars.

Pressure-vessel work in particular is axisymmetric, with stress classification lines through the wall (ADR 0042, 0046), and 2D magnetodynamics was accepted as the next stage of ADR 0047.

What exists:
- `Component.dimension` already takes `3d`, `2d_planar` or `2d_axisymmetric`, and Elmer, Code_Aster and FEniCSx list 2D in their capabilities. But meshing refuses anything but 3D (`mesh/generate.py`), so no 2D study can run.
- The geometry tools are 3D, with pieces 2D can use:
  - constrained sketches (G5, ADR 0017) whose sides are named;
  - flat faces from points (`sheet`, for shells);
  - DXF profiles (ADR 0014);
  - `split`, which cuts a solid by a plane;
  - parameters.

The maintainer asked for 2D geometry creation and updating sooner rather than later.

## Decision

1. **Where a 2D model lives:** in the XY plane at z = 0. For an axisymmetric model, **Y is the axis and X the radius** (x ≥ 0). That is the convention of CalculiX (CAX), Code_Aster (AXIS), Elmer (Axi Symmetric) and Gmsh, so nothing is turned on the way to a solver. The component's Dimension picks planar or axisymmetric. Parts must be faces lying in that plane, and a face reaching x < 0 in an axisymmetric component is a problem (`negative_radius`) before any run.
2. **Making 2D geometry**, in the Geometry tab, as a history like 3D (ADR 0014), so it regenerates when a parameter or an earlier step changes:
   - **from a sketch:** "Face from sketch" makes a region from a closed sketch; its edges keep the sketch's side names (G5), so selections survive edits;
   - **shapes:** rectangle, circle and annulus, polygon, slot; each names its sides (`-x`, `+x`, `inner`, `outer`, ...);
   - **2D operations:** union, cut and intersect of faces; fragment (several regions sharing edges, for several materials); fillet and chamfer of corners; mirror and pattern; and **offset**, a wall of a given thickness on either side of a line, the quick way to draw a vessel's section;
   - **from 3D:** "Section of a part", a 3D part cut by a plane (for an axisymmetric model, the plane through its axis). It is a history step that names the 3D part, so the section updates when the part changes;
   - **imports:** DXF regions (as profiles now) and planar faces from STEP and IGES.
3. **Selections in 2D** follow the dimension down:
   - a region (a face) is what a body is in 3D: materials and body loads go there;
   - an edge is what a face is: supports, pressure, convection and flux go there;
   - a point is what an edge or a point is: point loads and probes go there.

   The pick bar's filter offers Regions, Edges and Points. Every feature's selection kind maps the same way (`FeatureSpec.entity`), so the catalog needs no 2D copy.
4. **Meshing** uses Gmsh in 2D, with triangles or quadrilaterals of order 1 or 2 and the same controls (ADR 0019): sizes, refining near, transfinite edges, and boundary layers on edges (for walls, skin depths and boundary layers). Quality is measured in 2D. Netgen (2D) stays optional.
5. **Solvers write 2D in their own terms:**
   - **CalculiX:** CPS6/CPS8 (plane stress, with a thickness), CPE6/CPE8 (plane strain), CAX6/CAX8 (axisymmetric), DC2D/DCAX for heat. CalculiX expands 2D elements internally; results are mapped back to the 2D nodes, as for shells (ADR 0027).
   - **Code_Aster:** C_PLAN, D_PLAN, AXIS (and PLAN, AXIS for heat).
   - **Elmer:** Coordinate System = Cartesian 2D or Axi Symmetric; MagnetoDynamics2D and its harmonic version (ADR 0047).
   - **FEniCSx:** 2D meshes, and axisymmetric weak forms written with the radius in the integrals.
   - **CFD:** Code_Saturne and OpenFOAM need a mesh one cell thick (OpenFOAM's `empty`, or `wedge` for axisymmetric). That is a later stage.

   Plane stress takes a **thickness per part** (as shell sections do). Plane strain and axisymmetric need none.
6. **Results and their meaning:**
   - 2D results show flat in the view. An axisymmetric result can also be shown **revolved** (a sweep angle, 270° by default, as Ansys draws it) to read it as the part.
   - Probes, paths and stress linearization work on 2D results. In axisymmetric linearization the hoop stress is the out-of-plane component.
   - **Totals mean the whole part:** an axisymmetric reaction or force is for the full 360°; a plane-strain one is per metre of depth. Each is labelled so.
7. **Checks before claims:**
   - plane stress: **NAFEMS LE1** (an elliptic membrane; its reference is 2D) and a plate with a hole (Kirsch, Kt = 3);
   - plane strain: the Lamé thick cylinder;
   - axisymmetric: a thick cylinder and a sphere under pressure (Lamé), a rotating disc, and the linearization of a cylinder's wall against p·ri/t;
   - heat: a 2D and an axisymmetric conduction case;
   - each against the 3D model of the same part, and shot out across CalculiX, Code_Aster, Elmer and FEniCSx.
8. **Twins:** the same history steps through the command layer, MCP and CLI as 3D geometry.

**Order of work:**
1. Meshing and the four FE solvers on 2D faces made from existing sketches and sheets: plane stress, plane strain and axisymmetric structures and heat, with the benchmarks; and section of a 3D part;
2. the 2D shapes and operations, with named sides;
3. planar imports;
4. the revolved view and per-360° totals in the result tools;
5. 2D magnetics (ADR 0047), then 2D CFD.

## Built: round 1 (2026-10-31)

- **Geometry:**
  - `face` (Face from sketch) makes a flat region from a profile: a sketch, typed points, or circles and circular holes. Each side is named after the sketch's edge ("Ring/outer"; typed profiles side-1, side-2, ...).
  - Edge names ride in the history's name table with the face names, so every operation carries them through OCCT's history. `name_all` names edges an operation named before naming the rest by the faces they join.
  - `section` (Section of a part) cuts another geometry by a plane and lays the cut in XY at z = 0. `half` keeps the side at u ≥ 0, for an axisymmetric section. Each edge is named after the 3D face it cuts ("Cut/Tube/wall"), so loads on it follow the 3D part through its edits.
  - `ops_of(geometry, project)` hands a section the part's own operations, or its file. The cut part is a dependency, so editing it stales the section.
  - Both steps are in the Geometry tab's ribbon; the part to cut is a dropdown of the other geometries by name.
- **Meshing:**
  - A 2D component's parts must be faces. They are meshed by Gmsh in 2D, as triangles or as 8-node quadrilaterals (the recipe's hexahedra), and regions of one part share their edges.
  - Faces off z = 0 are refused (`not_planar`), as are axisymmetric faces at x < 0 (`negative_radius`). Nodes are snapped to exactly z = 0, since CalculiX refuses round-off.
  - Quality is measured on the 2D elements. `Mesh.planar` keeps a 2D model's faces from being taken for shells, and `Mesh.region_blocks()` returns them.
- **Model:**
  - `Component.planar_model` is plane stress (the default) or plane strain.
  - The plane-stress thickness comes from a thickness section per part (`common.plane_thickness`).
  - A force on an edge spreads over `common.load_area`: length × thickness, 1 m deep in plane strain, or 2π∫r dl axisymmetric. An axisymmetric force is the whole revolution's.
- **Solvers:**
  - **CalculiX:** CPS/CPE/CAX elements. It has no DC2D elements, so heat runs on CPS, or on DCAX for axisymmetric heat. Thickness goes on the `*SOLID SECTION`; edge loads are written by edge number; results come back at the 2D nodes (OUTPUT=2D).
  - **Code_Aster:** C_PLAN, D_PLAN and AXIS, and PLAN and AXIS for heat. C_PLAN works per unit thickness, so an edge force becomes F / load_area in `FORCE_CONTOUR`. Pressure edges are turned outward by ORIE_PEAU, because Gmsh doesn't orient 2D boundary lines.
  - **Elmer:** `Cartesian 2D` or `Axi Symmetric`, with `Plane Stress = True` in the Equation. Two displacement dofs, 2D element codes 303–408 and 202–203.
  - **FEniCSx:** the mesh is read with gdim = 2. Integrals are weighted by thickness, or by 2πr axisymmetric, with hoop strain u/r; plane stress uses λ* = 2λμ / (λ + 2μ).
  - On all four: stress is stored as six components (zz is the hoop stress axisymmetric; yz = zx = 0), and fixed holds x and y. What isn't written in 2D is refused by name: beams, fasteners, remote loads, pretension, non-conformal contacts, and on FEniCSx 2D dynamics and body loads.
- **Benchmarks** (`tests/integration/test_planar_benchmarks.py`, `test_section_solve.py`):

  | Case | CalculiX | Code_Aster | FEniCSx | Elmer |
  | --- | --- | --- | --- | --- |
  | Plane-stress strip in tension (σ, εx, εy) | within 0.2 % | within 0.2 % | within 0.2 % | within 0.2 % |
  | Kirsch, σx at the hole (exact 3.003 σ) | 3.046 σ | 3.033 σ | 3.030 σ | 3.030 σ |
  | Lamé plane strain, hoop at the bore (166.67 MPa) | 166.57 | 166.57 | 166.05 | 166.05 |
  | Lamé axisymmetric, hoop at the bore (166.67 MPa) | 166.33 | 166.43 | 166.92 | 166.91 |
  | Axisymmetric bar, axial stretch | exact | exact | exact | exact |
  | Planar conduction with a flux; axisymmetric conduction (log) | within 0.1 % | within 0.1 % | within 0.1 % | within 0.1 % |
  | A 3D tube's half section, Lamé (outer radial displacement) | within 0.5 % | within 0.5 % | within 0.5 % | within 0.5 % |

- **Left after round 1** (all built in rounds 2 to 5 below): the 2D shapes and operations, planar imports, the revolved view and per-360° totals, the pick bar's labels, 2D dynamics on FEniCSx with 2D dynamics benchmarks, 2D magnetics and CFD.

## Built: round 2, 2D shapes and operations (2026-11-05)

- `geometry/planar.py`: shapes as history steps, joined by `mode` (new, add, cut, intersect, separate), on `plane`/`offset`, the region `<step>/face`:
  - `rectangle` (dx, dy, corner x0, y0): sides -x, +x, -y, +y;
  - `circle` (r, x, y): `outer`; `annulus` (r_inner, r_outer): `inner`, `outer`;
  - `polygon` (sides, r to the corners, angle of the first corner): side-1..n;
  - `slot` (overall length, width, angle): side-1, end-1, side-2, end-2.
- `offset`: a wall of thickness `t` along a line, to its left, right or half each side, corners mitred; edges `left`, `right`, `start`, `end`; a closed line makes a ring. Refused: a wall thicker than a side or corner allows, a line turning back, a wall crossing itself. Lines are straight segments.
- Booleans keep the side names; `separate` regions share their edges; `fragment` works on faces; `mirror` of a flat part is one region; `pattern` repeats a shape.
- `fillet`/`chamfer` on a part made of faces round its corners (OCCT's `MakeFillet2d`), new edges `<step>/edge-i`; corners by `all`, `outer`, `between: [edge, edge]` or `near: [x, y]`; a corner two regions share is refused.
- Each region faces the plane's normal whichever way its outline runs, and the mesher turns clockwise 2D entities round before writing (`generate._counter_clockwise`): a region outlined clockwise had given CalculiX nonpositive Jacobians.
- The Geometry tab's 2D ribbon group: Face from sketch, Rectangle, Circle, Annulus, Regular polygon, Slot, Wall along a line, Section of a part; the mode choice gains "A region of its own".
- The schema's history kinds now list ADR 0065's steps too (they couldn't be saved before); a test checks the schema against the registered operations.
- Benchmarks (`tests/test_planar_geometry.py`, `tests/integration/test_planar_shapes.py`): areas and edge lengths exact for every shape, fillet, chamfer and wall; hoop at the bore against 166.67 MPa: plane-strain annulus ∩ rectangle CalculiX 166.57, FEniCSx 166.05; axisymmetric wall CalculiX 166.26, FEniCSx 166.95.

## Built: round 3, planar imports (2026-11-05)

- `import` with `flat`: flat faces from STEP, IGES or BREP laid on XY at z = 0 (a face square to z keeps its x, y; one square to x or y takes the YZ or XZ plane's u, v; others their own plane's axes). Solids, curved faces and faces in more than one plane are refused. A flat import names its edges `edge-N` in file order, as its faces are `face-N`.
- DXF drawn as closed outlines: several regions, fused so touching ones share edges, holes by nesting. DXF whose lines are shared or cross: the plane is split by its lines, every enclosed cell a region, edges named side-N after the line they lie on, stray lines dropped (an inner loop is then a region, not a hole).

## Built: round 4, results (2026-11-05)

- **Revolved view** (`post/revolve.py`): axisymmetric results are drawn turned about Y, 270° by default (Settings > Results > Axisymmetric results: revolved or flat; Revolved through: 90 to 360°). Triangles become wedges and quadrilaterals hexahedra, about 5° a segment; vectors turn with the section, so the warp is right; tensors keep their r, y, hoop slots. Probes and picks map back to the section at r = hypot(x, z). Planar results stay flat. The results store keeps no dimension, so a stored mesh is marked from its component (`revolve.mark_dimension`).
- **Totals mean the whole part.** Code_Aster's AXIS reactions were per radian (−1500 N for −9425 N) and its C_PLAN ones per unit thickness; its reader now scales them. CalculiX's were already whole-part. Reaction and section tools say what a total covers (`probes.totals_basis`: "for the full 360°", "per metre of depth", "through the part's thickness"), shown as a Totals row.
- Section and plane tools work in 2D: the cut is a line weighted by 2πr, the thickness or 1 m. 2D linearization takes the hoop as z (it took y before).
- The pick bar reads Regions, Edges and Points in a 2D component.
- Benchmarks (`tests/integration/test_planar_totals.py`, `tests/test_revolve.py`):

  | Case | Exact | CalculiX | Code_Aster |
  | --- | --- | --- | --- |
  | Axisymmetric tube, foot reaction (360°) | −9424.78 N | −9424.79 | −9424.78 |
  | Plane-strain strip, per metre | 400000 N/m | 399999 | 400000 |
  | Plane-stress strip, t = 2 mm | −400 N | −400 | −400 |
  | Lamé cylinder, linearized hoop membrane p·a/t | 100 MPa | 99.984 | 100.007 |
  | Lamé cylinder, axial force across a cut | 18849.6 N | 18849.5 | 18851.7 |

## Built: 2D dynamics, body and temperature loads (2026-11-05)

- Modal, transient (Newmark, HHT) and harmonic steps in plane stress, plane strain and axisymmetric models on all four FE solvers. FEniCSx assembles thickness- or 2πr-weighted mass and stiffness once (`solve_solid_2d`; `solve_transient`/`solve_harmonic` take two dofs per node).
- CalculiX loses point loads on 2D elements in steps solved over modes ("1-D or 2-D elements may cause problems in steady state calculations": the bar's end moved 760× too little), so in harmonic and modal-transient steps an edge force square to the edge is written as the pressure −F·n/A; other directions are refused.
- Body loads (gravity, acceleration, rotation) in 2D on all four: planar in the XY plane and spin about z; axisymmetric along Y and spin about the axis.
- Temperature loads in 2D on all four. Elmer's plane strain leaves out the out-of-plane thermal strain (off by 1/(1+ν)), so α(1+ν) is written and σz corrected on reading; materials of different E·α in one plane-strain model are refused there for now.
- 2D models refuse unwritten contacts on CalculiX too (only conformal bonds), as on Code_Aster and Elmer.
- Benchmarks (`tests/integration/test_planar_dynamics.py`, 49 cases, all four solvers): axial modes in plane stress and plane strain against Rayleigh–Love (to 0.02 %), cantilever bending 208.6 Hz against Euler–Bernoulli 208.88 less shear, ring breathing 8233.0 Hz (8231.8), sudden end load peak 1.8 % under 2× static (sampling at T/200), harmonic end response within 0.1 % (CalculiX 1.2 % with 200 modes), rotating disc σr and σθ, hanging strip, heated strips and rod exact.

## Built: round 5, 2D magnetics and CFD (2026-11-05)

- **Planar magnetics (A_z)** on Elmer (MagnetoDynamics2D/Harmonic, Cartesian 2D) and FEniCSx, static and frequency-domain, through the axisymmetric path (`eddy` with `planar`): currents along z (a return conductor carries a negative current or a 180° phase), windings J = N I / area, solid bars by superposition of unit-drop bases (1 V/m), B = (∂A/∂y, −∂A/∂x). Totals are per metre of depth (W/m, J/m, Ω/m, H/m). Transients are refused. The skin-depth checks at a solve's start now read 2D elements (they had failed on every 2D mesh and been skipped).

  | Case (copper wire a = 5 mm, 100 A) | Exact | Elmer = FEniCSx |
  | --- | --- | --- |
  | DC energy per metre | 2.5526 mJ/m | 2.5491 |
  | DC resistance | 2.1952e-4 Ω/m | 2.1966e-4 |
  | AC R/R_dc at a/δ = 2.5 (Bessel) | 1.5050 | 1.5067 |
  | Winding inductance | 8.1683 µH/m | 8.1572 |
  | Two-wire line loop inductance | 8.1598e-7 H/m | 8.1487e-7 |

- **2D CFD** on OpenFOAM and Code_Saturne: the 2D mesh is extruded one cell thick in the adapter (`mesh/layer.py`). Planar: front and back `empty` (OpenFOAM) or symmetry (Code_Saturne). Axisymmetric: a 5° wedge about Y, OpenFOAM `wedge`, Code_Saturne symmetry (axisymmetry without swirl); cells on the axis collapse, a quad with corners on the axis is cut in two. Edge selections become the swept faces under the same names; an edge on the axis has no faces, so a symmetry or wall there is dropped and anything else refused. Results come back per 2D element (each layer cell keeps its element's id, matched one-to-one by nearest centre). Code_Saturne's steady algorithm runs away on a wedge, so with no pseudo time step it time-marches at 0.5 h/U. OpenFOAM's first-order check now names prisms `wedge6` (it refused prism boundary layers).

  | Case | Exact | OpenFOAM | Code_Saturne |
  | --- | --- | --- | --- |
  | Plane Poiseuille, peak u/U | 1.5 | −0.10 % | −0.21 % |
  | Plane Poiseuille, dp/dx | −0.12024 Pa/m | −0.34 % | −0.45 % |
  | Hagen–Poiseuille (axisymmetric), peak u/U | 2.0 | −0.25 % | −0.18 % |
  | Hagen–Poiseuille, dp/dy | −0.32064 Pa/m | +0.22 % | +0.17 % |
  | Heated plates, bulk dT/dx | 110.081 K/m | +0.23 % | +0.22 % |
  | Heated plates, Nu 8.235 profile | | 0.19 % | 0.09 % |

## Still open

- Offset walls of arcs or splines; the new shapes solved on Code_Aster and Elmer (only CalculiX and FEniCSx were run).
- Code_Aster's live reaction trackers in 2D show per-radian values; a node shared by plane-stress parts of different thicknesses takes the first part's; the revolved view's camera isn't set Y-up; derived results and the Compare window show 2D results flat.
- 2D magnetic transients; transient 2D CFD; swirl.
- On CalculiX, oblique edge forces in 2D steps solved over modes; FEniCSx 2D displacements from imported fields and loads in local axes.

## Consequences

- Vessels, shafts, discs and sections run in seconds instead of minutes, with meshes fine enough to trust a linearization.
- Every adapter gains 2D element and modelling choices; each is claimed only after its benchmark passes (CLAUDE.md).
- Selections and features gain a dimension-aware mapping, which the GUI, the Advisor and the agent must use consistently.
- "Section of a part" ties a 2D part to a 3D one: changing the 3D part makes its 2D studies out of date, as any input does.

## The maintainer's answers (2026-10-26)

1. **Axis:** Y is the axis and X the radius.
2. **First round:** meshing and the solvers on faces from existing sketches and sheets; the 2D shapes and operations second.
3. **Section of a 3D part:** in the first round.
4. **Revolved view:** shown by default for axisymmetric results, with a preference to turn it off (Settings > Results > Axisymmetric results: revolved or flat, and the sweep angle).
5. **Plane-stress thickness:** per part.

## Alternatives considered

- **2D as thin 3D slices:** no new code, but the wrong physics: no axisymmetric hoop stress, plane strain only approximately, and meshes many times larger.
- **A separate 2D geometry module:** OpenCASCADE handles planar faces, and the history, naming and selections are the same; one module keeps them in step.
- **Z as the axisymmetric axis:** familiar from cylindrical coordinates, but every solver Puzzle uses takes Y, so each adapter would rotate on the way in and out.
