# 0078. Generate welds along a path, light by default and modelled on request, checked by AISC 360 and 370

- Status: Accepted; step 1 built, step 2 in part (2026-11-11)
- Date: 2026-11-10

## Context

Puzzle connects parts by contacts (ADR 0039), bolts (ADR 0050, beam or modelled, ADR
0077) and threads (ADR 0076), but not by welds. Welded joints are most of a pressure
vessel's and a frame's connections, so today a user either bonds the parts (which says
nothing about the weld) or draws a bead by hand and reads its stresses, which are
singular at the toe and root and grow as the mesh is refined.

The maintainer's direction (2026-11-10):

- A weld generator in the vein of the bolt generator: the weld type and geometry from
  AWS A2.4's symbols, continuous or intermittent, and its path.
- A light weld is the default; a modelled bead is the option, as beam and modelled bolts.
- Strength by AISC first: **AISC 360** for carbon and low-alloy steel, **AISC 370** for
  stainless, chosen from the base metal's material. ASME BPVC later.
- Filler metals for every base metal in the generic library.
- Fatigue by AWS (D1.1 steel, D1.2 aluminium, D1.6 stainless, D1.9 titanium), AISC
  (360 and 370 Appendix 3) or ASME BPVC (VIII-2 Part 5).

What Puzzle has to build on: edge chains from the mesh (`mesh/adjacency.py`); swept
geometry (ADR 0065: `sweep`, `pipe`); node-pair springs that carry and report forces on
CalculiX and Code_Aster (ADR 0076's thread springs, `thread_pairs`); the section tool
(ADR 0042) and linearization (ASME VIII-2 Annex 5-A); contacts between joined bodies
parted after meshing by Gmsh's Crack plugin (ADR 0039 amended), which is how an unfused
root opens; the fatigue tool (ADR 0026); materials filed by type (stainless, structural
steel, aluminium, titanium, copper: `materials.classes`).

References in the maintainer's library (`Z:\nate\Engineering Info`): AISC 360-22 and
360-16, AISC 370-21, Design Guide 21 *Welded Connections*, Design Guide 27 *Structural
Stainless Steel* (2nd ed.), AWS A2.4-2020, D1.1, D1.2-2014, D1.6, D1.9, A5.9, B2.1, BPVC
VIII-1/VIII-2 2023 and IX 2023.

## Decision

1. **A weld is an object of the component** (`Component.welds`, a `Weld`, beside
   `connectors`), never a GUI-only thing:
   - `type`: AWS A2.4's fillet, groove (square, V, bevel, U, J, flare-V, flare-bevel;
     CJP or PJP with its depth of groove and effective throat), plug, slot, or edge;
     one side or both (`sides`: arrow, other, both);
   - `size`: the leg (or two legs, unequal fillet), the groove's depth and root
     opening, the effective throat computed from them by the code's rules (fillet
     0.707 a; PJP by AISC 360 Table J2.1 and 370's equivalent; flare by Table J2.2);
   - `path`: the edge selection it runs along, on the joint between two parts (an edge
     chain, followed round corners), and the two faces it fuses (`members`: the
     selections whose parts it joins), found by `weld_generator` from a picked edge;
   - `pattern`: continuous, or intermittent with segment length and pitch, chain or
     staggered (both sides), the ends' return; all-around;
   - `filler`: a filler-metal material (decision 4) or the base metal's matching one;
   - `formulation`: `line` (default) or `modelled`.
