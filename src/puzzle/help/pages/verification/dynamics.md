---
title: Dynamics, buckling and explicit
section: verification
order: 30
keys: verification:dynamics
summary: Natural frequencies, transient and harmonic response, buckling load factors, prestressed modes and explicit wave, impact and plasticity problems, each checked against a closed form or an implicit solve.
adrs: 0009, 0028, 0041, 0048, 0055, 0082, 0085
---

These benchmarks check the analyses that involve mass or stability: modes, Newmark
transients, harmonic response, linear buckling, a prestressed modal analysis, large
deflection in time, and explicit dynamics on OpenCourant. Most use the 200 x 10 x 10 mm
steel bar (`tests/conftest_geometry.py` `BAR`) clamped at one end. Its first bending
mode is at 209 Hz. Static checks are on [Structural statics](help:verification/structural).

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Cantilever first bending mode | Euler-Bernoulli | CalculiX, Elmer, Code_Aster, FEniCSx | 3 % | CalculiX 209.0 Hz |
| Sudden, damped and ramped tip load | sum of five modes | all four; modal on CalculiX | 4 % of the peak; peak 2 % | within 0.35 % to 1.98 %; Elmer 3.50 % |
| Harmonic tip load | modal sum | all four | amplitude 1 %, phase 2° | within 1 %, phase 0.1° |
| 2D modes, wave, harmonic | Rayleigh-Love, Love, closed forms | all four | 0.2 % to 3 % | to 0.02 %; 8233.0 of 8231.8 Hz |
| Beam section, first mode | Euler-Bernoulli | CalculiX | 1 % | −0.11 % |
| Euler column, with a preload | π²EI / (2L)², P_cr − P0 | CalculiX, Code_Aster | 0.5 %; 1 % | +0.085 %; +0.15 %, +0.17 % |
| Buckling shoot-out | CalculiX against Code_Aster | both | 0.1 % | 0.01 % |
| Prestressed modes | 1D beam with geometric stiffness | CalculiX | 0.25 % | 0.1 % |
| Damped transients settle on the elastica | Bisshopp and Drucker | CalculiX; Code_Aster solids | 1 % to 5 % | 0.3004 L, 0.0560 L |
| Explicit: waves, wall, impacts | ρ c v₀, 2L/c, momentum | OpenCourant | 1 % to 5 % | −0.10 % to −0.96 % |
| Explicit against implicit | CalculiX Newmark; modal sum | OpenCourant | peak 2 %, history 3 % | −0.18 %, +0.93 %, −0.33 % |
| Explicit plasticity and failure | σy, Johnson-Cook, D1 | OpenCourant | 2 % to 3 % | −1.6 %, +0.05 %, +0.67 % |

A test runs once per installed solver and skips the rest (`tests/solvers.py` `over`). A
solver left out of a row refuses that analysis (it says so before writing input), and
several tests assert the refusal. See [Structural dynamics](help:theory/structural-dynamics),
[Time integration](help:theory/time-integration), [Buckling](help:theory/buckling) and
[Explicit dynamics](help:theory/explicit-dynamics).

## The cantilever's modes

The bar's first bending frequency, by Euler-Bernoulli theory with β₁L = 1.875104:

$$f_1 = \frac{1.875104^2}{2 \pi L^2} \sqrt{\frac{E I}{\rho A}}$$

`tests/integration/test_benchmarks.py` `test_cantilever_first_bending_frequency` asks
for four modes and asserts the first two (a square section has two equal bending modes,
in y and z) within 3 % (`rel=0.03`), on CalculiX, Elmer, Code_Aster and FEniCSx. The
tolerance covers shear and rotary inertia, which Euler-Bernoulli leaves out. CalculiX's
209.0 Hz is recorded in ADR 0015. `tests/integration/test_multistep.py`
`test_static_then_modal` checks that a modal step after a static one in the same study
gives the modal-only study's frequencies to 1e-9.

## Transient and harmonic response of the cantilever

`tests/integration/test_dynamics.py` (ADR 0041): a 100 N tip load on the bar, against the
sum of its first five Euler-Bernoulli modes (β L = 1.875, 4.694, 7.855, 10.996, 14.137),
each a one-degree-of-freedom oscillator with the static share

