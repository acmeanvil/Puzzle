---
title: Electromagnetics
section: verification
order: 50
keys: verification:electromagnetics
summary: Electrostatic, magnetostatic, time-harmonic and transient magnetic fields in 3D, axisymmetric and planar models, checked against closed forms and the TEAM 7 measurements.
adrs: 0010, 0047, 0048
---

Electric and magnetic fields run on Elmer and FEniCSx, so every benchmark here is a
pair: the same model on both, each against a closed form, and where a test says so, the
two against each other on one mesh. Losses and forces handed on to CalculiX are
checked at the far end too.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Two-layer capacitor | series capacitance | Elmer, FEniCSx | 1e-6 | passes |
| Wire in air, DC | infinite wire | Elmer, FEniCSx | B mean 2 %, energy 2 % | passes |
| Skin effect in a round wire | Bessel solution | Elmer, FEniCSx | R, L 3 %; J 3 % | R 0.2-0.7 %, L 0.4 %, J 2.4 % |
| Winding (stranded coil) | DC values at any frequency | Elmer, FEniCSx | 2 % | R 0.74 %, L 0.5 % |
| Two windings, own phases | N I at its phase | Elmer, FEniCSx | 1e-3, 0.05° | 1e-3, 0.05° |
| Harmonic shoot-out | Elmer against FEniCSx | both | 2 %, passive loss 3 % | R 0.05 %, L 0.16 % |
| Step current into a wire | Bessel series | FEniCSx | 4 % of uniform J | within 0.5 % |
| Transient shoot-out | Elmer against FEniCSx | both | 1 % of uniform J | equal to 1e-6 |
| Losses heat a wire | 1D conduction | Elmer to CalculiX | 2 % of the rise | within 2 % |
| Lorentz force between wires | line currents with images | Elmer, FEniCSx (to CalculiX) | 2 %; reaction 3 % | 1.7 %; 1.5 % |
| Closed ring coil | thick solenoid's centre field | Elmer, FEniCSx | 3 % | -2.1 %, -0.9 % |
| Billet in a solenoid (axisymmetric) | Bessel solution | Elmer, FEniCSx | loss 2 %, B 3 % | 0.311 W of 0.31099 W |
| Solid tube (axisymmetric) | Bessel solution | Elmer, FEniCSx | 1 % | loss 0.01 %, R 0.04 % |
| Sphere in an AC field | Landau and Lifshitz §59 | Elmer, FEniCSx | 2 % | 0.0010917 W of 0.0010923 W |
| Planar wire, winding, two-wire line | closed forms per metre | Elmer, FEniCSx | 1 % to 2 % | see the section |
| TEAM Problem 7 | measured Bz | Elmer, FEniCSx | RMS 6 % of the largest | 2.2 % RMS |

