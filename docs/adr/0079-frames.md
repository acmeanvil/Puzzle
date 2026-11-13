# 0079. Model and check frames of line members, as SAP2000 and RISA do, by AISC 360 and 370 and the Aluminum Design Manual

- Status: Accepted; built (2026-11-12): all six steps of the plan
- Date: 2026-11-10

## Context

Many structures are best analysed as a "stick model": members as lines in 3D, joined
at nodes, with their sections' properties, releases and loads, then checked member by
member against a design code. SAP2000, RISA-3D and SkyCiv are built around it. Puzzle
already has much of the underneath: line bodies meshed into beam elements (ADR 0027:
CalculiX B31/B32 or exact Timoshenko springs for open sections, Code_Aster POU_D_T),
the AISC v16.0 and EN section tables with orientations and drawn shapes (ADR 0055),
buckling (ADR 0028), modal and harmonic dynamics (ADR 0041), load combinations and
envelopes as derived results (ADR 0025), polyline geometry and DXF import (ADR 0048
round 2).

What it lacks for frame work:

- frame modelling: a grid of nodes and members, end releases, rigid end offsets,
  member loads (point, distributed, trapezoidal, along local or global axes), self
  weight, member groups;
- load cases and combinations by building code, P-Delta, seismic analysis;
- results along members (axial, shear, moment, torsion diagrams; deflections) and
  member design checks with the governing clause.

The maintainer's direction (2026-11-10): design checks by **AISC 360** (carbon steel),
**AISC 370** (stainless) and the **Aluminum Association's Aluminum Design Manual**
(ADM), chosen by the member's material; seismic in scope; geometry from a grid editor
and from lines, including line geometry imported from DXF.

