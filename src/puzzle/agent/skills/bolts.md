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
     Sizes: "M3"…"M36", fine "M10x1.25", inch "1/2-13 UNC", "1/2-20 UNF".
   - The grade is a project material with proof and yield strengths: the library's
     Fastener group (ISO 898-1 Class 8.8, SAE J429 Grade 5, ASTM A325, …). Add one first
     with `{"command": "add_library_material", "key": "iso_898_1_class_8_8"}`
     (`materials_library` lists keys).
   - `preload`: none; force (`preload_force` quantity); yield_percent (default 70);
     torque (`torque` quantity in N*m, `nut_factor`, 0.2 plain, 0.15 lubricated).
   - `joint`: hole_walls; through_nut (bolt and nut: head and nut annuli);
     tapped (cap screw: head annulus and the tapped thread); stud_nuts; stud_tapped.
3. Many bolts at once: `bolts_generate(component, ...)`, then pass its `make_bolts` to
   `submit`. Filters: `min_diameter`/`max_diameter` of the hole (m), `parts`, `axis`
   [x, y, z], `min_length`/`max_length` (m). `bolt_type` by name, else the largest
   type that fits each hole. `ground_unpaired=true` anchors holes with no partner;
   `same_part=true` joins two holes of one part; `bearing_faces=true` imprints the rings
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

## Watch for

- Two bolts on one face selection, or a rigid end on a fixed face: CalculiX refuses
  ("dependent side of two MPCs", "a rigid end can't also be held there"). Use separate
  hole selections and distributing ends.
- A frictional contact plus bolts on Code_Aster may not converge (ADR 0053): run bolted
  joints with friction on CalculiX.
- `of_proof` above 1 means the bolt is past its proof load: say so, with the bolt's name.
- Bolt groups, rules and `through` (a stack's middle holes) never change a solve.
