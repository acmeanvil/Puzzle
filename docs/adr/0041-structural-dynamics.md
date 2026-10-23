# 0041. Structural dynamics: transient and harmonic response

- Status: Proposed
- Date: 2026-10-23

## Context

Puzzle solves structures statically and modally. Both reference tools go further (review item 16):

- **PrePoMax:** modal dynamics, steady-state dynamics, implicit and explicit dynamics.
- **Code_Aster:** `DYNA_VIBRA` (transient or harmonic, physical or modal basis) and `DYNA_NON_LINE`.

Machines, mounts and anything near a resonance need a response to a time-varying load (a shock, a ramp) or to a harmonic one (an unbalance, a shaker table). Amplitudes (ADR 0037) give time-varying loads.

## Decision

1. **Two solve step types:**
   - `transient_structural`: settings are the end time, the time step, the scheme (Newmark by default; HHT/Bossak where the solver has it), and the method (direct, or modal superposition over a named modal study).
   - `harmonic`: settings are the frequency range and points (linear or log spacing, optionally clustered at the modal study's frequencies), and the method (direct or modal).
2. **Damping**, a study setting: Rayleigh α and β, or a modal damping ratio (constant or per mode), and per material where the solver allows it.
3. **Solvers, first benchmarks then claims:**
   - **CalculiX:** `*DYNAMIC` (direct), `*MODAL DYNAMIC` and `*STEADY STATE DYNAMICS`, the modal ones after a `*FREQUENCY` step in the same deck.
   - **Code_Aster:** `DYNA_VIBRA` with `TYPE_CALCUL='TRAN'` or `'HARM'` and `BASE_CALCUL='PHYS'` or `'GENE'`.
   - **Elmer:** the elastic solver's transient (Newmark) and harmonic analysis.
   - **FEniCSx:** generalized-α in the weak form, later.
4. **Results.**
   - **Transient:** one result step per output time, as transient heat does now.
   - **Harmonic:** one step per frequency, with complex fields stored as amplitude and phase components. The results bar then offers amplitude, phase, or the real part at a chosen phase angle.
   - **Plots:** frequency response at a probe (ADR 0032, 0038).
5. **Benchmarks:**
   - a cantilever tip under a step load against the modal-superposition closed form;
   - a harmonic response of the same cantilever against Euler-Bernoulli with damping;
   - NAFEMS forced-vibration cases (the selected-benchmarks set) where an open reference exists;
   - a shoot-out between CalculiX and Code_Aster on each.

## Consequences

- Dynamic response joins the solver-neutral model. Modal studies become inputs to other studies, so a study may depend on another: a dependency like a transfer, which the graph already handles.
- Results grow (many steps, complex fields). The store already holds many steps per run (ADR 0008), and complex values are stored as two components.
- Explicit dynamics and nonlinear dynamics are out of scope here.

## Alternatives considered

- Harmonic only: it covers vibration checks, but not shocks or start-up transients.
- Modal superposition only: it is cheap, but wrong for nonlinear or heavily damped cases, and its error is hard for users to see. Direct integration stays available.