2. **The light weld (`line`, the default).** Stations along the path at the mesh's
   nodes on the joint edge (two per element side, so curved paths follow), each a
   short connector from one leg's footprint to the other: a node-pair spring set, stiff
   as the throat's area times E over a nominal length (effectively rigid, but finite so
   its force is read), spread to each footprint strip by RBE3-style equations (the
   connector's ends distribute, as a weld's legs do). CalculiX `SPRING2` plus
   `*EQUATION`, Code_Aster `DIS_T` with `LIAISON_RBE3` (ADR 0076's helpers), FEniCSx
   k n nᵀ added to K; Elmer refused (no node-pair springs, as for threads). The result
   is the **line force per unit length** at each station in the weld's own frame
   (along, normal to the throat, transverse), the stations' forces over their spacing
   (`weld_line_force`, `post/weld_fields.py`, after every solve, as `thread_fields`),
   which is what the codes check. No bead is meshed; the parts keep their own meshes.
3. **The modelled weld (`modelled`).** The bead drawn as a solid: the weld's section
   (a fillet's triangle with its legs, a groove's fill and reinforcement) swept along
   the path (ADR 0065's `sweep`), fused to both parts (shared nodes); an unfused root
   (a fillet, a PJP's unwelded depth) parted after meshing as a crack (ADR 0039's
   Crack-plugin path). Mesh sizes at the toes and root from the throat. Its line
   force comes from the section through the throat at each station (the section tool,
   ADR 0042), so the same checks run on it; its peak stresses are shown with the
   warning that they are mesh-dependent, and feed the structural-stress fatigue path
   (decision 6), not the strength checks.
4. **Filler metals in the generic library** (group "Filler metal"), one family per base
   metal there, with their classification's minimum tensile strength (F_EXX) and yield,
   from AWS's filler specifications:
   - carbon and low-alloy steel: E60XX, E70XX, E80XX (A5.1, A5.5), ER70S (A5.18), E7XT
     (A5.20);
   - stainless: E/ER308L, 309L, 316L, 2209 (A5.4, A5.9);
   - aluminium: ER4043, ER5356, ER5556 (A5.10);
   - titanium: ERTi-2, ERTi-5 (A5.16);
   - copper: ERCu, ERCuSi-A (A5.7);
   - each base metal names its matching filler (`Material.filler`), so a weld left
     without one takes it. Electrical steel and the gases have none.
5. **Strength by AISC, chosen by the base metal.** `puzzle/welds/aisc.py`:
   - **AISC 360 Chapter J2** for carbon and low-alloy steel: fillet R_n = F_nw A_we with
     F_nw = 0.60 F_EXX (1.0 + 0.50 sin^1.5 θ) where the load direction is known (Eq.
     J2-5), the instantaneous-centre method for weld groups in plane (J2.4 and its
     Commentary, the Manual's Part 8), the
     base metal's limit (J4), φ 0.75 (LRFD) or Ω 2.00 (ASD); CJP at the base metal's
     strength; PJP by Table J2.5; minimum and maximum sizes and lengths (Tables J2.4,
     J2.3; intermittent at least 4 sizes and 1½ in) as warnings.
   - **AISC 370 Chapter J2** for stainless: its own filler and base-metal strengths and
     rules, taken from 370-21 itself (not assumed the same as 360's), checked against
     Design Guide 27's examples.
   - Which one: the weld's base metals' class (`materials.classes`): stainless → 370;
     carbon and low-alloy steel → 360; aluminium → the Aluminum Association's ADM
     (welds in ADR 0079's member checks share it; the ADM's weld provisions, Chapter J,
     with the heat-affected zone's reduced strengths), else refused with the reason.
     Two different base metals (a stainless-to-carbon weld) take the weaker's rules
     and say so.
   - Results per weld and station: the demand (line force), the capacity, the
     utilisation (demand over φ R_n or R_n / Ω), the governing station, and the clause;
     a table like the bolt table (`gui/weld_table.py`), MCP `weld_results`.
6. **Fatigue by the user's code** (a weld's `fatigue_code`): AWS D1.1/D1.2/D1.6/D1.9 (the
   weld's detail category from the joint and loading, the stress range on the throat
   from the line force's range), AISC 360/370 Appendix 3 (the same categories, Table
   A-3.1), or ASME VIII-2 Part 5's structural-stress method (5.5.5: the through-
   thickness structural stress from the nodal forces at the weld toe, mesh-insensitive,
   on modelled welds or the plates at a light weld's toe). Through the fatigue tool's
   duty (ADR 0026); each code's curves and categories as data with their references.
7. **The generator, and everything a command.** `puzzle/weld_generator.py`: pick an edge
   (or an edge selection) on a joint, choose the type, size and pattern; it finds the
   two parts, their faces, the path's chain and the filler, and returns one batch (the
   selections, the weld). Rules as with bolts (`Component.weld_rules`: "every edge
   where these two parts meet"). GUI: Model → Welds → Add weld… and a weld table with
   AWS symbols drawn (`post/weld_symbols.py`); MCP `weld_types`, `weld_add`,
   `weld_results`, `weld_make_modelled`/`weld_make_line`; schematic nodes (ADR 0071
   decision 8); skill `welds` (ADR 0057).

## Benchmarks

- AISC Design Guide 21's and the Manual's worked examples (eccentrically loaded weld
  groups by the instantaneous centre, Manual Part 8 tables) for 360; Design Guide 27's
  for 370: capacities to the printed digits.
- A light weld's line force against hand statics: a lap joint in tension (uniform
  P / L), a cantilevered bracket welded all around (the elastic method's
  f = sqrt(f_x² + f_y²) at the corners), on CalculiX and Code_Aster, the two agreeing.
- A modelled fillet's section line force against the light weld's on the same joint
  (within 2 %), and its toe structural stress against VIII-2's example.
- Fatigue: AWS D1.1's and AISC Appendix 3's tabulated examples.

## Consequences

- Welded joints get the checks engineers make by hand, at FE cost, with the line force
  as the quantity (mesh-insensitive), not peak stress.
- The light weld adds springs like the thread's, so its solvers are CalculiX,
  Code_Aster and FEniCSx; Elmer is refused.
- Filler metals join the generic library (data only; their strengths cited).
- To watch: a weld's footprint strips must not overlap another weld's or a bolt's
  spider; intermittent welds on curved paths; welds between shells (ADR 0027) need the
  shell-edge form of the connector (a later step).

## Alternatives considered

- **Bond the parts and post-process stresses at the joint.** Mesh-dependent peak
  stresses, no line force, and no throat: the codes can't be applied.
- **Modelled beads only.** Costly for many welds and their peak stresses still need
  interpreting; the light weld is what most checks need.
- **ASME first.** The maintainer chose AISC first; VIII-2's structural-stress fatigue
  comes with decision 6.

## Built: step 1 (2026-11-11)

- **`Weld`** (`Component.welds`; left out of content hashes when empty): `type` fillet or
  groove (`groove` cjp/pjp), `sides`, `leg` and `leg2`, `path` (an edge selection),
  `faces` (the path's part's leg face, then the other part's), `pattern` continuous or
  intermittent (`segment`, `pitch`), `filler`, `formulation` line or modelled.
- **The light weld** (`adapters/weld_pairs.py`):
  - Stations are the path's mesh nodes in order along it (an open chain, or a loop for a
    weld all round).
  - Each face's footprint is its nodes within its leg of the path, placed by (s, d).
  - The other part's footprint keeps only nodes outside the path's part. A node there
    with a node of the path's part about on it is the seat under a lap, or a bracket's
    foot, not weld.
  - Each footprint node is paired with the other footprint's node nearest the same
    place across the weld, both ways, so the pairs fill the fillet's triangle.
  - Each pair is a spring in x, y and z, E × length / pairs (CalculiX `SPRING2` node to
    node, `inp._welds`).
  - This realises decision 2's "spread to each footprint" with node pairs rather than
    RBE3 equations: no helper nodes, and the pairs across the legs also carry the
    weld's moment about its own line.
- **Results** (`post/weld_fields.py`, after every solve, solver-neutral, from the
  displacements):
  - `weld_line_force` at the stations: along, throat_normal, throat_shear, magnitude,
    x, y, z, and twist (N·m/m about the path).
  - `weld` per weld: the total force, the peak line force, and the moment about the
    origin.
  - `results()` summarises them for MCP `weld_results`.
- **The generator** (`weld_generator.py`):
  - From edges of one part: of each edge's two faces, the one touching another part
    (`geometry.contacts.find_contacts`) is the seat, the other carries the first leg, and
    the touching face carries the second.
  - `commands` is one batch: the selections (existing ones holding just those faces
    reused) and the weld. MCP `weld_add`.
- **Refused:** every solver but CalculiX (`Capabilities.welds`), and 2D models.
- **Skill** `welds`.

| Benchmark (CalculiX, 3 mm tets) | Puzzle | Hand |
| --- | --- | --- |
| Lap joint, the weld's total force | 10000 N, y 0.0008 N | P = 10 kN |
| Lap joint, mean line force | 207.8 N/mm | P/L = 200 N/mm (+3.9 %: the peel's z share) |
| Bracket all round, shear carried | 10000 N | P |
| Bracket, moment about x | 600.0 N·m | P h = 600 N·m |
| Bracket, normal line force on the ±y sides | 419.6 N/mm | elastic method M (d/2)/I = 450 N/mm (−6.8 %) |

The bracket's moment balances exactly only with the twist: about 7 % of it is the fillets' resistance to turning about their own lines (on the ±y sides, which run along x), which the elastic method leaves out. Tests: `tests/integration/test_welds.py`, `tests/test_welds.py`.

## Built: step 2 in part (2026-11-11)

- **Filler metals** in the generic library (group "Filler metal"), with AWS's minimum
  all-weld-metal strengths:
  - carbon and low-alloy steel: E60XX, E70XX and E80XX electrodes (A5.1, A5.5), ER70S-6
    (A5.18) and E71T-1 (A5.20), with tensile and yield;
  - stainless: E/ER308L, 309L, 316L and 2209 (A5.4, A5.9), tensile only.

  Aluminium, titanium and copper fillers are not in yet. The base metal's matching filler
  is by its group (`weld_strength.MATCHING`: carbon and low-alloy steel → E70XX, as AWS
  D1.1 Table 5.4), not a `Material.filler` field.
- **AISC 360-16 J2** (`weld_strength.py`) at each station of a light weld:
  - the effective throat (0.707 w, or l1 l2 / √(l1² + l2²); a PJP's depth as given);
  - F_nw = 0.60 F_EXX (Eq. J2-3), or with `directional` 1.0 + 0.50 sin^1.5 θ (Eq. J2-5);
  - φ 0.75 and Ω 2.00, giving the LRFD and ASD utilisations and the governing station.
  - CJP is its base metal's (J2.5).
  - Stainless (AISC 370) and aluminium (ADM) are refused with the reason.
  - The base metal (J4) and Table J2.4's minimum sizes are not checked: they need the
    plates' thicknesses.
  - It is in `weld_fields.results`' `strength`, so MCP `weld_results` (with
    `directional`) gives it.
- **Checks:**
  - `tests/test_weld_strength.py`: the Manual's 1.392 kip/in per sixteenth (Eq. 8-2a,
    E70) and 1.5 times it across the weld; throats; utilisations; the filler chosen.
  - `tests/integration/test_welds.py`: the lap joint's E70XX check at its governing
    station, 0.412 (379.7 N/mm against 921.4 N/mm).
- **Not yet: the instantaneous-centre method.** The 15th Edition Manual's Table 8-4 on
  the maintainer's drive is a low-resolution scan (only its first row, C = 3.71 at
  a = 0, reads: the two lines' 2 × 0.60 × 70 × 0.707 / 16). The 14th Edition PDFs are
  JBIG2-coded, which nothing here decodes, and Design Guide 21 (2nd edition) has no
  instantaneous-centre example. It waits for a readable table or worked example to
  check it against.
- **Also left:** the weld table in the GUI.

## Plan

1. `Weld` in the schema, the generator, the light weld on CalculiX (springs and
   equations, forces per station), the lap-joint and bracket benchmarks.
2. Filler metals in the generic library; AISC 360 J2 and the instantaneous centre;
   Design Guide 21's examples; the weld table and MCP.
3. Code_Aster and FEniCSx light welds; AISC 370 (Design Guide 27's examples).
4. Modelled welds (sweep, fused, the root parted); the section line force.
5. Fatigue: AWS D1.x and AISC Appendix 3 categories; VIII-2's structural stress.
6. Shell edges; the ADM's weld provisions with ADR 0079.
