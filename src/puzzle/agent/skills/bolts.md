---
name: bolts
description: adding, generating, editing or checking bolts (fasteners): bolt types, sizes, preload, joint styles, anchors to ground, bolt results
adr: 0050
---
Bolts are `connectors` on a component: each joins two face selections (usually hole
walls) with a beam, or one hole to the ground. A bolt may name a **bolt type**, which
holds its size, grade, preload, joint style, formulation and ends; what the bolt sets
itself wins.

## Steps

1. `outline`: find the component, its parts and face selections, and any bolt types.
2. A bolt type, if there is none for the size wanted (one `submit`):
   `{"command": "create", "kind": "bolt_type", "data": {"name": "M10 8.8",
   "standard": "iso_coarse", "size": "M10", "material": "<grade's material name or id>",
   "preload": "yield_percent", "preload_percent": 70, "joint": "through_nut"}}`.
   - `standard`: iso_coarse, iso_fine, unc, unf, or custom (then give `diameter`).
     Sizes: "M3"…"M64", fine "M10x1.25" (and "M12x1.25", "M20x2", …), inch "1/2-13 UNC"
     to "4-4 UNC", "1/2-20 UNF" to 1 1/2 in. `bolt_sizes(standard)` lists them with stress
     area, bearing diameter, ISO 273 hole and the head's and nut's sizes.
   - The grade is a project material with proof and yield strengths: the library's
     Fastener group (ISO 898-1 Class 8.8, SAE J429 Grade 5, ASTM A325, F1554 anchor rods,
     F593 stainless, A193/A320 bolting, …; some specify no proof load, so bolt results
     give only the share of yield). Add one first
     with `{"command": "add_library_material", "key": "iso_898_1_class_8_8"}`
     (`materials_library` lists keys).
   - `preload`: none; force (`preload_force` quantity); yield_percent (default 70);
     torque (`torque` quantity in N*m, `nut_factor`, 0.2 plain, 0.15 lubricated).
   - `joint`: hole_walls; through_nut (bolt and nut: head and nut annuli);
     tapped (cap screw: head annulus and the tapped thread); stud_nuts; stud_tapped.
3. Many bolts at once: `bolts_generate(component, ...)`, then pass its `make_bolts` to
   `submit`. Filters: `min_diameter`/`max_diameter` of the hole (m), `parts`, `axis`
   [x, y, z], `min_length`/`max_length` (m). `bolt_type` by name, else the largest
   type that fits each hole. Holes stack: coaxial holes in different bodies (of
   different parts, or several bodies of one part, as a STEP assembly imported as one
   part), any number of layers, make one bolt from the first hole along the axis to the
   last, the holes between in its `through` ("Stack hole n" selections); a line splits
   into two bolts where one hole ends more than a hole diameter before the next begins.
   `ground_unpaired=true` anchors holes with no partner;
   `same_part=true` joins two holes of one body (a clamp); `bearing_faces=true` imprints the rings
   under heads and nuts in the geometry (exact bearing faces). Run the same `rule` again
   after the geometry changes: `already_there` and `stale` say what changed.
4. One bolt by hand: `{"command": "edit", "object": "<component>", "ops": [{"op":
   "append", "path": "connectors", "value": {"name": "Bolt 1", "source": "Lower/Hole 1",
   "target": "Upper/Hole 1", "type": "<bolt type id>"}}]}`. Without a type give
   `diameter` and `material`. A bolt to ground: `"ground": true`, no `target`,
   `ground_length` (default 2 d) along the hole's axis toward `ground_direction`
   (default down).
5. After a run: `bolt_results(study)`: each bolt's axial, shear, torsion, bending at
   both ends, stress and its share of proof and yield, worst first.

6. Hand checks of one joint, before or beside the FE model: `bolt_vdi2230(size,
   clamp_length, ...)` (VDI 2230-1:2015): bolt and clamped-part resiliences (the bolt's
   stiffness 1/δS is what a beam bolt should have), load factor Φ, the assembly preload
   FMzul at 90 % of Rp0.2 and its torque MA, the nut factor K that torque implies (use it
   as `nut_factor` with `preload` torque), embedding loss, and with `axial_load` the
   bolt's extra load, equivalent stress (`of_rp02`) and fatigue safety. Lengths in m,
   stresses in Pa, loads in N; `tapped=true` for a cap screw in a tapped hole, `head`
   socket for ISO 4762. Friction μ: 0.10-0.16 oiled steel (Table A5 class B), 0.12 if
   unknown. Standard heads, nuts and washers by table: `fastener_tables(table, size)`.

## Watch for

- Two bolts on one face selection, or a rigid end on a fixed face: CalculiX refuses
  ("dependent side of two MPCs", "a rigid end can't also be held there"). Use separate
  hole selections and distributing ends.
- A frictional contact plus bolts on Code_Aster may not converge (ADR 0053): run bolted
  joints with friction on CalculiX.
- `of_proof` above 1 means the bolt is past its proof load: say so, with the bolt's name.
- A bolt from the head's hole to the nut's, not one per pair of layers: three plates on
  one axis are one bolt, not two or three.
- Bolt groups, rules and `through` (a stack's middle holes) never change a solve.
- `bolt_vdi2230` defaults the parts' outside diameter to 3 dW (its `notes` say so): give
  `outer_diameter` (the flange width, or twice the edge distance) for a real joint. Φ is
  for a load at the head and nut (n = 1); a load entering mid-plate needs `n` < 1
  (Table 2: 0.3-0.5 is common).
