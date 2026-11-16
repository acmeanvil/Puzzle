---
title: Heat transfer and transfers
section: verification
order: 40
keys: verification:heat
summary: Steady, transient and nonlinear conduction, heat generation, thermal loads passed between studies, imported fields and custom PDEs, each checked against a closed form.
adrs: 0009, 0012, 0044, 0047, 0048
---

These benchmarks check conduction on its own, then the paths that carry values into a
study: a temperature handed from a thermal study to a structural one, fields imported
from files, and equations written by the user. Most run on the 200 x 10 x 10 mm steel
bar (`tests/conftest_geometry.py` `BAR`), with constant properties unless the test is
about the temperature tables.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Steady conduction, tets and hexes | linear profile | CalculiX, Elmer, Code_Aster, FEniCSx | 1e-6 K | passes |
| Transient conduction | series solution | all four | 1 % of the 80 K step | 0.346 K on all four (backward Euler) |
| Time schemes | series solution | each scheme a solver offers | 2 % of 80 K; BDF2 0.02 K | BDF2 under 0.02 K |
| Conductivity table, steady | Kirchhoff transform | all four | 0.05 K | within 0.002 K |
| Tables, transient | solvers against each other | all four | 0.1 K; Code_Aster 1 K | Code_Aster 0.7 K warmer |
| Heat generation | 1D closed forms | all four | 0.05 % of the rise | five digits; peak 0.003 % |
| Thermal bending by transfer | beam theory | six solver pairs | 2 % | within 1.6 % |
| Imported temperature, pressure, film | free expansion, -pL/E, uniform feature | all four | 1 % | +0.3 %, -0.18 % |
| Imported heat flux and films | energy balance, cosine series, 1D | all four | 0.2 % of the rise, 1 % | 775.023 K, 10.175-10.176 K |
| Fields in time | uniform steps, an amplitude | CalculiX, Code_Aster | 1e-6, 1e-9 | 1e-6, 1e-9 |
| Fields with frequency | modal sum | CalculiX, Code_Aster, FEniCSx | 1 % | within 0.6 % |
| Vector fields | gravity, Timoshenko, pure bending | all four | 1e-6 to 3 % | -0.62 %, -0.30 % |
| Initial stress, thickness, grading | closed forms | CalculiX, Code_Aster | 1 %, 1.5 % | within 1 %, 1.5 % |
| 2D fields | spinning ring, Lamé, Timoshenko, 1D | CalculiX, Code_Aster; heat on all four | 0.5 % to 2 % | within 0.12 % to 0.5 % |
| Custom PDEs | Kirchhoff; built-in solvers | FEniCSx | 1e-5; 1e-9 | passes |

A test runs on each solver installed and skips the others (`tests/solvers.py` `over`),
so a solver missing from a machine means the row was not checked there. A solver left
out of a row's list refuses that feature (`Adapter.check`), and the refusal is the
answer the user gets.

## Steady conduction in the bar

The bar's root is held at 100 °C and its tip at 20 °C, the sides insulated. With a
constant conductivity the temperature is linear along the bar:

$$T(x) = 373.15 - 80\frac{x}{L}$$

`tests/integration/test_benchmarks.py` `test_steady_conduction_is_linear` (tetrahedra)
and `test_hexahedra_axial_stretch_and_conduction` (first-order hexahedra) assert every
node's temperature within 1e-6 K, on CalculiX, Elmer, Code_Aster and FEniCSx. A linear
profile lies in the elements' own space, so any error larger than round-off would be a
writing or reading fault, not a discretisation one.

## Transient conduction and time schemes

The bar starts at 20 °C and its root is held at 100 °C from t = 0; everything else is
insulated. Constant steel properties give the diffusivity
$\alpha = k / (\rho c_p)$, and the series solution (Carslaw and Jaeger) is

$$T(x,t) = T_1 + (T_0 - T_1)\sum_{n=0}^{\infty}\frac{4}{(2n+1)\pi}\sin(\lambda_n x)\,e^{-\lambda_n^2 \alpha t}$$

with $\lambda_n = (2n+1)\pi/(2L)$, summed to 200 terms. The mesh is 5 mm, the step
10 s, the end 600 s.

`test_transient_conduction_matches_the_series_solution` runs the default scheme
(backward Euler) on all four solvers and asserts the largest nodal error at 300 s and
600 s below 2 % of the 80 K step (1.6 K). `test_time_schemes` repeats it for every
scheme each adapter's capability matrix lists: backward Euler on CalculiX; backward
Euler, Crank-Nicolson and theta (0.6) on Code_Aster; those and BDF2 on Elmer and
FEniCSx. BDF2 must also come within 0.02 K.

