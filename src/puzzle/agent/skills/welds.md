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
3. Run the study (CalculiX for now: Code_Aster and FEniCSx come with plan step 3).
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
- Elmer can't take a weld (no node-pair springs); 2D models are refused for now.
