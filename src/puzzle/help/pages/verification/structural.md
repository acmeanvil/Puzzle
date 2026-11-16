---
title: Structural statics
section: verification
order: 20
keys: verification:structural
summary: Static stress and deflection checked against closed forms and published benchmarks: the cantilever bar, NAFEMS LE1, local axes, body and remote loads, springs, orthotropic and plastic materials, 2D models, element choices, large deflection, shells, beams, symmetry and hexahedral meshing.
adrs: 0019, 0023, 0027, 0037, 0048, 0049, 0055, 0072, 0081, 0082
---

These benchmarks check a static solve from end to end: geometry, mesh, the input each
solver is given, the solve and the results read back. Most use the 200 x 10 x 10 mm
steel bar (`tests/conftest_geometry.py` `BAR`, generic structural steel, E = 210 GPa,
ν = 0.3) with constant properties. Conduction in the same bar is on
[Heat transfer and transfers](help:verification/heat); modes, buckling and time
response are on [Dynamics, buckling and explicit](help:verification/dynamics).

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Cantilever bar: tip force, pull, hexahedra | Timoshenko beam, F L / E A | CalculiX, Elmer, Code_Aster, FEniCSx | 2 % | Elmer = CalculiX to 4 digits |
| NAFEMS LE1 | 92.7 MPa at D | all four; Netgen mesh on CalculiX | 1 % | CalculiX 0.00 %, Code_Aster +0.01 %, FEniCSx −0.11 %, Elmer within 0.13 % |
| Displacements in local axes | uniform expansion, rigid turn | all four; partial on CalculiX | 0.5 %, 2 % stress | 6 digits; σ +0.35 % |
| Axes from a face; results in axes | F L / E A; Lamé | CalculiX, Code_Aster; CalculiX | 1 %; 3 %, 5 % | passes |
| Self weight, acceleration, spin | Timoshenko ratio; plane-stress disc | all four | 0.5 %, 1e-6, 1 % | −0.128 %, −0.007 % |
| Remote loads in torsion | Saint-Venant | all four; rigid on CalculiX, Code_Aster | 3 %, 2 % | +0.04 % to +0.07 %; 0.00 % |
| Elastic support | p h / E + p / kn | all four; tilted CalculiX against Code_Aster | 0.2 %; 1e-5 | 0.000 %; 4e-6 |
| Orthotropic elasticity and conduction | F L / A E_i, k_i | CalculiX, Code_Aster | 0.5 %, 1 % | exact; 1.3861 K of 1.3863 |
| Plasticity, small and large strain | bilinear hardening | CalculiX, Code_Aster | 2e-5, 1e-8; 0.5 % | CalculiX +0.19 %, Code_Aster −0.08 % |
| 2D: strip, Kirsch, Lamé, axisymmetric bar | closed forms | all four | 0.2 % to 2 % | Kirsch 3.030 to 3.046 σ |
| 2D totals and result tools | p A, p W, p H t, p a / t | CalculiX, Code_Aster | 0.2 %, 0.5 % | to 5 digits |
| 2D body and thermal loads | spinning disc, hanging strip, free expansion | all four | 0.5 %, 1 % | exact |
| Second-order bricks | Timoshenko | per brick | 1 % | 0.9951, 0.9956, 0.9957 |
| Element choices | Timoshenko, Euler-Bernoulli | per element | 1e-6 to 2 % | C3D8I 0.9934, p:2 0.995 |
| Large deflection | Bisshopp and Drucker's elastica | CalculiX, Code_Aster | 1 % to 4 % | 0.3004 L and 0.0560 L |
| Plates and the Scordelis-Lo roof | plate tables; 0.3024 | CalculiX, Code_Aster | 1.5 %, 2 % | −1.04 % to +0.74 % |
| Thick and quadrilateral shells | Kirchhoff, Mindlin | Code_Aster, CalculiX, PyNite | 0.5 % to 2 % | +0.04 %, +0.19 % |
| Beams and frames | Timoshenko; matrix analysis | CalculiX, Code_Aster | 1 % | −0.85 % to +0.48 % |
| Table sections and angles | Timoshenko, tables | CalculiX, Code_Aster | 1e-4 to 4 % | +0.00 % |
| Symmetry, cyclic, periodic | σL/E, Lamé, TL/GJ | per case | 1e-4 to 0.5 % | 1.3e-5, +0.077 % |
| Hexahedra by blocks and sweeps | F / A, Lamé, Castigliano | CalculiX, Code_Aster | 0.5 %, 1 % | 0.02 %, −0.005 %, +0.25 % |

A test runs once per installed solver and skips the rest (`tests/solvers.py` `over`), so
a solver missing from a machine means that row was not checked there. A solver left out
of a row refuses that feature before writing input; several tests assert the refusal
itself.

<!-- key: verification:structural-cantilever -->
## The cantilever bar

