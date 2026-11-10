---
name: contacts
description: where the parts of an assembly, or the bodies of one imported part, touch; contacts or bonds there in one batch; one part per body; contact types and which solvers write them
adr: 0039
---
Read this when a component has two or more parts, or one part holding several bodies
(an imported STEP assembly is usually one part), and they need joining, when the user
asks to "find contacts", "bond the parts" or "set up the assembly", or when
`next_steps` says a component has several parts and no contacts. To check contacts that already
exist (gaps, overhangs, warnings, results), read the `contact-tool` skill instead.

A contact is an entry in a component's `contacts`. It joins a **source** face selection
to a **target** face selection, each written `Part/Selection`. The source is the slave
side and the target the master side. Make the smaller (or finer-meshed) face the
source.

The search looks at every named face of each part, placed where the mesher places it,
against every face of every other part. A pair counts when the faces are within
`tolerance` of each other, overlap by more than a small area, and face each other
(outward normals opposite within `angle`). Pairs are grouped per pair of parts: one
proposal per pair ("Plate to Bracket"), which may hold several faces on each side.

## Bodies of one part (an imported assembly)

A STEP file with several bodies imports as **one part**. The search works body by body
there too: a proposal between two bodies has `source_part == target_part`, and
`source_body`/`target_body` name the bodies (by a body selection holding just that
body, such as "Pipe and Flange", else `body-N`).

- Touching bodies of one part already share their nodes (the geometry's Share faces,
  on by default). A **bonded** contact between them records that and writes nothing.