$$s_n = \frac{4 P}{\rho A L \omega_n^2}$$

Suddenly applied and undamped, the tip moves

$$u(t) = \sum_n s_n \left( 1 - \cos \omega_n t \right)$$

With Rayleigh mass damping a = 2 ζ ω₁ (5 % in the first mode, ζ_n = a / 2ω_n) each term
decays as

$$1 - e^{-\zeta_n \omega_n t} \left( \cos \omega_{dn} t + \frac{\zeta_n}{\sqrt{1 - \zeta_n^2}} \sin \omega_{dn} t \right)$$

and under a harmonic load at ω, with r_n = ω / ω_n,

$$U = \sum_n \frac{s_n}{1 - r_n^2 + 2 i \zeta_n r_n}$$

- `test_ramped_pressure_follows_its_amplitude`: a pressure P / A on the tip face ramped
  over T1, thirty of the bar's axial periods, so the tip moves quasi-statically,
  u_x = −p L / E f(t): within 2 % of the largest on all four solvers; measured 1.00 %
  on each (the axial ringing), 2026-11-17 (ADR 0090: pressure amplitudes had no
  benchmark);
- `test_sudden_load` (200 steps a period, so the second mode is followed),
  `test_rayleigh_damping` and `test_ramped_load_follows_its_amplitude` (ramped over one
  period by an amplitude) assert the whole history within 4 % of the largest deflection
  and the peak within 2 %, on all four solvers. The damped case also checks the swing
  after two periods has died down;
- `test_modal_superposition_transient`: the sudden load by superposing 20 modes, to the
  same tolerances, CalculiX only;
- `test_harmonic_response`: 100, 150, 300 and 600 Hz, undamped and damped; the tip's
  amplitude within 1 % and its phase within 2°, all four solvers;
- `test_what_a_solver_cannot_do_is_refused`: a damping ratio without modes, modal
  transients on Code_Aster, FEniCSx and Elmer, and HHT on Elmer are refused.

Measured (ADR 0041), as the largest error over the history in per cent of the peak:

| Case | CalculiX | Code_Aster | FEniCSx | Elmer |
| --- | --- | --- | --- | --- |
| Sudden load, undamped | 1.33 %, peak −1.3 % | identical | 1.33 % | 1.66 % |
| Sudden load, 5 % damping | 1.97 %, peak −1.2 % | identical | 1.98 % | 3.50 % (1.3 % on average) |
| Ramped by an amplitude | 0.35 % | identical | 0.35 % | 0.35 % |
| Modal superposition, 20 modes | 1.15 % | refused | refused | refused |
| Harmonic | within 1 %, phase exact undamped, within 0.1° damped | the same | within 1 % (−0.83 % at 600 Hz), 0.1° | identical to FEniCSx |

Elmer's Bossak scheme starts from zero acceleration rather than M⁻¹F(0), so after a
sudden load its first step moves half as far and its history lags early; its worst error
falls at t = 0.22 T1 and shrinks with the time step. With 60 steps a period instead of
200, the undamped peak read 2.5 % low on every solver: Newmark stretches the second
mode's period by 3.6 %, and that mode carries 2.5 % of the deflection.

## 2D dynamics

`tests/integration/test_planar_dynamics.py` (ADR 0048), generic steel, c = √(E/ρ), on
CalculiX, Code_Aster, FEniCSx and Elmer:

- `test_axial_modes_of_a_bar` (plane stress and plane strain): a 200 x 10 mm strip, its
  axial modes f_n = (2n − 1) c / 4L lowered by lateral inertia (Rayleigh-Love); in plane
  strain c = √(E / ρ (1 − ν²)) and ν / (1 − ν) for ν. The first three within 0.2 %;
- `test_cantilever_first_bending_mode`: the strip clamped, Euler-Bernoulli's f₁ less
  0.3 % for Timoshenko's shear and rotary inertia, within 0.6 %;
- `test_ring_breathing_mode`: a 4 x 4 mm section at R = 100 mm, axisymmetric,
  f = c / 2πR, within 0.3 %;
