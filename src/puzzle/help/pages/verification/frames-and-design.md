---
title: Frames and design codes
section: verification
order: 80
keys: verification:frames-and-design
summary: Frames of line members against beam closed forms, load combinations, P-Delta, response spectra and the member, seismic and fatigue checks against the AISC, ADM, ASCE 7 and Shigley worked examples.
adrs: 0026, 0079
---

Frames are checked in two layers. The solves (PyNite, with CalculiX and Code_Aster beams
as the cross-check) are held to closed forms of beam theory: indeterminate moments,
the beam-column, cantilever modes. The design checks on top of them are held to the
published worked examples of the codes they implement, to the printed digits.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Propped cantilever, P at midspan | 3PL/16, 5P/16 | PyNite, CalculiX, Code_Aster | PyNite 1e-6, others 1 % | PyNite exact; CalculiX and Code_Aster −0.57 % |
| Propped cantilever, uniform load | wL²/8, 3wL/8 | PyNite | 1e-6 | exact |
| Two-span continuous beam | 3PL/16 | PyNite | 1e-6 | exact |
| Fixed-feet portal frame | Hh(3k+1)/(2(6k+1)) | PyNite, CalculiX, Code_Aster | mean 0.2 % (PyNite) or 1 %; each 1 % | PyNite mean +0.05 % |
| Released joint (hinge) | PL/2, P/2 | PyNite (others refuse) | 1e-6 | exact |
| Self weight | ρAgL²/2 | PyNite | 1e-6 | exact |
| Member force signs | N, T, My of a lever | PyNite, CalculiX, Code_Aster | 1e-6 | passes |
| Combinations on PyNite | factored sums | PyNite | 1e-6 | 56000, 86400 N·m |
| Combinations on FE solvers | factored sums of cases alone | CalculiX, Code_Aster | 1e-6 of the deflection | passes |
| P-Delta column | H tan(kh)/k | PyNite | 0.1 % | 27021.3 N·m |
| Direct Analysis Method | (H ± 0.002P) tan(k'h)/k' | PyNite | 0.1 % | 39659.3, 19979.7 N·m |
| X-braced bay from a grid | H/(2 cos θ) | PyNite | 2 % | +0.5 % |
| Cantilever modes, PyNite | β_n, effective mass | PyNite | 1e-4, 1e-3; mass ±2e-4 | 12.23448 Hz (12.23443) |
| Single mass, spectrum scaled to ELF | W S_a I_e/R, C_s W | PyNite | 0.2 % | V_t 52180.2 N, V 62500 N |
| ELF loads and drifts | 12.8-11, 12.8-12, C_d δ/I_e | PyNite | forces exact, drifts 1e-4 | 69.106, 140.869 mm |
| Cantilever modes, FE beams | β_n, effective mass | CalculiX, Code_Aster | 2.5 % and 12 %; mass ±0.015 | 12.015, 12.007 Hz (12.234) |
| Spectrum on FE beams | C_s W | CalculiX, Code_Aster | 3 % | passes |
| AISC 360 member checks on solves | hand calculation | PyNite | 0.2 % to 1e-4 | φM_n 114.88 kip-ft, φP_n 876.7 kips |
| AISC 360 strengths | Manual Table 3-2 | none (unit test) | printed digits | 379, 252, 233, 192, 574 |
| AISC 370 strengths | Design Guide 27 examples | none (unit test) | printed digits | 101, 186, 56.7, 98.1 kips |
| ADM strengths | ADM Part VII examples | none (unit test) | printed digits or 0.3-0.4 % | 92.4 k (92.6), 93.05 in-k (92.8) |
| ASCE 7 seismic procedures | the standard's equations | none (unit test) | 1e-6 | passes |
| ASCE 7 combinations | 2.3.1, 2.4.1 | none (unit test) | exact factors | passes |
| Fatigue formulas | Shigley's examples | none (unit test) | printed digits, 3-6 % on read-off lives | passes |

All of these run with `pixi run -e dev pytest tests/integration/test_frames_solved.py`
and so on, naming the file. The PyNite tests need the pixi `pynite` environment, the
CalculiX and Code_Aster ones their solvers; a test whose solver is missing is skipped,
not passed. The method behind the checks is in
[Frame member design](help:theory/frame-design) and [Seismic design](help:theory/seismic);
how to set a frame up is in [Beams, shells and frames](help:guides/beams-shells-frames).

## Frames against beam theory

`tests/integration/test_frames_solved.py`. A W12X26 (AISC shapes database v16.0) in
the generic structural steel, bent about its strong axis, spans of 8 m, P = 20 kN. The
frames are solved on PyNite and, where the test is `over("pynite", "calculix",
"code_aster")`, on CalculiX's and Code_Aster's beams on the same lines.

- **Propped cantilever** (`test_propped_cantilever`), fixed at A, pinned at B, P at
  midspan:

$$M_A = \frac{3PL}{16}, \quad R_B = \frac{5P}{16}$$

- **The same under a uniform load** w = 5 kN/m (`test_propped_cantilever_uniform`,
  PyNite only, since only PyNite takes line loads):

$$M_A = \frac{wL^2}{8}, \quad R_B = \frac{3wL}{8}$$

- **Two-span continuous beam** (`test_continuous_beam`), P at each midspan, the
  moment over the middle support by the three-moment equation, 3PL/16. PyNite only: the
  beam's roll is held by a `node_spring`, which CalculiX and Code_Aster don't take yet.
- **Fixed-feet portal frame** (`test_portal_frame`), h = 4 m, b = 8 m, H = 10 kN at
  the beam, each base moment

$$M = \frac{Hh(3k+1)}{2(6k+1)}, \quad k = \frac{I_b/b}{I_c/h}$$

The closed form neglects the beam's axial shortening, which parts the two base moments
by about ±0.6 %; the test holds their mean to 0.2 % on PyNite (1 % on the others) and
each to 1 %, and the horizontal reactions to −H within 1e-6.

- **Released joint** (`test_released_joint`): a cantilever with a hinge at L carrying a
  simply supported span with P at its middle, so M_A = PL/2 and R_C = P/2, and the
  moment at the hinge below 1e-9 PL. CalculiX and Code_Aster refuse member releases;
  the test asserts they say so.
- **Self weight** (`test_self_weight`, PyNite): a cantilever's root moment ρAgL²/2.
- **Force signs** (`test_member_force_signs`, all three): a cantilever pulled by P and
  twisted by a crossbar must give N = +P, T = −Pa and My = +PL at the root, so
  `beam_forces` means the same on every solver.

PyNite is held to 1e-6 throughout, CalculiX and Code_Aster to 1 %. Measured (ADR 0079):
PyNite exact (M_A 30000 N·m, R_B 6250 N); CalculiX and Code_Aster both 29829.5 N·m
(−0.57 %) and 6271.31 N, agreeing with each other to every printed digit. The portal's
base moments: PyNite 12583 and 12429 N·m (mean +0.05 % of 12500), CalculiX and
Code_Aster 12622.9 and 12474.3. The difference is the element: PyNite's members are
Euler-Bernoulli, CalculiX's and Code_Aster's Timoshenko, whose shear deformation moves
the indeterminate moments by about 0.6 %.

## Load combinations

`tests/integration/test_frame_analysis.py` `test_combinations_are_factored_sums`
(PyNite): a 4 m cantilever with a dead tip load of 10 kN (case D) and a live one of
6 kN (case L). ASCE 7-16's LRFD combinations from those cases are 1.4D and
1.2D + 1.6L; each is a result step of its own, and its root moment must be the factored
sum times L within 1e-6. Measured (ADR 0079): 56000 and 86400 N·m, exact.

`tests/integration/test_fe_combinations.py`
`test_combinations_are_solved_as_factored_steps` (`over("calculix", "code_aster")`):
a steel bar on second-order tets, a dead tip force of 200 N down and a live one of
100 N sideways. The combinations 1.2D + 1.6L and 0.9D become a static step each, and
the tip deflection of each must equal the factored sum of each case solved alone, to
an absolute 1e-6 of the deflection (the digits CalculiX's .frd prints). Measured:
passes. `test_a_case_on_an_imposed_value_is_refused` checks that a case on a
prescribed displacement is refused rather than factored.

`tests/test_combinations.py` checks the combinations generated from a frame's cases
against ASCE 7-16 2.3.1 (LRFD) and 2.4.1 (ASD): the nine LRFD combinations of D, L, S
and W with wind both ways, the ASD seismic factors (D + 0.75L + 0.525E, 0.6D − 0.7E),
one combination per roof load, and notional loads of 0.002 of the gravity at a node.

## P-Delta and the Direct Analysis Method

`tests/integration/test_frame_analysis.py` (PyNite). A W12X26 cantilever column,
h = 4 m, in 16 members, an axial load P of 0.3 of Euler's load and a lateral H = 5 kN at
its top. The exact second-order base moment is the beam-column's (Timoshenko and Gere):

$$M = \frac{H \tan(kh)}{k}, \quad k = \sqrt{\frac{P}{EI}}$$

against the first-order Hh. `test_p_delta_amplifies_a_column` holds PyNite's P-Δ to
0.5 % and asks for more than 1.3 Hh. Measured (ADR 0079): 27021.3 N·m against 20000
first order.

`test_direct_analysis_reduces_stiffness_and_adds_notional_loads`: AISC 360 C2, the
same column with EI × 0.8 and a notional load of 0.002 P in each sense, one combination
per sense:

$$M = \frac{(H \pm 0.002P) \tan(k'h)}{k'}, \quad k' = \sqrt{\frac{P}{0.8EI}}$$

within 0.5 %. Measured: 39659.3 and 19979.7 N·m.

## A braced bay from the grid editor

`tests/integration/test_frame_grid_solve.py`
`test_an_x_braced_bay_carries_its_shear_in_the_diagonals` (PyNite): a 6 m by 4 m bay
made through MCP `frame_grid`, W14X90 columns and beam, W8X31 X braces pinned at the
corners, H = 100 kN at the top. The reactions must sum to −H within 1e-9, and the
largest brace force must be the truss's

$$N = \frac{H}{2 \cos \theta}$$

within 2 % (θ the diagonal's angle from horizontal). Measured (ADR 0079): 60.41 kN
against 60.09 kN, +0.5 %, the columns' share of the shear.

## Modes and response spectra

`tests/integration/test_seismic_frames.py`
`test_a_cantilevers_modes_and_effective_masses` (PyNite): a 6 m W14X90 cantilever
column, its own mass only. Its bending modes about each axis are

$$f_n = \frac{\beta_n^2}{2\pi} \sqrt{\frac{EI}{\rho A L^4}}$$

with β₁L = 1.8751 and β₂L = 4.6941, and effective masses of 0.6131 and 0.1883 of ρAL
(Chopra). The test holds f₁ to 1e-4, f₂ to 1e-3 and the effective masses to ±2e-4,
about both axes. Measured (ADR 0079): 12.23448 Hz against 12.23443, effective masses
0.6130 and 0.1882.

`test_a_single_mass_by_response_spectrum_scaled_to_elf` (PyNite): a 500 kN weight on
a massless 4 m column. Its period and the modal base shear are

$$T = 2\pi \sqrt{\frac{W h^3}{3 g EI}}, \quad V_t = \frac{W S_a(T) I_e}{R}$$

with S_a from ASCE 7-16 Figure 11.4-1 (S_DS 1.0, S_D1 0.6, R 8). V_t is scaled up to
the ELF base shear C_s W at C_u T_a (12.9.1.4.1), so the base moment is Vh. The test
holds V_t, the scale and the base moment to 0.2 %, the period used to 1e-9 and the ELF
V to 1e-6; each combination with Ex comes back as a (+E) and a (−E) step carrying Vh,
and the vertical reaction stays 0.9W. Measured: V_t 52180.2 N, ELF V 62500 N, scale
1.1978.

`test_elf_loads_and_drifts` (PyNite): a two-level column, 300 kN at 3 m and 200 kN at
6 m. The ELF forces by 12.8-11 and 12.8-12 must be exact, and the drifts of the Ex
combination, C_d δ/I_e with δ from the cantilever's two point loads, within 1e-4.
Measured: 69.106 and 140.869 mm.

`tests/integration/test_fe_spectra.py` (`over("calculix", "code_aster")`) runs the 6 m
column on the finite-element beams. `test_modes_and_effective_masses_from_the_solver`
holds the first mode to 2.5 % and the second to 12 % of the Euler-Bernoulli closed
form, and asks that both be lower: both solvers' beams are Timoshenko's, with shear
and rotary inertia, and the I's shear area is its web. Effective masses, from the
solvers' own participation factors, are held to ±0.015 of ρAL. Measured (ADR 0079):
12.015 Hz on CalculiX and 12.007 on Code_Aster against 12.234; effective masses 0.620
and 0.619 (0.6131), the second 0.202 and 0.201 (0.1883).
`test_a_columns_spectrum_against_its_closed_form_modes` holds the modal base shear
to the CQC of the solvers' own modes within 1e-9, and the ELF base shear within 1e-6
of C_s ρALg/2: the FE solvers take the frame's levels as PyNite does (each member's
weight half to each end, the support's half left out, ASCE 7 12.7.2; it was one level
and 3 % until 2026-11-15).
`test_combinations_take_the_spectrum_both_ways` checks that 1.2D + Ex sways ± the
spectrum's envelope to 1e-9. Elmer and FEniCSx refuse a spectrum, since they give no
participation factors (`test_elmer_refuses_a_spectrum`).

`tests/test_seismic.py` checks the ASCE 7-16 procedures by hand from the equations:
the design spectrum (Figure 11.4-1), C_s and the clause that governs (12.8-2, 12.8-3,
12.8-5, 12.8-6), C_u (Table 12.8-1), T_a (12.8-7), the exponent k and the vertical
distribution (12.8-11, 12.8-12), CQC (equal to SRSS for well separated modes, a plain
sum for one mode twice) and the drift at the centres of mass against Table 12.12-1.

## AISC 360 member checks

`tests/test_aisc360.py` holds the strengths to AISC Steel Construction Manual
Table 3-2 (Fy = 50 ksi): W18X50 φM_p 379 kip-ft, M_p/Ω 252 kip-ft, L_p 5.83 ft, L_r
16.9 ft, φM_r 233 kip-ft, φV_n 192 kips; W14X90's noncompact flange by F3, φM_p
574 kip-ft. Each is asserted to the Manual's printed digits, and Puzzle matches all
of them (ADR 0079). Hand calculations in kips and inches check C_b, E3 and E4
compression, E7 on a slender HSS20X20X5/16, F7 on an HSS8X8X1/4, round HSS flexure,
G5 shear and H3.1 torsion, weak-axis flexure, D2 tension and H1-1, mostly to 1e-6.
Channels and angles are refused (`NotCovered`), not checked approximately.

`tests/integration/test_member_checks.py` runs the checks on PyNite solves, each
against the specification worked by hand from the AISC shapes database v16.0:

- `test_an_unbraced_beam_by_elastic_lateral_torsional_buckling`: a simply supported
  W18X50, 30 ft, 1 kip/ft, unbraced. M_u = wL²/8 and φM_n by F2-4 within 0.2 %, clause
  F2.2(c), C_b = 12.5/11 = 1.136 for the parabola. Measured: φM_n 114.88 kip-ft.
- `test_a_beam_braced_at_its_third_points`: the middle segment's C_b from its own
  diagram, F2.2(b), φM_n within 0.2 %. Measured: C_b 1.0135, 328.57 kip-ft.
- `test_a_beam_against_its_deflection_limit`: the deflection against 5wL⁴/384EI within
  0.5 %, and its ratio to L/360. Measured: 19.953 mm.
- `test_a_column_by_flexural_buckling`: a W14X90 at L_c = 20 ft, E3-2 about y with the
  tabulated r_y, as the Manual's Table 4-1a does, within 1e-4. Measured: 876.7 kips.
- `test_a_beam_column_by_the_interaction`: the column with a lateral load at
  mid-height, H1-1a from the solved P and M within 0.2 %. Measured: 0.5073.

The stainless and aluminium tests in the same file
(`test_a_stainless_beam_is_checked_by_aisc_370`, `test_an_aluminium_beam_is_checked_by_the_adm`)
check that the code follows the material and that the solved check equals the
module's own strength; the strengths themselves are verified by the unit tests below.

## AISC 370 (stainless) and the Aluminum Design Manual

`tests/test_aisc370.py` holds AISC 370-21 to AISC Design Guide 27, 2nd edition,
Chapter 12, built from each example's given section properties and its rounded
intermediate values (L_c/r, r_ts), to the printed digits:

| Example | Published | Puzzle (ADR 0079) |
| --- | --- | --- |
| Ex. 1: round HSS, S30400, P_n | 101 kips | 101 |
| Ex. 2: slender square HSS, S32101, P_n | 186 kips (A_e 3.70 in²) | 186 |
| Ex. 4: W shape, φP_n (curve A) | 56.7 kips | 56.7 |
| Ex. 4: L_p, L_y, L_r | 2.42, 4.54, 34.2 ft | 2.42, 4.54, 34.2 |
| Ex. 4: M_nx, M_ny | 22.3, 9.95 kip-ft | 22.3, 9.95 |
| Ex. 6: φP_n (curve B), φM_px | 98.1 kips, 26.1 kip-ft | 98.1, 26.1 |

`tests/test_adm.py` holds the Aluminum Design Manual 2015 to its Part VII examples on
6061-T6, as allowable (ASD) values:

| Example | Published | Puzzle (ADR 0079) | Test tolerance |
| --- | --- | --- | --- |
| Ex. 9: I 8 × 6.18 column, torsional buckling | 92.6 k | 92.4 | 0.3 % |
| Ex. 9: flange and web element stresses | 21.1, 18.1 ksi | 21.1, 18.0 | ±0.05, ±0.08 ksi |
| Ex. 9: weighted local buckling (E.3.1) | 104.3 k | 104.3 | 0.3 % |
| Ex. 15: I 5 × 3.70, M_np/Ω | 130.2 in-k | 130.2 | ±0.1 in-k |
| Ex. 15: local buckling, lateral-torsional buckling | 133.3, 92.8 in-k | 133.3, 93.05 | ±0.4 in-k, 0.4 % |
| Ex. 26: yielding and rupture | 350, 322 in-k | 350, 322 | ±0.5 in-k |

The Example 15 difference is the example's own rounding: it takes C_c as 66. The
test file also checks the 6061-T6 buckling constants (C_c 66, C_t 141 as Part VI).

## Fatigue formulas

`tests/test_fatigue_formulas.py` checks the fatigue formulas (ADR 0026) against the
worked examples of Shigley's Mechanical Engineering Design, 10th edition, chapter 6;
see [Fatigue](help:theory/fatigue).

- `test_marin_factors_match_shigley_examples`: k_a 0.860 (Ex. 6-3), k_b 0.858 rotating
  and 0.954 non-rotating (Ex. 6-4), k_d 1.007 (Ex. 6-5), each within ±5e-4; Table 6-5's
  reliability factors within ±6e-4; S'_e of Eq. 6-8 exact.
- `test_notch_sensitivity_and_fatigue_fraction`: Ex. 6-6, K_f = 1.55 within ±0.005;
  the fatigue fraction off Fig. 6-18, 0.837 and 0.795.
- `test_example_6_7_life_on_the_sn_line`: about 32 300 cycles at a reversed 403 MPa,
  within 3 %, because Shigley reads f = 0.845 off the figure where the equations give
  0.8435.
- `test_example_6_15_duty_cycle_by_gerber`: the first cycle's reversed stress 70.3 kpsi
  within ±0.05 kpsi, its life 619 000 cycles within 6 %, the others infinite, and
  Miner's sum.
- `test_strain_life_models`: Langer's form for austenitic stainless at 1e6 cycles,
  a strain amplitude of 0.139 % within ±0.002 % (NUREG/CR-6909 Eq. 29).

`tests/test_fatigue_tool.py` `test_neuber_gives_kf_from_kt_and_the_radius` repeats
Ex. 6-6 through the derived-result tool (K_f 1.55 within ±0.005, applied to the
alternating stress only). Measured: passes.

## Sources

- AISC 360-16, Specification for Structural Steel Buildings, American Institute of
  Steel Construction, 2016: Chapters C (C2), D2, E3, E4, E7, F2, F3, F6, F7, F8, F11,
  G2, G5, H1, H3.
- AISC Steel Construction Manual, 15th edition, American Institute of Steel
  Construction, 2017: Table 3-2 and Table 4-1a.
- AISC Shapes Database v16.0, American Institute of Steel Construction.
- AISC 370-21, Specification for Structural Stainless Steel Buildings, American
  Institute of Steel Construction, 2021.
- AISC Design Guide 27, Structural Stainless Steel, 2nd edition, American
  Institute of Steel Construction: Chapter 12, Examples 1, 2, 4 and 6.
- The Aluminum Association, Aluminum Design Manual 2015: Part I Specification and
  Part VII Illustrative Examples 9, 15 and 26; Part V shapes; Part VI constants.
- ASCE/SEI 7-16, Minimum Design Loads and Associated Criteria for Buildings and Other
  Structures, American Society of Civil Engineers, 2017: 2.3.1, 2.4.1, Figure 11.4-1,
  12.8, 12.9.1, Table 12.12-1.
- S. P. Timoshenko and J. M. Gere, Theory of Elastic Stability, 2nd edition,
  McGraw-Hill, 1961: the beam-column.
- A. K. Chopra, Dynamics of Structures: a cantilever's modes and effective modal
  masses.
- E. L. Wilson, A. Der Kiureghian and E. P. Bayo, "A replacement for the SRSS method in
  seismic analysis", Earthquake Engineering and Structural Dynamics 9, 1981: CQC.
- R. G. Budynas and J. K. Nisbett, Shigley's Mechanical Engineering Design, 10th
  edition, McGraw-Hill: chapter 6, Examples 6-3 to 6-7 and 6-15, Table 6-5.
- NUREG/CR-6909, Effect of LWR Coolant Environments on the Fatigue Life of Reactor
  Materials, U.S. Nuclear Regulatory Commission.