The bar is fixed at x = 0 (`Bar/Root`) and loaded at its tip (`Bar/Tip`), meshed in
second-order tetrahedra of 2.5 mm. A 100 N tip force across it deflects the tip by
Timoshenko's beam formula, the shear coefficient κ = 5/6 for a rectangle and G = E / 2(1 + ν):

$$\delta = \frac{P L^3}{3 E I} + \frac{P L}{\kappa G A}$$

A tension F/A on the tip face stretches it by F L / E A.

- `tests/integration/test_benchmarks.py` `test_cantilever_tip_force`: the tip's mean
  y displacement within 2 % (`rel=0.02`), and six stress components stored;
- `test_cantilever_tip_traction_as_pressure_on_a_side_face`: the axial stretch within 2 %;
- `test_hexahedra_axial_stretch_and_conduction`: the stretch on first-order hexahedra
  (the mesh checked to be hex8 only) within 2 %.

All three run on CalculiX, Elmer, Code_Aster and FEniCSx. The project notes record that
Elmer agrees with CalculiX to four digits on this set.

## NAFEMS LE1: the elliptic membrane

A quarter of an elliptic annulus (inner semi-axes 2.0 x 1.0 m, outer 3.25 x 2.75 m,
0.1 m thick), E = 210 GPa, ν = 0.3, under 10 MPa outward on the outer edge, on symmetry
planes x = 0 and y = 0. NAFEMS's target is σyy = 92.7 MPa at point D (2, 0). Puzzle
models it as a 3D solid of second-order tetrahedra with u_z = 0 on z = 0 and reads σyy at
D at the nodes on the line through the thickness (`benchmarks/nafems_le1`).

`tests/integration/test_nafems_le1.py` `test_le1_sigma_yy_at_d` asserts σyy at D within
1 % (`rel=0.01`) on the project's default mesh, on CalculiX, Elmer, Code_Aster and
FEniCSx. `test_le1_meshed_by_netgen` meshes it with Netgen instead and asserts the same
on CalculiX.

Measured (CalculiX 2.23, 2026-10-17, `benchmarks/nafems_le1/README.md`):

| Mesh (global / inner edge) | Nodes | σyy at D | Error |
| --- | --- | --- | --- |
| 200 / 50 mm | 6,843 | 92.46 MPa | −0.26 % |
| 100 / 20 mm (default) | 39,604 | 92.70 MPa | 0.00 % |
| 100 / 10 mm | 107,102 | 92.71 MPa | +0.01 % |
| 50 / 10 mm | 226,807 | 92.72 MPa | +0.02 % |

The project notes give Code_Aster +0.01 %, FEniCSx −0.11 % and Elmer within 0.13 %; ADR
0019 gives 92.74 MPa (+0.04 %) on the Netgen mesh.

## Local and derived coordinate systems

