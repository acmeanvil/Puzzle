# 0047. Magnetodynamics: time-harmonic eddy currents first, then transient, on Elmer and FEniCSx

- Status: Accepted (2026-10-26, with the maintainer's answers); built 2026-11-04
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

## Built (stage 1, 2026-11-04)

Items 1 to 4 of the order of work.

- **Coils.** The coil type is the feature: `coil_current` is a massive conductor (`I`, `phase`), `stranded_coil` a winding (`I` per turn, `phase`, `turns`, `fill`). Each coil is paired with the terminal faces whose nodes lie on its body. A winding without terminals is a closed loop (Elmer). Any number of coils is allowed. `magnetic_symmetry` writes the natural condition. `adapters/eddy.py` holds what both solvers share: the coils, the reader's metadata, and the results.
- **Elmer** (`adapters/elmer/magnetics.py`):
  - The study runs as a Scanning run, with `Angular Frequency` a table over the scan index.
  - The harmonic A–V system is solved by BiCGStab(4): UMFPACK reports it singular, and the real-arithmetic direct solve gave NaN.
  - A massive conductor gets its current as a uniform normal current density on its start face (I over the face's area as meshed) and V = 0 on its end face.
  - A winding runs CoilSolver first, as its own Component with `Coil Type`; without one, CoilSolver scales all windings together to 1 A. CoilSolver uses unit conductivity, and its field flows from the end face to the start, hence a factor −N I. The winding's body has σ = 0 in the solve.
  - Losses, energy and impedance are computed from the per-element output.
- **FEniCSx** (`study.py` `solve_magnetodynamics`):
  - The unknowns are A (Nédélec) and V (Lagrange) in real and imaginary blocks. V is coupled in every conductor; with V = 0 there (the A* form), a passive conductor whose faces have n × A = 0 let current out through them.
  - Each winding's source comes from a unit conduction solve in its body.
  - Element values are matched to Puzzle's elements by corner centres.
- **Results**, per frequency:
  - B per node, and J per element, as amplitude, phase and the field at the peak instant;
  - `loss_density`, ½|J|²/σ per element (a winding's ½|J|²/(σ fill));
  - `joule_loss`: the total and each body;
  - `magnetic_energy`: ¼ ∫ B·H*;
  - `coil_resistance` and `coil_inductance`: each coil's voltage over its current. That is the massive conductor's start-face potential, or the winding's DC resistance plus jω times its flux linkage ∫ A·J / I.
- **Skin depth** (`puzzle/magnetics.py`):
  - A warning when a conductor's recipe size is coarser than δ/2 at the study's highest frequency. It appears in Problems, the outline, the Advisor's Skin depth step and MCP `magnetic_check`.
  - At a solve's start, warnings for the elements meshed and for an air region under 2.5 coil sizes.
  - "Add a skin-depth refinement" (Results menu, the Advisor's button) adds a refine-near control per conductor: δ/3 within δ of its surface, back to the body's size by 4δ. It is not a boundary layer, because prisms would not run on FEniCSx's Nédélec solve.
- **Heat generation** (`heat_generation`, W/m³ on a body) on all four FE solvers:
  - CalculiX `*DFLUX ... BF`;
  - Code_Aster `SOURCE`, and an `OnCells` FORMULE for a field;
  - Elmer `Heat Source` = q/ρ;
  - FEniCSx q·v dx.

  It takes a number, an imported field or a transferred `loss_density`. A transferred loss is averaged to nodes over the losing elements, mapped, and scaled so the target body takes the power lost in it.
- **Measured** (`tests/integration/test_eddy_currents.py`, `test_heat_generation.py`):
  - Skin effect in a round copper wire, against the Bessel solution: R within 0.2 % at a/δ = 0.48 and 0.7 % at a/δ = 2.1; L within 0.4 %; |J| across the wire within 2.4 % on average. The same on both solvers.
  - A winding at 0 Hz and 5 kHz: R within 0.74 % (the meshed section is smaller) and L within 0.5 % of N² μ0 L (¼ + ln R/a) / 2π.
  - Two windings at 10 A, 0° and 6 A, 90°: each section carries its own current to 1e-3 and 0.05°.
  - Shoot-out, Elmer against FEniCSx on one mesh, a driven wire beside a passive one at 10 kHz: losses and energy equal to 7 digits, R 0.05 %, L 0.16 %.
  - Heat generation on all four solvers: uniform, the middle's rise equal to the closed form to five digits; from a field rising along the bar, the peak within 0.003 %.
  - Elmer's 1 kHz losses heating a CalculiX wire: the temperature rise within 2 % of the 1D solution.

## Built (stage 2, 2026-11-04)

Items 5 to 7.

- **Transient** (item 5): A–V in time, each coil's current following its Amplitude, or a step from t = 0.
  - Elmer runs WhitneyAVSolver in a transient simulation, solved by BiCGStab: UMFPACK took minutes a step. It is backward Euler only. Elmer's `BDF Order = 2` measured wrong on this system: it lagged like backward Euler and worse, where FEniCSx's BDF2 matched a time-converged run.
  - Elmer's `Exec Intervals` counts from step 1, and its output solver ignores `Output Intervals`. So Elmer writes every step, and the reader keeps the output steps.
  - FEniCSx factors the system once (again when BDF2 starts), with backward Euler or BDF2.
  - Results per output time are instantaneous: B, J, the loss and the energy, with no R or L.
- **Lorentz forces** (item 6): `lorentz_force_density`, ½ Re(J × B*) per element. A transfer of it into a structural study becomes a `body_force` on its target body, each component's total kept, as for losses.
  - A transferred field is made linear in each element: a tet10's edge nodes become their corners' mean.
  - The totals are integrated over the elements' own curved shape. The solvers' consistent loads integrate it the same way.
  - CalculiX's field-driven heat generation is now consistent nodal flows (CFLUX) rather than a mean per element.
- **Axisymmetric 2D** (item 7): A_φ for windings and passive conductors, static or frequency-domain.
  - Elmer runs MagnetoDynamics2D(Harmonic), solved directly; FEniCSx a Lagrange A_φ in real and imaginary blocks.
  - FEniCSx uses the curl form, ∫ ν curl A·curl v r. The Laplacian form ∇A·∇v + A v/r² misses a boundary term, so its natural condition pinned the field 21 % high.
  - Puzzle derives B, J, losses, energy, impedance and forces from the nodal A_φ, the same for both solvers (`eddy.axisymmetric_fields`). The hoop direction is the out-of-plane component, and the (r, z, hoop) frame is left-handed, so the force's sign is flipped.
  - A massive ring is refused (it needs a circuit). The face steps of one 2D geometry fuse into one face, so regions are separate parts bonded edge to edge.
- **Measured:**
  - Transient skin effect, a step current into the wire, against the Bessel series. FEniCSx BDF2 at Δt = τ/200, in the wire's middle third, is within 0.5 % of the uniform density at 0.1, 0.2 and 0.3 τ. Near the terminals the current enters evenly, not in a skin.
  - Elmer and FEniCSx, backward Euler on one mesh: equal to 1e-6.
  - Two windings at 10 A each way: the force on one within 1.7 % of the line-current value with the outer boundary's images, on both solvers. Handed to a CalculiX study of the wire held at one end, the reaction is within 1.5 % of the total, the rest being the load on the held nodes.
  - A copper billet in a long solenoid at a/δ = 3.4, on both solvers: loss 0.311 W against 0.31099 W, the centre's B within 0.2 %.

## Finished (2026-11-04)

- **Closed windings on FEniCSx.** The source follows E0 = a × (x − c)/ρ², the gradient of the angle about the coil's axis, less ∇φ so it stays in the coil. The axis is the principal axis with the largest moment of inertia, through the centroid (`eddy.coil_axis`): a ring's or a racetrack's own. Elmer gets the same centre and axis (`Coil Center`, `Coil Normal`), so the two agree on the direction.
- **A winding's current is uniform along its turns,** on both solvers. The density is N I × t/|t|, not the potential flow's own, which crowded toward a ring's inner radius as 1/ρ.
  - Elmer: `Normalize Coil Current` with each winding a Component of Coil Type stranded.
  - FEniCSx: the unit direction, with its divergence taken out inside the coil (as Elmer's Fix Input Current Density; ψ = 0 on an open winding's terminals), then divided by the current it carries. That is ∫ J·(−∇φ) dV for an open winding and (1/2π) ∫ J·E0 dV for a closed one, exact for a divergence-free flow kept in the coil. Without the cleaning, the divergence leaked into A through the gauge term and spoilt the flux linkage, 440 % on L.
- **Solid rings in 2D** by superposition. One basis solution has the windings alone; one more per ring has a unit voltage per turn, J = σ/2πr. Each ring's current is linear in its voltage V, so Puzzle solves for the V giving each its current and combines the basis (`eddy.combine_axisymmetric`); Z = V/I.
  - Elmer runs the basis as scans, the sources picked by MATC tests on the scan index.
  - FEniCSx solves every basis from one factored matrix per frequency.
- **2D regions in one geometry:** the history mode `separate` fuses touching faces without merging them, so each stays a region with its own material and the shared edge is meshed once.
- **Elmer performance.** On 56 000 tetrahedra one transient step costs about 5 s on one core: about 3 s for the A–V solve, 1 s for the fields, 1 s for the ASCII output. The 30 s steps measured earlier came from other runs sharing the machine and from writing every step.
  - Measured and rejected: ILU0 cut the iterations from 50 to 15, but diverged after a few steps on a finer mesh, because the system is singular in the air. `Constant Bulk Matrix` changed nothing.
  - Done: the fields and the output only at output steps (`Exec Intervals` with `Exec Intervals Offset`).
  - Done: **MPI** under Settings > Solvers > Parallel processes per run, as for Code_Aster (ADR 0053). ElmerGrid partitions the mesh with METIS, `mpirun ElmerSolver_mpi` runs it, and the reader merges each step's partitions by node position and cell centre. It applies to 3D magnetic studies, the image's MPI or `ElmerSolver_mpi` beside a native Elmer. Five steps took 28 s on one process, 17 s on two and 13 s on four. The fine 60-step benchmark took 210 s on one and 83 s on four, with the same fields.
- **Measured:**
  - Ring coil, centre field: −0.9 % on FEniCSx and −2.1 % on Elmer, whose CoilSolver measures the section 1.2 % larger.
  - Solid tube at 500 Hz against the Bessel solution: loss within 0.01 %, R within 0.04 %, L to three digits, on both solvers.
  - Conducting sphere at a/δ = 2.1 (Landau and Lifshitz §59): 0.0010917 W against 0.0010923 W, on both solvers.
  - TEAM 7 at 50 Hz: B_z along A1–B1 against the measurements, on both solvers.

## Alternatives considered

- **Only Elmer:** quicker, but no shoot-out partner for a physics where solver settings and gauges easily change answers; FEniCSx already solves the static case on the same mesh.
- **Transient only** (harmonic results by running to steady periodic state): general, but many times slower for the common AC case, and it hides the impedance and phase a harmonic solve gives directly.
- **A separate "eddy currents" physics:** the same unknowns, sources and boundaries as magnetic fields; a step type is the established way to choose the analysis (ADR 0041).
