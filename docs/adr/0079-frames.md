# 0079. Model and check frames of line members, as SAP2000 and RISA do, by AISC 360 and 370 and the Aluminum Design Manual

- Status: Accepted; steps 1 and 2 built (2026-11-11)
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

## Plan

1. Frame components and members (sections, releases, offsets, supports), DXF lines, the
   PyNite adapter, linear static, member diagrams; the closed-form benchmarks, the
   CalculiX/Code_Aster cross-check.
2. Load cases and ASCE 7 combinations; P-Delta and the Direct Analysis Method.
3. AISC 360 member checks; the member table.
4. Modal, ELF and modal response spectrum; drift.
5. AISC 370 and the ADM (with the Aluminum Association's shapes).
6. The grid editor in the GUI.
