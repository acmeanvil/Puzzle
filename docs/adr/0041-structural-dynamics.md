# 0041. Structural dynamics: transient and harmonic response

- Status: Accepted (2026-10-25, the maintainer); built 2026-10-25 on CalculiX (direct and modal) and Code_Aster (direct); Elmer and FEniCSx to follow
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

## Built (2026-10-25)

- **Step types:** no new ones. A `transient` step that solves solid mechanics is a transient structural analysis, and a `frequency_domain` step a harmonic one; New study offers "Frequency domain" and fills in the settings below so they show in Properties.
- **Settings** (neutral, translated by each adapter):
  - transient: `time_step`, `end_time`, `method` (direct or modal), `modes` (for modal), `hht_alpha` (0 by default: Newmark's average acceleration, no numerical damping);
  - harmonic: `frequencies` (text, "50, 100, 200") or `start_frequency`, `end_frequency`, `points`, `spacing` (linear or log), `method`;
  - damping: Rayleigh `rayleigh_alpha` (mass, 1/s) and `rayleigh_beta` (stiffness, s), one pair per study (`common.rayleigh`); or `damping_ratio` for every mode, with the modal method only (a direct solve with one is refused with the reason, `common.check_dynamics`).
- **Loads in time:** structural force and pressure may follow an amplitude in transient steps (`Capabilities.amplitude_features`); loads without one act from t = 0 at full value (a sudden load).
- **CalculiX:**
  - direct: `*DYNAMIC, DIRECT, ALPHA=0` (its own default −0.05 damps numerically, and without DIRECT it chose its own increments: 11 instead of 90, the response half what it should be); Rayleigh damping as `*DAMPING` with each material;
  - modal: a `*FREQUENCY, STORAGE=YES` step with the supports, written before the step, then `*MODAL DYNAMIC` or `*STEADY STATE DYNAMICS` with `*MODAL DAMPING` (a ratio, or Rayleigh). CalculiX has no direct harmonic solve, so its harmonic steps are modal;
  - a list of frequencies is one steady-state step each (lower = upper), since a range also returns the eigenfrequencies inside it;
  - every `*STEP` in the deck is tagged `** puzzle step k` (0 for helpers: the modes, a replayed prestress step); the reader maps results back by these tags. This replaced the prestress step count, and buckling modes are now numbered by study step.
- **Code_Aster:** transient by `DYNA_NON_LINE` (`SCHEMA='NEWMARK'`, or HHT with `hht_alpha`), loads following an amplitude in their own charge with `FONC_MULT`; harmonic by `ASSEMBLAGE` (stiffness, mass, damping, load vector) and `DYNA_VIBRA` (`TYPE_CALCUL='HARM'`, `BASE_CALCUL='PHYS'`). Rayleigh damping goes in `ELAS` as `AMOR_ALPHA` (stiffness) and `AMOR_BETA` (mass), Code_Aster's names being the other way round. Its tables take no complex values, so each frequency's real and imaginary parts are written as two real results and joined by the reader. The modal method is refused for now.
- **Results:**
  - transient: one step per time;
  - harmonic: one step per frequency (`StepResult.kind` "frequency_domain", labelled "f = 100 Hz"), each with `displacement_amplitude` (per component), `displacement_phase` (degrees) and `displacement` drawn at the instant the largest component peaks, so the deformed view shows the response even at a resonance (`common.harmonic_fields`);
  - the Plots tab draws a probe on the amplitude against frequency.
- **Benchmarks** (`tests/integration/test_dynamics.py`; the 200 × 10 × 10 mm steel cantilever, 100 N at the tip, against the sum of its first five Euler-Bernoulli modes):

  | Case | CalculiX | Code_Aster |
  | --- | --- | --- |
  | Sudden load, undamped (200 steps a period) | history within 1.33 % of the peak, peak −1.3 % | identical |
  | Sudden load, 5 % Rayleigh mass damping (60 a period) | within 1.97 %, peak −1.2 % | identical |
  | Load ramped over one period by an amplitude | within 0.35 % | identical |
  | Sudden load by modal superposition (20 modes) | within 1.15 % | refused |
  | Harmonic, undamped, 100 to 600 Hz | amplitude within 1 %, phase exact | the same |
  | Harmonic, 5 % Rayleigh damping | within 1 %, phase within 0.1° | the same |

  With 60 steps a period the undamped peak read 2.5 % low on both solvers: Newmark stretches the second mode's period by 3.6 %, and that mode carries 2.5 % of the deflection. That is a time-step matter, not a solver error.
- **Not yet:** Code_Aster's modal method (`PROJ_BASE` and `DYNA_VIBRA` on a generalized basis); Elmer and FEniCSx dynamics; amplitudes in harmonic steps (frequency-dependent loads); base excitation; stress results in harmonic steps; the NAFEMS forced-vibration cases.

## Consequences

- Dynamic response joins the solver-neutral model. Modal studies become inputs to other studies, so a study may depend on another: a dependency like a transfer, which the graph already handles.
- Results grow (many steps, complex fields). The store already holds many steps per run (ADR 0008), and complex values are stored as two components.
- Explicit dynamics and nonlinear dynamics are out of scope here.

## Alternatives considered

- Harmonic only: it covers vibration checks, but not shocks or start-up transients.
- Modal superposition only: it is cheap, but wrong for nonlinear or heavily damped cases, and its error is hard for users to see. Direct integration stays available.
