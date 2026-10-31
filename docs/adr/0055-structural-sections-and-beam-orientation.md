# 0055. Standard structural sections for beams, and orienting them

- Status: Accepted (2026-10-30, at the maintainer's request)
- Date: 2026-10-30

## Context

Beams (ADR 0027) take four shapes typed by their sizes (rectangle, circle, tube, box), oriented by one vector, `BeamSection.orientation`, the section's first axis. Frames are made of rolled shapes: W, HP, channels, angles, HSS and pipe in North America (the AISC Shapes Database), IPE, HE, UPN/UPE, angles in Europe (EN 10365, EN 10056-1). The maintainer asked for AISC and ISO/EN sections in a section library, and for beams to be oriented by the world or a local coordinate system, by geometry, or by a named selection.

The maintainer's Lacewing Lab app already carries both tables, generated from their sources: the AISC Shapes Database v16.0 (W, M, S, HP, C, MC, L, 2L, WT, MT, ST, HSS, pipe; 43 tabulated values each, US units) and ArcelorMittal's Sections and Merchant Bars sales programme (EN 10365:2017 I, H and channel sections and EN 10056-1:2017 angles, with EN 1993-1-1 properties).

The solvers: Code_Aster's POU_D_T takes any section as `SECTION='GENERALE'` (A, IY, IZ, JX, shear coefficients AY, AZ, and the shear centre's offsets EY, EZ). CalculiX expands B31/B32 beams into solids, which needs a closed outline (RECT, CIRC, PIPE, BOX). Its other beam, the U1 element with `*BEAM SECTION, SECTION=GENERAL`, reads A, I11, I22 and one shear factor, but its source (`e_c3d_u1.f`, CalculiX 2.23) gives torsion the stiffness G (I11 + I22) / L, the polar moment, not G J: right for a round bar, and about 1,700 times too stiff for a W12X26 (J 5.2 cm⁴ against 9,211 cm⁴). It also takes no offsets and no second-order terms. Elmer and FEniCSx refuse beams.

## Decision

1. **A section library** (`puzzle.sections`): the AISC v16.0 and EN tables converted once from Lacewing Lab's data by `scripts/import_sections.py` into JSON shipped with Puzzle (`sections/aisc.json`, `sections/en.json`, SI, each row its designation, family and the tabulated values), with the sources named. A `Section` gives a designation's A, the second moments about its strong and weak axes, J, the shear areas (EN's Avz and Avy where tabulated; else the web's d·tw and the flanges' 2·bf·tf, each × 5/6 for a solid shape), the shear centre's offset, the principal axes' angle (angles) and the outline's sizes for drawing.
2. **`BeamSection.shape = "library"`** with `standard` (aisc or en) and `designation` ("W12X26", "IPE 300", "L4X4X1/2"); the existing shapes stay. The section's first axis is its strong axis's direction across the flanges (EN y-y; AISC x-x), so the web lies along the second axis: an I-beam set like a rectangle of the same depth and width bends the same way.
3. **Orienting a beam** (`BeamSection.orient`), resolved to each member's first axis before writing (`puzzle.beams.orientation`), always made square to the member, then turned by `roll` degrees about it:
   - `vector`: a direction in the world (as now);
   - `axes`: an axis (x, y or z) of a coordinate system (ADR 0049), Cartesian, cylindrical (radial, hoop, axial at the member) or spherical;
   - `toward`: toward a point: a point selection, a construction point or typed coordinates (each member's own direction to it, so a ring of columns faces a centre);
   - `normal`: along a face's normal, a face selection or a picked face (purlins square to a roof);
   - `edge`: along a geometry edge or a named edge selection's direction.
   A member that runs along its orientation (no square direction left) is refused with its name.
4. **Solvers:** Code_Aster writes library sections as `GENERALE` (A, IY strong, IZ weak, JX, AY = A/A1, AZ = A/A2): the table's values, exact. CalculiX writes the closed shapes it can expand: rectangular HSS as `BOX` and round HSS and pipe as `PIPE`, of the design wall thickness (square corners, so A and I are a few per cent above the table's rounded-corner values); open shapes (I, channels, tees, double angles) are refused on CalculiX, naming Code_Aster, since U1 would get their torsion wrong. Single angles, whose principal axes lie at an angle to their legs, come once a benchmark fixes the sign of that angle; until then they are refused on both.
5. **GUI:** the beam section dialog chooses the standard, the family and the designation (with its main sizes and properties shown, and the outline drawn), and how it is oriented; the tree names it ("W12X26 on Frame/Columns"); the view draws library members with their outline.
6. **Benchmarks** (`tests/integration/test_beam_sections.py`, both solvers): a cantilever of a W and of an IPE, tip load along each axis against Timoshenko's δ = PL³/(3EI) + PL/(κGA); torsion against φ = TL/(GJ); the same frame oriented each way (vector, a coordinate system's axis, toward a point, a face's normal) giving the same answer; the library's areas and second moments against the tables.

**Order:** the library and its data; library sections on Code_Aster, HSS and pipe on CalculiX; orientation by systems, points, faces and edges; the dialog and drawing; single angles.

## Consequences

- Frames are built from the shapes engineers buy, and oriented the way they think about them.
- Two data tables ship with Puzzle (published section data, with their sources), generated, not edited.
- The adapters gain a section kind each. CalculiX frames of open sections run on Code_Aster; a CalculiX path for them (an exact beam written as springs, as ADR 0027's bolts are) can follow if wanted.

## Alternatives considered

- **Section properties typed by hand only:** error-prone for hundreds of shapes and no outline to draw.
- **Meshing the cross-section (sectionproperties) for every shape:** exact for any outline, but slow and a large dependency; the tables already hold the properties, and remain the reference engineers check against.
- **Per-element orientation written to the solvers:** what `toward` needs for a curved member; members are straight edges, so one direction per member is written and a curved member is split by its mesh into members.

## Built (2026-10-30)

- **The library** (`puzzle.sections`; `sections/aisc.json`, 2,299 sections, and `sections/en.json`, 738; `scripts/import_sections.py` from Lacewing Lab's tables): designations by family, A, strong and weak second moments, J, shear areas, mass and sizes; checked against the AISC Manual and EN tables (`test_beam_sections.py::test_the_tables_hold_the_published_values`).
- **Library sections** (`BeamSection.shape = "library"`, `standard`, `designation`): Code_Aster `GENERALE` with the tables' values, within 0.01 % of Timoshenko on a W12X26, an IPE 300 and an HSS6X4X1/4, both axes; CalculiX HSS as `BOX` and round HSS and pipe as `PIPE` on B32R elements (CalculiX expands those on B32R only; Puzzle's own box and tube shapes now get B32R too), within 3 % (its expanded box takes next to no shear strain; the thin-wall shear term is 3 % of the total); CalculiX refuses open sections, naming Code_Aster.
- **Orienting** (`BeamSection.orient`: vector, axes with `axes_axis`, toward a point selection or `toward_point`, normal or edge of a `reference` selection; `roll`): resolved by `puzzle.beams.with_orientations` after meshing; a section over members needing different directions is split into one per member, each on a mesh line group of its own ("Part/member k"). An L-frame of W12X26, whose tip deflection is nearly all the first member's twist through J, gives the same answer within 0.01 % oriented by a vector, a coordinate system's z turned 90°, and toward a far point (one direction per member).
- **GUI**: Add beam section… takes a table section (standard, family, designation, its sizes and properties shown) and how its first axis is set; Properties offers the section's family's designations and shows only the orientation's own fields.
- **Not done yet:** single angles (refused), drawing members with their outline, and orienting by a picked face in the view.