**A disc's rim moved in cylindrical axes.** A steel disc, R = 50 mm, 2 mm thick, its
rim given u_r = δ = 10 µm, u_θ = 20 µm and u_z = 0 in a cylindrical system. In plane
stress that is a uniform expansion plus a rigid turn, u = (δ e_r + δ' e_θ) r / R, with

$$\sigma_r = \sigma_\theta = \frac{E \delta}{R (1 - \nu)}$$

`tests/integration/test_local_axes.py` `test_cylindrical_rim_displacement` reads inside
r < R/2 (the rim's z restraint adds a boundary layer about one thickness wide): u_r and
u_θ within 0.5 %, σ within 2 %, on all four solvers. ADR 0037 records the displacements
matching to six digits and σ_r +0.35 % on all four.

**Some components only.** `test_partial_displacement_in_rotated_axes` moves the example
cantilever's tip by δ along an x axis turned 30° about z, the other two components
free; the tip's displacement along that axis must be δ to `rtol=1e-6`. CalculiX only:
the other solvers refuse partial displacements in local axes.

**Axes taken from a face.** `tests/integration/test_axes_derived_solve.py`
`test_a_force_in_axes_from_the_tip_face`: 1 kN along the z of a system made from the
bar's tip face (its normal) stretches the bar by F L / E A within 1 %, on CalculiX and
Code_Aster.

**Results read in a system.** `tests/integration/test_results_in_axes.py`
`test_lame_cylinder_in_cylindrical_axes`: a steel tube, inner radius 20 mm, outer
40 mm, 20 mm long, under 100 MPa inside, solved in global axes on CalculiX and read in
a cylindrical system. Lamé's hoop stress at the bore and outside,

$$\sigma_\theta(r_i) = p \frac{r_o^2 + r_i^2}{r_o^2 - r_i^2}$$

$$\sigma_\theta(r_o) = \frac{2 p r_i^2}{r_o^2 - r_i^2}$$

166.7 and 66.7 MPa, are asserted within 3 %, the radial stress −p within 5 % and 0
outside within 3 % of p. See [Stress results](help:theory/stress-results).

## Body loads

**Self weight.** The example cantilever under its own weight at 1000 g (heavy, so the
deflection is well above round-off), against the same mesh under a tip force. Mesh
error largely cancels in the ratio, with w = ρ g A and Cowper's κ = 10(1 + ν)/(12 + 11ν):

$$\frac{\delta_w}{\delta_P} = \frac{w L^4 / 8EI + w L^2 / 2 \kappa G A}{P L^3 / 3EI + P L / \kappa G A}$$

`tests/integration/test_body_loads.py` `test_self_weight_cantilever` asserts the ratio
within 0.5 % (`rel=5e-3`), and that an acceleration up equals gravity down to 1e-6.
ADR 0037 records −0.128 %, with all four solvers agreeing to six digits.

**A spinning disc.** A steel quarter disc, R = 100 mm, 4 mm thick, on symmetry planes,
at 1000 rad/s. Plane-stress theory gives the rim's radial displacement

$$u(R) = \frac{(1 - \nu) \rho \omega^2 R^3}{4 E}$$

`test_spinning_disc` asserts it within 1 %; ADR 0037 records −0.007 %.

**In a coordinate system.** `tests/integration/test_body_loads_in_axes.py`: gravity
given in axes turned 90° about z, and a rotation about a cylindrical system's axis (the
feature's own axis set wrong, so only the system's can be used), each against its
plain twin on the same mesh to 1e-6.

**An amplitude on a heat flux.** `test_amplitude_heat_flux_energy_balance`: a tip flux
switched on and off by a trapezoid amplitude (area 20 s), every other face insulated;
backward Euler at 1 s sums the amplitude to that area, so the mean temperature rise is
exactly q A (20 s) / (ρ c V). Asserted to 0.1 % of the rise; ADR 0037 records
298.943 K on all four.

All of these run on CalculiX, Code_Aster, Elmer and FEniCSx.

## Remote loads in torsion

A steel shaft, R = 10 mm, L = 100 mm, fixed at z = 0, loaded at z = L
(`tests/integration/test_remote_loads.py`). Saint-Venant's torsion gives the surface
shear at mid-length and the end's twist, J = π R⁴/2:

$$\tau = \frac{2 T}{\pi R^3}$$

$$\theta = \frac{T L}{G J}$$

- `test_deformable_remote_moment`: T = 100 N·m as a distributing (RBE3-like) moment; the
  surface shear within 3 %;
- `test_remote_rotation`: the end face turned 0.01 rad; τ = G θ R / L within 3 %, the
  twist itself to 1e-6;
- `test_rigid_remote_moment`: the moment through a rigid end; the twist within 2 %,
  CalculiX and Code_Aster only (Elmer and FEniCSx refuse a rigid remote load).

The tolerance allows for nodal stresses on the surface of quadratic tetrahedra, which
read about 1 % low. Measured (ADR 0037):

| Check | CalculiX | Code_Aster | Elmer | FEniCSx |
| --- | --- | --- | --- | --- |
| Remote moment: shear | +0.04 % | +0.04 % | +0.07 % | +0.06 % |
| Prescribed rotation: shear | +0.04 % | +0.04 % | +0.07 % | +0.06 % |
| Rigid moment: twist | 0.00 % | 0.00 % | refused | refused |

## Elastic support

A 20 x 20 x 10 mm steel block on a foundation of stiffness kn = 1e13 N/m³, held on two
side faces by symmetry and pressed by p = 10 MPa on top. The stress is uniaxial and
uniform, so the top moves exactly p h / E + p / kn.
`tests/integration/test_elastic_support.py` `test_block_on_a_foundation` asserts it
within 0.2 % on all four solvers; ADR 0037 records 0.000 % on CalculiX, Elmer and
FEniCSx and an exact answer on Code_Aster. Springs are lumped as consistent loads are
(∫N dA), which is why a uniform state comes out exact.

`test_tilted_support_agrees` turns the block 30° about x and adds a tangential
stiffness. With no closed form, CalculiX and Code_Aster must give the same displacements
on the same mesh to 1e-5 of the largest; ADR 0037 records 4e-6 (CalculiX's six printed
digits).

## Orthotropic materials

`tests/integration/test_orthotropic.py`, on CalculiX and Code_Aster (Elmer and FEniCSx
refuse orthotropic and anisotropic models). E1 = 200, E2 = 50 and E3 = 100 GPa differ by
factors of two and more, so a wrongly mapped axis can't pass. Strain is read as the
slope of u_x in the bar's middle half.

- `test_a_turned_orthotropic_bar_stretches_by_the_axis_along_it`: material axes turned
  90° about z, the strain F / (A E2) (unturned, F / (A E1)) within 0.5 %;
- `test_cylindrical_material_axes_make_3_the_axial_one`: a round rod in cylindrical
  axes about its own axis, transversely isotropic, F / (A E3) within 0.5 % (with
  E1 ≠ E2 the rod would build radial and hoop stresses, Lekhnitskii, 1.8 % stiffer);
- `test_conduction_along_a_turned_axis`: T(L) = T0 − q L / k2 within 0.1 % of the drop;
- `test_radial_conduction_in_cylindrical_axes`: a tube heated through its bore, its
  rise within 1 %:

$$\Delta T = \frac{q r_i \ln(r_o / r_i)}{k_1}$$

ADR 0049 records E2 exact, E3 to 1e-5, and the tube's bore 1.3861 K against 1.3863 K on
CalculiX.

## Plasticity

A 10 x 10 x 100 mm bar on symmetry supports, its end moved 1 mm (1 % strain), E = 200 GPa,
σy = 250 MPa, linear hardening H = 500 MPa (`tests/integration/plastic_bar.py`). The
stress is uniform, so every node has the bilinear closed form:

$$\varepsilon_p = \frac{\varepsilon - \sigma_y / E}{1 + H / E}$$

$$\sigma = \sigma_y + H \varepsilon_p$$

εp = 8.7282e-3 and σ = 254.36 MPa; plastic flow keeps volume, so εp_yy = εp_zz = −εp/2.
`tests/integration/test_plasticity.py` `test_a_bar_yields_as_bilinear_hardening_says`
asserts, at every node of the last of ten increments, the axial stress, the equivalent
plastic strain and the three plastic strain components to 2e-5 on CalculiX (its `.frd`
keeps five digits) and 1e-8 on Code_Aster, shear plastic strain below 1e-9, and the first
increment (200 MPa) elastic.

`test_a_bar_drawn_to_large_strain` draws the bar to 1.3 of its length with geometric
nonlinearity (CalculiX NLGEOM; Code_Aster GDEF_LOG on its mixed u-p element, since plain
quadratic tetrahedra stalled at 1.21). In logarithmic strain ln 1.3 = σ/E + εp with σ the
true stress, and the force is σ times the current area. The stress within 0.5 %, εp
within 1 % and the end force within 0.5 %; ADR 0082 records CalculiX +0.19 % and
Code_Aster −0.08 % against 380.2 MPa.

## 2D models

`tests/integration/test_planar_benchmarks.py`, generic steel in the XY plane, on
CalculiX, Code_Aster, FEniCSx and Elmer. With A = p a² / (b² − a²) and
B = p a² b² / (b² − a²), Lamé's thick cylinder gives

$$\sigma_\theta(r) = A + \frac{B}{r^2}$$

$$u(r) = \frac{1 + \nu}{E} \left( (1 - 2\nu) A r + \frac{B}{r} \right)$$

- `test_plane_stress_strip_in_tension`: a 40 x 20 mm strip 2 mm thick pulled by 4 kN;
  σx = F / H t, the end's stretch and the side's contraction, each within 0.2 %;
- `test_plane_stress_kirsch_hole`: a quarter of a 200 mm plate with a 10 mm hole under
  10 MPa; σx at the hole's top 3.003 σ (Kirsch's 3, with Peterson's finite-width
  correction +0.1 % at d/W = 0.05), within 2 %;
