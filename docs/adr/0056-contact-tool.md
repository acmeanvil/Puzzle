# 0056. A contact tool: each contact's particulars before and after the solve

- Status: Accepted (2026-10-30; built after ADR 0054)
- Date: 2026-10-30

## Context

Contacts are found automatically (ADR 0039) and each has a type (bonded, no separation, frictionless, frictional, rough), a friction coefficient, a meshing (conformal or nonconformal) and a stiffness choice (accurate, balanced, fast: CalculiX's penalty 50, 10 or 1 E/h). What each solver is then given differs: CalculiX `*TIE`, or surface-to-surface `*CONTACT PAIR` with a linear penalty; Code_Aster `LIAISON_MAIL`, or continuous `DEFI_CONTACT` with Lagrange multipliers, `ORIE_PEAU`, and fastener nodes left out of the zone (`SANS_GROUP_NO`, ADR 0053). None of this is visible before a run, and nothing about contact comes back after one but the displacements and stresses.

It cost time on the bolted bracket: frictional contact on Code_Aster failed four ways before the log, read by hand, showed singular friction multipliers on seat nodes; the seat's slave and master faces have the same outline, and Code_Aster's 3D friction is fragile there. Ansys's Contact Tool answers these questions before the solve (initial status, gap, penetration, pinball region per contact) and its contact results after (pressure, status, sliding, penetration). The maintainer asked for the same.

## Decision

1. **The Contact tool** (Model → Contact tool…, a component's menu; a docked panel like the bolt table, ADR 0050), with three tabs:
   - **Setup**: one row per contact: name, the two sides, type, friction, meshing, stiffness, and *what the solver will be given* in its own words (CalculiX `*CONTACT PAIR`, penalty K = 10 E/h = … N/m³; Code_Aster continuous, Coulomb 0.2, `SANS_GROUP_NO` 24 nodes), from the adapters themselves (`Adapter.contact_summary`), so the table never says what the deck doesn't. Cells edit as commands, several rows at once.
   - **Initial state**, worked out from the mesh before any solve (`puzzle.contact_check`): for each slave face's nodes, the nearest point on the master side, the gap or penetration along the normal, and whether it lies within the search distance (Ansys's pinball; default 1 % of the model, per contact). Each contact gets a status (closed, near, open, far, or overhanging where slave nodes find no master face under them), the largest gap and penetration, and the share of its area in touch; drawn on the faces in the view (closed green, near yellow, open orange, far red, overhang purple).
   - **Results**, from a chosen run and step: contact pressure, status (sticking, sliding, open), sliding distance, gap and penetration per slave node, drawn as fields; per contact the total normal and tangential force, the area in contact and the largest pressure. CalculiX gives them in the `.frd` (`CONTACT` stresses CSTR: pressure, two shears, and `CDIS`: opening and slips); Code_Aster in `CONT_NOEU` (contact status, pressure, gap, slips). Both read into `Results` as `contact_*` fields on the slave nodes.
2. **Interface treatment** per contact (Ansys's): keep the geometry as it is (default); *adjust to touch* (close an initial gap or penetration by moving the slave nodes onto the master in the deck: CalculiX `ADJUST` on `*CONTACT PAIR`, Code_Aster `ADAPT_COEF`/initial closure as `CONTACT_INIT='OUI'`); or an *offset* (a set clearance or interference: a press fit).
3. **Checks before the run** (`Project.problems` and the Advisor, ADR 0040), each naming the contact and the fix:
   - a slave face overhanging its master (CalculiX inverts elements; swap the sides or trim the face);
   - slave nodes also held, tied or in a fastener's spider (Code_Aster's singular multipliers: the bolted bracket's case; they are left out or the contact refused);
   - contacts initially far apart (never close: a bonded contact would be wrong; a frictional one leaves a body free);
   - a frictional contact on Code_Aster where slave and master share their outline, which is known to fail (ADR 0053), with the suggestion to run it on CalculiX;
   - a body held only by frictional contact (no other path to ground).
4. **The agent** gets `contact_check` (the initial state and the checks) and the Results tab's per-contact table through MCP, so it can explain a contact problem from numbers, not log tails.
5. **Benchmarks:** the initial state against hand geometry (two blocks a known gap apart, one overhanging); Hertz contact of a cylinder on a plate (peak pressure and contact half-width) on both solvers; a block sliding on a plane under a known friction (sliding starts at μ N); a press fit (an offset) against Lamé's interference pressure.

## Consequences

- A contact model can be checked before it is run, and a failed or doubtful contact explains itself after.
- The adapters gain a contact summary and contact result readers; what they report must match what they write (the summary comes from the same code that writes the deck).
- Results carry contact fields only on slave nodes; quantities and the view handle fields defined on part of the mesh.

## Alternatives considered

- **Leave it to the log and the solver's own messages:** what we have; it needed an expert to read a singular friction multiplier.
- **An initial-state check only, no contact results:** the most useful half before a run, but pressure and status are what tell a converged contact from a wrong one.
- **One tool per solver:** the questions are the same; only what each solver is given differs, which the Setup tab shows.
