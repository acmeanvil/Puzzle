---
name: explicit-dynamics
description: impact, drop tests and short pulses with explicit dynamics on OpenCourant: the explicit step, initial and imposed velocities, rigid walls, the stable time step and mass scaling, what the energies say
adr: 0085
---
Read this when the user wants an impact, a drop, a crash, a blast or any event over
microseconds to milliseconds, or asks why an explicit run is slow or what its energies
mean. For vibration, slow transients and steady loads use the implicit solvers instead
(skill dynamics): explicit dynamics needs millions of tiny steps for anything slow.

## When explicit, when implicit

- **Explicit** (OpenCourant, solver `opencourant`): waves, impact, contact chatter,
  large distortion, events shorter than a few periods of the lowest mode that matters.
  Each step is cheap, but the step must stay under the time a wave takes to cross the
  smallest element.
- **Implicit** (`transient` on CalculiX or Code_Aster): vibration, anything slower,
  where a step can be a fraction of the period. The two agree on the slow part: a
  suddenly loaded cantilever's tip on both matched to 0.3 % (ADR 0085's benchmark).

## Steps

1. `outline` with `ids: true`: the component, its supports, and whether every material
   has a density, Young's modulus and Poisson's ratio (all three are needed).
2. A study with one explicit step (one `submit`):
   `{"command": "create", "kind": "study", "data": {"name": "Drop", "component":
   "<component id>", "solver": "opencourant", "steps": [{"type": "explicit",
   "physics": ["solid_mechanics"], "settings": {"end_time": 0.002, "frames": 40}}]}}`.
   Settings (SI): `end_time` (s, required), `frames` (animation frames, default 20) or
   `frame_interval` (s), `history_interval` (s, the energies, default end_time / 1000),
   `time_step_scale` (Courant factor, default 0.9), `mass_scaling_time_step` (s, unset
   = none), `precision` (double or single).
3. Loads and conditions (features on the component's solid mechanics):
   - `initial_velocity` on a body: `vx`, `vy`, `vz` (m/s): what it moves at, at t = 0;
   - `velocity` on faces: `x`, `y`, `z` (m/s), imposed throughout (an amplitude makes
     it vary);
   - `rigid_wall` on a body (the nodes that can touch it): a point `x0`, `y0`, `z0` and
     a normal `nx`, `ny`, `nz` pointing toward the model; `mu` friction (0: sliding);
   - `fixed`, `displacement`, `force`, `pressure` (first-order meshes), `gravity`,
     `acceleration`, each following an amplitude where given.
   Not yet (ADR 0085 step 2): contacts between parts, shells, Johnson–Cook; they are
   refused, never ignored.
4. Before running: `explicit_estimate(study)` gives the stable time step (s, the
   element setting it and where), the cycles, the mass, and for a run over 1e7 cycles
   the mass scaling time step for about 1e6 cycles with the mass it would add. Tell
   the user the cost in those words; `check_study` for anything refused.
5. Run, then `run_progress` (cycle, time against the end time) and `run_log` for
   OpenCourant's own messages (its `ERROR ID` blocks).
6. Results: one result step per frame, `value` the time. `displacement`, `velocity`,
   `acceleration`, `stress` (averaged at the nodes), `plastic_strain_equivalent`, and
   per frame the global energies `internal_energy`, `kinetic_energy`,
   `hourglass_energy`, `contact_energy`, `external_work`, `plastic_work`,
   `added_mass`, `time_step`. `result_history` follows one over the frames.

## Reading the energies

- Kinetic + internal + contact + hourglass should equal the initial energy plus the
  external work; a gap over 5 % means the run lost or made energy (too large a time
  step scale, a contact or wall going wrong).
- Hourglass energy over 10 % of the internal energy: the bricks' zero-energy modes are
  taking part; refine, or use tetrahedra there.
- Added mass (mass scaling) over 5 % of the mass changes inertial results: lower it or
  remesh the small elements instead.

## Mass scaling

`mass_scaling_time_step` adds nodal mass wherever an element's own step would be
smaller, so those elements no longer set the step. Fine for slow parts of a model and
quasi-static runs; wrong where inertia is the point (a wave, an impact's first
microseconds). Always say the mass it adds (`explicit_estimate`, `added_mass`).

## Mistakes to avoid

- Running an explicit study to seconds of physical time: count the cycles first.
- A rigid wall's normal pointing away from the model (it then holds nothing).
- A kinematic rigid wall stops the nodes that reach it: it loses a little momentum per
  impact (a bar thrown at one left at 0.964 of its speed on 40 elements along, 0.976 on
  80). Fine meshes where it matters.
- Reading σ = ρ c v from a bar struck past yield: in 3D the plastic zone's lateral
  inertia lowers the precursor; benchmark plasticity with a slow pull instead.