- `test_plane_strain_lame_ring`: a quarter ring a = 10, b = 20 mm under 100 MPa inside;
  the outer radial displacement within 0.5 %, the bore's hoop stress within 2 %;
- `test_axisymmetric_lame_cylinder`: the same ring as a cylinder, its ends held
  axially; the same checks (the hoop stress is the zz component);
- `test_axisymmetric_bar_pulled_along_its_axis`: a solid bar r = 10 mm pulled by 10 kN,
  stretch F L / E π r² and stress F / π r², each within 0.5 %: an axisymmetric edge
  force is the whole revolution's;
- `test_planar_conduction_with_a_flux` and `test_axisymmetric_conduction_through_a_wall`:
  linear and logarithmic conduction to 0.1 % of the drop.

Measured (ADR 0048):

| Case | CalculiX | Code_Aster | FEniCSx | Elmer |
| --- | --- | --- | --- | --- |
| Strip in tension | within 0.2 % | within 0.2 % | within 0.2 % | within 0.2 % |
| Kirsch, σx at the hole (3.003 σ) | 3.046 σ | 3.033 σ | 3.030 σ | 3.030 σ |
| Lamé plane strain, bore hoop (166.67 MPa) | 166.57 | 166.57 | 166.05 | 166.05 |
| Lamé axisymmetric, bore hoop (166.67 MPa) | 166.33 | 166.43 | 166.92 | 166.91 |
| Axisymmetric bar | exact | exact | exact | exact |

**Drawn shapes.** `tests/integration/test_planar_shapes.py` repeats the Lamé checks on
geometry drawn with the 2D shapes (an annulus cut to a quadrant; a wall offset from a
line), on CalculiX and FEniCSx, to the same tolerances. ADR 0048 records the bore hoop
166.57 and 166.05 MPa (annulus) and 166.26 and 166.95 MPa (wall).