- `test_sudden_end_load`: the strip's end moves as a triangle wave, peaking at
  2 F L / E A at t = 2L/c: the peak within 2 %, its time within 0.02 of the period, the
  rise's slope F / ρ c A within 3 %;
- `test_harmonic_end_load_plane_stress` and `test_harmonic_end_load_axisymmetric_rod`:
  at half and one and a half times the first axial frequency, within 1 % (CalculiX
  1.5 % with 200 modes: it superposes modes, and an end load's share falls off as 1/n²);
- `test_calculix_refuses_an_oblique_edge_force_over_modes`: CalculiX loses point loads
  on 2D elements in steps solved over modes, so an edge force square to the edge is
  written as a pressure and an oblique one refused, asserted.

$$u(L) = \frac{F \tan k L}{E A k}$$

with k = ω / c. ADR 0048 records the axial modes to 0.02 %, the cantilever 208.6 Hz
against Euler-Bernoulli's 208.88 less shear, the ring 8233.0 Hz against 8231.8, the
sudden load's peak 1.8 % under twice the static (sampling at T/200), and the harmonic
end response within 0.1 % (CalculiX 1.2 % with 200 modes). The static 2D body and
thermal loads in the same file are on [Structural statics](help:verification/structural).

## A beam section's first mode

`tests/integration/test_beam_sections.py` `test_open_section_modes`: a 4 m W12X26
cantilever of 40 line elements, its first bending mode about the weak axis against
Euler-Bernoulli, within 1 %, CalculiX only (its springs for open sections lump the mass
at the nodes and leave rotary inertia out). ADR 0055 records 6.902 Hz against 6.910 Hz
(−0.11 %).

## Linear buckling

`tests/integration/test_buckling.py` (ADR 0028): a 10 x 10 mm steel column 300 mm tall,
fixed at its base and pushed down at its top by 1 N, 2.5 mm quadratic tetrahedra. The
lowest load factor is the Euler load of a fixed-free column, I = b⁴/12:

$$P_{cr} = \frac{\pi^2 E I}{(2 L)^2}$$

- `test_euler_column`: the first factor within 0.5 %, the second equal to it to 1e-4
  (the square's twin mode), the third above five times the first;
- `test_preloaded_column`: P0 = 2400 N held in a static step before, so the factor on
  the 1 N load is P_cr − P0, within 1 %;
- `test_preloaded_column_under_large_deflection`: the preload solved with geometric
  nonlinearity; P_cr − P0 within 1 % (a straight column shortens by P0 L / E A = 0.1 mm
  before buckling);
- `test_solvers_agree`: CalculiX's four factors against Code_Aster's to 0.1 %;
- `tests/integration/test_beam_sections.py` `test_calculix_refuses_buckling_of_open_sections`:
  CalculiX's spring beams have no geometric stiffness, so it refuses buckling and names
  Code_Aster.

All run on CalculiX and Code_Aster; Elmer and FEniCSx refuse buckling. Measured:

| Check | CalculiX | Code_Aster | Reference |
| --- | --- | --- | --- |
| Euler, fixed-free | +0.085 % | +0.085 % | 4797.7 N |
| Preload 2400 N, factor on 1 N | +0.15 % | +0.17 % | P_cr − P0 |
| Preload by a large-deflection step | within 0.18 % | within 0.18 % | P_cr − P0 |
| Shoot-out, four modes | agree to 0.01 % | | |

(ADR 0028; the large-deflection preload ADR 0082.) CalculiX's preloaded factor is 0.02 %
below Code_Aster's because its preload step is geometrically nonlinear.

## Prestressed modes

`tests/integration/test_prestress.py` `test_tension_raises_the_first_frequency`: the bar
clamped at its root, its tip pulled 0.2 mm along its axis and held, so the tension
P = E A d / L stiffens bending. A static study's stress is handed to a modal study by a
transfer with the role prestress. The reference is a clamped-clamped Euler-Bernoulli beam
with the geometric stiffness of P, solved in the test with 200 Hermite elements. The 3D
model is compared by the ratio f(P) / f(0), in which shear and 3D effects largely
cancel: the ratio must exceed 1.05 (the tension matters) and match within 0.25 %
(`rel=2.5e-3`). CalculiX only: it replays the static study as a geometrically nonlinear
first step. The project notes record agreement to 0.1 %.

## Large deflection in time

The elastica's loads (P L² / EI = 1 at the tip; Bisshopp and Drucker: 0.3017 L down,
0.0566 L in) applied at once in a transient step with mass-proportional Rayleigh damping
near 0.7 of critical in the first mode; once still, the shape must be the static one.

- `tests/integration/test_element_choice.py`
  `test_a_damped_transient_settles_on_the_elastica`: the 2 mm strip in solids, within
  2 % down and 5 % in, CalculiX and Code_Aster. ADR 0082 records 0.3004 L and 0.0560 L on
  both;
- `test_the_elastica_on_beams` (damped transient): the 1 m frame, within 1 % and 3 % on
  CalculiX (recorded 0.2996 L and 0.0557 L); Code_Aster refuses it (POU_D_T_GD has no
  RIGI_MECA for DYNA_NON_LINE), asserted;
- `tests/integration/test_thick_shells.py` `test_the_elastica_on_shells` (damped
  transient): within 1.5 % and 4 % on CalculiX (recorded 0.3017 L and 0.0564 L);
  Code_Aster refuses shells for large rotations in time, asserted.

## Explicit dynamics on OpenCourant

`tests/integration/test_explicit.py` (ADR 0085). Each test runs the whole path: project,
mesh, the Starter deck, the pinned OpenCourant image (or the native release), and the
frames read back. Every run also asserts the Starter placed every field of every card
(no warning 100213 or 100214), and several check Puzzle's time-step estimate against
OpenCourant's own within 25 %. Steel bars 200 x 10 x 10 mm, E = 200 GPa, ρ = 7850 kg/m³,
c = √(E/ρ), mostly with ν = 0 so the bar is one-dimensional.

**The 1D wave.** A bar held at one end and driven at v₀ = 1 m/s at the other carries,
behind a front travelling at c,

$$\sigma = \rho c v_0$$

and nothing ahead of it.

- `test_a_wave_carries_rho_c_v0_behind_its_front` (hex8, tet4, tet10): σ behind the
  front within 1 %, ahead of it below 2 % of σ;
- `test_a_pressure_on_a_bar_end_sends_minus_p_down_it`: p = 10 MPa switched on at a
  free end sends −p down the bar, within 2 % (and checks the sign: a pressure pushes
  into the face);
- `test_a_bonded_split_bar_carries_the_wave_across_its_tie`: two parts meshed at 5 and
  4 mm, tied nonconformally, σ past the tie within 2 %;
- `test_two_processes_give_the_one_process_wave` and
  `test_a_native_run_from_the_release_gives_the_wave`: the same wave on two MPI
  processes and from the native release, within 1 %.

**Loads and amplitudes on free bars** (ADR 0090 OC-3).

- `test_free_bars_move_as_their_loads_and_amplitudes_say`: a free bar under gravity,
  an acceleration (inertia: it moves at −a), a force on its end and a pressure on its
  start, each ramped over T = 1 ms: by momentum the centre of mass (lumped masses)
  moves at v = (F + p A, −a m, −g m) t² / (2 T m), within 0.3 % from 0.4 T on (central
  differences keep velocities half a step behind); measured within 0.25 %;
- `test_a_ramped_end_displacement_follows_its_amplitude`: a bar's end moved 0.1 mm
  times a ramp over 25 wave transits: the end's displacement at each frame within 0.1 %
  and the stress in the middle E u / L within 3 % (measured 99.83 MPa of 100); again
  with the amplitude named `#1 ramp` (ADR 0090 OC-1: Radioss read that title as a
  comment and the first point as the title, so the ramp became a step), the same;
- `test_mass_scaling_holds_its_time_step_and_slows_the_wave` (ADR-64): the wave bar
  with `mass_scaling_time_step` twice its stable step. The Engine runs at the target
  (within 1e-5), and the stress behind the front is the scaled bar's impedance,
  v₀ √(E ρ M / M₀), M with the mass added, within 2 %; measured: the target to seven
  digits, 3.42 times the bar's mass added, −83.19 MPa for −83.31 (−0.14 %).

Frictional contact between parts is written but refused until benchmarked. A nonzero
displacement with no amplitude, or one starting above zero, is refused (ADR 0090 OC-2:
applied at once, a 20 mm block moved 0.01 mm rang at 7.5 times its static stress and
the energy check blamed the time step; ramped, 1.008 times).

**Contact and impact.**

- `test_a_bar_thrown_at_a_rigid_wall_leaves_at_its_speed`: a bar at 10 m/s against a
  rigid wall touches for 2L/c and leaves at v₀; separation within 4 % and speed within
  5 %, on 5 and 2.5 mm meshes, the finer closer;
- `test_a_block_sliding_on_a_rough_rigid_wall_slows_at_mu_g` (ADR-64): a block thrown
  at 1 m/s along a wall with μ = 0.3 under 1000 m/s² of gravity: by momentum
  v = v₀ − μ g t, within 0.2 % of v₀ at every frame; measured within 5e-4 m/s;
- `test_two_equal_bars_colliding_exchange_their_velocities`: two equal bars 0.1 mm
  apart, one at 10 m/s, frictionless contact: afterwards the first below 5 % of v₀ and
  the second within 5 % of v₀;
- `test_a_self_contact_stops_a_part_passing_through_itself`: the same bars as one part
  with one self-contact, 0.1 and 1 mm apart, to the same tolerances.

**Against an implicit solve or a modal sum.**

- `test_a_suddenly_applied_load_against_an_implicit_transient`: a 100 N tip force on the
  bar (ν = 0.3) switched on at t = 0, against CalculiX's direct Newmark (average
  acceleration, no damping, T1/400 steps) on the same geometry; the peak within 2 % and
  the whole history within 3 % of the peak. Skipped without CalculiX;
