# 0039. Find contacts between parts automatically

- Status: Accepted (2026-10-24); built
- Date: 2026-10-23

## Context

An assembly needs a contact (or a bond) at every place two parts touch. Puzzle makes them one at a time: choose a type, a source face and a target face. In an assembly of 20 parts that's dozens of picks. PrePoMax has "Search Contact Pairs" (review item 14): it finds face pairs between parts by distance and angle, groups them, and creates contacts or ties in one go. Ansys does the same on import. Puzzle can already measure distances between named faces (OCC) and keys every face by name and signature (G3).

## Decision

1. **"Find contacts…"** on a component searches every pair of parts for faces that face each other:
   - **distance:** within a tolerance, by default 0.5 % of the model's size, set in the display length unit;
   - **angle:** normals opposite within a set angle (default 10°);
   - **overlap:** a projected area over a minimum.
   - The check uses `BRepExtrema_DistShapeShape` on candidate pairs, first filtered by bounding boxes. Faces already shared (a fragmented, conformal assembly) are reported as conformal and need no contact.
2. **A preview list,** one row per pair: the two faces by name, the distance, the overlap area, and a proposed type (bonded by default). Choosing a row highlights both faces.
   - Per row, the type can be changed (bonded, frictionless, frictional with μ) or the row skipped.
   - Rows can be grouped by part pair, as PrePoMax 3.0 does, so one decision covers a bolted flange.
3. **Accept makes every contact in one batch command** (`Session.submit_batch`), so one undo removes them all.
   - Contact names are readable ("Plate to Bracket 1").
   - Each new contact's faces are saved as selections on the parts, so they survive regeneration (G3).
4. **Agents get the same search** (`contacts_find` over MCP, returning the proposals). They make contacts through the same batch.

## Built (2026-10-24)

- **The search** (`geometry/contacts.py`, `find_contacts`): every named face of each part, placed as the mesher places it (rotated about the origin x, y, z, then moved), against every face of every other part. Candidates are pairs whose bounding boxes, grown by the tolerance, overlap. A candidate is a contact when:
  - `BRepExtrema_DistShapeShape` puts them within the tolerance (default 0.5 % of the model's size);
  - with the gap closed (one face moved along the shortest line), their common area (`BRepAlgoAPI_Common`) is above a minimum (default 1e-6 of the size squared), so faces meeting along an edge don't count;
  - their outward normals at the centre of that common area face each other within the angle (default 10°).
- **Proposals** group the face pairs by pair of parts (one decision for a bolted flange), bonded by default, named "Plate to Cube".
- **Making them** (`contact_commands`): a face selection on each side's geometry holding exactly its faces (an existing one if it matches, else "Touches Cube"), then the contacts, in one batch: one history entry, one undo.
- **GUI:** a component with two or more parts has "Find contacts…". The review table shows the parts, the faces, the gap (display length unit), the overlap (display area unit), the type (Bonded, Frictionless, Frictional; a type none of the component's study solvers write is greyed, the tooltip naming who does) and the friction. Choosing a row highlights both sides' faces in the view. The gap and angle can be changed and searched again.
- **Agents:** MCP `contacts_find(component, tolerance, angle)` returns the proposals and `make_bonded`, the batch for all of them as bonded, to edit and pass to `submit`.
- **Tests** (`tests/test_contact_search.py`): a cube on a plate (its base area found), a 0.2 mm gap found and a 5 mm one not, a cube against the plate's side (the 20 × 10 mm overlap), a cube turned 90° about x, the batch and its undo, the MCP tool and the dialog.
- **Not yet:** imported assemblies whose parts share faces after fragmenting are not reported as conformal separately (they are found as touching, distance 0, and proposed bonded, which meshes conformally by default).

## Amended (2026-11-09): bodies of one part, one part per body

An imported STEP assembly is one part holding every body, so the search found nothing
("add a second part") and the contact tool pointed to a Model → Add contact… that
didn't exist. Now:

- The search works between bodies of one part too (`contacts._face_owners`: each face's
  body; `body_labels`: a body selection holding just that body names it). A face's
  overlap is checked at its centre, or at a point on it when the centre is off it (an
  annulus, a pin in its bore: those were missed between parts too).
- Touching bodies of one part stay joined by Share faces; a frictionless, frictional or
  tie contact between two of them is parted after meshing (`mesh.generate.
  _part_contacts`: Gmsh's Crack plugin duplicates the shared face's nodes, its rim
  opened where only those two bodies meet, the moved body's other faces following).
  Fragmenting the bodies in separate groups was tried first: a body fragmented twice
  loses its first join. The same pass parts parts joined by other parts' bonds (three
  parts, two bonded, one sliding: all three used to be fragmented together, the
  sliding pair joined too). With Share faces off, a bonded contact joins its bodies.
  Code_Aster refuses a tie between two bodies of one part (its LIAISON_MAIL ties into
  the target's whole part).
- Model → Add contact… (two face selections in one form), Find contacts… on every
  component with a part.
- One part per body (`split_parts.plan`; a part's right-click, Model → One part per
  body…, MCP `parts_split`, CLI `split-parts`): each body a geometry (the file
  imported, then a new `keep` history step holding that body), references following
  their bodies, those on a spanning selection repeated per part where they are lists
  (materials, features, sections), bonded contacts where Share faces had joined them;
  one batch, one undo.

Benchmarks: `tests/test_contacts_in_a_part.py`, `tests/test_split_parts.py`,
`tests/integration/test_contact_in_a_part.py` (a stack in one part carries p A = 2000 N
through a frictionless contact on CalculiX).

## Consequences

- Setting up an assembly's contacts becomes one review instead of many picks.
- A tolerance too large finds false pairs (near-miss faces across a gap). The preview and its distance column are the guard, and nothing is made without accept.
- Contact capability per solver is unchanged (CLAUDE.md contacts): the search proposes only types the study's solver can write, and greys out the others with the reason.

## Alternatives considered

- Make contacts silently on import (Ansys's default): it is fast, but users then don't know what was assumed; Puzzle shows proposals first, as for agents.
- Search on the mesh: faster for faceted geometry, but loses face names; the geometric search keeps them.
