---
name: beam-sections
description: giving line bodies (frames, beams, columns) a cross-section, typed or a standard AISC/EN shape, and orienting it
adr: 0055
---
A line body (a `polyline` geometry step) is solved as beams once it has a **beam
section** in the component's `beam_sections`: on the part (all its members) or an edge
selection. The section's first axis lies across a rectangle's width and an I's flanges
(the strong axis's direction), so the web runs along its second axis.

## Steps

1. `outline`: find the component and its line parts and edge selections.
2. A standard shape: `sections_find("aisc")` lists AISC families (W, HP, C, MC, WT, HSS,
   PIPE, …), `sections_find("en")` EN ones (IPE, HE A, HE B, UPE, UPN, …);
   `sections_find("aisc", "W", contains="W12")` lists designations with depth, width,
   A, I and J. Pick by depth or I as the user asks.
3. Add it (one `submit`): `{"command": "edit", "object": "<component>", "ops": [{"op":
   "append", "path": "beam_sections", "value": {"selection": "F", "shape": "library",
   "standard": "aisc", "designation": "W12X26", "orientation": [0, 1, 0]}}]}`.
   Typed shapes: `shape` rectangle (width, height), circle (diameter), tube (diameter,
   thickness), box (width, height, thickness), as quantities.
4. Orient it (`orient`, plus `roll` degrees about each member):
   - `vector`: `orientation` [x, y, z], made square to each member;
   - `axes`: `axes` (a coordinate system) and `axes_axis` x, y or z (radial, hoop, axial
     for a cylindrical one);
   - `toward`: `toward` (a point selection) or `toward_point` [x, y, z] in m: each member
     faces it (a ring of columns facing a centre);
   - `normal` or `edge`: `reference`, a face selection's normal or an edge's direction.

## Watch for

- CalculiX takes table HSS and pipe only, on second-order meshes (B32R); I-shapes,
  channels and tees need Code_Aster (CalculiX's general beam gets their torsion wrong).
  Code_Aster beams need first-order meshes (element order 1).
- Single angles (L) aren't taken yet; double angles (2L) are.
- An orientation along a member gives no direction across it: the run is refused with
  the member named; choose another.
- Elmer and FEniCSx refuse beams.
