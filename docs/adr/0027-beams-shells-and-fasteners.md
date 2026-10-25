# 0027. Beams and shells beside solids, starting with beam fasteners

- Status: Accepted (2026-10-24); beam fasteners (2026-10-24), beams and shells (2026-10-25) built on CalculiX and Code_Aster
- Date: 2026-10-22

## Context

Puzzle meshes and solves solids only (second-order tets). Structural models also need:

- **Fasteners in solid assemblies:** bolts, pins and rivets. Meshing every bolt as a solid, with threads and contact, is slow and rarely needed. The maintainer asked for beam fasteners like those in Ansys, Algor and Nastran (2026-10-22):
  - **Ansys Mechanical:** a *Beam Connection* is a circular beam between two faces or edges. Each end is joined to its face by a spider of constraint equations, either rigid or deformable, within a pinball radius. *Bolt Pretension* adds a preload (a force, or an adjustment length) in a first load step, then locks it. Beam probes report the axial force, shear forces and moments.
  - **Nastran:** CBAR and CBEAM beams, or CBUSH springs, attached to the hole faces through RBE2 (rigid) or RBE3 (distributing, adds no stiffness) spiders. CFAST and CWELD are dedicated fastener elements.
  - **Algor:** a bolt element is a beam between two hole surfaces with a preload, made in a few clicks from the holes.
- **Frames and thin walls:** line bodies with a cross-section (beams), and sheet bodies with a thickness (shells), mixed with solids.

What the solvers offer:

| Solver | Beams | Shells | Joining them to solids | Preload |
| --- | --- | --- | --- | --- |
| Code_Aster | `POU_D_T`, `POU_D_E` on SEG2 elements, with section libraries (`AFFE_CARA_ELEM`) | `DKT`, `DST`, `COQUE_3D` | `LIAISON_ELEM` (`3D_POU`, `COQ_POU`, `COQ_3D`); `LIAISON_RBE3` (distributing); `LIAISON_SOLIDE` (rigid); `LIAISON_MAIL` for meshes that don't match | `PRE_EPSI` (an imposed axial strain) on the beam |
| CalculiX | `B31`, `B32`, expanded internally into solid elements joined by "knots" | `S3`, `S4`, `S6`, `S8`, also expanded | `*COUPLING` with `*KINEMATIC` (rigid) or `*DISTRIBUTING` to a reference node; `*RIGID BODY` | `*PRE-TENSION SECTION` (beam elements take `ELEMENT=`) |
| Elmer | beam and shell solvers | ShellSolver, recently coupled to solids | less mature | none known |
| FEniCSx | a custom mixed-dimensional formulation | the same | not built in | none |

The details of CalculiX's knots and couplings, and how beam rotations meet a coupling's reference node, need trying before they are relied on.

## Decision

1. **Fasteners come first, as component connectors without geometry.** A component gains `connectors`. A *beam fastener* has:
   - **Ends:** two selections, usually the hole faces, or edges, or points.
   - **Section:** a circle given by its diameter, or a tensile stress area for bolts.
   - **Material:** a project material.
   - **End behaviour:** rigid, like RBE2 or Ansys rigid, which stiffens the hole; or distributing, like RBE3 or Ansys deformable, which spreads the load without stiffening. An optional pinball radius limits which nodes the spider takes.
   - **Pretension (optional):** a force or an adjustment, applied in the first step and then locked, as Ansys does.

   No geometry is made. The mesher adds the beam as line elements between the ends' centres: the axis of a cylindrical hole at the face's mid-plane, or else the centroid. The default is one element, with a setting for more. Each end's spider covers the nodes of that end's selection.
2. **Making fasteners is quick, as in Algor.** You pick two hole faces and choose "Fastener". "Fasteners from holes…" pairs coaxial holes across parts, with a preview, and makes them all in one command. Fasteners appear in the tree under the component's Connectors, with readable names ("Bolt M10, Plate/Hole 3 → Bracket/Hole 1").
3. **Each adapter writes them, or refuses them.**
   - **Code_Aster:** `POU_D_T` with a circular section; `LIAISON_ELEM(OPTION='3D_POU')` or `LIAISON_RBE3` for the ends; pretension by `PRE_EPSI` set from the force, in a first step.
   - **CalculiX:** `B31` with `*BEAM SECTION, SECTION=CIRC`; `*COUPLING` for the ends; `*PRE-TENSION SECTION` for the preload.
   - **Elmer and FEniCSx:** refused (`unsupported`) until a benchmark passes, as ADR rules require.
