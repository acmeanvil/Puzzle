# 0056. A contact tool: each contact's particulars before and after the solve

- Status: Accepted (2026-10-30); built 2026-10-31 (Hertz on Code_Aster refused up front; making it run is open)
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

## Built

Phase 1 (2026-10-31): decision items 1 (Setup and Initial state tabs), 3 and 4; the
initial-state benchmark of item 5.

- `puzzle.contact_check`: a face selection as flat triangles with outward normals
  (from the volume element each lies on); the nearest target point to every source
  node (a scipy KD-tree over triangle centres, then an exact check of any triangle that
  could be closer: centre within the best distance plus the triangle's reach); the gap
  along the target's normal and the offset to the side. Node statuses: closed (a
  penetration, or a gap within 2 % of the target's element size, as two differently
  meshed curved faces never coincide), near (within a tenth of the search distance),
  open (within it), far, overhanging (off to the side by more than 10 % of the element
  size, with the normal gap in reach). Shares are of the source's area (corner nodes, a
  third of each triangle). `Contact.search_distance` (default 1 % of the model's
  bounding-box diagonal).
- Checks (`checks`, `held_by_contact_only`) are warnings, not `Project.problems`
  (which refuse a run: a body held by a preloaded frictional joint is fine):
  `contact_overhang`, `contact_slave_held` (held or another contact's source),
  `contact_slave_bolted` (Code_Aster's SANS_GROUP_NO), `contact_far`,
  `contact_shared_outline` (frictional on Code_Aster; the source outline's nodes on the
  target's outline), `contact_only_hold` and `free_body` (a graph from supports
  through bonds, no-separation contacts and bolts; sliding contacts don't join). They
  are logged as progress warnings when a solve starts (`solve._contact_warnings`),
  listed in the GUI's Problems as contact warnings, and given by the Advisor's new
  Contacts step.
- `Adapter.contact_summary(project, component, mesh)`: CalculiX (`*TIE`, or
  `*CONTACT PAIR` with K = factor E/h and its numbers, from `_penalty`, which the deck
  uses too) and Code_Aster (`LIAISON_MAIL`, or `DEFI_CONTACT` with the law,
  `CONTACT_INIT` and the SANS_GROUP_NO node count from `_contact_exclusions`).
- GUI `gui/contact_tool.py`: Window → Contacts (Ctrl+7), Model → Contact tool, a
  component's menu; Setup edits as commands (several rows at once; a frictional type
  gets μ = 0.2, a non-bond leaves conformal meshing); Initial state checks in the
  background (meshing first) and draws the source faces by status over a see-through
  mesh (`Viewport.show_contact_state`, colours `Tokens.contact`).
- MCP `contact_check(component, solver)`, CLI `puzzle contacts PROJECT [component]
  [--solver] [--json]`, skill `contact-tool`.
- Tests: `tests/test_contact_check.py` (the split bar a known gap apart, into a known
  penetration, 5 mm apart, 3 mm sideways: 30 % overhanging; a shared outline; held
  source nodes; the agent's report, MCP, CLI, Advisor, the solve's log),
  `tests/test_gui_contact_tool.py`. On the bolted bracket the Code_Aster view gives the
  bolt-end exclusion and the shared seat outline: what the 2026-10-30 failure came to.

Phase 2 (2026-10-31): the Results tab (item 1), interface treatment (item 2) and the
benchmarks (item 5).

- Contact fields on the source nodes (`post/contact.py`, run after reading):
  `contact_pressure`, `contact_shear` (the frictional stress's size), `contact_gap`
  (− penetration), `contact_sliding`, `contact_status` (0 open, 1 sliding, 2
  sticking). CalculiX: `*CONTACT FILE` CDIS, CSTR on static contact steps (one CONTACT
  block: COPEN, CSLIP1/2, CPRESS, CSHEAR1/2, on every node, zero off contact), the
  status from p and μ (sticking below 99 % of μ p). Code_Aster: the CONT_NOEU table
  (status, gap, slip, nodal forces) and, from DEPL, the multipliers: LAGS_C is the
  pressure itself, LAGS_F1/F2 the friction semi-multipliers (τ = μ p |λ_f|). Forces
  turned into pressures by the consistent area matrix reproduce LAGS_C on quadratic
  faces but oscillate on linear ones (Code_Aster integrates at the nodes there), so
  that is only the fallback. Per contact (`contact_results`): normal and frictional
  force, area in contact, sticking share, largest pressure, penetration, sliding, gap.
  The view draws a field defined on part of the mesh on those faces over a
  see-through body (`Viewport.show_result`), as contact faces lie inside joints.
- Interface treatment moves the mesh, not a solver card: `Contact.interface` adjust
  (source nodes within the search distance onto the target) or offset (`offset` from
  it along the target's normal: + clearance, − interference), in the mesh written
  (`contact_check.adjusted`, `solve._adjust_contacts`), the same for every solver.
  CalculiX's `*CLEARANCE` was tried first: it sets the gap rather than adding to it,
  and on the press fit its displacements were right but its contact pressures three
  times Lamé's; the moved geometry gave Lamé's to 0.2 %. Nearest points are found on
  the target's own six-node faces (`on_curved`, Gauss-Newton from the flat triangle's
  point): flat triangles are off by the chord's sag (8 µm on the press fit, against a
  20 µm interference), which also sharpens the initial state on curved faces.
- GUI Results tab (run and step; Pressure, Status, Sliding, Gap, Frictional stress
  buttons), Setup's Interface and Offset columns; MCP `contact_results`; the skill;
  Code_Aster's "échec de l'appariement" translated, with the diagnosis
  `pairing_failed`. `contact_slave_held` now flags any hold only under friction (a
  frictionless source edge on a symmetry or plane-strain face is fine).
- Benchmarks: `tests/integration/test_contact_results.py`: the frictionless stack
  carries p A = 2000 N (CalculiX 2000.04 N, every node 20 MPa to 0.04 %; Code_Aster
  2000.0 N, its pressure its own LAGS_C, mean 20.1 MPa); the sliding block slides
  everywhere with frictional over normal force = μ (0.300 on both).
  `tests/integration/test_press_fit.py`: Lamé, plane strain, 20 µm interference:
  pressure CalculiX −0.32 %, Code_Aster −0.20 %; the bore's hoop stress −2.5 %, −2.3 %.
  `tests/integration/test_hertz.py`: a cylinder on a block, plane strain: CalculiX's
  peak pressure within 0.7 % of Hertz's for the force carried, the half-width within an
  element. Code_Aster's pairing fails before its first increment on this 3D line
  contact (the whole quarter or its bottom 1 mm as the source, POINT_FIXE or Newton
  geometry, quadratic or linear tets). So Code_Aster refuses such contacts up front
  (2026-11-01): `contact_check.nonconforming` finds a curved source face (normals turning
  by more than 2°) whose gaps differ across it by more than a quarter of the target's
  element size (a cylinder or ball on a plate; a press fit and flat faces don't count);
  the writer refuses it (`unsupported`, naming CalculiX) and `checks` warns
  (`contact_nonconforming`) in Problems and the Advisor. Making it run stays open.

