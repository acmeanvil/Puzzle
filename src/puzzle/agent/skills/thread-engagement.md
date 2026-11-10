---
name: thread-engagement
description: threaded contacts: a tapped hole or a screw in a nut as two smooth cylinders whose contact acts along the flanks; per-thread loads, wedge, flank pressure
adr: 0076
---
Read this when a bolt, stud or screw sits in a tapped hole or a nut and the user asks
how the load shares between the threads, about thread stripping, the nut's opening
(wedge), or the engagement's stiffness; when `contacts_find` gives a proposal a
`thread`; or when `next_steps` says a contact "bonds two coaxial cylinders". Contacts
in general are in the `contacts` skill, checking and their results in `contact-tool`.

## What a thread is here

A thread is a correction on a contact between two **smooth coaxial cylinders**: the
screw's (inner) face and the nut's or boss's bore. Nothing is meshed as a helix. The
contact's `thread` gives the form and size; the solver then acts along each flank's
normal (the helix's lead and the flank angle) instead of across the cylinders. So the
load goes in thread by thread, the first engaged thread (at the mouth) carrying the
most, and the flank angle pushes the nut open (the wedge).

- Types: `bonded` ties the **bearing flank's** normal only (the flank slides, as real
  threads do); `no_separation` ties both flanks; `frictionless` lets each flank bear
  one way only (unilateral: the step solves nonlinear, slower). Frictional threads
  are not written yet.
- Written by CalculiX (3D and 2D axisymmetric). `contact_thread` says in
  `written_by` which of the component's solvers write it; one that doesn't refuses
  the study.
- A threaded contact is always meshed apart (two sides); `contact_thread` clears a
  `conformal` setting.

## Set one up

1. Have the contact: `contacts_find(component)`. A proposal whose two sides are coaxial
   cylinders of near-equal radius has `thread` = {`size` (a likely ISO coarse size),
   `matches` (which diameter it fits: major, pitch_diameter, minor, screw_minor),
   `radius`, `inner` (source or target: the screw)}. Submit the batch as usual.
2. `thread_forms()` lists the forms; `thread_forms(form="iso")` the sizes by name.
   Forms: iso (ISO 68-1, 60°), un (ASME B1.1), acme (29°), stub_acme, trapezoidal
   (ISO 2904, 30°), buttress (7°/45°), buttress_din (3°/30°), square, npt, bspt
   (tapered 1/16), custom.
3. `contact_thread(component, contact, form, size, ...)` validates and returns
   `command` (an Edit) with the resolved numbers in `thread` (m) and `notes`. Read the
   notes to the user, then `submit` the command. Arguments:
   - `size`: for iso and un a standard name (M16, M16x1.5, 1/2-13 UNC). Other forms
     take `major` and `pitch` as quantities ({"value": 20, "unit": "mm"}); custom
     also `flank_angles` [load, other] in degrees from the radial plane.
   - `starts` (multi-start lead = starts × pitch), `hand` right/left.
   - `bearing`: auto (the pull decides which flank bears: the one facing the way the
     screw would come out), or load/other to say which **angle** is on the bearing
     flank (a buttress loaded on its 45° side: other).
   - `compliance`: teeth (Yamamoto's tooth give, tuned on an M16×2), rigid
     (equations: no thread results), or a number in N/m³.
   - `clearance` (backlash along the flanks, a quantity, frictionless only matters),
     `engaged_length` (default the overlap), `cs` a coordinate system's name whose z is
     the axis (default fitted to the faces).
   - `type` changes the contact's type with it; `remove=true` clears the thread.
4. Run the study: `submit` the command `{"command": "run_study", "study": ...}`
   (solving needs autonomy act, as always).

## Read the results

`thread_results(study, contact=None, step=None)`: per threaded contact the total axial
load (N), each thread's axial load and `share` (thread 1 = the first engaged, at the
mouth), `first_thread_share`, `wedge_N` (the total radial force: the nut's opening) and
`peak_pressure_Pa` (the largest flank pressure). The nodal fields `thread_force`
(components normal, axial, radial, N) and `thread_pressure` (Pa) are on the screw's
face; read them with `result_stats`, look with `render`. `thread_load` and
`thread_wedge` are global fields.

## Units

Everything is SI: lengths in m, forces in N, pressure in Pa, flank angles in degrees
from the radial plane (30 for ISO and UN: half the 60° form), compliance in N/m³.

## Mistakes to avoid

- **Model both cylinders at one radius.** The screw's minor radius d3/2 (= (d −
  1.226869 P)/2 for ISO: 6.773 mm for M16×2) gave the meshed thread's share best in the
  M16×2 benchmark (first thread 47 % against 51 %); at the pitch radius the first share
  came out 39 %. Faces whose radii are more than 2 % of d apart are refused
  (`thread_radii`); the axis must be common (`thread_axis`), and there should be four
  or more nodes per pitch along it (`thread_mesh`).
- A CAD model often has the bore at the nut's minor D1 and the shank at d: those are
  more than 2 % apart, so make the two faces one radius in the geometry first.
- **Thread-root peak stresses aren't represented**: the smooth model gives the load
  per thread, not the notch stress at a root. For root fatigue mesh the thread.
- Large rotations of the bolt are outside the small-strain pairing.
- Don't add friction to a thread: frictional threads are refused for now.
- Rigid threads give no thread results; use teeth for the share.
- A bolt modelled as a beam (`connectors`) bonds its thread end to the hole's wall
  with no thread yet; a solid shank face on the thread face takes a threaded contact.