**Totals.** `tests/integration/test_planar_totals.py`, CalculiX and Code_Aster: a total
in 2D is the whole part's. The axisymmetric tube's foot reaction p π (b² − a²) for the
full 360°, a plane-strain strip's p W per metre and a plane-stress strip's p H t, each
within 0.2 % and equal between the reaction tool and `results.query.total`;
`test_result_tools_on_an_axisymmetric_cylinder` checks the linearized hoop membrane
p a / (b − a) = 100 MPa and the axial force 2 ν A π (b² − a²) across a cut and at the
foot, within 0.5 %. Measured (ADR 0048):

| Case | Exact | CalculiX | Code_Aster |
| --- | --- | --- | --- |
| Axisymmetric foot reaction (360°) | −9424.78 N | −9424.79 | −9424.78 |
| Plane strain, per metre | 400000 N/m | 399999 | 400000 |
| Plane stress, t = 2 mm | −400 N | −400 | −400 |
| Linearized hoop membrane | 100 MPa | 99.984 | 100.007 |
| Axial force across a cut | 18849.6 N | 18849.5 | 18851.7 |

**Body and thermal loads in 2D** (`tests/integration/test_planar_dynamics.py`, the
static cases, all four solvers):

- `test_rotating_thin_disc_axisymmetric`: a disc b = 100 mm at 10 000 rpm, σr at the
  centre (3 + ν)/8 ρ ω² b² and σθ at the rim (1 − ν)/4 ρ ω² b², within 1 %;
- `test_hanging_strip_under_gravity`: a strip hanging under 1000 g, its bottom
  ρ g L² / 2E, within 0.5 %;
- `test_heated_strip_held_lengthwise` (plane stress and strain): 100 K from a thermal
  study by a transfer, σx = −E α ΔT (divided by 1 − ν in plane strain) and the top's
  lift, within 0.5 %;
- `test_heated_rod_expands_freely_axisymmetric`: α ΔT r out and α ΔT L along within
  0.5 %, stress-free.

ADR 0048 records these exact. Elmer's plane strain omits the out-of-plane thermal
strain, so Puzzle writes α(1 + ν) there and corrects σz on reading.

## Second-order bricks and element choices

`tests/integration/test_quadratic_bricks.py`: the cantilever bar on structured 5 mm
bricks, two through the depth, against Timoshenko's δ within 1 %:

- `test_twenty_node_bricks`: CalculiX C3D20, Code_Aster HEXA20, Elmer 820;
- `test_twenty_seven_node_bricks`: Code_Aster HEXA27, Elmer 827, FEniCSx Q2;
- `test_reduced_quadratic_bricks`: CalculiX C3D20R and Code_Aster 3D_SI;
- `test_twenty_and_twenty_seven_nodes_agree`: Code_Aster and Elmer, each with both
  bricks on one mesh, within 0.5 % of each other;
- CalculiX refuses 27-node bricks and FEniCSx 20-node ones (both asserted).

ADR 0082 records 0.9951 of Timoshenko's on all three 20-node bricks, 0.9956 on all three
27-node bricks and 0.9957 reduced; measured again 2026-11-15, and 20 against 27 nodes
0.99959 on both solvers.

`tests/integration/test_element_choice.py` (one formulation per body, ADR 0082):

| Test | Solver, element | Asserted | Measured (ADR 0082) |
| --- | --- | --- | --- |
| `test_incompatible_modes_unlock_linear_bricks` | CalculiX C3D8I | within 2 %; plain C3D8 below 0.92 | 0.9934; C3D8 0.896 |
| `test_reduced_linear_bricks_converge_from_above` | CalculiX C3D8R, 4 deep | between 1.0 and 1.025 | 1.016 |
| `test_p_elements_unlock_linear_tetrahedra` | Elmer p:2 on tet4 | within 2 %; plain below 0.8 | 0.995; plain 0.503 |
| `test_mixed_elements_for_nearly_incompressible_bending` | Code_Aster 3D_INCO_UPG, FEniCSx Taylor-Hood, ν 0.4999 | within 2 % | 0.988 both |
| `test_euler_bernoulli_beams` | Code_Aster POU_D_E, W12X26 | P L³ / 3EI to 1e-6 | to every digit |
| `test_reduced_integration_beams` | CalculiX B32R | within 1.5 %, no worse than B32 | −1.04 % (B32 −1.05 %) |
| `test_lumped_capacity_has_no_undershoot` | Code_Aster 3D_DIAG | no node below 20 °C | none |

C3D8R converges from above (1.035, 1.016, 1.003 at 2, 4 and 8 bricks deep) where C3D8
locks from below (0.896, 0.966, 0.988). B31R and S4R are refused: their one-point bricks
hourglassed, 6.3 and 3.6 times too soft.

## Large deflection

`tests/integration/test_element_choice.py`, CalculiX and Code_Aster:

