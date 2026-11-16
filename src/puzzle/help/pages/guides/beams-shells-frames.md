---
title: Beams, shells and frames
section: guide
order: 90
keys: panel:members
summary: Model slender members as beams and thin walls as shells, build frames from a grid, combine load cases and check members by AISC 360, AISC 370 or the ADM.
adrs: 0027, 0055, 0079
---

A beam is a line with a cross-section; a shell is a surface with a thickness. Use them
when a part is long and slender, or thin, and you want member forces or plate stresses
without meshing the solid. A frame is a model made only of beams: a building
skeleton, a truss, a portal or a continuous beam.

## How to make a beam

1. Draw the members as a line body: a polyline step in a geometry's history, or
   Geometry → New frame from a grid… (below).
2. Model → Beams and shells → Add beam section… (or right-click the component).
3. Choose which line part or edge selection it covers, and the shape:
   - typed: rectangle, circle, tube or box, sizes in the units shown;
   - a standard section from the AISC v16.0 tables (W, HP, C, WT, HSS, PIPE...) or
     EN 10365/10056-1 (IPE, HE A, HE B, UPE...). The form shows its sizes and A, I, J.
4. Orient it. The section's **first axis** lies along a rectangle's width and an I's
   flanges. Set it by a global direction, a coordinate system's axis, toward a point,
   along a face's normal or an edge, then a roll angle about the member.
5. To orient by a face or edge in the view, use Pick in the view in the form, or Model
   → Beams and shells → Orient a beam section by a pick…, or the section's right-click
   Orient by a picked face… / Orient along a picked edge….

The view draws each member with its section's outline on the axes the solver gets, so
you can see whether the flanges face the right way. The view toolbar's Beams toggle
draws them as lines again.

## How to make a shell

1. Draw a sheet: a sheet or curved sheet step in a geometry's history.
2. Model → Beams and shells → Add shell thickness…: the part or a face selection, and
   its thickness.
3. The offset (in thicknesses, −0.5 to 0.5, default 0) moves the mid-surface: +0.5 puts
   the sheet on the shell's bottom face.

A positive pressure on a sheet pushes against the face's normal.

## How to build a frame

1. Geometry → New frame from a grid…: bays along x and y and story heights, typed as
   `6, 6, 8` or `3@6` (three bays of 6), in the length unit shown. Choose columns,
   beams, and X or chevron bracing in the end bays or all.
2. Nodes are named by column line, row and level (A1-0 at the base) and become point
   selections. Groups Columns, Beams X, Beams Y and Braces are edge selections.
3. Give each group a beam section, and the material (an ASTM grade, for checks).
4. Hold the base nodes: fixed, or a displacement with x, y, z zero (a pin). The twist
   about each straight run must be held somewhere, or the frame is a mechanism.
5. Loads: a [force](help:feature:force) on node selections, a
   [line load](help:feature:line_load) on members (N/m), [gravity](help:feature:gravity)
   for self weight.
6. A study on PyNite (one static step). Cross-check it on CalculiX or Code_Aster.

<!-- key: form:frame_dxf -->
### A frame from a DXF

Geometry → New frame from a DXF… reads a drawing's LINE, LWPOLYLINE and POLYLINE
entities as members, nodes where they meet. Choose the file (copied into the project
when outside it), every layer or one, and the scale in metres per drawing unit (from the
drawing's units; millimetres when it gives none). Each layer becomes a group of members
for its section (layer 0's lines have none); the form shows the members, nodes, size and
groups before OK. Then steps 3 to 6 above.

## Load combinations, P-Delta and the Direct Analysis Method

1. Give each load its case: D, L, Lr, S, R, W, E, or a name of your own.
2. Model → Studies → Load combinations… makes ASCE 7-16's LRFD or ASD combinations from
   the cases used. Wind and seismic act both ways. Each combination is a result step.
3. In the solve step's settings, second order runs P-Delta, and direct analysis runs
   AISC 360's Direct Analysis Method: 0.8 EI and EA, plus notional loads of 0.002 times
   each node's factored gravity load. Both are PyNite's.

CalculiX and Code_Aster take combinations too: each becomes a static step with its
cases' loads factored.

## Seismic design

Model → Studies → Seismic design… sets ASCE 7-16's parameters (S_DS, S_D1, R, C_d,
I_e). Model → Studies → ELF loads… writes the equivalent lateral forces as story loads
of case Ex or Ey. A modal step's response spectrum setting combines modes by CQC, scaled
to the ELF base shear, on PyNite, CalculiX and Code_Aster.

## How to check members

1. Model → Beams and shells → Member design…: which members (every one, a part, a group
   or a member), LRFD or ASD, unbraced lengths, K factors, and a deflection limit as
   L/δ (such as 360; none by default).
2. Run the study, then Window → Members (Ctrl+Shift+K), or Results → Result tools →
   Member checks.
3. One row per member, worst first: section, governing ratio, check, clause and
   combination. A selected member's checks are listed below; Export CSV… saves them.

The code follows the material's group: carbon steel by AISC 360-16, stainless by AISC
370-21, aluminium by the Aluminum Design Manual 2015. Checked: doubly symmetric I
shapes, HSS, pipe, and Puzzle's box, tube, rectangle and circle. Channels, tees and
angles are listed as not checked.

## Options worth knowing

- **Solvers**: CalculiX writes beams as B31/B32 or, for open table sections, exact
  Timoshenko springs; Code_Aster as POU_D_T on first-order meshes. Elmer and FEniCSx
  refuse beams. Shells: CalculiX S6/S8, Code_Aster DKT. See
  [elements](help:reference/elements).
- **Mesh size** sets the stations along each member: 0.5 m is a good start for a frame.
- **Member releases and offsets** (pins, rigid panel zones) are PyNite only so far.

## Mistakes to avoid

- A member with no section: refused. Give the part one, or each group.
- An orientation along a member (a vertical column with first axis z): refused.
- Generic Structural steel has no yield strength, so its members aren't checked. Use an
  ASTM grade (A992, A36, A572 Gr. 50, A500 Gr. C).
- Member checks on unfactored loads: the ratios are not the code's. Use combinations of
  the method's kind.
- Single angles: give the first axis along a leg; Puzzle turns it to the principal axes.
  Don't add α as a roll.
- PyNite's members are Euler-Bernoulli, CalculiX's and Code_Aster's Timoshenko: stocky
  members differ by their shear deflection (0.6 % on a W12X26 over 8 m).
- On CalculiX's spring beams, heat, buckling, large deflection and plasticity are
  refused: run those on Code_Aster.

## For agents and scripts

The agent finds sections with [sections_find](help:mcp:sections_find), builds frames
with [frame_grid](help:mcp:frame_grid) and [frame_geometry](help:mcp:frame_geometry),
lists them with [frame_members](help:mcp:frame_members), and uses
[load_combinations](help:mcp:load_combinations),
[seismic_design](help:mcp:seismic_design), [seismic_elf](help:mcp:seismic_elf),
[seismic_report](help:mcp:seismic_report), [member_design](help:mcp:member_design) and
[member_checks](help:mcp:member_checks). In a terminal, `puzzle members PROJECT STUDY`
([cli:members](help:cli:members)) prints the checks.

## See also

- Theory: [Frame design](help:theory/frame-design), [Seismic design](help:theory/seismic).
- [Geometry](help:guides/geometry)
- [Loads and supports](help:guides/loads-and-supports)
- [Studies and solvers](help:guides/studies-and-solvers)
- [PyNite](help:solver:pynite)