Each test runs on each solver installed (`tests/solvers.py` `over`) and skips the
rest; a shoot-out skips unless both are there. FEniCSx tests marked `needs_petsc` skip
where FEniCSx has no PETSc (Windows' SciPy backend ran out of memory or time on them).
The theory behind them is in [Electromagnetics](help:theory/electromagnetics).

## Two-layer capacitor

Two square electrodes 50 mm on a side hold 2 mm of PTFE and 3 mm of air, each its own
part bonded at the interface, the PTFE's face grounded and the air's at 100 V. The
sides are insulated, so the field is one-dimensional: the potential is linear in each
layer and the capacitance is

$$C = \frac{\varepsilon_0 A}{d_1/\varepsilon_{r1} + d_2/\varepsilon_{r2}}$$

`tests/integration/test_electrostatics.py` `test_two_layer_capacitor` asserts every
node's potential within 1e-6 of 100 V and the capacitance from the stored energy,
$2W/V^2$, within 1e-6, on Elmer and FEniCSx. Linear potentials lie in the elements'
space, so the answer is exact to round-off.

## A wire in air at DC

A copper wire of radius a = 1 mm carries 100 A inside a 5 mm tube of air, 10 mm long,
the air's ends and outer face magnetically insulated. The field is that of an infinite
wire with its return on the tube:

$$|B| = \frac{\mu_0 I}{2\pi r}$$

outside the wire, $\mu_0 I r/(2\pi a^2)$ inside, and the stored energy

$$W = \frac{\mu_0 I^2 L}{4\pi}\left(\frac{1}{4} + \ln\frac{R}{a}\right)$$

`tests/integration/test_magnetostatics.py` `test_wire_field` checks the air away from
the terminals, the wire's surface and the outer wall: the mean error of |B| below 2 %,
the largest below 12 %, the energy within 2 %, and B along the circle (more than 0.9 of
|B|). The wide nodal limits are the elements': lowest-order edge elements give B
constant per element, and a nodal B is an average. CLAUDE.md records FEniCSx giving
the same discrete answer as Elmer on the same mesh.

## Skin effect, windings and two conductors

`tests/integration/test_eddy_currents.py`: a copper wire of radius a = 1 mm in a 4 mm
tube of air, 3 mm long, the air insulated on its ends and outside.

**A massive conductor** carrying 10 A peak at 1 kHz and 20 kHz
(`test_skin_effect_in_a_round_wire`, `needs_petsc`). With $k = (1 - j)/\delta$ and
$\delta = \sqrt{2/(\omega\mu_0\sigma)}$, the internal impedance per length is

$$Z' = \frac{k J_0(ka)}{2\pi a\sigma J_1(ka)}$$

so $R = \mathrm{Re}(Z')L$ and $L_{coil} = (\mathrm{Im}(Z')/\omega + \mu_0\ln(R/a)/2\pi)L$.
The test asserts R and L within 3 %, the Joule loss within 1 % of $I^2R/2$, and |J|
across the wire against the Bessel profile within 3 % on average, the surface carrying
at least 1.5 times the core's density. ADR 0047: R within 0.2 % at a/δ = 0.48 and
0.7 % at a/δ = 2.1, L within 0.4 %, |J| within 2.4 % on average, the same on both.

**A winding** of 5 turns at fill 0.6, at 0 Hz and 5 kHz
(`test_a_winding_keeps_its_current_uniform`). Its current stays uniform, so at any
frequency

$$R = \frac{N^2 L}{\sigma f\pi a^2}$$

and $L_{coil} = N^2\mu_0 L(1/4 + \ln(R/a))/2\pi$, asserted within 2 %; the density's
spread below 0.2 % of its mean; B in the gap within 3 % of the wire's. ADR 0047: R
within 0.74 % (the meshed section is a little smaller), L within 0.5 %.

**Two windings** side by side at 2 kHz, 10 A at 0° and 6 A at 90°
(`test_two_windings_each_carry_their_own_current`): the current through each section
within 1e-3 of its own and its phase within 0.05°, as ADR 0047 records.

## Shoot-outs and parallel runs

- `test_shootout_elmer_and_fenicsx`: one mesh, a driven wire beside a passive copper
  wire at 10 kHz. Loss, energy, resistance and inductance must agree within 2 %
  (`relative_value` metrics), and the passive wire's loss within 3 %. ADR 0047: losses
  and energy equal to 7 digits, R 0.05 %, L 0.16 %.
- `test_transient_shootout_elmer_and_fenicsx`: a step current, backward Euler on one
  coarse mesh; the current densities at each step within 1 % of the uniform density.
  ADR 0047: equal to 1e-6.
- `test_elmer_in_parallel_gives_the_same_answer` (Elmer, two cores or more): a
  transient and a harmonic run on MPI equal one process's to 1e-4 of the uniform
  density, and R to 1e-4. This checks the partitioned run, not physics.

Elmer runs magnetic transients by backward Euler only; its BDF2 measured wrong on this
system (ADR 0047), so the shoot-out compares backward Euler.

## A step current into the wire

`test_a_step_current_diffuses_into_the_wire` (FEniCSx): a step current I from t = 0.
With $\tau = \mu_0\sigma a^2$ and $J_1(\beta_n) = 0$,

$$J(r,t) = \frac{I}{\pi a^2}\left(1 - \sum_n \frac{J_0(\beta_n r/a)}{J_2(\beta_n)}e^{-\beta_n^2 t/\tau}\right)$$

