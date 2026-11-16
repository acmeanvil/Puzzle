---
title: Choosing an analysis
section: guide
order: 95
keys: analysis
summary: Which analysis answers your question, which solvers run it, and the settings that matter.
adrs: 0028, 0041, 0047, 0085
---

A study's step says what kind of answer you want: a steady state, a history in time, the
natural frequencies, a buckling load, a response over frequencies, or an impact. The
component's physics says what is solved: structure, heat, electric or magnetic fields,
flow, or your own equation.

## How to choose and set an analysis

1. Model → Studies → New study… asks for the component, the solver and the
   **Analysis**: Stationary, Transient, Modal, Buckling, Frequency domain or Explicit
   dynamics. A component with several physics also asks which one the step solves.
2. To add another step to a study, right-click it in the model tree, Add step.
3. Select the step in the tree: its settings show in Properties, in the units shown.
4. A solver that can't run the analysis is refused when you choose it, never solved
   without it. [What each solver does](help:reference/capabilities) has every matrix.

## The analyses

### Stationary

The steady answer: a static stress, a steady temperature, a converged flow. Every physics
has one. Choose it when the loads are slow next to the structure's lowest natural
period, or don't change. A structural study needs a support, or it is refused as
`unconstrained`.

### Transient

A history in time. Set `time_step` and `end_time` (s). Heat and magnetics take a
`time_scheme`: Backward Euler by default on every solver, so a shoot-out compares like
with like; Crank Nicolson, BDF2 and Theta where the solver allows. On solid mechanics it
is structural dynamics (Newmark): `method` direct or modal, `hht_alpha` from −1/3 to 0
(0 is no numerical damping). Take about 20 steps per period of the highest mode that
matters. Runs on CalculiX, Code_Aster, Elmer and FEniCSx; heat also on Code_Saturne;
magnetics on Elmer and FEniCSx.

### Modal

Natural frequencies and mode shapes, set by `modes` (6 when the step is made). Every
material needs a density. A free body is allowed: its first six modes are near 0 Hz.
Runs on CalculiX, Code_Aster, Elmer, FEniCSx and PyNite.

### Buckling

A linear eigenvalue solve: each mode's **load factor**, the multiple of the step's loads
at which it buckles. Settings `modes` (default 4) and `preload` (hold the static step
just before it, such as self weight or a bolt). `shift` (search near a factor) works on
Code_Aster only. Runs on CalculiX and Code_Aster. A factor below 1 buckles before the
applied load; linear buckling overestimates thin shells.

### Frequency domain

A steady response to a load at each listed frequency. Give `frequencies` as text in Hz
("50, 100, 200"), or `start_frequency`, `end_frequency`, `points` and `spacing` (linear
or log; default 10 to 1000 Hz, 50 points). On solid mechanics it is harmonic response:
amplitude, phase, and the shape at the peak instant. CalculiX solves it over modes;
Code_Aster, Elmer and FEniCSx solve it directly. On magnetic fields it is time-harmonic
eddy currents (Elmer, FEniCSx).

### Explicit dynamics

Impact, drops, crashes, short pulses: events over microseconds to milliseconds, on
OpenCourant. Set `end_time` (s, required; 1 ms when made), `frames` (default 20) and
`time_step_scale` (default 0.9). Each step is cheap but tiny, so anything slower than a
few periods of the lowest mode belongs in an implicit transient instead. A suddenly
loaded cantilever matched CalculiX's implicit transient within 0.3 %.

## The physics

- **Solid mechanics**: stress, deflection, reactions; every analysis above. CalculiX,
  Code_Aster, Elmer and FEniCSx; PyNite for frames; OpenCourant for explicit.
- **Heat transfer**: conduction, convection, generation, stationary or transient on the
  four FE solvers. Materials' temperature tables are used by default, which makes it
  nonlinear; the physics setting `temperature_dependent_materials` off uses single
  values. `initial_temperature` starts a transient (default 20 °C).
- **Electrostatics**: potential, field and stored energy, stationary, on Elmer and
  FEniCSx.
- **Magnetics and eddy currents**: static coils, time-harmonic and transient eddy
  currents, losses, coil resistance and inductance, on Elmer and FEniCSx. Check the
  mesh against the skin depth (Results → Optimise and check the mesh → Add a skin-depth
  refinement).
- **Fluid flow**: steady incompressible flow, laminar or RANS (`turbulence_model`
  laminar, k-epsilon or k-omega-SST on the physics), with heat, on Code_Saturne and
  OpenFOAM. The step's `iterations` defaults to 500 on Code_Saturne and 2000 on OpenFOAM.
- **Custom PDE**: your own equation in weak form, one Lagrange unknown, scalar or vector,
  steady or transient, on FEniCSx. Its conditions are Fixed value and Flux into the body.
  It runs only in a trusted project.

Most solvers solve one physics per step. For thermal stress, solve heat first and pass
the temperature into a structural study with Model → Studies → New transfer….

## Options worth knowing

- 2D models (plane stress, plane strain, axisymmetric about Y) run the same analyses on
  the four FE solvers: Model → Symmetry → Model type (3D, 2D planar, axisymmetric)….
- Damping: `rayleigh_alpha` (1/s) and `rayleigh_beta` (s) on dynamic steps, C = αM + βK;
  `damping_ratio` only with the modal method (CalculiX).
- Without an amplitude, a load acts at full value from t = 0. Project → New amplitude…
  ramps it.

## Mistakes to avoid

- A material without density: modal, transient and harmonic steps are refused
  (`incomplete_material`).
- `damping_ratio` with the direct method is refused; two dynamic steps with different
  Rayleigh pairs in one study too.
- Elmer starts a transient from zero acceleration, so after a sudden load its early
  history lags; a smaller time step closes the gap.
- Explicit runs to seconds of physical time take millions of cycles: count them first.
- An axisymmetric model means round loads too: a side load on a shaft needs 3D.

## For agents and scripts

The agent creates studies through [submit](help:mcp:submit), checks them with
[check_study](help:mcp:check_study) and reads [capabilities](help:mcp:capabilities);
[explicit_estimate](help:mcp:explicit_estimate) gives an explicit run's time step and
cost, [magnetic_check](help:mcp:magnetic_check) the skin depth. From a terminal,
[puzzle studies](help:cli:studies) lists them and [puzzle run](help:cli:run) solves one.

## See also

- Theory: [Structural dynamics](help:theory/structural-dynamics), [Time integration](help:theory/time-integration), [Explicit dynamics](help:theory/explicit-dynamics), [Buckling](help:theory/buckling), [Electromagnetics](help:theory/electromagnetics), [Flow (CFD)](help:theory/cfd).
- [Studies and solvers](help:guides/studies-and-solvers)
- [Loads and supports](help:guides/loads-and-supports)
- [What each solver does](help:reference/capabilities)
- [Solver settings](help:reference/settings)