- A **frictionless** or **frictional** contact (or a tie, `meshing: nonconformal`)
  between them parts those two faces after meshing: the mesher gives one side its
  own nodes there (Gmsh's Crack plugin), and every other join stays. Only a line
  where a third body meets both stays joined.
- With Share faces off, each body is meshed apart; a bonded contact joins its two
  bodies.
- Code_Aster can't tie (`bonded` + `nonconformal`) two bodies of one part: refused.

To work part by part instead, split the part: `parts_split(component, part, bond)`
returns a plan and a `batch` for `submit` (one undo). Each body becomes a part of its
own (named by its body selection), every reference follows its body, and with `bond`
(default true) bonded contacts join the bodies that touched, as Share faces did. Read
its `notes` to the user: references on a selection spanning bodies that couldn't be
repeated per part. Materials, loads, supports and sections on such a selection are
repeated per part.

## Contact types

| `type` | What it does | Written by |
| --- | --- | --- |
| `bonded` | glued: no gap, no sliding | CalculiX, Code_Aster; Elmer and FEniCSx only when conformal |
| `frictionless` | may open and slide, no friction | CalculiX, Code_Aster |
| `frictional` | may open; slides past `friction` (μ, required) | CalculiX, Code_Aster |
| `no_separation`, `rough` | in the schema | no solver writes them yet |

- A bonded contact is meshed conformally by default: the parts share nodes and no
  constraint is written. `"meshing": "nonconformal"` meshes the parts apart and ties
  them (CalculiX `*TIE`, Code_Aster `LIAISON_MAIL`; Elmer's mortar tie isn't written,
  so Elmer refuses it).
- Every other type is always nonconformal.
- Elmer, FEniCSx and the CFD solvers write no frictionless or frictional contact. A
  study on them with such a contact is refused (code `unsupported`).
- `capabilities(solver)` lists what a solver writes under `contacts`:
  `bonded_conformal`, `bonded_nonconformal`, `frictionless`, `frictional`.

## Steps

1. `outline`: find the component and its parts. The parts' geometries must regenerate.
2. `contacts_find(component, tolerance, angle)`:
   - `component`: the component's name.
   - `tolerance`: the largest gap in **metres** (default 0.5 % of the model's size).
     A 0.2 mm gap is `0.0002`.
   - `angle`: how far from face to face, in degrees (default 10).
   - It returns `proposals` (each with `name`, `source_part`, `source_faces`,
     `target_part`, `target_faces`, `source_body`, `target_body`, `gap_m`,
     `overlap_m2`) and `make_bonded`.
     Nothing is made yet.
3. Read the proposals to the user: the parts, the gap in mm, the overlap in mm².
   Ask which to keep and which type each should be, unless they already said.
4. `make_bonded` is a list of `edit` commands, ready for `submit`:
   - first, one edit per part geometry that appends the face selections (an existing
     selection holding exactly those faces is reused; new ones are named
     "Touches <other part>");
   - last, one edit on the component that appends every contact, all bonded.

   It looks like this:
   ```
   [{"command": "edit", "object": "<plate geometry id>", "ops": [
      {"op": "append", "path": "selections",
       "value": {"name": "Touches Cube", "entity": "face", "members": ["P/+z"]}}]},
    {"command": "edit", "object": "<cube geometry id>", "ops": [
      {"op": "append", "path": "selections",
       "value": {"name": "Touches Plate", "entity": "face", "members": ["C/-z"]}}]},
    {"command": "edit", "object": "<component id>", "ops": [
      {"op": "append", "path": "contacts",
       "value": {"name": "Plate to Cube", "type": "bonded",
                 "source": "Plate/Touches Cube", "target": "Cube/Touches Plate"}}]}]
   ```
5. Change it before submitting, if needed:
   - another type: in the contact's `value`, set `"type": "frictional"` and
     `"friction": 0.2`, or `"type": "frictionless"`;
   - drop a proposal: remove its contact op, and the selection ops only it used;
   - swap the sides: exchange `source` and `target`.
6. `submit(command=<the whole list>, rationale="...")`. A list is one batch: all or
   none, one history entry, one undo.
7. Then read the `contact-tool` skill and run `contact_check(component)` before the
   first solve.

One contact by hand, when the face selections already exist:
`{"command": "edit", "object": "<component>", "ops": [{"op": "append", "path":
"contacts", "value": {"name": "Bolt head to plate", "type": "frictionless", "source":
"Bolt/Head underside", "target": "Plate/Top"}}]}`.

## Units

`tolerance`, `gap_m` in metres; `overlap_m2` in m²; `angle` in degrees. `friction` is a
plain number (μ). A contact's `search_distance` and `offset` are quantities, e.g.
`{"value": 0.5, "unit": "mm"}`.

## Mistakes to avoid

- Passing the tolerance in mm (`0.2` means 200 mm): it finds false pairs across gaps.
- A `frictional` contact without `friction`, or `friction` on any other type: refused.
- `"meshing": "conformal"` on anything but bonded: refused.
- Frictional or frictionless contact on a study run by Elmer or FEniCSx: refused. Set
  the study's solver to CalculiX or Code_Aster, or use bonded.
- `frictional` contact on Code_Aster where it can't run it. Refused before writing:
  a second-order mesh (set the recipe's `element_order` to 1, or run on CalculiX,
  which takes quadratic meshes), and a source (slave) face sharing nodes with a
  `fixed`, `displacement` or `remote_displacement` selection (hold those parts away
  from the interface, or swap source and target). Not checked, but needed for it to
  converge: the source face lies inside the target face (the smaller face is the
  source).
- On Code_Aster any contact whose source face is curved and meets its target along
  a line or at a point (a cylinder or ball on a plate): refused (`unsupported`).
  Run it on CalculiX.
- Submitting the commands one by one: a failure leaves half the selections made. Submit
  the list in one `submit`.
- Making contacts no one asked for: show the proposals first. A large tolerance finds
  near-misses; the gap column is the guard.
- In the GUI the same search is the component's right-click Find contacts… (a review
  table with type and friction per row).
- Telling the user an imported assembly "has no contacts to find" because it is one
  part: its bodies are searched too. Splitting it (`parts_split`) is a choice, not a
  need.
- Splitting a part to get a sliding contact: not needed; a frictionless or frictional
  contact between two bodies of one part already parts their faces.
