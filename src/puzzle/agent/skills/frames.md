---
name: frames
description: frames of line members (stick models): members from nodes or a DXF, sections, releases, offsets, supports and loads, solved on PyNite, cross-checked on CalculiX and Code_Aster
adr: 0079
---
Read this when the user wants a frame, truss, portal, building skeleton or continuous
beam analysed as members (a "stick model"), imports line drawings (DXF) of one, or asks
for member forces (axial, shear, moment, torsion diagrams) rather than stresses in a
solid. Beam sections and their orientation are in the `beam-sections` skill; bolts and welds in
a solid joint are other skills.

## Making a frame

1. Geometry: `frame_geometry(name, nodes, members)` with `nodes` {"A": [0, 0, 0], ...}
   in metres and `members` [["AB", "A", "B"], ["BC", "B", "C", "Beams"], ...] (the
   fourth item a group), or `frame_geometry(name, dxf="frame.dxf", layer=None,
   scale=None)` (a file in the project; LINE, LWPOLYLINE and POLYLINE entities, each
   segment a member, coincident ends one node, layers groups, units from $INSUNITS,
   millimetres when unset). Pass its `command` to `submit`. The geometry has edge
   selections named by member and group, and point selections named by node.
2. A component with that geometry as a part ("F"), a material on "F", a mesh recipe
   (first order; `global_size` sets the stations along each member, 0.5 m is a good
   start), and a beam section on "F" or on each group ("F/Columns"): library sections
   (`shape` library, `standard` aisc or en, `designation` W12X26 / IPE 300) or
   rectangle, circle, tube, box. Its `orientation` is the section's first axis (the
   strong one's bending axis for an I shape): e.g. [0, 1, 0] for a frame in the XZ
   plane loaded in z.
3. Supports at nodes: `fixed` (all six held), `displacement` with x, y, z 0 (pinned) or
   some of them (a roller), `node_spring` (kx, ky, kz in N/m, krx, kry, krz in
   N·m/rad; PyNite only). A frame needs its twist about each straight run held
   somewhere (a fixed node, or a `node_spring` with krx), or it is a mechanism.
4. Loads: `force` on a node selection (N, split over its nodes; a load mid-member needs
   a node there), `line_load` on a member or group (x, y, z in N/m, global; PyNite
   only so far), `gravity` on "F" (self weight: ρ A g per length; materials need a
   density).
5. Member ends, on the component (`edit` the component):
   `member_releases` [{"selection": "F/BC", "at": "F/B", "released": ["ry", "rz"]}]
   frees those dofs (in the member's axes: x along it) where the members of
   `selection` meet node `at` (a pin: ry and rz); `member_offsets` [{"selection":
   "F/BC", "at": "F/B", "length": {"value": 150, "unit": "mm"}}] makes that length rigid
   (a mesh node must fall at the length). PyNite only so far.
6. A study on solver `pynite` (one static step). `frame_members(component)` lists
   members, sections, releases, supports and loads to check before running.
7. Cross-check on `calculix` or `code_aster` (a shoot-out): they take sections,
   supports and nodal forces (benchmarked against PyNite here), not releases, offsets,
   line loads or node springs yet.

## Load cases, combinations and second order (ADR 0079 step 2, PyNite)

1. Give each load its `case`: D (dead), L (live), Lr, S, R, W (wind), E (seismic), or a
   name of your own. Supports have none.
2. `load_combinations(study, method="LRFD")` (or "ASD") makes ASCE 7-16's combinations
   from the cases the loads use. Wind and seismic act both ways; combinations led by a
   load the frame hasn't are left out. Or pass `combinations=[{"name": "...",
   "factors": {"D": 1.2, "L": 1.6}}]`. With neither argument it lists the cases and the
   current combinations.
3. Each combination is a result step of its own, named by the combination; probes and
   operators read it like any step.
4. Second order: the solve step's setting `second_order` true (P-Delta, each
   combination solved on its own).
5. AISC 360's Direct Analysis Method: `direct_analysis` true. It runs P-Delta with
   0.8 EI and 0.8 EA (τb = 1), plus notional loads of 0.002 times each node's factored
   gravity load. `notional_directions` is "x,y" by default (both senses of each, each a
   combination of its own named "<combination>, notional +x"), or "+x", "x" and so on.
   Gravity is along −z.
6. CalculiX, Code_Aster and the others refuse combinations, `second_order` and
   `direct_analysis` so far.

## Results

- `beam_forces` (per node): N, Vy, Vz, T, My, Mz in each member's axes (x along it, y
  the section's first axis), Code_Aster's signs (N tension positive) on every solver;
  at a node where members meet at an angle it is their average, so read a joint's
  forces from `member_forces` (per element: the six at its start, then at its end).
- `displacement` and `rotation` (per node, global), `reaction_force` and
  `reaction_moment` at held nodes; `result_total(study, "reaction_force", "F/A")`.

## Mistakes to avoid

- No section on some members: refused; give the part one, or each group.
- An orientation along a member (a vertical column with orientation [0, 0, 1]):
  refused; columns need their own section or a different first axis.
- PyNite's members are Euler-Bernoulli; CalculiX's and Code_Aster's beams are
  Timoshenko, so stocky members differ by their shear deflection (W12X26 over 8 m:
  0.6 % in the indeterminate moments).
- Torsion released at both ends of a member: refused (a mechanism).
- Combinations with a load that has no case: refused. Every load needs one once the
  study combines cases.
- Direct Analysis assumes τb = 1: where a member's αPr/Py passes 0.5, its flexural
  stiffness should be reduced further (AISC 360 C2.3(b)). Check the axial loads.