References in the maintainer's library: AISC 360-22/-16, AISC 370-21, Design Guide 27
(stainless), the Steel Construction Manual (14th, 15th) and the Seismic Design Manual;
the Aluminum Design Manual 2015 (the current edition is 2020: its changes to be checked
before relying on 2015's numbers); ASCE 7-16 (and 7-10, 7-05); the shapes database v16.

## Decision

1. **A frame is a component of line members** (`Component.dimension = "frame"`), its
   geometry lines, not solids:
   - built by a **grid editor** (`gui/frame_editor.py`: grid lines by spacing in X, Y,
     Z; nodes at intersections; members drawn between nodes, or generated: bays,
     stories, bracing patterns), by **lines from geometry** (polylines and sketches,
     ADR 0048), or **imported from DXF** (LINE, LWPOLYLINE, POLYLINE entities; layers
     become member groups, so a DXF's layer "Columns" is a group with its section);
   - members carry a section (ADR 0055's tables, plus the Aluminum Association's
     standard shapes added to `sections`), an orientation (ADR 0055's `orient`), end
     **releases** (each end's six dofs: fixed, pinned, or a partial-fixity spring),
     rigid **end offsets** (a joint's panel zone), and a material;
   - supports at nodes (fixed, pinned, rollers, springs), and the existing coordinate
     systems for skewed supports.
2. **Loads by case, combinations by code.** `LoadCase`s (dead, live, roof live, snow,
   wind, seismic, and user-named) hold member loads (point, uniform, trapezoidal,
   projected or along the member; local or global axes), nodal loads and self weight;
   `LoadCombination`s are generated from ASCE 7's LRFD and ASD combinations (Chapter 2;
   seismic ones with E = ρQ_E ± 0.2 S_DS D), editable, and solved as linear
   combinations where the analysis is linear, or each solved on its own under P-Delta.
3. **Analyses**: linear static; **P-Delta** (geometric stiffness, iterated, the
   "second-order" analysis AISC's Direct Analysis Method needs, with its notional loads
   and reduced stiffness, 360 Chapter C); modal; **seismic** by ASCE 7: the
   Equivalent Lateral Force procedure (12.8: C_s, the vertical distribution, accidental
   torsion) and **Modal Response Spectrum analysis** (12.9: the design spectrum from
   S_DS, S_D1, T_L, (11.4); enough modes for 90 % mass participation in each
   direction; CQC combination; scaled to the ELF base shear as 12.9.1.4 says); story
   drift (12.12).
4. **The solver.** A frame adapter on **PyNite** (MIT licence, a pure-Python 3D frame
   solver: 12-dof members with releases, P-Delta, combinations, modal) for speed and
   member-native results, run as any solver (its own small environment). The same
   frames also run on CalculiX and Code_Aster's beams (written from the frame model:
   releases as end springs or released dofs, offsets as rigid links), so every frame
   result is cross-checked in a shoot-out (ADR 0011). A solver claims a frame
   capability only once its benchmark passes; releases and P-Delta on CalculiX and
   Code_Aster may come later than on PyNite.
5. **Results along members.** Axial, shear (both axes), torsion and moment (both axes)
   diagrams and deflected shapes along each member, at stations (ends, loads, and a set
   count between), per case, combination and envelope; drawn on the members in the
   view (ADR 0055's swept shapes, diagrams beside them), in a member table, and as
   series for operators (ADR 0071).
6. **Member design checks, by the member's material.** `puzzle/frames/checks/`:
   - **AISC 360**: tension (D), compression with flexural, torsional and
     flexural-torsional buckling (E; slender elements, E7), flexure by section class
     and lateral-torsional buckling with C_b (F), shear (G), combined forces (H1), and
     the Direct Analysis Method's requirements (C); effective lengths from the bracing
     the user gives (unbraced lengths per axis and for LTB);
   - **AISC 370**: the same chapters by 370-21's own provisions for stainless (its
     material's nonlinear stress-strain), checked against Design Guide 27's examples;
   - **ADM**: Chapters D-H for aluminium, with welded members' heat-affected-zone
     strengths (shared with ADR 0078's aluminium welds);
   - per member: each check's demand, capacity, ratio and clause; the governing one;
     deflection limits (L/360 and the like) as user limits. A table of members by
     ratio (`gui/member_table.py`), MCP `member_checks`.
7. **Everything a command and reachable by the agent**: frame objects in the schema,
   the grid editor's actions as commands, MCP `frame_grid`, `frame_members`,
   `load_combinations`, `member_checks`, `seismic_spectrum`; schematic nodes; skill
   `frames` (ADR 0057).

## Benchmarks

- Closed forms: a portal frame's moments, a propped cantilever, a continuous beam with
  releases (three-moment equation), each on PyNite, CalculiX and Code_Aster.
- P-Delta: a cantilever column's amplified moment against B1/B2 (360 Appendix 8) and
  the exact P-Δ solution; AISC's Direct Analysis benchmark problems (360 Commentary
  C, Figure C-C2.3 cases).
- Modal and seismic: a shear building's periods and modes in closed form; an ELF and an
  MRS base shear against a hand calculation to ASCE 7-16.
- Checks: AISC Manual and Design Guide examples (compression, flexure with LTB,
  combined), Design Guide 27's for 370, the ADM's Part VII examples, to the printed
  digits.

## Consequences

- Frames become a fast, member-level analysis in Puzzle, with code checks, cross-
  checked by two FE solvers; detailed connections still go to solid models (bolts,
  welds, ADR 0077, 0078).
- PyNite is a new adapter and environment; its results must map onto Puzzle's line
  meshes (stations as nodes of a refined line mesh) so the existing results store,
  views and operators work.
- To watch: AISC 341 seismic detailing (special frames' rules) is out of scope here;
  ASCE 7 editions (7-16 first; 7-22's multi-period spectra later); the ADM 2015/2020
  difference.

## Alternatives considered

- **Only CalculiX and Code_Aster beams.** No member-native results or releases on
  CalculiX's B31 without workarounds, and slow for many combinations; kept as the
  cross-check.
- **OpenSees.** Very capable (nonlinear, seismic), but its licence restricts
  commercial use; not shippable in a GPL release without clearance.
- **Write Puzzle's own frame solver.** PyNite is that, tested and MIT-licensed; Puzzle
  contributes fixes upstream.

## Built: step 1 (2026-11-11)

A frame is a 3D component whose parts are line bodies, not a new `Component.dimension`
(the smallest change: the beams' sections, orientations, line meshes, point supports and
CalculiX/Code_Aster solves of ADR 0027 and 0055 all carry over).

- **Geometry:** the `frame` history step (`frames/lines.py`, `geometry/history.py`).
  - Members are edges between named nodes. Members at a node share its vertex, so they are joined there.
  - It takes `nodes` {name: [x, y, z]} and `members` [[name, i, j, group]], or a DXF's LINE, LWPOLYLINE and POLYLINE entities. Coincident ends become one node, layers become groups, and the units come from $INSUNITS (mm when unset).
  - Each member's and group's edges carry its name. Line bodies now keep their history's edge names beside `edge-N` (`naming.name_all`).
  - `frames.geometry` adds edge selections per member and group, and a point selection per node (`near` its point).
- **Model:**
  - `Component.member_releases` (`MemberRelease`: a member, group or part, the node `at`, the dofs `released` in the member's axes; a pin by default; `springs` refused so far).
  - `Component.member_offsets` (`MemberOffset`: rigid for a length from the node; a mesh node must fall there).
  - Both are left out of content hashes when empty.
  - Features: `line_load` (N/m, global x, y, z, on members), `node_spring` (kx..krz at a node), and `gravity` as self weight (ρ A g per length).
  - Pinned and rollers are `displacement` with the held components at zero.
  - `Capabilities.member_ends` gates releases and offsets: CalculiX and Code_Aster refuse them.
- **The PyNite adapter** (`adapters/pynite`, MIT PyNiteFEA 3.2; the pixi `pynite` env, or `PUZZLE_PYNITE_PYTHON`):
  - Each line element is a PyNite member, so stations are the mesh's nodes. A three-node element becomes two members.
  - Members are rolled so their local y is the section's first axis, with Iy = I1, as Code_Aster's POU_D_T.
  - `frame.py` runs in PyNite's environment without Puzzle, reading `case.json` and writing `results.json`.
  - Results:
    - `displacement` and `rotation`;
    - `reaction_force` and `reaction_moment`;
    - `beam_forces` per node (N, Vy, Vz, T, My, Mz, averaged where members meet, as EFGE_NOEU);
    - `member_forces` per element (both ends).
  - PyNite gives N, Vy, Vz, T and Mz the opposite sign to Code_Aster's EFGE and CalculiX's springs. The reader flips them, so `beam_forces` means the same on every solver (`test_member_force_signs`).
- **Agent:** MCP `frame_geometry` (returns a create command) and `frame_members`; skill `frames`.

Benchmarks (`tests/integration/test_frames.py`; a W12X26 in steel, spans of 8 m):

| Case | Closed form | PyNite | CalculiX | Code_Aster |
| --- | --- | --- | --- | --- |
| Propped cantilever, P at midspan: M_A | 3PL/16 = 30000 N·m | 30000 | 29829.5 (−0.57 %) | 29829.5 (−0.57 %) |
| ... R_B | 5P/16 = 6250 N | 6250 | 6271.31 | 6271.31 |
| Propped cantilever, uniform w: M_A, R_B | wL²/8, 3wL/8 | exact | refused (line load) | refused |
| Two-span continuous beam: M_B | 3PL/16 | exact | not run (twist needs a node spring) | not run |
| Fixed-feet portal, H at the beam: base moments | 12500 N·m | 12583, 12429 (mean +0.05 %) | 12622.9, 12474.3 | 12622.9, 12474.3 |
| Hinge (released joint): M_A, R_C | PL/2, P/2 | exact, 0 at the hinge | refused | refused |
| Self weight: root moment | ρAgL²/2 | exact | — | — |
| Signs: a pulled, twisted cantilever | N +P, T −P a, My +P L | as Code_Aster | the same | the same |

- PyNite's members are Euler-Bernoulli, so it matches the closed forms exactly.
- CalculiX's and Code_Aster's beams are Timoshenko: their shear deflection moves the indeterminate moments by 0.6 %. They agree with each other to every printed digit.
- The portal's closed form neglects the beam's axial shortening, which parts its two base moments by ±0.6 % on every solver.

Not in step 1:
- partially fixed releases (springs at a member's end);
- releases, offsets, line loads and node springs on CalculiX and Code_Aster;
- member point loads between nodes (put a node there);
- the pixi `pynite` env's lock (`pixi lock` in the main checkout);
- drawing diagrams beside members in the view (the fields are stored per node and per element; the GUI's diagram drawing is a later step).

## Built: step 2 (2026-11-11)

- **Load cases and combinations.** `Feature.case` gives each load its case, and
  `Study.combinations` (`LoadCombination`: name, factors) lists the combinations; both are
  left out of hashes when unset.
  - `frames/combinations.asce7(cases, "LRFD" | "ASD")` gives ASCE 7-16 2.3.1 and 2.4.1
    from the cases a frame has:
    - one combination per roof load (Lr, S, R);
    - wind and seismic both ways;
    - wind, seismic and roof-led combinations left out where the frame has no such load;
    - names written from the factors used (`1.2D + L + 0.5S - W`).
  - E is the load effect as given. Ev = 0.2 SDS D and ρ are the user's to include for
    now.
- **PyNite solves the combinations.** PyNite's own load cases and `add_load_combo`; each
  combination is a result step of kind `combination`, named by a global field
  `combination` (`StepResult.label`). `run_study` keeps that kind.
- **Second order and Direct Analysis**, as step settings:
  - `second_order` runs P-Delta (`analyze_PDelta`, each combination on its own, P-δ from
    the members the mesh makes);
  - `direct_analysis` (AISC 360 C2) runs P-Delta with 0.8 E and 0.8 G (τb = 1), plus
    notional loads N_i = 0.002 Y_i from each combination's factored gravity at each
    node, in each `notional_directions` (default x,y, both senses). Each is a
    combination of its own, `<combination>, notional ±x`.
- **Refusals.** Other adapters refuse combinations, second order and direct analysis
  (`Capabilities.frame_analysis`).
- **GUI and agent.** Model → Studies → Load combinations… (one form: LRFD, ASD or none),
  MCP `load_combinations`, and the skill `frames`.
- **Benchmarks** (`tests/integration/test_frame_analysis.py`, all exact to the printed
  digits):
  - superposition of 1.4D and 1.2D + 1.6L on a cantilever: 56000 and 86400 N·m;
  - P-Delta on a cantilever column at 0.3 of Euler's load: H tan(kh)/k = 27021.3 N·m,
    against 20000 first order;
  - Direct Analysis on the same column: (H ± 0.002 P) tan(k'h)/k' with k' = √(P/0.8EI),
    39659.3 and 19979.7 N·m.

  `tests/test_combinations.py` checks the generated combinations.
- **Not yet:**
  - τb below 1 when αPr/Py > 0.5;
  - notional loads only in gravity-only combinations where Δ2/Δ1 ≤ 1.7 (C2.2b(4));
    they go in every combination, which is conservative;
  - combinations on CalculiX and Code_Aster;
  - envelopes over combinations (operators' Envelope reads the steps meanwhile).

## Built: step 3 (2026-11-12)

- **Strengths** (`frames/aisc360.py`), from the specification's own equations, in SI.
  Each limit state gives its nominal strength, φ, Ω and clause:
  - tension: yielding and rupture on A_e = `net_area_ratio` A_g (D2);
  - compression: flexural buckling about each axis (E3) and torsional buckling of a
    doubly symmetric I (E4-2), each with E7's effective widths (c1 and c2 by element)
    or a round HSS's E7-7;
  - flexure: F2 (L_p, L_r, C_b) and F3's flange local buckling; F6 about the weak axis;
    F7 for rectangular HSS (flange local buckling with S_e, web local buckling,
    lateral-torsional buckling); F8 for round HSS; F11 for solid bars;
  - shear: G2.1(a) and (b), G4, G5 (L_v half the member), G6;
  - torsion of HSS (H3.1); combined forces (H1-1a and b; H3-6 past 0.2 T_c).

  Tabulated rx, ry and rts are used where the table has them, as the Manual does.
  Channels, tees, angles and double angles are refused (E4's shear-centre offset, F9,
  F10), and so is an I with a noncompact web in flexure (F4, F5).
- **Checks along members** (`frames/design.py`):
  - PyNite now writes `member_stations`: each element's six forces at nine points, so
    a member's diagram is read between nodes (a quadratic element is two PyNite
    members, every other station of each). Other solvers' `beam_forces` at the nodes
    are the fallback.
  - Each static or combination step is checked at every station. Strong-axis flexure
    is checked per unbraced segment, with C_b from that segment's diagram (F1-1, the
    quarter points interpolated by position), or 1.0 when L_b runs past the member.
  - Deflection is the largest distance of a node from the line through the displaced
    ends, against `deflection_limit`.
  - The code follows the material's group: carbon and low-alloy steel → AISC 360;
    stainless and aluminium are refused with the reason.
- **`MemberDesign`** (`Component.member_design`, out of hashes): selection, code,
  method, the lengths and K factors, `cb`, `net_area_ratio`, `deflection_limit`. The
  most specific entry for a member wins.
- **Materials.** The generic library adds ASTM A992, A36, A572 Gr. 50, A500 Gr. C
  (rectangular and round) and A53 Gr. B, with E = 29,000 ksi.
- **GUI, CLI, agent:**
  - GUI: Model → Beams and shells → Member design… (one form) and the Members panel
    (Window → Members, Results → Result tools → Member checks): worst first, a member's
    checks below, CSV export.
  - CLI: `puzzle members`.
  - MCP: `member_design`, `member_checks`.
  - The skill `frames`.
- **Benchmarks.** Against the Manual's tabulated values (Table 3-2, `tests/test_aisc360.py`):

  | Value | Puzzle | Manual |
  | --- | --- | --- |
  | W18X50 φM_p | 379 kip-ft | 379 |
  | W18X50 ASD M_p/Ω | 252 kip-ft | 252 |
  | W18X50 L_p | 5.83 ft | 5.83 |
  | W18X50 L_r | 16.9 ft | 16.9 |
  | W18X50 φM_r | 233 kip-ft | 233 |
  | W18X50 φV_n | 192 kips | 192 |
  | W14X90 noncompact flange (F3) | 574 kip-ft | 574 |

  Against hand calculations from the specification on PyNite solves
  (`tests/integration/test_member_checks.py`), all to the printed digits:
  - an unbraced 30 ft W18X50 under 1 kip/ft: C_b 1.136, F2.2(c), φM_n 114.88 kip-ft;
  - the same braced at thirds: the middle segment's C_b 1.0135, F2.2(b), 328.57 kip-ft;
  - its deflection against 5wL⁴/384EI, 19.953 mm;
  - a W14X90 column at L_c 20 ft: 876.7 kips;
  - a beam-column's H1-1a, 0.5073.

  The HSS, E7 and E4 branches are checked by hand in kips and inches in the unit
  tests.
- **Not yet:** channels, tees and angles (E4-3/E4-4, F9, F10); noncompact webs (F4,
  F5); stiffened webs and tension field action (G2.2); single angles (E5, F10); B1
  amplification for first-order analyses (Appendix 8). Use the Direct Analysis
  Method, which needs none of it. Torsion of I shapes (Design Guide 9) is not
  checked either.

## Built: step 4 (2026-11-12)

- **Modal on PyNite** (`analyze_modal`). The mass is the members' density, as PyNite's
  consistent mass (given ρ g, its weight density, with its self weight in a mass-only
  case), plus the seismic design's `mass_cases` gravity loads as lumped masses. A
  modal run keeps Puzzle's own self-weight line loads in a case of their own
  (`<case>#self`), so the mass doesn't count them twice.
  - `frame.py` takes each mode's participation factor and effective mass in x, y and z
    from PyNite's mass matrix on the free dofs; `mass_fraction` is over the free mass,
    as SAP2000 reports it.
  - The results are one step per mode (kind `modal`, value in Hz), with the global
    fields `participation_factor`, `effective_mass` and `mass_fraction`.
- **ASCE 7-16** (`frames/seismic.py`, pure):
  - the design spectrum (Figure 11.4-1);
  - C_s by 12.8-2 to 12.8-6, saying which governs;
  - T_a (Table 12.8-2), C_u (Table 12.8-1, interpolated) and the period used (12.8.2);
  - k and the vertical distribution (12.8-11, 12.8-12), story shears (12.8.4);
  - CQC with Der Kiureghian's ρ_ij;
  - story drift at the levels' centres of mass (12.8.6), against Table 12.12-1.
- **The frame's side** (`frames/seismic_frame.py`):
  - `node_weights` (W: self weight half to each end, plus `mass_cases` loads), and
    `levels` above the base (the lowest held node);
  - `elf_commands`: the ELF story forces as `force` features on each level's nodes, by
    weight, case Ex or Ey, marked generated so a rerun replaces them; one Edit;
  - `report`: ELF in x and y (with the modal period where there is one), the modes, the
    spectra's base shears and scales, and the drifts.
- **Modal response spectrum** (12.9.1). A modal step with `response_spectrum`
  (`spectrum_directions`, default x,y) solves each mode's equivalent static loads
  M φ Γ S_a g I_e/R in a load-free model. `case.py`:
  - combines every field by CQC;
  - takes V_t from the reactions' CQC and the ELF V at the dominant mode's period
    capped at C_u T_a;
  - scales the forces by `scale_to_elf` V/V_t where V_t is less (12.9.1.4.1, 7-16's
    100 %), and records the drift scale of 12.9.1.4.2.

  Each direction is a step of kind `spectrum` ("Ex response spectrum (CQC)", with the
  global field `spectrum_base_shear`). Each combination with Ex or Ey becomes two
  combination steps, the static part ± the factored envelope, "(+E)" and "(−E)", which
  the member checks read. Generated ELF loads are left out of a spectrum run.
- **Combinations.** `asce7` takes E, Ex and Ey, each direction in combinations of its
  own, both ways.
- **Schema.** `Study.seismic` (`SeismicDesign`: S_DS, S_D1, S1, T_L, R, C_d, I_e, risk
  category, structure, drift structure, period, mass cases, scale, damping), out of
  hashes when unset. PyNite claims `modal`.
- **GUI and agent.** Model → Studies → Seismic design… and ELF loads… (one form each);
  MCP `seismic_design`, `seismic_elf` (writes unless `write` is false) and
  `seismic_report`; the skill `frames`.
- **Benchmarks** (`tests/integration/test_seismic_frames.py`, against closed forms to the
  printed digits):
  - a cantilever W14X90's first two modes about each axis, 12.23448 Hz against
    12.23443, with effective masses 0.6130 and 0.1882 of ρAL (0.6131, 0.1883);
  - a single mass on a column: V_t = W S_a I_e/R 52180.2 N exact, the ELF V 62500 N at
    C_u T_a, scale 1.1978, base moment V h, the ±E combinations;
  - ELF on a two-level column: each level's force exact, and the drifts
    C_d δ/I_e = 69.106 and 140.869 mm against the cantilever formulas.

  `tests/test_seismic.py` checks the spectrum, C_s's clauses, C_u, T_a, k, the
  distribution, CQC and the drift by hand.
- **Not yet:**
  - accidental torsion (12.8.4.2) and its amplification;
  - the 100 %/30 % orthogonal combination (12.5.3) for SDC C to F with irregularities;
  - E_v = 0.2 S_DS D and ρ;
  - the stability coefficient θ (12.8.7);
  - drifts of moment frames in SDC D to F divided by ρ (12.12.1.1);
  - spectra on CalculiX and Code_Aster.

## Built: step 5 (2026-11-12)

- **AISC 370-21 for stainless** (`frames/aisc370.py`), from the specification's pages:
  - D2;
  - E3 with Table E3.1's curves (A: an I about its minor axis and the rest; B: an I about
    its major axis and round HSS; C: rectangular HSS), E4-2 on curve A, and E7's
    effective widths (E7-3, k 0.425 or 4.00) or E7-8 for round HSS;
  - F2 with L_p, L_y and L_r from Table F2.1 by alloy family, and α_LT;
  - F3's local buckling with S_xe, and F6 with S_ye: effective section moduli from
    rectangles;
  - F7 for rectangular HSS (no lateral-torsional buckling), F8 and F9;
  - G2.1 (C_v1 up to 1.2), G3, G4 (C_vM and C_vL) and G5; H1-1 as 360's.

  The alloy family comes from the material's name: duplex for 2101, 2205, 2304, 2507 and
  the like, else austenitic. F4/F5, slender HSS webs, torsion (G8, G9), channels, tees
  and angles are refused.
- **Direct Analysis on stainless** (370 C2.3). τ_g = 0.70 instead of 0.8, and τ_b = 1
  with the further 0.002 Y_i of C2.3(c), so notional loads of 0.004 Y_i where the frame
  has stainless members.
- **The Aluminum Design Manual 2015** (`frames/adm.py`), from the Specification's pages:
  - buckling constants: Table B.4.1 for O, H and T1-T4 tempers, Table B.4.2 for T5-T9 (the
    temper from the material's name), C_t where the round element's curves first meet
    (6061-T6: 141, as Part VI), and Table B.4.3's k1 and k2;
  - elements: B.5.4.1, B.5.4.2, B.5.4.5, B.5.5.1 (m 0.65), B.5.5.2, B.5.5.4, and
    Table B.5.1;
  - tension D.2, with k_t 1;
  - compression: E.2 (flexural, and torsional for doubly symmetric members), E.3.1
    weighted average, E.4;
  - flexure: F.2 yielding and rupture, F.3.1 for I and box sections, F.3.3 otherwise,
    F.4 (r_ye by F.4-4, closed shapes F.4-6, bars F.4-7) and F.4.3;
  - shear G.2 to G.5, with rupture; H.1-1, linear;
  - φ and Ω for building-type structures, the governing limit state picked for the
    method asked (rupture's factors differ).

  F_cy is the new property `compressive_yield_strength` (else the yield), F_sy 0.6 F_ty
  and F_su 0.6 F_tu. Welded members (heat-affected zones) and torsion (H.2) are not built.
- **The code follows the material** (`design.code_for`): Carbon Steel → 360, Stainless
  Steel → 370, Aluminum → ADM; `MemberDesign.code` may name one.
- **Sections.** The Aluminum Association's shapes, standard `aa` (`sections/aa.json`,
  `scripts/import_aluminum_sections.py` from the Manual's Part V; each row's area checked
  against its sizes): 16 standard I-beams (Table 8), 99 square and rectangular tubes
  (Tables 23 and 24) and 82 pipes (Table 22).
- **Materials.** Generic stainless 304 gains F_y and F_u (A240), the library adds duplex
  2205 and lean duplex 2101, and 6061-T6 gains the ADM's extrusion strengths.
- **Benchmarks:**

  | Example | Puzzle | Published |
  | --- | --- | --- |
  | Design Guide 27, Ex. 1: round HSS, P_n | 101 kips | 101 |
  | DG27, Ex. 2: slender square HSS, P_n | 186 kips | 186 (A_e 3.70 in²) |
  | DG27, Ex. 4: W shape, φP_n | 56.7 kips | 56.7 |
  | DG27, Ex. 4: L_p, L_y, L_r | 2.42, 4.54, 34.2 ft | 2.42, 4.54, 34.2 |
  | DG27, Ex. 4: M_nx, M_ny, H1-1b | 22.3, 9.95 kip-ft | 22.3, 9.95 |
  | DG27, Ex. 6: φP_n, φM_p | 98.1 kips, 26.1 kip-ft | 98.1, 26.1 |
  | ADM Part VII, Ex. 9: I 8×6.18 column, torsional buckling | 92.4 k | 92.6 |
  | ADM Ex. 9: element stresses | 21.1, 18.0 ksi | 21.1, 18.1 |
  | ADM Ex. 9: weighted local buckling | 104.3 k | 104.3 |
  | ADM Ex. 15: I 5×3.70, M_np/Ω | 130.2 in-k | 130.2 |
  | ADM Ex. 15: local buckling, LTB | 133.3, 93.05 in-k | 133.3, 92.8 (C_c rounded to 66) |
  | ADM Ex. 26: yielding and rupture | 350, 322 in-k | 350, 322 |

  The DG27 rows use the examples' rounded L_c/r and r_ts. On PyNite runs
  (`test_member_checks.py`), a stainless beam is checked by 370 and an aluminium I-beam
  by the ADM, automatically, each against the module by hand.
- **Not yet:** welded aluminium members, AISC 370's continuous strength method (Appendix
  2), ADM bridge-type factors, torsion on stainless and aluminium.

## Built: step 6 (2026-11-12)

- **The grid** (`frames/grid.py`, pure): bays along x and y and story heights (typed as
  "6, 6, 8", "3@6" or "2 x 3.5"); nodes at every intersection, named by column line
  (A, B... without I and O), row and level (`A1-0` at the base).
  - Members: columns at every plan point, and beams along x and y at each level above the
    base, grouped "Columns", "Beams X" and "Beams Y".
  - Bracing: X (four halves meeting at a node at the bay's centre) or chevron (to a node
    at the beam's middle, the beam split there), group "Braces", in the end bays or every
    bay of the outside frame lines, each story.
  - `plan` gives the nodes and members `frames.geometry` takes, so a grid frame is the
    same `frame` history step as a typed or DXF one.
- **GUI and agent.** Geometry → New frame from a grid… (`gui/frame_editor.py`, one form in
  the display length unit, one Create) and MCP `frame_grid`, which returns the create
  command with counts by group.
- **Checks.**
  - `tests/test_frame_grid.py`: counts by hand for plane, space, X and chevron grids.
  - `tests/integration/test_frame_grid_solve.py`: an X-braced bay made through MCP and
    solved on PyNite. Equilibrium is exact; the diagonals carry 60.41 kN against the
    truss's H/(2 cos θ) = 60.09 kN (+0.5 %, the columns' share); plus the form.
- **Not yet:** drawing members node to node in the 3D view (the grid, DXF and typed lists
  are the ways in), members between nodes off the grid lines, and editing a grid frame
  as a grid after it is made: its `frame` step's nodes and members are edited instead.

## Plan

1. Frame components and members (sections, releases, offsets, supports), DXF lines, the
   PyNite adapter, linear static, member diagrams; the closed-form benchmarks, the
   CalculiX/Code_Aster cross-check.
2. Load cases and ASCE 7 combinations; P-Delta and the Direct Analysis Method.
3. AISC 360 member checks; the member table.
4. Modal, ELF and modal response spectrum; drift.
5. AISC 370 and the ADM (with the Aluminum Association's shapes).
6. The grid editor in the GUI.
