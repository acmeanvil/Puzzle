# 0047. Magnetodynamics: time-harmonic eddy currents first, then transient, on Elmer and FEniCSx

- Status: Accepted (2026-10-26, with the maintainer's answers)
- Date: 2026-10-26

## Context

Puzzle's magnetic fields are static only. Elmer runs CoilSolver, WhitneyAVSolver and MagnetoDynamicsCalcFields; FEniCSx runs a coil conduction solve, then a Nédélec curl-curl solve with a small gauge mass term. Both handle one coil with a steady current and give B, J and the stored magnetic energy (`test_magnetostatics.py`, a wire in air).

The capability matrices already list `transient` and `frequency_domain` for magnetic fields, but both adapters refuse them. Elmer's docstring names the solvers it would use (WhitneyAVHarmonicSolver; MagnetoDynamics2DHarmonic in 2D).

What users ask magnetodynamics for:
- **eddy currents and their losses:** induction heating, shields, enclosures, a transformer tank, the eddy losses in a busbar;
- **AC conductors:** skin and proximity effect, AC resistance, a coil's impedance (resistance and inductance at a frequency);
- **switching transients:** a solenoid or actuator coil switched on, eddy currents decaying in a core;
- **coupling:** losses as a heat source for a thermal study; time-averaged Lorentz forces as loads on a structure (ADR 0009 transfers, ADR 0044 body forces).

Out of scope here: moving parts (rotating machines, sliding interfaces), circuits coupled to the field (Elmer's CircuitsAndDynamics), nonlinear B–H materials in time-harmonic analysis (they need an effective permeability or harmonic balance), and high-frequency waves (`electromagnetic_waves`, a separate physics).

## Decision

1. **Two analyses in the existing magnetic fields physics**, chosen by the step type as for structures (ADR 0041):
   - a **frequency-domain step:** time-harmonic A–V. The fields are phasors at one frequency, or each frequency of a list or sweep, using the step settings a harmonic structural step uses (`frequencies`, or start/end/points/spacing);
   - a **transient step:** A–V in time, with `time_step`, `end_time` and `time_scheme` (backward Euler by default, as for heat). The coil current follows an Amplitude (ADR 0037).

   **Stage 1 is time-harmonic** (the common case, and both solvers' formulations are a step from what runs today); **stage 2 is transient.**
2. **The solvers:**
   - **Elmer:** WhitneyAVHarmonicSolver (stage 1) and WhitneyAVSolver with a time dependency (stage 2), each followed by MagnetoDynamicsCalcFields for B, H, J, Joule heating and nodal forces. The A–V system is solved directly, as for magnetostatics (iterative solvers stall here).
   - **FEniCSx:** the same A–V weak form on Nédélec elements, with jωσ terms in conductors. It is written in **real arithmetic as a 2 × 2 block** (real and imaginary parts as two unknowns), so the existing FEniCSx environment, including Windows's SciPy fallback, runs it. dolfinx's complex mode would need a second, complex-PETSc build (Question 2). The gauge stays as in magnetostatics: a small mass term where σ = 0.
3. **Sources and conductors:**
   - `coil_current` gets a **phase** (degrees) beside its amplitude I, and a **coil type**:
     - **stranded** (a wound coil: uniform current density over its section, turns and fill factor, no eddy currents in it);
     - **massive** (a solid conductor carrying the current: eddy currents and skin effect inside it, the total current imposed).
   - Several coils, each with its own current and phase (today: one coil).
   - Every body whose material has an electrical conductivity carries eddy currents; one with none (air) doesn't. The material needs `electrical_conductivity` and `relative_permeability`, as now.
   - Boundaries: `magnetic_insulation` (flux tangential, as now) and a new `magnetic_symmetry` (flux normal), so half and quarter models work.
   - **Air region guidance:** the Advisor warns when the outer boundary is closer than about five times the coil's size.
4. **Results**, solver-neutral, as structural harmonic results (`common.harmonic_fields`):
   - per frequency: B, H and J (eddy plus source) as amplitude, phase and the field at the peak instant;
   - the **time-averaged loss density** ½ J·E\* (W/m³) and **total loss** per body (global field);
   - the **stored magnetic energy**, and each coil's **impedance** R + jωL from its voltage and current (global);
   - the **time-averaged Lorentz force density** for transfers.

   Transient results are those fields at each output time (`StepResult.value` = time).
5. **Couplings through transfers (ADR 0009):**
   - **loss density → heat:** a new heat transfer feature, **heat generation** (W/m³ on bodies), takes a number, an imported field (ADR 0044) or a transferred field. It is useful on its own, too;
   - **Lorentz force → structure:** as a body force density (ADR 0044's stage-2 quantity). With CalculiX for the structure, the workflow is the one Elmer's adapter already describes: electromagnetics on Elmer, forces transferred to a CalculiX study.
6. **Mesh against the skin depth.** δ = √(2/(ωμσ)). A problem is reported when a conductor's elements are larger than δ/2 at the study's highest frequency, with the depth in the message. A mesh control helper sizes a boundary layer (ADR 0019) from δ.
7. **Checks before claims:**
   - **skin effect in a long round wire:** the current density across it against the Kelvin-function solution, and its AC resistance ratio;
   - **a conducting sphere in a uniform AC field:** the induced loss against the closed form;
   - **TEAM Problem 7** (an aluminium plate with an off-centre hole under a racetrack coil, 50 and 200 Hz): Bz along the published lines;
   - **a shoot-out**, Elmer against FEniCSx on one mesh, on loss and impedance;
   - **stage 2:** a step current into a conducting half-space (the erfc diffusion solution), and switching on a coil: the current's rise against L/R.
8. **Settings (ADR 0023):** shared ones on the step; Elmer's own (e.g. the A–V solver's linear system) and FEniCSx's (element order 1 or 2) as solver settings.

**Order of work:**
1. time-harmonic on Elmer: stranded and massive coils, several coils, losses, impedance;
2. the same on FEniCSx, and the shoot-out;
3. the skin-depth check and its mesh helper;
4. heat generation and the loss → heat transfer;
5. transient on both solvers;
6. Lorentz forces to structures (with ADR 0044 stage 2);
7. axisymmetric 2D (MagnetoDynamics2DHarmonic), once 2D geometry exists (ADR 0048).

## Consequences

- Induction heating, shielding, AC losses and impedance become studies Puzzle can run, check and compare across two solvers, and hand on to thermal and structural studies.
- Magnetic fields gain complex-valued results. They reuse the amplitude, phase and peak-instant form harmonic structures already use, so the result bar, probes, plots and exports need no new kind of field.
- Each coil and conductor setting is a new input the GUI must show by name: coil type, phase, turns and fill.
- Heat generation is a new heat feature on all four FE solvers, with its own benchmark.
- Meshing for the skin depth can make eddy-current meshes large; the check says so before a run, not after.

## The maintainer's answers (2026-10-26)

1. **Order:** time-harmonic first, then transient.
2. **FEniCSx:** one environment: the real 2 × 2 block form.
3. **Coils:** stranded and massive both in stage 1.
4. **Coupling:** heat generation and the loss → heat transfer in stage 1.
5. **Axisymmetric 2D:** yes, with Elmer's MagnetoDynamics2DHarmonic. The 2D geometry it needs (creating and updating planar and axisymmetric models) comes sooner, as its own decision (ADR 0048), and 2D magnetics follows it.

## Alternatives considered

- **Only Elmer:** quicker, but no shoot-out partner for a physics where solver settings and gauges easily change answers; FEniCSx already solves the static case on the same mesh.
- **Transient only** (harmonic results by running to steady periodic state): general, but many times slower for the common AC case, and it hides the impedance and phase a harmonic solve gives directly.
- **A separate "eddy currents" physics:** the same unknowns, sources and boundaries as magnetic fields; a step type is the established way to choose the analysis (ADR 0041).
