# 0039. Find contacts between parts automatically

- Status: Proposed
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

## Consequences

- Setting up an assembly's contacts becomes one review instead of many picks.
- A tolerance too large finds false pairs (near-miss faces across a gap). The preview and its distance column are the guard, and nothing is made without accept.
- Contact capability per solver is unchanged (CLAUDE.md contacts): the search proposes only types the study's solver can write, and greys out the others with the reason.

## Alternatives considered

- Make contacts silently on import (Ansys's default): it is fast, but users then don't know what was assumed; Puzzle shows proposals first, as for agents.
- Search on the mesh: faster for faceted geometry, but loses face names; the geometric search keeps them.