- `test_a_pressed_plate_against_an_implicit_transient`: a clamped 0.2 m square plate,
  2 mm thick, under 1 kPa from t = 0; OpenCourant's QEPH quadrilaterals against
  CalculiX's S8 shells, peak within 2 % and history within 3 %, and the sign (a positive
  pressure pushes against the face's normal). OpenCourant gives a shell's membrane
  stress only, stored as `membrane_stress` (ADR 0090 OC-7): it peaked at 14 kPa, 0.46 %
  of the 3.08 MPa static edge bending stress 6 M / t², M = 0.0513 p a² (Timoshenko and
  Woinowsky-Krieger), and the test holds it under 2 % of that, with no `stress` field;
- `test_a_beam_cantilever_swings_as_its_modes_say` (along z and y): a 1 m cantilever of
  20 two-node beams, 10 x 20 mm, a 10 N tip force at t = 0, against the
  Euler-Bernoulli modal series (twelve modes; L/h = 50, so shear and rotary inertia are
  under 0.1 %); peak within 2 %, history within 3 %, and the section's orientation (I1
  along z, a quarter of it along y).

**Plasticity and failure.** The bar's end pulled at 1 m/s, its speed ramped over 25 wave
transits so the pull is slow, to about ten times the yield strain:

- `test_a_bar_pulled_slowly_past_yield_holds_the_yield_stress`: a flat curve at
  σy = 250 MPa; the middle's stress within 2 % of σy;
