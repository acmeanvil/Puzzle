# 0027. Beams and shells beside solids, starting with beam fasteners

- Status: Proposed
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

## Consequences

- Bolted assemblies are modelled as engineers usually do, quickly, with bolt loads to check, and without contact unless it's wanted.
- The mesh model, the VTK conversion, the result catalogue (`post.quantities`) and every structural adapter grow element types beyond tets. That is the main cost, and is why fasteners come first: they add only a few line elements.
- Code_Aster and CalculiX get fasteners first; Elmer and FEniCSx refuse them for now, so shoot-outs with fasteners are between those two.
- Buckling (ADR 0028) gains much of its use once beams and shells exist.

## Alternatives considered

- Solid bolts with contact: the most faithful, and already possible, but slow to build and to solve. It stays available for the bolts that matter.
- Springs only (CBUSH-like): simpler, but they need stiffnesses the user must work out. A spring connector with given stiffnesses may come later beside the beam.
- Beams as geometry first: a larger change to geometry, naming and meshing before any fastener could be made.