Measured: the test's comment and the [time integration note](help:theory/time-integration)
put backward Euler's largest error at about 0.35 K and BDF2's below 0.02 K. CLAUDE.md
records the four solvers within 0.34 %. Theta schemes started from a sudden load
differ between Elmer and the others, since Elmer applies the boundary values to the
starting state first; the note explains why backward Euler or BDF2 are the schemes to
compare.

## Temperature-dependent conductivity

The root is held at 100 °C and the tip at 700 °C. Over that range the EN 1993-1-2
conductivity of structural steel falls from 50.7 to 30.7 W/(m K), so the profile is far
from linear. The Kirchhoff transform makes it exact for a table interpolated linearly,
as every solver interpolates it:

$$F(T) = \int_{T_0}^{T} k(T')\,dT'$$

$F$ is linear in $x$, so $T(x) = F^{-1}(F(T_1)\,x/L)$.

`tests/integration/test_nonlinear_conduction.py` `test_conductivity_table` asserts the
largest nodal error below 0.05 K on all four solvers, and first that a linear profile
would be more than 20 K off, so the table matters. CLAUDE.md records all four within
0.002 K.

`test_transient_tables_agree_across_solvers` heats the bar from 20 °C with the root at
700 °C, conductivity and specific heat both from the tables, 5 s steps to 120 s. There
is no closed form, so the solvers check each other at 10, 30 and 60 mm from the root:
CalculiX, Elmer and FEniCSx within 0.1 K of their mean, Code_Aster within 1 K. The
difference is in the formulation: the three discretise ρ c_p(T) dT/dt, Code_Aster steps
the enthalpy. The test's comment records Code_Aster 0.7 K warmer at 5 s steps and
within 0.15 K at 1 s steps, the closer to the converged answer. It needs two solvers.

## Heat generation

Both ends of the bar are held at T0 = 300 K and the sides insulated, so the temperature
is one-dimensional. A uniform source q = 2 MW/m³ gives

$$T = T_0 + \frac{q\,x(L - x)}{2k}$$

and a source from an imported field rising along the bar, q = q0 x / L, gives

$$T = T_0 + \frac{q_0\,x(L^2 - x^2)}{6kL}$$

whose peak, at $x = L/\sqrt{3}$, is $q_0 L^2 / (9\sqrt{3}k)$ above the ends.

`tests/integration/test_heat_generation.py` `test_uniform_heat_generation` and
`test_heat_generation_from_a_field` assert every node within 0.05 % of the rise (the
middle's, or the peak's), on CalculiX, Code_Aster, Elmer and FEniCSx. ADR 0047
records the uniform case equal to the closed form to five digits and the field's peak
within 0.003 %.

## Thermal bending through a transfer

The bar is held at 120 °C on its bottom face and 20 °C on its top, so the temperature
is linear through the thickness h. A thermal study solves it, the temperature is mapped
onto the structural study's mesh, and the clamped bar bends and stretches against a
20 °C reference:

$$w_{tip} = \frac{\alpha\,\Delta T\,L^2}{2h}$$

$$u_{tip} = \frac{\alpha\,\Delta T\,L}{2}$$

`tests/integration/test_transfers.py` `test_thermal_bending` asserts both within 2 %,
and the axial stress at mid-span below 1 % of E α ΔT (free thermal bending is
stress-free away from the clamp). It runs six chains, thermal solver to structural:
CalculiX to CalculiX, Elmer to CalculiX, CalculiX to FEniCSx, CalculiX to Code_Aster,
CalculiX to Elmer, and CalculiX to CalculiX with a 5 mm thermal mesh, so the field is
interpolated rather than copied. A rerun must reuse both stored studies. ADR 0009
records beam theory matched within 1.6 % and the same answer on every pair.

## Imported fields on the bar

`tests/integration/test_imported_fields.py`, on all four solvers:

- `test_an_imported_temperature_expands_the_bar`: a field T = T0 + g x as a body
  temperature, the root held, against the free expansion
  $u(L) = \alpha(gL^2/2 + (T_0 - T_{ref})L)$, within 1 %. ADR 0044: +0.3 % on all four.
- `test_an_imported_pressure_is_the_uniform_one_and_survives_a_move`: a uniform 10 MPa
  from a file on the tip, against $-pL/E$ within 1 %; a copy written turned 90° and
  moved 1 m, placed back by the field's placement, equal to 1e-6. ADR 0044: -0.18 %,
  the mesh's own error.
- `test_an_imported_film_coefficient_is_the_uniform_one`: an imported h equals the
  uniform h to 1e-9.

`tests/integration/test_imported_fields_parity.py` checks scalar face values on all
four solvers, the root held at T0 = 400 K, 2.5 mm mesh:

- **Pressure** (`test_an_imported_pressure_is_the_uniform_one`): within 1 % of
  $-pL/E$ and 1e-6 of the uniform feature.
- **A linear heat flux** q(y) = 2 q̄ y / w on the tip
  (`test_an_imported_linear_heat_flux_balances_and_spreads`). With the sides insulated
  the tip's mean is the energy balance, $T_0 + \bar{q}L/k$, asserted within 0.2 % of
  the rise. The spread across the tip follows the cosine series of the flux's varying
  part, asserted within 1 %:

$$T(L,y) - \bar{T}(L) = \sum_{n\ \mathrm{odd}}\frac{a_n w}{n\pi k}\tanh\left(\frac{n\pi L}{w}\right)\cos\left(\frac{n\pi y}{w}\right)$$

  with $a_n = -4q'w/(n\pi)^2$. ADR 0044: the mean 775.023 K and the spread 10.177 K,
  measured 10.175 to 10.176 K on all four.
- **A flux following an amplitude** (`test_an_imported_heat_flux_follows_its_amplitude`):
  the insulated bar heated through its tip by the linear flux times a pulse, backward
  Euler at 1 s. The mean temperature rise is $\bar{q}A\,t_{eff}/(\rho c V)$, with
  t_eff 10.5 s by 15 s and 20 s by 40 s, asserted within 0.1 % of the rise.
- **Films**: conduction in series with the film on the tip,

$$T_{tip} = T_\infty + (T_0 - T_\infty)\frac{R_h}{R_k + R_h}$$

  with $R_k = L/(kA)$ and $R_h = 1/(hA)$. `test_an_imported_uniform_film_is_the_uniform_one`:
  the uniform feature within 0.05 K of it and the imported one within 1e-6 K of the
  uniform; `test_imported_film_values_with_temperature_tables` the same with steel's
  tables, to 1e-5 K; `test_an_imported_linear_ambient_keeps_the_mean`: a linear T_inf
  keeps the 1D answer within 0.05 K. ADR 0044 records 351.61134 K with or without the
  tables.
- **A linear film coefficient** has no closed form here:
  `test_an_imported_linear_film_coefficient_matches_calculix` holds each solver within
  0.01 K of CalculiX's 351.68533 K (recorded in the test) and at least 0.05 K away from
  the uniform answer.

## Fields varying in time and with frequency

`tests/integration/test_imported_fields_time.py`:

- `test_each_static_step_takes_the_pressure_at_its_time` (CalculiX, Code_Aster): a tip
  pressure given at t = 0 (0 MPa) and t = 2 (20 MPa); the two static steps must equal
  uniform 10 and 20 MPa runs to 1e-6, and the first within 1 % of $-pL/E$.
- `test_a_transient_flux_follows_the_field_as_an_amplitude_would` (CalculiX): a tip
  flux given at 0, 5 and 10 s equals a uniform flux on an Amplitude through the same
  points to 1e-9 at every output. Other solvers refuse field histories
  (`Capabilities.field_histories`).

ADR 0044 records both to the tolerance asserted.

`tests/integration/test_imported_fields_frequency.py`
`test_a_pressure_varying_with_frequency_applies_per_frequency` (CalculiX, Code_Aster,
FEniCSx) loads the clamped bar's top face with a pressure given at 0, 200, 400 and
800 Hz and asks for 100, 150, 300 and 600 Hz. Each tip amplitude must equal a uniform
10 kPa run scaled by p(f) / 10 kPa to 1e-5, and the Euler-Bernoulli modal sum of the
first five modes within 1 %:

$$w(L) = \sum_n \frac{\phi_n(L)\int q\,\phi_n\,dx}{\rho A\int \phi_n^2\,dx\,(\omega_n^2 - \omega^2)}$$

ADR 0044 records within 0.6 %: -2.2219e-4 m at 100 Hz against -2.2307e-4 m, and
1.79572e-5 m at 600 Hz (CalculiX; Code_Aster and FEniCSx 1.79575e-5) against
1.79319e-5 m. `test_a_swept_range_reads_the_amplitudes_against_frequency` (CalculiX)
checks a swept range against the uniform run to 1e-5.

## Vector fields

`tests/integration/test_imported_vectors.py`, the bar held at its root, on all four
solvers:

- A body force density $f_z = -\rho g$ equals gravity's tip displacement to 1e-6;
  CalculiX's and Code_Aster's root reactions balance the weight to 1e-5
  (`test_a_body_force_density_is_gravity_without_the_density`).
- An inward traction equals the pressure to 1e-6, and a shear traction τ on the tip
  bends the bar as a tip load $F = \tau b h$, against Timoshenko's
  $FL^3/(3EI) + FL/(\kappa GA)$ with κ = 5/6 within 3 %
  (`test_a_traction_is_a_pressure_inward_and_bends_the_bar_along_z`). ADR 0044: -0.47 %.
- A traction field, and a copy turned 90° about z and moved, placed back, equal the
  uniform traction to 1e-7
  (`test_an_imported_traction_is_the_uniform_one_and_turns_with_its_points`).
- A body force field of -ρg equals gravity to 1e-6 and the cantilever under its own
  weight, $qL^4/(8EI) + qL^2/(2\kappa GA)$, within 3 %
  (`test_an_imported_body_force_is_gravity_and_the_cantilever_under_its_weight`).
  ADR 0044: tip -8.7695284e-06 m on all four, -0.62 %.
- A linear traction $t_x = \sigma(2z/h - 1)$ has no resultant: pure bending, tip
  $u_z = -\sigma L^2/(Eh)$ within 2 %, the stretch below 0.1 % of it
  (`test_an_imported_linear_traction_bends_the_bar`). ADR 0044: -1.89901e-04 m, -0.30 %.
- A linear displacement field is imposed at the tip's nodes to 1e-5 relative
  (`test_an_imported_linear_displacement_is_imposed_exactly`). ADR 0044: Elmer 5e-13 m
  and FEniCSx 7e-21 m off, of 3e-5 m.
- Conservative mapping: a coarse Lorentz-force cloud (J × B with each point's volume)
  keeps its total in the root reaction to 1e-5 relative on CalculiX and Code_Aster
  (`test_a_coarse_lorentz_cloud_mapped_conservatively_keeps_its_total`), and moves the
  tip on Code_Aster, Elmer and FEniCSx as on CalculiX to 1e-4
  (`test_a_coarse_lorentz_cloud_moves_the_bar_as_on_calculix`; Elmer and FEniCSx store
  no reactions). A coarse wall pressure with its faces' areas keeps its force to 1e-5
  (`test_a_coarse_wall_pressure_mapped_conservatively_keeps_its_force`, CalculiX and
  Code_Aster). ADR 0044: the reactions exact on Code_Aster and within 7e-7 on
  CalculiX; the other solvers' tips as on CalculiX within 7e-6 (ux) and 8e-8 (uz).

## Initial stress, shell thickness and graded materials

`tests/integration/test_imported_stage3.py`, on CalculiX and Code_Aster (Elmer and
FEniCSx refuse these by name):

- **Initial stress.** The free bar relaxes from σxx = 100 MPa, σxy = 20 MPa and
  σzx = -30 MPa: $u_x(L) = -\sigma_{xx}L/E$, $u_y(L) = -\sigma_{xy}L/G$,
  $u_z(L) = -\sigma_{zx}L/G$, within 1 %; a uniform field and a copy turned 30°, 40°,
  50° and moved, placed back, equal it to 1e-6
  (`test_a_free_bar_relaxes_from_an_initial_stress`). With large deflection the tip is
  the same within 1 %
  (`test_an_initial_stress_relaxes_the_same_with_large_deflection`). A
  self-equilibrated residual stress moves the tip less than 2 % of σ0 L / E
  (`test_a_self_equilibrated_residual_stress_barely_moves_the_bar`).
- **Shell thickness.** A cantilever strip (L 200 mm, b 20 mm, ν = 0) tapering from
  2 mm to 1 mm under a pressure q:

$$\delta = \int_0^L \frac{M(x)(L - x)}{EI(x)}dx$$

  with $M = qb(L-x)^2/2$ and $I = bt^3/12$, within 1.5 %; a uniform thickness from a
  field equals the plain section to 1e-9
  (`test_a_tapered_strip_takes_its_thickness_from_a_field`).
- **Graded materials.** E from 200 to 100 GPa along the bar under an end force F:
  $u(L) = FL\ln(E_0/E_1)/((E_0 - E_1)A)$, within 1 %
  (`test_a_graded_bar_takes_its_stiffness_from_a_field`). Conductivity from 50 to
  25 W/(m K) under a tip flux: the rise $qL\ln(k_0/k_1)/(k_0 - k_1)$ within 1 %
  (`test_a_graded_bar_takes_its_conductivity_from_a_field`).

ADR 0044 records the initial stress within 1 %, the taper within 1.5 % and the graded
bars within 1 %, the elements binned within 0.5 %.

## Imported fields on 2D models

`tests/integration/test_imported_fields_2d.py`, structural cases on CalculiX and
Code_Aster, heat cases on all four:

- **A radial body force** f = c r is a spinning disc's load with ρω² = c, on a
  plane-stress quarter ring (2 mm thick), a plane-strain ring and an axisymmetric
  cylinder with its ends held. The rim displacement within 0.5 % and the bore's hoop
  stress within 2 % of the free-bore, free-rim solution (Timoshenko and Goodier),

$$\sigma_\theta = mc\left(a^2 + b^2 + \frac{a^2b^2}{r^2} - sr^2\right)$$

  with m = (3 + ν)/8 and s = (1 + 3ν)/(3 + ν) in plane stress
  (`test_a_radial_body_force_field_spins_the_ring`). ADR 0044: rim within 1e-5, hoop
  within 0.12 %, on both solvers and all three models.
- **A traction on an axisymmetric bore**, uniform and from a field, against Lamé: rim
  within 0.5 %, hoop within 2 %
  (`test_a_traction_on_the_bore_is_lames_pressure`). ADR 0044: rim -0.010 % CalculiX,
  0.000 % Code_Aster.
- **A parabolic shear traction** on a plane-stress cantilever's end, against
  Timoshenko's tip deflection, within 1 %
  (`test_a_shear_traction_bends_a_plane_stress_cantilever`). ADR 0044: -0.27 %
  CalculiX, -0.14 % Code_Aster; the uniform shear -0.31 % and -0.18 %.
- **A linear heat flux** on a planar slab: the far edge's mean within 0.5 % of the
  rise of the energy balance and the spread within 1 % of the cosine series
  (`test_a_linear_heat_flux_field_on_a_slab_balances_and_spreads`). ADR 0044: the mean
  to 1e-5 on all four; the spread, 20.354 K, to 4 digits on Code_Aster, Elmer and
  FEniCSx and -0.5 % on CalculiX, which takes one value per edge.
- **Films and an inner flux**: a slab's far edge against the 1D answer within 1e-4 of
  the drop (`test_imported_films_on_a_slab_give_the_1d_answer`); an axisymmetric wall
  with an inner flux, $T(a) = T_b + qa\ln(b/a)/k$, or an outer film in series with
  conduction, within 1e-3 of the rise
  (`test_imported_flux_and_film_on_an_axisymmetric_wall`). ADR 0044: 7 digits on all
  four.

## Custom PDEs

`tests/integration/test_custom_pde.py`, FEniCSx only (the solver that runs custom
equations):

- `test_nonlinear_conduction_matches_kirchhoff`: $-\nabla\cdot((1 + u^2)\nabla u) = 0$
  with u = 1 at the root and 2 at the tip. $u + u^3/3$ is linear in x, which gives u
  exactly; the largest nodal error must be below 1e-5.
- `test_hand_written_elasticity_equals_built_in`: linear elasticity written in UFL
  equals the built-in solver's tip deflection to 1e-9.
- `test_hand_written_diffusion_equals_built_in_heat`: transient diffusion written in
  UFL equals the built-in transient heat solver over 10 steps to 1e-9 of 373.15 K.

The two equality checks hold the weak-form path to the built-in one, which the rows
above verify. No measured values are recorded beyond passing.

## See also

- [Time integration](help:theory/time-integration)
- [Imported fields](help:guides/imported-fields)
- [Analyses](help:guides/analyses)
- [Electromagnetics](help:verification/electromagnetics) for losses heating a wire.

## Sources

- H. S. Carslaw and J. C. Jaeger, *Conduction of Heat in Solids*, 2nd edition, Oxford
  University Press, 1959: the series solution of the transient bar.
- EN 1993-1-2, *Eurocode 3: Design of steel structures, Part 1-2: General rules,
  Structural fire design*, CEN, 2005: the steel tables of the nonlinear benchmarks.
- S. P. Timoshenko and J. N. Goodier, *Theory of Elasticity*, 3rd edition, McGraw-Hill,
  1970: the rotating disc, Lamé's thick cylinder and the shear-loaded cantilever.
- S. P. Timoshenko and J. M. Gere, *Mechanics of Materials*, Van Nostrand, 1972: beam
  deflections with shear (κ = 5/6 for a rectangle).
- R. D. Blevins, *Formulas for Natural Frequency and Mode Shape*, Van Nostrand
  Reinhold, 1979: the cantilever's modes used in the modal sum.