- `test_johnson_cook_hardening_follows_a_plus_b_strain_to_the_n`: A = 250 MPa,
  B = 500 MPa, n = 0.5; within 2 % of A + B εpⁿ at the plastic strain reached;
- `test_johnson_cook_rate_term_raises_the_flow_stress`: C = 0.05 at a reference rate of
  1e-3 /s, the rate filtered at 5 kHz (below the bar's 12.6 kHz ringing); within 3 % of
  A (1 + C ln(ε̇/ε̇₀)) at the measured rate;
- `test_a_bar_pulled_past_its_failure_strain_breaks`: D1 = 0.005 on linear hardening
  H = E/10 (with ν = 0 nothing necks); the last frame with no element eroded must come
  before 1.03 times the end travel below and the first with one after 0.97 times it, and
  every element left must have a plastic strain under 1.05 D1.

$$\sigma = A \left( 1 + C \ln \frac{\dot{\varepsilon}}{\dot{\varepsilon}_0} \right)$$

$$\delta_{break} = L \left( \frac{\sigma_y + H D_1}{E} + D_1 \right)$$

Measured on the pinned image (ADR 0085, 2026-11-14 and 2026-11-15):

| Benchmark | Measured | Reference |
| --- | --- | --- |
| Wave, hex8 / tet4 / tet10 | −39.52 / −39.58 / −39.53 MPa | −39.62 MPa (−0.27 / −0.10 / −0.23 %) |
| Wave, two processes; native release | −39.518 MPa both | as one process |
| Pressure on a bar end | −9.964 MPa | −10 MPa |
| Wave past a bonded tie | −39.24 MPa | −39.62 MPa (−0.96 %) |
| Rigid wall, separation | 81.4 µs | 2L/c = 79.3 µs (+2.7 %) |
| Rigid wall, leaving speed | 0.964 v₀ (40 along), 0.976 v₀ (80) | v₀ |
| Two bars colliding | 0.166 and 9.834 m/s | 0 and 10 m/s |
| Self-contact, 0.1 mm and 1 mm apart | 0.189 and 9.811; 0.200 and 9.800 m/s | 0 and 10 m/s |
| Sudden tip load against CalculiX | peak 3.1211 mm, 0.28 % of it at worst | 3.1267 mm (−0.18 %) |
| Pressed plate against CalculiX's S8 | −2.891e-5 m, 1.99 % at worst | −2.865e-5 m (+0.93 %) |
| Beam cantilever, along z / y | 4.895 / 19.58 mm; 1.03 / 0.93 % at worst | 4.911 / 19.65 mm (−0.33 %) |
| Slow pull past yield | 245.9 MPa | 250 MPa (−1.6 %) |
| Johnson-Cook hardening at εp 0.0113 | 303.21 MPa | 303.07 MPa (+0.05 %) |
| Johnson-Cook rate term | 359.74 MPa | 357.35 MPa (+0.67 %) |
| Failure, end travel | broke between 1.288 and 1.337 mm, plastic strain left at most 0.005 | 1.35 mm |
| Time-step estimate | hex8 +5.1 %, tet4 +5.1 %, tet10 +1.0 %, shells +8.3 %, beams +1.4 % | OpenCourant's own |

A kinematic rigid wall stops the nodes that reach it, so a little momentum goes each
impact, which is why the leaving speed is a few per cent low and closer on a finer mesh.
Unfiltered, the Johnson-Cook rate term reads the bar's axial ringing and the flow stress
came out 4 % high. On a flat hardening curve the pulled bar broke early (0.69 mm of end
travel), the strain gathering where yield came first; the failure benchmark hardens for
that reason.

## Sources

- S. P. Timoshenko, J. M. Gere, *Theory of Elastic Stability*, 2nd edition, McGraw-Hill
  1961: Euler's column.
- R. D. Blevins, *Formulas for Natural Frequency and Mode Shape*, Van Nostrand Reinhold
  1979: cantilever mode constants, the breathing ring.
- A. E. H. Love, *A Treatise on the Mathematical Theory of Elasticity*, 4th edition,
  Cambridge University Press 1927: longitudinal vibration with lateral inertia, rings.
- R. W. Clough, J. Penzien, *Dynamics of Structures*, 2nd edition, McGraw-Hill 1993:
  modal superposition, Rayleigh damping, harmonic response.
- N. M. Newmark, "A method of computation for structural dynamics", *Journal of the
  Engineering Mechanics Division*, ASCE 85 (EM3) (1959) 67-94.
- K. F. Graff, *Wave Motion in Elastic Solids*, Ohio State University Press 1975: waves
  in bars, impact of bars.
- K. E. Bisshopp, D. C. Drucker, "Large deflection of cantilever beams", *Quarterly of
  Applied Mathematics* 3 (1945) 272-275.
- G. R. Johnson, W. H. Cook, "A constitutive model and data for metals subjected to
  large strains, high strain rates and high temperatures", *Proceedings of the 7th
  International Symposium on Ballistics*, The Hague 1983, 541-547.
- G. R. Johnson, W. H. Cook, "Fracture characteristics of three metals subjected to
  various strains, strain rates, temperatures and pressures", *Engineering Fracture
  Mechanics* 21 (1985) 31-48.