4. **Results per fastener.** Each fastener reports its axial force, its shear forces and its bending and torsional moments, at each end and each step. These come in a table (as Ansys beam probes) and as a global field per fastener, so combinations (ADR 0025) and agents can use them. A bolt check (tensile stress against proof strength, plus a separation warning) can follow. The view draws fasteners as tubes coloured by axial force.
5. **Line and sheet bodies follow, in this order.**
   - **Beams:** geometry line bodies with a cross-section from a section library (rectangle, round, tube, I, channel, angle) and an orientation.
   - **Shells:** sheet bodies, or a solid's mid-surface, with a thickness and an offset.

   Both join solids where they share nodes, or through the couplings above where they don't. The `Mesh` gains beam and shell element blocks (SEG3, TRI6), and results gain section quantities (forces and moments for beams; membrane, bending and top and bottom stresses for shells).
6. **Benchmarks before claims.**
   - **Fasteners:** two plates joined by one beam fastener under shear and tension, with the fastener force from statics exact; a pretensioned bolt holding its preload; a solid block hung from a beam fastener against beam theory.
   - **Beams:** a cantilever and a portal frame against closed form.
   - **Shells:** NAFEMS and MacNeal-Harder cases, such as the Scordelis-Lo roof and the pinched cylinder.

## Built: beam fasteners (2026-10-24)

- **Model:** `Component.connectors`, a list of `BeamFastener`: a name, two face selections (`source`, `target`, usually hole faces), a diameter, a project material, rigid or distributing ends, and `elements` (along the length). It is part of the component's run key (its material is a dependency) and of `used_selections`.
- **Where it runs:** between its two ends' centres. Each end is its selection's nodes weighted by ∫N dA (`common.fastener_end`); a hole face's centre lies on its axis.
- **A small solid bolt, not a beam element**, on both solvers: a round bar of quadratic tets meshed by Gmsh (`common.bolt_mesh`).
  - **What failed on CalculiX:** B31 refuses round sections (B32 needs them, with CIRC taking the diameter twice), and CalculiX allows no beam node in a rigid MPC. Tying a beam end's rotations by `*EQUATION` left the joint near a hinge: a side load moved the end 15 mm, between the guided (4.8 mm) and pinned (19 mm) values, and two plates in shear were a mechanism. `*DISTRIBUTING` on the beam node left it free to rotate too.
  - **What the solid gives:** the fastener's forces and moments are exact, by equilibrium. They are the sums of the reactions on the bolt's end-face nodes, and their moments about the end's centre (`common.fastener_values`). Its stiffness is the solid's. Beam elements stay the plan for frame members (point 5), where Code_Aster's `POU_D_T` is the better path.
- **Ends:**
  - **Rigid:** the hole's nodes and the bolt's end face form one rigid body (CalculiX `*RIGID BODY`, Code_Aster `LIAISON_SOLIDE`). A rigid end sharing nodes with a fixed or displaced selection is refused with the fix (the solver would see one dof held twice).
  - **Distributing (RBE3):** the bolt's end face is a rigid body with a translation and a rotation node; the hole follows them on average. On CalculiX these are RBE3 relations written as `*EQUATION`, solved for six face-node dofs chosen by pivoting, so each equation has one dependent dof. With the helper nodes dependent CalculiX printed no reaction; with face dofs dependent in several equations its MPC decascading ran for ten minutes. On Code_Aster it is `LIAISON_RBE3` to a master point (a `DIS_TR` discrete of negligible stiffness) tied to the bolt end by `LIAISON_SOLIDE`.