BDF2 at Δt = τ/200, outputs at 0.1, 0.2 and 0.3 τ. In the wire's middle third (near
the terminals the current enters evenly), the mean density of the core (r < 0.4 a) and
of the skin (r > 0.7 a) must be within 4 % of the uniform density of the series. ADR
0047: within 0.5 %.

## Losses and forces handed on

- `test_losses_heat_the_wire` (needs Elmer and CalculiX): Elmer's 1 kHz losses with
  2 kA (a/δ = 0.48, nearly uniform) become heat generation in a CalculiX study of the
  wire alone, its ends at T0. Against $T = T_0 + \bar{q}z(L - z)/(2k)$, with q̄ the loss
  over the volume, every node within 2 % of the rise, as ADR 0047 records.
- `test_lorentz_forces_push_the_wires_apart` (Elmer, FEniCSx): two windings, 10 A each
  way at 50 Hz. The time-averaged force on one wire, half the peak line-current force
  with the images in the insulated outer cylinder, within 2 %, the sideways component
  below 1 % of it. Handed to a CalculiX study of the wire held at one end (when
  CalculiX is installed), the reaction is within 3 % of the total and below it: the
  load on the held face's own nodes goes straight into the support. ADR 0047: the
  force within 1.7 % and the reaction within 1.5 %, on both solvers.

## A closed ring coil

`tests/integration/test_closed_coil.py` `test_a_closed_ring_coil` (Elmer, FEniCSx with
`needs_petsc`; static and 50 Hz): a winding with no terminal faces, radii 10 and 14 mm,
6 mm high, 100 turns of 1 A, in a cylinder of air insulated far away. Its field at the
centre, along the axis, is

$$B(0) = \mu_0 J b\ln\frac{R_2 + \sqrt{R_2^2 + b^2}}{R_1 + \sqrt{R_1^2 + b^2}}$$

with $J = NI/((R_2 - R_1)2b)$. The test asserts the mean Bz within 2.5 mm of the
centre within 3 % and the coil's largest |J| within 5 % of J. ADR 0047: -0.9 % on
FEniCSx and -2.1 % on Elmer, whose CoilSolver measures the section 1.2 % larger.

## Axisymmetric eddy currents

`tests/integration/test_eddy_axisymmetric.py`, Elmer and FEniCSx:

- **A billet in a long solenoid** (`test_induction_heating_of_a_long_billet`): a copper
  billet of radius 10 mm inside a winding of 10 turns of 10 A at 500 Hz, the model a
  5 mm slice with natural edges, so the solenoid is infinitely long and
  $H_0 = NI/h$ inside. In the billet $J_\phi = H_0 k J_1(kr)/J_0(ka)$ and the loss is
  $\frac{1}{2}\int |J|^2/\sigma\,2\pi r h\,dr$. Asserted: the billet's loss within
  2 %, the centre's B, $\mu_0 H_0/|J_0(ka)|$, within 3 %, and B in the gap within 1 %
  of $\mu_0 H_0$. ADR 0047: at a/δ = 3.4, 0.311 W against 0.31099 W and the centre's B
  within 0.2 %, on both.
- **A solid tube** carrying 100 A round it (`test_a_solid_ring_carries_its_current`):
  in the wall $H = aI_0(\kappa r) + bK_0(\kappa r)$, $\kappa = (1 + j)/\delta$, H = I/h
  inside and 0 outside; the impedance from Faraday's law at the outer radius. Loss, R
  and L within 1 %, B in the bore within 1 % of $\mu_0 I/h$. ADR 0047: loss within
  0.01 %, R within 0.04 %, L to three digits, on both.
- **A conducting sphere** of radius 10 mm in a uniform 1 mT field at 200 Hz
  (`test_a_conducting_sphere_in_an_ac_field`), from its polarisability

$$\alpha = -\frac{3}{8\pi}\left(1 - \frac{3}{(ka)^2} + \frac{3\cot(ka)}{ka}\right)$$

  with $k = (1 + j)/\delta$ and $P = (\omega\mu_0/2)4\pi|\mathrm{Im}\,\alpha|H_0^2(4/3)\pi a^3$,
  asserted within 2 %. ADR 0047: 0.0010917 W against 0.0010923 W at a/δ = 2.1, on
  both.

## Planar eddy currents