- `test_follower_pressure_under_large_deflection`: the bar pressed on its top face to
  about L/7; the tip must fall short of the small-deflection w L⁴ / 8EI and draw in,
  and the two solvers must agree within 2 % down and 5 % in. ADR 0082 records
  −27.2247 and −27.2248 mm down, −2.1355 mm in: five digits;
- `test_the_elastica`: a 200 x 10 x 2 mm strip under a tip load of fixed direction,
  P L² / EI = 1. Bisshopp and Drucker's elastica puts the tip 0.3017 L down and
  0.0566 L in; asserted within 2 % and 4 %. ADR 0082 records 0.3004 L and 0.0560 L on
  both;
- `test_the_elastica_on_beams` (static): a 1 m frame of line elements, within 1 % and
  3 %. ADR 0082 records Code_Aster (POU_D_T_GD) 0.3017 L and 0.0564 L, CalculiX
  0.2996 L and 0.0558 L. On Code_Aster the bending moment at a quarter, half and three
  quarters of the length is P times the reach left to the deformed tip, within 1 %;
- `tests/integration/test_thick_shells.py` `test_the_elastica_on_shells` (static):
  the strip as shells, a force along its tip edge, within 1.5 % and 4 %.

The same loads applied at once in damped transient steps are on
[Dynamics, buckling and explicit](help:verification/dynamics).

## Plates and shells

`tests/integration/test_shells.py`, CalculiX S6 (second order) and Code_Aster DKT:

- `test_square_plate_under_pressure`: a 1 m square plate, 10 mm thick, under 1 kPa; the
  centre deflects α q a⁴ / D with α = 0.0040624 simply supported and 0.0012653 clamped
  (Timoshenko and Woinowsky-Krieger, tables 8 and 35), within 1.5 %;
- `test_scordelis_lo_roof`: a cylindrical panel R = 25, L = 50, 80° wide, t = 0.25,
  E = 4.32e8, ν = 0, under its own weight of 90 per unit area on rigid diaphragms; the
  middle of a free edge drops 0.3024 (MacNeal and Harder), within 2 %;
- Elmer and FEniCSx refuse sheet bodies, and CalculiX refuses three-node shells (S3
  expands into one layer of wedges, which lock), all asserted.

$$D = \frac{E t^3}{12 (1 - \nu^2)}$$

Measured (ADR 0027):

| Check | CalculiX S6 | Code_Aster DKT |
| --- | --- | --- |
| Simply supported plate | +0.74 % (25 mm) | −0.12 % (25 mm) |
| Clamped plate | −0.81 % (25 mm; −0.3 % at 12.5 mm) | −0.02 % |
| Scordelis-Lo free edge | −0.99 % (2.5 m) | −1.04 % (0.625 m) |

DKT converges from below on the roof: −7 % at 2.5 m, hence its finer mesh.

**Thick shells.** `tests/integration/test_thick_shells.py`: a strip 300 mm long, 100 mm
wide and 100 mm thick (L/h = 3), ν = 0 so it is exactly a Timoshenko beam in Mindlin's
theory, clamped, under uniform pressure. Kirchhoff gives w L⁴ / 8EI and Mindlin adds
w L² / 2κGA; the shear part is 8.9 %, so each element is told apart by its own theory.

- `test_dst_takes_the_shear_deflection_dkt_leaves_out` (Code_Aster): DKT against
  Kirchhoff and DST against Mindlin, within 1 %. ADR 0082 records 60.774 µm against
  60.750 (+0.04 %) and 66.275 µm against 66.150 (+0.19 %);
- `test_quadrilateral_shells`: Code_Aster DKQ and DSQ, COQUE_3D on 9-node quadrilaterals
  and Q4G, within 0.5 % (recorded +0.03 % and Mindlin to eight digits); PyNite's MITC4
  plates within 0.5 % (recorded 0.03 %); CalculiX S4 and S8R within 2 % (recorded
  −1.4 % and −1.3 %, as its S6: they are expanded into bricks).

## Beams and frames

`tests/integration/test_beams.py`, a steel 20 x 40 mm rectangle, on CalculiX B31 and
B32 (expanded into solids) and Code_Aster POU_D_T:

- `test_cantilever_beam`: 1 m, 100 N across the stiff direction; Timoshenko's δ within
  1 %, and on Code_Aster the root's shear P and moment P L within 0.5 %;
- `test_portal_frame`: two 1 m columns and a 1 m beam, feet clamped, pushed sideways
  by 200 N; the sway against a three-member direct stiffness analysis
  (Euler-Bernoulli, exact for these members) within 1 %;
- Elmer and FEniCSx refuse beams (asserted).

Measured (ADR 0027):

| Check | CalculiX B31 | CalculiX B32 | Code_Aster POU_D_T |
| --- | --- | --- | --- |
| Cantilever tip | −0.09 % | −0.49 % | 0.00 % |
| Root shear and moment | | | exact |
| Portal sway | −0.33 % | −0.85 % | +0.48 % (shear flexibility) |

