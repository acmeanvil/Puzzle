---
name: welds
description: welds between parts: a fillet or groove along an edge, continuous or intermittent, as a light weld (springs across the joint); its line force per length along the path, total force and twist
adr: 0078
---
Read this when the user wants two parts welded, asks what a weld carries, or asks
whether a weld is big enough. A weld here is a connection along a path, not bonded
faces: the parts keep their own meshes, and the weld's **line force** (N/m) at stations
along it is what the codes check. AISC 360/370 strength checks come in a later step
(ADR 0078 plan step 2); for now read the line force and compare by hand
(φ R_n = 0.75 × 0.60 F_EXX × throat per length for a fillet by AISC 360 J2.4).

## Steps

1. Find the edge the weld runs along: an edge of one part lying on the other, named
   by its two faces, e.g. `B/-x|B/-z` (a lapped plate's end edge on the plate below;
   a bracket's foot edges for a weld all round). `selections_check` and the geometry's
   entities list names.
2. Plan it: `weld_add(component, part, edges=[...], leg={"value": 6, "unit": "mm"},
   name="End fillet")`. Of each edge's faces, the one sitting on another part is the
   seat; the other carries the first leg; the other part's touching face the second.
   Options: `leg2` (unequal fillet), `type` "groove" with `groove` "cjp"/"pjp",
   `pattern` "intermittent" with `segment` and `pitch`. It returns `joins`, the faces
   it found, and `batch`: pass that to `submit` (one undo). It reuses face selections
   that already hold exactly those faces.
3. Run the study on CalculiX, Code_Aster or FEniCSx (they agree to four digits).
4. `weld_results(study)`: per weld the total force (N, which balances what the joint
   carries), the largest line force (N/m) and where, the mean, the welded length, and
   each station's line force in the weld's frame: `along` the path, `throat_normal`,
   `throat_shear`, `magnitude`, x, y, z, and `twist` (N·m/m, the moment about the path
   itself). The nodal field `weld_line_force` and the global `weld` (x, y, z, peak, and
   mx, my, mz about the origin) are read with the `result_*` tools too.

## Units

Legs and lengths are quantities (mm is fine); line forces come back in N/m (1 N/mm =
1000 N/m), twist in N·m/m.

## Mistakes to avoid

- Picking an edge neither of whose faces touches another part: the weld must sit on the
  joint. Two parts meshed as one (bonded conformally, or one STEP part) have no joint
  to weld; make them separate parts with no contact between them, the weld joining them.
- Leaving a bonded contact on the same faces as a weld: both would carry the load.
- Comparing the peak line force to a capacity without saying where it is: ends and
  corners gather force (the parts aren't rigid), as in the elastic method's corners.
- Expecting the elastic method's moment from the line forces alone: the fillet's own
  resistance to turning about its line (`twist`) carries some of a bending moment.
- Elmer can't take a weld (no node-pair springs); 2D models and shell edges are refused
  for now.

## Strength (AISC 360 J2, ADR 0078 step 2)

`weld_results(study)` gives each light weld's `strength` at its governing station:
- the filler and F_EXX: the weld's `filler` (a material with a tensile strength, the
  generic library's group "Filler metal": E70XX, ER70S-6, E308L...), else the base
  metal's match (carbon steel: E70XX);
- the effective throat, the design (LRFD, φ 0.75) and allowable (ASD, Ω 2.00) strength
  per length, and the utilisations.

Use the LRFD utilisation when the loads were factored for LRFD, the ASD one for service
loads. Pass `directional=true` for Eq. J2-5's 1 + 0.5 sin^1.5 θ, allowed only for linear
welds and groups loaded in plane through their centre. The base metal (J4) and the
minimum size (Table J2.4) are yours to check.

- Stainless base metal: AISC 370-21 J2 (the same F_nw, φ, Ω), matching filler E308L
  (304), E316L (316), E2209 (duplex).
- Aluminium: the Aluminum Design Manual 2015 J.2, the lesser of the weld metal (0.6 ×
  0.85 F_tuw of the filler, 4043 by default, on the throat) and the base metal (0.6
  F_tuw welded, the material's `welded_tensile_strength`, on the leg); φ 0.75, Ω 1.95;
  `directional` takes 1.36 where the load runs across the weld.
- `strength.instantaneous_centre`: the group's in-plane force and moment about its
  centroid (from the solve) against its strength by the instantaneous centre of rotation
  (the Manual's Part 8 method): P_n, eccentricity, utilisations. A `note` says when the
  group is also bent out of its plane, which the method leaves out.
- The Welds panel (Window → Welds) shows the same per weld.

## Fatigue (AISC 360 Appendix 3; AWS D1.1 nontubular is the same)

`weld_fatigue(study, weld, category="F", cycles=2e6, steps=[1, 2])`: the stress range
between two steps (or one step from zero) against F_SR(n). Category F (Table A-3.1 Case
8.2: shear on a fillet's throat) reads the line forces; A to E′ read the plates' largest
principal stress range at the weld's footprint (mesh-dependent: refine there, and pick
the category of the detail as Table A-3.1 draws it).