- **Results:** a whole-model field `fastener` per step, one row per fastener: axial force (tension positive), shear force, torsion, bending moment, and the force and moment components the bolt puts on the part at its first end. CalculiX prints the bolt end faces' reactions to the `.dat`; Code_Aster's come from `REAC_NODA`, read before the bolt's nodes are dropped from the results.
- **GUI:**
  - a component's menu has Add fastener… (two face selections, the diameter in the display unit, a material, the ends) and, with two or more parts, Fasteners from holes…;
  - Fasteners from holes… pairs coaxial cylindrical faces of different parts (`geometry/holes.py`: axis lines within 0.1 % of the model's size, radii within 25 %), takes the smaller hole's diameter, and makes a "Hole n" selection per hole and the fasteners in one batch;
  - the tree lists each fastener under its component ("Fastener Bolt 1 (Ø 8 mm): Lower/Hole 1 → Upper/Hole 1"), the outline names it, and the view draws it as a tube of its diameter.
- **Refused:** Elmer and FEniCSx (`Capabilities.connectors`); fasteners in Code_Aster thermal studies.
- **Benchmarks** (`tests/integration/test_fasteners.py`, CalculiX and Code_Aster, rigid and distributing ends):

  | Check | CalculiX | Code_Aster |
  | --- | --- | --- |
  | Hanging block: axial force = weight | exact | exact |
  | Hanging block: stretch against F L / E A (rigid) | −0.7 % | −0.7 % |
  | Plates in shear: shear force = load | exact | exact |

- **Pretension** (2026-10-25): a fastener's optional `pretension` (a force). Its bolt is meshed in two halves sharing a mid-plane, cut there as ADR 0037's bolt section: twins for the far half, u_twin − u = axis × u_ref. The force goes in at the first static structural step and is locked after (CalculiX). Code_Aster runs each step as its own job, so it takes it in one-step studies and refuses it in longer ones.
  - Benchmark: a block hung on a tightened fastener over a support as stiff as the bolt. Step 1 carries exactly F on both solvers. Step 2, locked, with P pulling the block down: F + P/2 within −0.2 % (CalculiX).
  - Add fastener… has a pretension field (empty: none).
- **Fixed on the way:** Code_Aster numbers nodes in the order the MED file lists them, so helper nodes written out of id order (a reference node before the bolt's) shifted the result tables by one; the written mesh now sorts its nodes by id. A distributing end's RBE3 relations no longer pick a held node as a dependent dof, and refuse a face that is all held.
- **Over results:** the view draws each fastener as a tube, red in tension and blue in compression, labelled with its axial force in the display unit.
- **Note:** a distributing end on a large face (a plate's whole side) writes dense relations that CalculiX solves slowly (minutes); hole faces are small.
- **Not yet:**
  - a pinball radius;
  - a bolt check against proof strength;
  - the MCP twin of Fasteners from holes…;
  - shells (point 5): built 2026-10-25, below.

## Built: beams (2026-10-25)

- **Geometry:** a `polyline` history step (points in metres; the GUI's step form takes mm as "x, y, z; x, y, z") makes a wire, one edge per segment. Shapes without faces get positional names (`edge-1`, `vertex-2`), and point selections by rule (a plane) find the frame's joints and ends. A part's own name is a selection (the whole part).
- **Mesh:** a part's top dimension decides its elements: a wire meshes into line elements (`line2`, or `line3` at order 2), kept as beam blocks in `Mesh`. Joining beams to solids is not written yet.
- **Model:** `Component.beam_sections`, a list of `BeamSection`: a selection (the part, or an edge selection), a shape (rectangle, circle, tube, box) with only the sizes it takes, and the section's first axis (`orientation`, made square to the beam; a rectangle's width lies along it). `properties()` gives area, second moments and torsion constant. A line part without a section is refused.
- **CalculiX:** `B31` (order 1) or `B32` (order 2) with `*BEAM SECTION` (RECT, CIRC on B32 only, PIPE, BOX). CalculiX expands beams into solids internally; `OUTPUT=2D` maps the results back to the beam's own nodes. A fixed point holds rotations too.
- **Code_Aster:** `POU_D_T` (Timoshenko) on SEG2 (it refuses order 2), `AFFE_CARA_ELEM` `POUTRE` (RECTANGLE, CERCLE, with EP for hollow) and `ORIENTATION VECT_Y`. Forces on points are `FORCE_NODALE`. Section forces (`EFGE_NOEU`) come back as the field `beam_forces` (N, Vy, Vz, T, My, Mz).
- **Refused:** Elmer and FEniCSx (`Capabilities.beams`, and the writers refuse line elements).
- **Benchmarks** (`tests/integration/test_beams.py`; a steel 20 × 40 mm rectangle):

  | Check | CalculiX B31 | CalculiX B32 | Code_Aster POU_D_T |
  | --- | --- | --- | --- |
  | Cantilever tip, 1 m, 100 N, against Timoshenko | −0.09 % | −0.49 % | 0.00 % |
  | Cantilever root shear and moment (P, P L) | | | exact |
  | Portal frame sway, 1 × 1 m, against a matrix analysis | −0.33 % | −0.85 % | +0.48 % (shear flexibility) |

- **GUI:** Polyline in the Geometry ribbon; a component's Add beam section… (the beams, a shape by name, sizes in the display unit showing only those the shape takes, the first axis as Global X/Y/Z); the tree lists "Beam section on F: tube 60 × 4 mm", the outline names it, and the properties panel hides sizes the shape doesn't take. The view draws beams as thick lines, coloured by the result.
- **Not yet:** joining beams to solids or shells where nodes aren't shared; I, channel and angle sections; section stresses on beams; a section's orientation from a point or edge; beam display with its real section.

## Built: shells (2026-10-25)

- **Geometry:** `sheet` (a flat polygon through points; its face is `face`) and `curved_sheet` (a cylindrical panel along x, centred on +z: radius, length, angle; its face is `surface`) make sheet bodies. Both are in the Geometry ribbon and the menu. A part's own dimension (3, 2 or 1) decides its elements, so a sheet meshes into triangles (`tri3`, or `tri6` at order 2); `Mesh.shell_blocks()` tells them from the faces a solid's selections name.
- **Model:** `Component.shell_sections`, a list of `ShellSection`: a selection (the part or a face selection), a thickness and an offset in thicknesses (0 centred, +0.5 the sheet on the shell's bottom). A sheet without a thickness is refused.
- **Loads:** a positive pressure on a sheet pushes against the face's normal, as on a solid's face (Gmsh orients the elements with the face). Gravity takes the density and thickness. A `fixed` support on shell nodes clamps the rotations too; `displacement` leaves them free (a simple support).
- **CalculiX:** S6 (and S8) with `*SHELL SECTION` and `OFFSET`; its pressure label `P` pushes along the element normal, so −p is written. S3 is refused: it expands into one layer of wedges, which lock in bending (a simply supported plate came out 70 % too stiff). `OUTPUT=2D` maps results back to the shell's nodes.
- **Code_Aster:** DKT on three- and four-node faces (order 2 is refused), `AFFE_CARA_ELEM` `COQUE` with `EPAIS` and `EXCENTREMENT`, pressure by `FORCE_COQUE`. A model of shells alone asks no stress field (`CONTRAINTE` left out).
- **Refused:** Elmer and FEniCSx (`Capabilities.shells`, and the writers refuse sheet elements).
- **Benchmarks** (`tests/integration/test_shells.py`):

  | Check | CalculiX S6 | Code_Aster DKT |
  | --- | --- | --- |
  | Simply supported square plate, 1 kPa, against 0.00406 q a⁴ / D | +0.74 % (25 mm) | −0.12 % (25 mm) |
  | Clamped square plate against 0.00127 q a⁴ / D | −0.81 % (25 mm; −0.3 % at 12.5 mm) | −0.02 % |
  | Scordelis-Lo roof, free edge against 0.3024 | −0.99 % (2.5 m) | −1.04 % (0.625 m; DKT converges from below: −7 % at 2.5 m) |

- **GUI:** a component's Add shell thickness… (the sheets, the thickness in the display unit, where the sheet sits), in its menu and Model → Add shell thickness…; the tree lists "Shell on P: 4 mm thick"; the outline names it; the view draws the shells as surfaces coloured by the result.
- **Not yet:** a solid's mid-surface as a sheet; joining shells to solids or beams where nodes aren't shared; section results (membrane and bending stresses, top and bottom); orienting normals across several faces; the pinched cylinder; curved panels about other axes.

## Consequences

- Bolted assemblies are modelled as engineers usually do, quickly, with bolt loads to check, and without contact unless it's wanted.
- The mesh model, the VTK conversion, the result catalogue (`post.quantities`) and every structural adapter grow element types beyond tets. That is the main cost, and is why fasteners come first: they add only a few line elements.
- Code_Aster and CalculiX get fasteners first; Elmer and FEniCSx refuse them for now, so shoot-outs with fasteners are between those two.
- Buckling (ADR 0028) gains much of its use once beams and shells exist.

## Alternatives considered

- Solid bolts with contact: the most faithful, and already possible, but slow to build and to solve. It stays available for the bolts that matter.
- Springs only (CBUSH-like): simpler, but they need stiffnesses the user must work out. A spring connector with given stiffnesses may come later beside the beam.
- Beams as geometry first: a larger change to geometry, naming and meshing before any fastener could be made.

## Review notes (2026-10-23)

The interface review (item 11) found that PrePoMax has no beam elements in its GUI. Forum users add them with keywords and find it problematic. That gap is a reason to keep beam fasteners early in Puzzle's plan. ADR 0034's "same as" selection (all holes of one radius) is what fastener creation from holes builds on.