**Table sections.** `tests/integration/test_beam_sections.py`, CalculiX and Code_Aster:

- `test_the_tables_hold_the_published_values` (no solver): W12X26's A, I1, I2 and J
  against the AISC Manual's Table 1-1 to 0.1 %, IPE 300 against EN 10365 to 0.2 %, its
  Avz exactly, HSS6X4X1/4's I1 to 0.2 %;
- `test_cantilever`: W12X26, IPE 300 and HSS6X4X1/4 cantilevers, strong and weak axis,
  P L³ / 3EI + P L / G A_s within 1 %, except CalculiX's HSS within 4 % against the
  square-cornered box it expands into (that box takes next to no shear strain);
- `test_calculix_open_section_springs_are_exact`: CalculiX writes open sections as
  exact Timoshenko springs; the tip to 1e-4 on both mesh orders, the root's Vz = −P and
  My = P L to 1e-5, the reaction P;
- `test_calculix_open_section_under_gravity`: a 3 m W12X26 under its own weight,
  w L⁴ / 8EI + w L² / 2 G A_s to 1e-4;
- `test_l_frame_twists_by_j`: two 1 m W12X26 members at right angles, P at the free end;
  the tip falls almost all by the first member's twist through J, within 1 %, oriented
  three ways (a vector, a coordinate system's axis turned 90°, toward a point);
- `test_space_frame_shoot_out`: three IPE 300 members, a force along each axis;
  CalculiX's springs against Code_Aster's POU_D_T, displacements and every section force
  at every node to 1e-4 of the largest.

ADR 0055 records +0.00 % of Timoshenko for the open sections on both solvers (W12X26
+0.0001 % on CalculiX's springs), Code_Aster within 0.01 % on all three shapes, the
self weight −0.0002 %, the L-frame +0.00 % all three ways, and the space frame within
1.7e-6 (displacements) and 1e-9 (section forces).

**Single angles.** `tests/integration/test_beam_angles.py`
`test_an_angle_bends_about_its_principal_axes`: L4X4X1/2, L5X3-1/2X1/4 and
L 200 x 100 x 14 cantilevers, mirrored or not, a tip load along one leg. Bending about
the principal axes u and v (u at α from the first axis),

$$\delta = (F \cdot u) u \left( \frac{L^3}{3 E I_v} + \frac{L}{G A_u} \right) + (F \cdot v) v \left( \frac{L^3}{3 E I_u} + \frac{L}{G A_v} \right)$$

so the tip also moves along the other leg, with the sign of α. Asserted: that sign, and
both tip components within 1 %, on Code_Aster and CalculiX. ADR 0055 records five digits.
See [Beams, shells and frames](help:guides/beams-shells-frames).

## Symmetry, cyclic symmetry and periodicity

`tests/integration/test_symmetry_solved.py` (ADR 0081):

- `test_symmetry_planes_hold_a_bar_in_tension`: an eighth of the bar held only by
  symmetry on three planes, pulled by 10 MPa; the end σL/E and the side νσb/E to 1e-4,
  nothing through the planes. All four solvers; ADR 0081 records 9.5238095e-6 m and
  −1.4285714e-7 m, the closed form's digits;
- `test_symmetry_planes_across_no_axis`: the same bar turned 30° about z, to 1e-4 on
  CalculiX and Code_Aster; Elmer refuses two oblique planes meeting at an edge (it
  averages their normals there), asserted;
- `test_a_ring_sector_on_symmetry_planes`: a 30° sector of a ring (a = 20, b = 40 mm,
  10 mm high, 50 MPa inside) on planes at 0° and 30°; Lamé's plane-stress outer
  displacement within 0.2 %, the same all round to 0.2 %, on CalculiX, Code_Aster and
  Elmer. Recorded within 1.3e-5;
- `test_antisymmetry_halves_a_shaft_in_torsion`: half a round shaft (r = 10 mm,
  L = 100 mm) on an antisymmetry plane, half the torque; the twist T L / G J within
  0.5 %, all four solvers. Recorded +0.077 % on all four;
- `test_a_cyclic_sector_of_a_ring` and `test_a_cyclic_sector_with_kinked_cuts`: the
  ring as one sector of twelve, its cuts plane or bent at r = 30 mm, joined cyclically;
  Lamé within 0.2 %, each target node its source turned to 1e-5, on CalculiX and
  Code_Aster. Recorded 1.3e-5 and 1.2e-5;
- `test_cyclic_heat_on_kinked_cuts` (CalculiX): 100 °C at the bore, 20 °C at the rim;
  the logarithmic profile on the cut faces within 0.05 K. Recorded 0.007 K;
- `test_a_periodic_slice_of_a_slab`: the bar as a slice of an endless slab, its ends
  linked, pressed by 10 MPa on top; u_z = −p h (1 − ν²) / E and the free side
  ν (1 + ν) p b / E to 1e-4, on CalculiX and Code_Aster. Recorded exact.

$$u(b) = \frac{(1 - \nu) A b + (1 + \nu) B / b}{E}$$

FEniCSx refuses symmetry planes across no axis. See [Symmetry](help:theory/symmetry).

## Hexahedral meshing by blocks and sweeps

Twenty-node bricks on bodies Gmsh can't hex-mesh directly (ADR 0072 section 5). Each test
also asserts the element kinds.

- `tests/integration/test_hex_revolution.py`: a stepped shaft (r 6 and 10 mm) pulled by
  20 kN, σzz summed over a section cut through each segment equal to F / A within 0.5 %
  (CalculiX, Code_Aster); Lamé's thick tube, ends held axially, the bore's
  u = (1 + ν) p a² [(1 − 2ν) a + b²/a] / E (b² − a²) within 0.5 % (CalculiX); a shaft
  tapered by a cone and one with a filleted shoulder, revolved from the profile, F / A
  in each segment within 0.5 %. ADR 0072 records F / A to 0.02 % (CalculiX) and exact
  (Code_Aster), the tube −0.005 %, and the tapered and filleted shafts within 0.02 %;
- `tests/integration/test_hex_bends_and_cuts.py`: a quarter bend of tube (R = 200 mm,
  r 7 to 10 mm) pushed out of its plane, against Castigliano's curved cantilever with
  Cowper's shear coefficient for a hollow circle, within 0.5 %; a square bar stepped on
  all four sides, F / A in each segment within 0.5 %. CalculiX and Code_Aster. ADR 0072
  records +0.25 % on both for the bend (+1.08 % at R = 10 r: the formula leaves out the
  section's curvature, of order (r/R)²) and the stepped bar within 0.05 %;
- `tests/integration/test_hex_tet_mixed.py`: a hex20 block bonded to a tet10 block with
  a blind hole, pulled; the reaction P to 1e-4 and the end's stretch within 1 % of the
  same bar all in tetrahedra; and a holed plate swept into hexahedra against
  tetrahedra, likewise. CalculiX and Code_Aster. ADR 0072 records −0.11 % and +0.18 %.

$$\delta = P R^3 \left( \frac{\pi / 4}{E I} + \frac{3 \pi / 4 - 2}{G J} \right) + \frac{P R \pi / 2}{\kappa G A}$$

## Consistency checks

Two files check that the same model solved two ways gives the same answer, rather than
a closed form:

- `tests/integration/test_multistep.py`: a static then a modal step in one study equal
  the two one-step studies to 1e-9 (`test_static_then_modal`); a force in step 2 only
  and a pressure in step 1 only each match a one-step study to 1e-6
  (`test_load_in_one_step_only`). All four solvers;
- `tests/integration/test_solver_settings_runs.py`: the cantilever's tip with each
  linear solver equal to the default's to 1e-8 (direct) and 1e-5 (iterative; CalculiX's
  iterative Cholesky 1e-4, since its stop test doesn't bound the error), all four
  solvers; load stepping, cut-backs and line search on a large-deflection cantilever
  leave the tip to 1e-5 (CalculiX, Code_Aster); and on Code_Saturne and OpenFOAM the
  laminar channel's peak velocity stays 1.5 times the mean within 1 %. ADR 0023 records
  the CFD peaks 1.4976 and 1.4969 and the large-deflection tip to eight digits.

## Sources

- NAFEMS, *The Standard NAFEMS Benchmarks*, TNSB Rev. 3, 1990: test LE1.
- S. P. Timoshenko, J. N. Goodier, *Theory of Elasticity*, 3rd edition, McGraw-Hill
  1970: Lamé's cylinder, the rotating disc, Saint-Venant torsion, the hole in a plate.
- S. P. Timoshenko, S. Woinowsky-Krieger, *Theory of Plates and Shells*, 2nd edition,
  McGraw-Hill 1959: square plates, tables 8 and 35.
- W. D. Pilkey, D. F. Pilkey, *Peterson's Stress Concentration Factors*, 3rd edition,
  Wiley 2008: the finite-width correction for a hole.
- G. R. Cowper, "The shear coefficient in Timoshenko's beam theory", *Journal of
  Applied Mechanics* 33 (1966) 335-340.
- R. H. MacNeal, R. L. Harder, "A proposed standard set of problems to test finite
  element accuracy", *Finite Elements in Analysis and Design* 1 (1985) 3-20: the
  Scordelis-Lo roof.
- K. E. Bisshopp, D. C. Drucker, "Large deflection of cantilever beams", *Quarterly of
  Applied Mathematics* 3 (1945) 272-275: the elastica.
- S. G. Lekhnitskii, *Theory of Elasticity of an Anisotropic Body*, Holden-Day 1963:
  cylindrically orthotropic rods.
- American Institute of Steel Construction, *Steel Construction Manual*, 16th edition,
  2023, Table 1-1; EN 10365:2017, hot rolled steel channels, I and H sections.
