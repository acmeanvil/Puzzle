---
name: contact-tool
description: contacts between parts: checking them before a run, fixing warnings, press fits and gaps (adjust, offset), and contact results (pressure, status, sliding, forces)
adr: 0056
---
Contacts are `contacts` on a component: each joins a **source** (slave) face selection
to a **target** (master) one, with a `type` (bonded, no_separation, frictionless,
frictional with `friction`, rough), `meshing` (conformal: shared nodes, bonds only;
nonconformal), `stiffness` (accurate, balanced, fast: CalculiX's penalty 50, 10 or 1
E/h), `search_distance` (how far apart faces may start and still count as in reach;
empty: 1 % of the model's size) and `interface`: `as_is` (the default), `adjust`
(source nodes within the search distance moved onto the target: closes small gaps
and penetrations) or `offset` with `offset` (a quantity: + a clearance, − an
interference, the faces start that far apart: a press fit). Adjust and offset move the
mesh's source nodes before writing, for every solver alike; frictionless and
frictional contacts only.

## Check contacts before a run

1. `contact_check(component)` (meshes it; `solver` for one solver's view). Per contact:
   - `solver_input`: what each solver of the component's studies is given, in its own
     words (CalculiX `*TIE` or `*CONTACT PAIR` with K = factor E/h in N/m³; Code_Aster
     `LIAISON_MAIL` or continuous `DEFI_CONTACT`, with SANS_GROUP_NO nodes on bolt ends).
   - `initial.status`: closed, near (gap within a tenth of the search distance), open
     (within it), far (beyond), overhanging (some source nodes have no target face under
     them). `shares`: of the source face's area by status; `largest_gap_m`,
     `largest_penetration_m`, `nearest_m`, `search_distance_m`, in metres.
   - `warnings`: each with a `code`, the contact or body in `object`, and the fix.
2. Fix, one `submit` of an `edit` on the component, e.g. swap the sides:
   `{"command": "edit", "object": "<component>", "ops": [
   {"op": "set", "path": "contacts.0.source", "value": "Plate/Top"},
   {"op": "set", "path": "contacts.0.target", "value": "Block/Bottom"}]}`.
   A frictional contact needs `friction` set in the same edit; leaving frictional, set
   it to null. Then `contact_check` again.

## A press fit or a set gap

`{"command": "edit", "object": "<component>", "ops": [
{"op": "set", "path": "contacts.0.interface", "value": "offset"},
{"op": "set", "path": "contacts.0.offset", "value": {"value": -0.02, "unit": "mm"}}]}`:
model the parts touching (coincident faces) and give the interference as a negative
offset. Benchmarked against Lamé on both solvers (pressure within 0.3 %).

## After a run: contact results

1. `contact_results("<study>")` (`step`: a result step among those with contact
   results): per frictionless or frictional contact `normal_force_N`,
   `tangential_force_N`, `area_m2` and `area_share` in contact, `sticking_share` (of
   the area in contact), `max_pressure_Pa`, `max_penetration_m`, `max_gap_m`,
   `max_sliding_m`.
2. The fields on the source faces, with the `result_*` tools: `contact_pressure` (Pa),
   `contact_shear` (Pa), `contact_gap` (m, − penetration), `contact_sliding` (m),
   `contact_status` (0 open, 1 sliding, 2 sticking; a closed frictionless contact
   slides).
3. Read them as checks: `area_share` near 0 means the contact never closed (a body
   held only by it is wrong); `tangential_force_N` / `normal_force_N` equal to μ with
   `sticking_share` 0 means the whole interface slides; a `max_penetration_m` of more
   than about 1 % of the elements means the penalty is soft (stiffness "accurate").

## Warnings and their fixes

- `contact_overhang`: the source face runs past its target's edge (CalculiX can invert
  elements there). Make the smaller face the source (swap the sides), or trim the face.
- `contact_slave_held`: a frictional contact's source nodes are also held (or any
  contact's are fully fixed, or another contact's source): Code_Aster's multipliers go
  singular. Hold the part away from the interface, or swap the sides. Symmetry or
  plane-strain conditions on a frictionless source face's edge are fine.
- `contact_slave_bolted` (Code_Aster): source nodes shared with bolt ends are left out of
  the contact (SANS_GROUP_NO); nothing to fix, but the rim at the holes isn't in contact.
- `contact_far`: the faces never meet within the search distance. A bonded contact there
  is wrong; check the selections, or raise `search_distance` if the gap is meant
  (quantity, e.g. `{"value": 2, "unit": "mm"}`).
- `contact_nonconforming`: a curved source face meeting a face of another shape (a cylinder or
  ball on a plate: line or point contact). Code_Aster refuses it (its pairing fails); set the
  study's solver to CalculiX.
- `contact_shared_outline`: frictional contact on Code_Aster between faces of the same
  outline is known to fail. Run that study on CalculiX (`solver`: "calculix"), or make
  one face smaller.
- `contact_only_hold`: a body held only by frictionless or frictional contact is free
  until the contact is pressed; the first solve is singular. Add a weak support, a bond
  or a bolt, or set the contact's `extensions.code_aster.contact_init` to "OUI".
- `free_body`: a body neither held nor joined: add a support, contact or bolt.

## Watch for

- Code_Aster's contact pairing can fail on line contact (a cylinder on a flat): the
  diagnosis `pairing_failed` in `run_progress`; run it on CalculiX.

- Warnings don't stop a run; they are logged at its start (`run_progress` messages).
  Explain the numbers (a 0.2 mm gap, 25 % overhanging) rather than the log.
- Contacts on curved faces meshed differently start with small gaps or penetrations
  (a few % of an element): that is meshing, and counts as closed.
- No contact types other than bonds are written by Elmer, FEniCSx or the CFD solvers.
