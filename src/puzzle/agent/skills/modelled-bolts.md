---
name: modelled-bolts
description: a bolt drawn as a solid from the fastener tables (head, fillet, shank, thread, nut) for its stresses: making, meshing, results, thread-root Kf, back to a beam
adr: 0077
---
Read this when the user wants a bolt's own stresses (the under-head fillet, the thread
root, bending in a short or prying joint), asks for a "solid" or "real" bolt, or a
bolt's `formulation` reads `modelled`. Bolts in general (types, sizes, preload, the
generator, beam bolts) are in the `bolts` skill; the thread's load sharing is in
`thread-engagement`.

## Making one

A bolt can be drawn as it is made, from the fastener tables, instead of a beam: for
the stresses at its fillet and thread, or bending in a short or prying joint. Its joint
must be `tapped` (cap screw) or `through_nut` (bolt and nut).

1. `bolt_make_modelled(component, bolt, bonded_heads=false)` (`bolt`: a name or a
   list), then pass its `batch` to `submit` (one undo). The bolt becomes parts of its
   own (`connectors[i].parts`: the bolt, and its nut), its type's head (`head`: hex or
   socket) and size, its grade as material, a frictional contact under its head and nut
   (the type's `head_friction`, 0.15; `bonded_heads` bonds them, a linear solve), its
   thread into the tapped hole or nut as a threaded contact, its preload as a
   `bolt_pretension` feature on "<part>/Section", finer mesh at "<part>/Fillet" and
   "<part>/Thread". `formulation` reads `modelled`.
   Washers: the bolt's `washers` (none, head, nut, both; over its type's) and the type's
   `washer` series (normal ISO 7089 / ASME type A narrow, small, large, extra_large,
   structural ASTM F436) draw plain washers from the tables as parts of their own
   ("<bolt> head washer", "<bolt> nut washer"): bonded to the head or nut, which don't
   slip on them, frictional on the plate (bonded with `bonded_heads`); the bolt is that
   much longer. A tapped joint has no nut washer. Set `washers` before making it (an
   edit of `connectors.<i>.washers`, or the type), or make it again.
2. Back to a beam: `bolt_make_beam(component, bolt)`, then `submit` its `batch`.
3. After a run, `bolt_results(study)` lists it with the beam bolts (`modelled`: true):
   axial, shear, torsion and bending from the stress across its section (the bending
   values are the section's), stress |N|/As + M/Z and use as for beams, and
   `thread_loads_N` from its threaded contact (first engaged first).
4. Thread-root fatigue: the thread is a smooth cylinder at the core, so its notch is a
   factor, `thread_kf` (Kf ≥ 1, 3 to 4 usual; the bolt type's, default 3; set it on the
   bolt with an edit of `connectors.<i>.thread_kf`, or on the type). A fatigue result
   (skill fatigue) applies it on its own at the nodes of "<part>/Thread", in place of
   its `notch_factor`, and says so in its warnings; the life is least at the first
   engaged thread. `thread_kf` 1 leaves the thread as meshed.
5. Bolts made by `bolts_generate`: submit `make_bolts` first, then
   `bolt_make_modelled` with their names.

## How it is meshed

A modelled bolt or nut is meshed as a body of revolution (`MeshRecipe.revolved_bolts`,
on by default): its profile meshed in 2D and swept round in `bolt_sectors` (24)
hexahedra and prisms, while the plates keep tetrahedra and meet it through contacts.
The fillet gets elements of a third of its radius, the thread a quarter pitch. Its
section carries the preload within 0.5 % (the M16 benchmark), and the fillet's peak
stress is Tipton's factor (Peterson 3rd ed. Eq. 3.5) within 1 %: Peterson's older
Chart 3.4 is up to 40 % low for fillets this sharp, so don't check against it.

## Watch for

- The frictional head and nut contacts make the solve nonlinear (slower): many bolts
  stay beams; model the ones whose stresses matter. `bonded_heads` keeps it linear.
- Solvers: CalculiX, and Code_Aster on a first-order mesh (`element_order` 1 on the
  component's recipe) when the heads are frictional: Code_Aster refuses friction on a
  second-order mesh; `bonded_heads` keeps second order. Not FEniCSx or Elmer yet (no
  frictional contacts; FEniCSx's threads are 2D only).
- Don't set `formulation` to `modelled` by an edit: only `bolt_make_modelled` draws the
  parts. A bolt type's `modelled` stays a beam until the bolt is drawn.
- Stresses near the pretension section are not uniform across it: read the bolt's
  force from `bolt_results`, not a node's stress near the cut.
- The thread is smooth: its meshed stress is not the thread root's; the fatigue result
  multiplies it by `thread_kf`. Report the fillet and the thread separately.
- Units: SI (N, N m, Pa); sizes are the tables' (M16, 1/2-13 UNC).
