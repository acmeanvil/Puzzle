---
title: Electromagnetics
section: theory
order: 150
keys: theory:electromagnetics
summary: How Puzzle solves electrostatic, magnetostatic and time-harmonic eddy-current fields, and how it gets losses, energy, resistance, inductance and forces from them.
adrs: 0047
---

Puzzle solves three kinds of field problem: electric fields between conductors held at
voltages (electrostatics), the magnetic field of steady currents (magnetostatics), and
the field of alternating currents, which drives eddy currents in nearby conductors
(time-harmonic magnetodynamics). This note says what each solves, what the skin depth
means for the mesh, and how the losses, energy and impedance are worked out.

## Electrostatics

With no currents, the electric field is the gradient of a potential, $\mathbf{E} =
-\nabla V$, and Gauss's law in a dielectric of permittivity $\varepsilon$ gives

$$\nabla \cdot (\varepsilon \nabla V) = 0$$

with potentials on electrodes and the natural condition (no normal flux) elsewhere.
The stored energy is a whole-model value, from which a capacitance follows:

$$W = \frac{1}{2} \int \mathbf{E} \cdot \mathbf{D} \, dV, \quad C = \frac{2W}{V^2}$$

Elmer (StatElecSolve) and FEniCSx solve it; the two-layer capacitor benchmark matches
$C = \varepsilon_0 A / (d_1/\varepsilon_{r1} + d_2/\varepsilon_{r2})$ to $10^{-6}$.

## Magnetic fields: the A–V formulation

The magnetic flux density is written as the curl of a vector potential,
$\mathbf{B} = \nabla \times \mathbf{A}$, so $\nabla \cdot \mathbf{B} = 0$ holds
exactly. With $\nu = 1/\mu$, a current density $\mathbf{J}_s$ imposed in windings and an
electric scalar potential $V$ in conductors of conductivity $\sigma$, the time-harmonic
equation at angular frequency $\omega$ (phasors, $e^{j\omega t}$) is

$$\nabla \times (\nu \nabla \times \mathbf{A}) + j \omega \sigma \mathbf{A} + \sigma \nabla V = \mathbf{J}_s$$

so the current in a conductor is $\mathbf{J} = -\sigma (j\omega \mathbf{A} + \nabla V)$.
At $\omega = 0$ it is magnetostatics. $\mathbf{A}$ is discretised with edge (Nédélec,
Whitney) elements, $V$ with nodal ones. Boundaries are either insulated,
$\mathbf{n} \times \mathbf{A} = 0$ (no flux through them,
[magnetic insulation](help:feature:magnetic_insulation)), or the natural condition,
flux normal to the face ([magnetic symmetry](help:feature:magnetic_symmetry)).

Both Elmer and FEniCSx solve it, and agree on one mesh: losses and energy to seven
digits in the shoot-out recorded in ADR 0047.

- **Elmer:** WhitneyAVHarmonicSolver, one frequency per scan of a Scanning run, solved
  by BiCGStab(4), since its direct solver reports the harmonic system singular; the
  static solve (WhitneyAVSolver) stays direct.
- **FEniCSx:** $\mathbf{A}$ and $V$ in real and imaginary blocks, $V$ in every
  conductor.

## Coils

A coil is one of two features on a body:

- a **massive conductor** ([coil current](help:feature:coil_current)): its total
  current $I$ enters at a start face and leaves at an end face, and eddy currents and
  the skin effect develop inside it. Elmer gets $I$ as a uniform current density over
  the start face, and $V = 0$ on the end face.
- a **winding** ([stranded coil](help:feature:stranded_coil)): $N$ turns of current
  $I$ spread evenly over its section, with $\sigma = 0$ in the solve so no eddy
  current flows in it. `fill` is the copper's share of the section and sets only its
  resistance. A winding with no terminal faces is closed; its current circulates
  right-handed about the principal axis with the largest moment of inertia
  (`eddy.coil_axis`), a ring's or a racetrack's own axis.

## Skin depth and the mesh

An alternating field decays into a conductor over the skin depth

$$\delta = \sqrt{\frac{2}{\omega \mu \sigma}}$$

(`eddy.skin_depth`), so the current crowds into a layer about $\delta$ thick. If the
elements there are coarse, that layer is smeared over them and the losses and
resistance come out low. `magnetics.py` warns when a conductor's mesh size, from the
recipe before meshing or from the elements at a solve's start, is above $\delta/2$
at the study's highest frequency. Add a skin-depth refinement puts a refine-near
control on each conductor: elements of $\delta/3$ within $\delta$ of its surface,
back to the body's size by $4\delta$. A second warning says when the insulated outer
boundary lies within 2.5 coil sizes of the coils' centre, where it squeezes their
field. Warnings never refuse a run.

