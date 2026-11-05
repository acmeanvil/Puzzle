---
name: eddy-currents
description: AC and transient magnetic fields: massive conductors and windings, losses, coil R and L, skin depth, axisymmetric models, losses as heat and forces as loads (Elmer, FEniCSx)
adr: 0047
---
Read this before setting up an AC magnetic study (eddy currents, induction heating, a
coil's impedance, skin or proximity effect), or when the Advisor's Skin depth step says
"needed".

A frequency-domain step in a component with magnetic fields solves the time-harmonic
A–V equations, on Elmer (WhitneyAVHarmonicSolver) and FEniCSx (real 2 × 2 blocks). The
two give the same discrete answer on one mesh. Every value is a peak phasor; losses and
energy are time averages.

## Coils (features on bodies)

- `coil_current`, a **massive conductor**: `I` (A, peak) and `phase` (deg). The current
  enters through a `coil_start` face and leaves through a `coil_end` face, both on its
  body; eddy currents and skin effect develop inside it.
- `stranded_coil`, a **winding**: `I` per turn, `phase`, `turns`, `fill` (the copper's
  share of the section, 0 to 1, which only sets its resistance). Its current stays
  uniform: no eddy currents in it. It takes both terminal faces or neither; without
  them it is a closed loop (Elmer only so far).
- Any other body with an `electrical_conductivity` carries eddy currents (a plate, a
  shield, a billet). Every body needs `relative_permeability` and
  `electrical_conductivity` (0 for air).
- Boundaries: `magnetic_insulation` (flux tangential, n × A = 0) on the outer air faces
  and on conductors' ends that lie on them; `magnetic_symmetry` (flux normal: nothing
  written) on a cut through the middle of a symmetric model.
- A static study takes windings, or one massive conductor (then no phase).

## Transient and axisymmetric

- A `transient` step (`time_step`, `end_time`, `time_scheme`, `output_every`) solves A–V
  in time; a coil's current follows its `amplitude` (none: a step from t = 0). Elmer takes
  backward Euler only (its BDF2 measured wrong on this system); FEniCSx backward Euler or
  `bdf2`. Results per output time: B, J, loss and energy as instantaneous values; no R
  or L.
- A `2d_axisymmetric` component (X the radius, Y the axis) solves A_φ: windings and
  passive conductors (a billet, a crucible) and solid rings (`coil_current` on a region,
  no terminals: its voltage per turn is solved so it carries I), static or
  frequency-domain. The hoop direction is a field's out-of-plane component. Give each
  region its own face step with `mode: "separate"` (touching faces share their edges
  and stay regions; the default "add" fuses them into one face), or separate parts
  bonded edge to edge.

## Steps

1. Build the parts: the conductors, the coil, and air around them out to about five
   times the coils' size. Bond them conformally (bonded contacts), name the terminal
   faces and the outer faces.
2. Add the features with one `submit` batch, then a study whose step is
   `{"type": "frequency_domain", "settings": {"frequencies": "50, 1000"}}`, or
   `start_frequency`, `end_frequency`, `points`, `spacing` (Hz).
3. Check the mesh against the skin depth, δ = √(2 / (ω μ σ)): `magnetic_check(study)`
   gives each conductor's δ at the highest frequency, its element size, and the
   refine-near controls to add when it is coarser than δ/2. Add them with one Edit of
   the recipe's `controls` (`{"op": "append", "path": "controls", "value": ...}`).
   Coarse elements make losses and resistance come out low.
4. Run the study (autonomy "act"). Read the results with `result_summary`: per
   frequency, `joule_loss` (W: "total" and each body), `magnetic_energy` (J),
   `coil_resistance` (Ω) and `coil_inductance` (H), one entry per coil. With several
   coils, each coil's R and L is its own voltage over its current, so the mutual
   coupling is in them. Fields: `magnetic_flux_density` (nodal, plus `_amplitude`,
   `_phase`), `current_density` (per element, likewise), `loss_density` (W/m³).
5. Losses as heat: a transfer with `field: "loss_density"`, `role: "load"` and
   `target_selection` the body to heat, into a heat study. It becomes a
   `heat_generation` there with the same total power, on any FE solver and any mesh.
   `heat_generation` also takes a number (W/m³) or an imported field.
6. Forces on a structure: a transfer with `field: "lorentz_force_density"` (½ Re(J × B*)
   per element, N/m³), `role: "load"`, `target_selection` a body, into a structural
   study: a `body_force` there with each component's total kept.

## Mistakes

- A massive conductor without both terminal faces is refused; a terminal face not on
  any coil's body is refused.
- A passive conductor whose end faces lie on an insulated plane needs
  `magnetic_insulation` on those ends too, or its natural condition forces the flux
  normal there and spurious eddy currents appear.
- A phase in a static step is refused.
- A winding with no terminal faces is a closed loop (both solvers): it circulates
  right-handed about its principal axis with the largest moment of inertia (a ring's
  or racetrack's own axis) through its centroid. A winding's turns carry a uniform
  current density along them.
- Settings > Solvers > Parallel processes per run splits a 3D magnetic study on Elmer
  across MPI processes (ElmerGrid, ElmerSolver_mpi): about 2.5 times faster on four.
- Reactions in a structural study fed by forces: CalculiX's reaction at a held node
  leaves out the load on that node itself.
