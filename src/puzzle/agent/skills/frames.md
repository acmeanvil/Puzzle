---
name: frames
description: frames of line members (stick models) from a grid, nodes or a DXF; sections, releases, loads, combinations, seismic; PyNite; member checks by AISC 360/370 and the ADM
adr: 0079
---
Read this when the user wants a frame, truss, portal, building skeleton or continuous
beam analysed as members (a "stick model"), imports line drawings (DXF) of one, or asks
for member forces (axial, shear, moment, torsion diagrams) rather than stresses in a
solid. Beam sections and their orientation are in the `beam-sections` skill; bolts and welds in
a solid joint are other skills.

## Making a frame

0. From a grid (the quickest): `frame_grid(name, bays_x=[6, 6, 6], stories=[4, 3.5],
   bays_y=[5, 5], bracing="x" or "chevron", braced_bays="ends" or "all")`. Nodes are
   named by column line, row and level (A1-0 at the base); groups Columns, Beams X, Beams
   Y, Braces take sections. Pass its `command` to `submit`. GUI: Geometry → New frame from
   a grid….
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

## Modes and seismic loads by ASCE 7-16 (ADR 0079 step 4, PyNite)

1. Modes: a study step `modal` with `modes` (12). Mass: the members' density, plus the
   seismic design's `mass_cases` gravity loads (default D at 1). Each mode is a result
   step with `participation_factor`, `effective_mass` (kg) and `mass_fraction` in x, y
   and z; `seismic_report` sums them (12.9.1.1 wants 90 % in each horizontal direction).
2. `seismic_design(study, sds, sd1, r, cd, s1=, tl=8, ie=1, risk_category="II",
   structure="steel_moment_frame", drift_structure="other")`. Accelerations are in g,
   from the site (11.4.4); R and C_d from Table 12.2-1.
3. ELF: `seismic_elf(study, direction="x")` writes the story forces as `force` loads of
   case Ex on each level's nodes (rerunning replaces them). Use the modal period with
   `period=`, else T_a; it is capped at C_u T_a either way. Then `load_combinations(study,
   "LRFD")` makes ASCE 7's combinations with Ex (and Ey) both ways.
4. Modal response spectrum: the modal step's settings `response_spectrum` true
   (`spectrum_directions` "x,y"). The study's combinations with Ex or Ey become "(+E)"
   and "(−E)" steps: static ± the CQC response, scaled up to the ELF base shear. The
   spectrum itself is a step of kind `spectrum` per direction.
5. Drift: `seismic_report(study)` gives each story's design drift C_d δ / I_e at the
   level's centre of mass against Table 12.12-1, for the spectrum steps and for
   combinations named exactly Ex or Ey (add {"name": "Ex", "factors": {"Ex": 1}} for
   ELF drift).

## Member checks by AISC 360-16 (ADR 0079 step 3)

1. Materials with a yield and a tensile strength: the generic library's ASTM A992 (W
   shapes), A36, A572 Gr. 50, A500 Gr. C (rectangular and round HSS), A53 Gr. B (pipe),
   stainless 304, duplex 2205, lean duplex 2101, aluminium 6061-T6. The code follows the
   material's group: carbon and low-alloy steel by AISC 360-16, stainless by AISC 370-21
   (austenitic or duplex from the name), aluminium by the Aluminum Design Manual 2015
   (temper from the name: T5-T9 use Table B.4.2). Aluminum Association shapes are the
   section standard `aa` (I8X6.18, RT2X2X0.125, PIPE2SCH40...).
2. `member_design(component, selection="", method="LRFD", unbraced_length="10 ft",
   k_x=1, k_y=1, deflection_limit=360)`: one entry per selection ('' every member, a
   part, Part/Group, Part/Member; the most specific wins). Unset lengths are the
   member's own; L_b also sets y and z unless given. K = 1 under Direct Analysis.
   Loads factored for the method (LRFD combinations with LRFD, ASD with ASD).
3. Run the study, then `member_checks(study)`: per member, worst first, the governing
   check over the combinations: tension (D2), compression (E3, E4 torsional, E7
   slender elements), flexure about x per unbraced segment with C_b from its diagram
   and about y (F2, F3, F6, F7, F8, F11), shear along each axis (G2, G4, G5, G6), HSS
   torsion (H3.1), combined forces (H1-1), deflection L/δ when a limit is set. Each
   check has its demand (N, N·m, m), available strength (φR_n or R_n/Ω), ratio, the
   station along the member (m) and the clause. `summary=False` keeps every step's
   checks.
4. The CLI's `puzzle members PROJECT STUDY [--details]`; the GUI's Members panel (Window
   → Members, Results → Result tools → Member checks) and Model → Beams and shells →
   Member design….

Sections checked: doubly symmetric I-shapes (W, M, S, HP, IPE, HE, UB, UC), rectangular
and round HSS and pipe, and Puzzle's box, tube, rectangle and circle. Channels, tees,
angles and double angles are listed under `not_checked` with the reason.

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
- Member checks on unfactored loads: give the study combinations of the method's kind,
  or the ratios are not the code's.
- An unbraced length spanning several members (a column of two members): C_b is taken
  as 1.0 there (allowed, conservative), since one member's diagram is all it sees. Give
  `cb` when the whole segment's diagram is known.
- ELF loads and a response spectrum together: the spectrum run leaves the ELF loads
  out (the spectrum is the seismic load). Don't add both to one combination by hand.
- Accidental torsion, the 100/30 orthogonal rule, E_v and ρ aren't applied: give them
  in the factors where they govern.
- An aluminium member with welds along it: the ADM's heat-affected zones aren't applied
  yet; its checks are an unwelded member's.
- Generic "Structural steel" has no yield strength: members on it are not checked; use
  an ASTM grade or give the material its yield and tensile strengths.