## Losses, energy and impedance

Puzzle computes the results itself from element values (`adapters/eddy.py`), the same
for both solvers. Every value is a peak phasor, so time averages carry a factor ½:

$$q = \frac{|\mathbf{J}|^2}{2\sigma}$$

is the [loss density](help:result:loss_density) (a winding's $|\mathbf{J}|^2 / (2
\sigma f)$, $f$ its fill), summed over each body's volume into `joule_loss`. The
time-averaged magnetic energy is

$$W_m = \frac{1}{4} \int \mathbf{B} \cdot \mathbf{H}^* \, dV$$

The [Lorentz force density](help:result:lorentz_force_density), averaged over a period,
is $\frac{1}{2} \mathrm{Re}(\mathbf{J} \times \mathbf{B}^*)$. At $\omega = 0$ nothing is
averaged: $|\mathbf{J}|^2/\sigma$, $\frac{1}{2} \int \mathbf{B} \cdot \mathbf{H} \, dV$.

Each coil's impedance is its voltage over its current, $Z = U/I$, and then
$R = \mathrm{Re} Z$ and $L = \mathrm{Im} Z / \omega$. A massive conductor's voltage is its
start face's potential. A winding's is its DC resistance plus the induced voltage from
its flux linkage:

$$U = R_{dc} I + j \omega \frac{1}{I} \int \mathbf{A} \cdot \mathbf{J} \, dV$$

with $R_{dc} = \int |\mathbf{J}|^2 dV / (\sigma f |I|^2)$. At $\omega = 0$ a winding's
inductance is its flux linkage over its current.

## 2D models

In a planar model the unknown is $A_z$, currents flow along z, and every total (losses,
energy, resistance, inductance) is per metre of depth; $\mathbf{B} =
(\partial A_z/\partial y, -\partial A_z/\partial x)$. In an axisymmetric model (Y the
axis, X the radius) the unknown is the hoop component $A_\varphi$, integrals are weighted
by $2\pi r$, and

$$B_r = -\frac{\partial A_\varphi}{\partial z}, \quad B_z = \frac{\partial A_\varphi}{\partial r} + \frac{A_\varphi}{r}$$

Puzzle derives B, J and the results from the nodal potential, linear in each triangle
(`eddy.axisymmetric_fields`). A solid ring needs a circuit: Puzzle solves one basis
with the windings alone and one per ring with a unit voltage per turn
($\mathbf{J} = \sigma/2\pi r$), then finds the voltages that give each ring its current
and combines them (`eddy.combine_axisymmetric`).

## Transients and transfers

A `transient` step solves A–V in time, each coil's current following its amplitude:
Elmer by backward Euler only (its BDF2 measured wrong on this system), FEniCSx by
backward Euler or BDF2. Results are instantaneous, with no R or L.

A transfer hands a `loss_density` to a heat study as heat generation on its target
body, scaled so the body takes the power lost in it, and a `lorentz_force_density` to a
structural study as a body force, each component's total kept.

## Limits

- Lowest-order edge elements give B constant in each element; nodal B is an average,
  so check it away from material interfaces, and use the energy for the whole field.
- Materials are linear: a constant $\mu_r$ and $\sigma$. Saturation and hysteresis are
  not modelled.
- Time-harmonic results assume a sinusoidal steady state at each frequency.
- The benchmarks: skin effect in a round wire against the Bessel solution (R and L
  within 3 % in the test, 0.2-0.7 % on R measured), windings, a billet in a solenoid, a
  solid tube, a conducting sphere, a ring coil's centre field (3 %), and TEAM Problem 7
  against its measurements (RMS under 6 % of the largest measured Bz in the test).

## Sources

- O. Bíró and K. Preis, "On the use of the magnetic vector potential in the finite
  element analysis of three-dimensional eddy currents", *IEEE Transactions on
  Magnetics* 25 (1989) 3145-3159: the A–V formulation.
- R. L. Stoll, *The Analysis of Eddy Currents*, Clarendon Press, 1974: skin depth and
  eddy currents in wires and plates.
- K. Fujiwara and T. Nakata, "Results for benchmark problem 7", *COMPEL* 9 (1990) 137:
  TEAM Problem 7, `tests/integration/test_team7.py`.
- L. D. Landau and E. M. Lifshitz, *Electrodynamics of Continuous Media*, §59: the
  conducting sphere benchmark.
- CSC, *Elmer Models Manual*: WhitneyAVSolver, CoilSolver, MagnetoDynamics2D,
  StatElecSolve.