`tests/integration/test_eddy_planar.py`, A_z on Elmer and FEniCSx, every total per
metre of depth. A copper wire of radius a = 5 mm carries 100 A in an air disc of radius
50 mm, A = 0 on the rim.

- `test_a_round_wire_at_dc`: B at a/2 and 4a within 2 % of the wire's field, the
  energy per metre $\mu_0 I^2(1/4 + \ln(R/a))/4\pi$ within 1 %, the resistance
  $1/(\sigma\pi a^2)$ within 1 %, the density within 2 % of uniform.
- `test_a_stranded_winding_at_dc`: 4 turns of I/4; B at 4a within 2 %, the inductance
  $N^2\mu_0(1/4 + \ln(R/a))/2\pi$ within 1 %.
- `test_the_ac_resistance_of_a_round_wire`: at a/δ = 2.5, R within 2 % of the Bessel
  internal impedance, L within 1 %, the loss within 1 % of $RI^2/2$; and the skin-depth
  check passes the 0.3 mm elements (below δ/3).
- `test_the_inductance_of_a_two_wire_line`: go and return 30 mm apart in a 0.5 m disc
  at 1 Hz. The loop inductance per metre, less the images in the rim,

$$L = \frac{\mu_0}{\pi}\left(\ln\frac{d}{a} + \frac{1}{4} - \ln\frac{R^2 + h^2}{R^2 - h^2}\right)$$

  with h = d/2, within 1 % from the coils' voltages and from the energy; the Lorentz
  force pushes the wires apart.
- `test_elmer_and_fenicsx_agree_on_one_mesh`: the line's loss, energy and inductance
  within 2e-3 of each other.

ADR 0048 records Elmer and FEniCSx equal on these, against the closed forms:

| Case (a = 5 mm, 100 A) | Exact | Elmer = FEniCSx |
| --- | --- | --- |
| DC energy per metre | 2.5526 mJ/m | 2.5491 |
| DC resistance | 2.1952e-4 Ω/m | 2.1966e-4 |
| AC R/R_dc at a/δ = 2.5 | 1.5050 | 1.5067 |
| Winding inductance | 8.1683 µH/m | 8.1572 |

## TEAM Problem 7

`tests/integration/test_team7.py` `test_team7_bz_along_a1_b1` (Elmer, FEniCSx): an
aluminium plate 294 x 294 x 19 mm (σ = 35.26 MS/m) with a 108 mm square hole off
centre, under a racetrack winding of 2742 turns carrying 1 A at 50 Hz, modelled as a
closed stranded coil in a box of air. Bz is read along A1-B1 (y = 72 mm, z = 34 mm,
x from 0 to 288 mm) at the 17 measured points, as tabulated in NGSolve's TEAM 7
notebook, and conjugated, since the measurements use the opposite time convention.

The test asserts the RMS difference below 6 % of the largest measured |Bz| and the
largest real-part difference below 12 % of it. CLAUDE.md records 2.2 % RMS. It skips
on machines with less than 24 GB of memory: FEniCSx peaked at 18.4 GB, and GitHub's
16 GB runners ran out. Only 50 Hz is tested; 200 Hz is on ADR 0072's list of
benchmarks never run.

## See also

- [Electromagnetics theory](help:theory/electromagnetics)
- [Analyses](help:guides/analyses)
- [Heat transfer and transfers](help:verification/heat) for heat generation and
  imported Lorentz clouds.

## Sources

- K. Fujiwara and T. Nakata, "Results for benchmark problem 7 (asymmetrical conductor
  with a hole)", *COMPEL* 9 (1990) 137-154; the problem statement at
  https://www.compumag.org/wp/wp-content/uploads/2018/06/problem7.pdf.
- L. D. Landau and E. M. Lifshitz, *Electrodynamics of Continuous Media*, 2nd edition,
  Pergamon, 1984, §59: the conducting sphere in a periodic field.
- R. L. Stoll, *The Analysis of Eddy Currents*, Clarendon Press, 1974: skin effect in
  round wires, tubes and billets.
- D. B. Montgomery, *Solenoid Magnet Design*, Wiley, 1969: the central field of a
  thick uniform-current solenoid.
