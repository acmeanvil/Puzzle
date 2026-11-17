---
title: Contact, bolts, threads and welds
section: verification
order: 60
keys: verification:contact-and-joints
summary: Contacts, compression-only supports, weak springs, bolt pretension, beam and modelled bolts, Belleville springs, threads and welds against closed forms, statics and the VDI 2230, AISC and ADM worked examples.
adrs: 0027, 0037, 0039, 0050, 0056, 0076, 0077, 0078, 0080
---

Joints are where a model is most likely to give a confident wrong answer, so each kind
of connection is held to an answer that does not depend on the solver: a closed form
(Hertz, Lamé, springs in series), exact statics (a bolt carries its preload, a weld its
load), or a standard's worked example. Contacts run on CalculiX and Code_Aster; Elmer
bonds only through shared nodes and FEniCSx has no mechanical contact, so neither is
checked here beyond what they refuse.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Split bar, uniform stress across a tie | F/A, FL/(EA) | CalculiX, Code_Aster | 0.1 % of u; σ 0.2 % away | passes |
| Split cantilever across a tie | Timoshenko beam | CalculiX, Code_Aster | 2 %; 0.5 % to conformal | passes |
| Heat across a tie | linear profile | CalculiX | 0.05 K | passes |
| Frictionless and no-separation stack | springs in series, ν p L/E | CalculiX, Code_Aster | 0.2 %; 1 % | CalculiX exact, Code_Aster 0.3 % |
| Sliding block | F_t = μ F_n | CalculiX, Code_Aster | 1 % (2 % per node) | 0.300 on both |
| No separation pulled | springs in series | CalculiX, Code_Aster | 0.5 % | 517.97, 518.56 N (518.79) |
| Rough contact | opens; sticks as bonded | CalculiX, Code_Aster | 1e-3; 2 % | 0.2 %, 0.8 % |
| Contact pressure through a stack | p A = 2000 N | CalculiX, Code_Aster | 0.1 % | 2000.04 N, 2000.0 N |
| Stack in one part | p A = 2000 N | CalculiX | 0.1 % | passes |
| Hertz line contact | Hertz a, p_0 | CalculiX (Code_Aster refuses) | 5 % peak; 1.5 elements | peak within 0.7 % |
| Press fit | Lamé | CalculiX, Code_Aster | 3 % pressure; 5 % hoop | −0.32 %, −0.20 % |
| Footing on a compression-only support | rigid footing | CalculiX, Code_Aster | 2 % | within 0.7 % |
| Weak springs | p A through the contact | CalculiX, Code_Aster | 1e-4 | 99.9995 N of 100 |
| Weak springs, sliding and factor | friction holds; factor-free | CalculiX, Code_Aster; CalculiX | 1 %; 1e-5 | 0.0994 N of 0.1; 3.2e-6 |
| Section pretension and lock | F, F + P/2, d k_b/2 | CalculiX, Code_Aster | 0.5 % | within 0.04 %; d +0.38 % |
| Beam fasteners | statics, FL/(EA) | CalculiX, Code_Aster | 1e-4; 2 % stretch; F + P/2 1 % | exact; F + P/2 −0.25 % |
| Bolt joint styles and anchors | preload, statics | CalculiX, Code_Aster | 0.1 %; 1e-4 | exact |
| Modelled bolt preload | 40 kN | CalculiX, Code_Aster | 2 % | −0.5 %, +0.8 % |
| Revolved bolt mesh | F/A; Tipton's K_t | CalculiX, Code_Aster | 1 %; 4 % | 0.04 % to tets; 2.80 (2.82) |
| Belleville spring | Almen–László | CalculiX | 12 % | 9.9-10.0 % below |
| Belleville springs stacked | the single spring's curve | CalculiX | series 1 %, parallel 5 % | 0.2 %; 3.5 % |
| Smooth thread against meshed teeth | meshed M16 thread | CalculiX | 3 points first; 5 % stiffness | 47.6 % (50.0); +2.6 % |
| Thread forms' wedges | F tan α | CalculiX, FEniCSx, Code_Aster | 10-15 % | four digits |
| Threads across solvers | CalculiX | FEniCSx, Code_Aster | 2 points; 2 % | four digits |
| Light weld, lap joint and bracket | statics, elastic method | CalculiX, Code_Aster, FEniCSx | 1e-4; 5 %, 10 % | exact; −6.3 % |
| Modelled and groove welds | statics | CalculiX, Code_Aster | 2-8 % | 98.8 % of P |
| VDI 2230 | Examples B1, B2, B5, Table A1 | none (unit test) | printed digits | passes |
| Weld strength | AISC Manual, DG 27, ADM | none (unit test) | printed digits | passes |
| Thread profiles and teeth | ISO, ASME, Liu et al. | none (unit test) | 2 µm; 0.2 % | passes |

The theory behind these is in [Contact](help:theory/contact),
[Bolted joints](help:theory/bolted-joints), [Thread loads](help:theory/thread-loads) and
[Weld strength](help:theory/weld-strength); setting them up is in the
[Contact](help:guides/contact) and [Bolts and welds](help:guides/bolts-and-welds) guides.

## Ties between separately meshed parts

A 200 × 10 × 10 mm steel bar in two halves meshed apart (2.5 and 1.6 mm), so the
interface nodes do not coincide, joined by a bonded nonconformal contact (CalculiX
`*TIE`, Code_Aster `LIAISON_MAIL`). With symmetry planes and an end force F the bar is
in uniform tension:

$$u_x = \frac{F x}{E A}$$

Fixed at one end with a tip load P it is a Timoshenko cantilever, $\kappa = 5/6$:

$$\delta = \frac{P L^3}{3 E I} + \frac{P L}{\kappa G A}$$

`tests/integration/test_contact.py` `test_tie_passes_uniform_stress` holds the
displacement to 0.1 % of its largest value and the stress to 0.2 % away from the joint;
within 5 mm of it the mean error must stay under 2 % and the worst node under 20 %
(the test records 17 % at the worst node on Linux's mesh, 1 % mean: a node-to-surface
tie disturbs the stress locally). `test_tie_carries_bending` holds the tip deflection
to 2 % of the beam and to 0.5 % of the same bar meshed conformally. Both run on
CalculiX and Code_Aster. `test_tie_conducts_heat` (CalculiX; Code_Aster's tie is
structural only) holds steady conduction from 100 to 20 °C to the linear profile within
0.05 K.

## Two blocks pressed, pulled and dragged

A 10 mm aluminium cube on a 10 mm steel one, meshed apart, symmetry planes, 20 MPa on
the top (`test_contact.py`). A frictionless or no-separation interface lets each block
grow sideways by its own Poisson strain, and the stack shortens as two springs in
series:

$$u_{top} = -p L \left( \frac{1}{E_s} + \frac{1}{E_a} \right)$$

`test_frictionless_stack_is_uniaxial` holds the top's displacement to 0.2 % and each
block's lateral growth $\nu p L / E$ to 1 %, on CalculiX and Code_Aster, with the
`accurate` penalty (the default, `balanced`, is 0.8 % over here, the test notes). Away
from the interface the stress is uniaxial to 1 % (1.5 % for Code_Aster's no
separation, whose nodal stresses next to the interface are noisier; its displacements
stay within 0.3 %). ADR 0039 records CalculiX exact and Code_Aster within 0.3 % on the
lateral growth.

Pulled 1 µm apart with no separation, the interface holds and the stack carries

$$F = \frac{\delta A}{L/E_s + L/E_a}$$

`test_no_separation_holds_a_pull` allows 0.5 %; ADR 0039 records 517.97 N (CalculiX)
and 518.56 N (Code_Aster) against 518.79 N. `test_rough_opens_under_a_pull`: rough and
frictionless interfaces open, carrying under 1e-3 of that (1e-13 N recorded).
`test_rough_sticks_as_a_bond_when_pressed`: pressed 1 µm and dragged 0.3 µm, rough
contact gives the bonded interface's reactions within 2 % (CalculiX 0.2 % tangentially
and 0.8 % normally, Code_Aster identical, by ADR 0039).

Pressed 1 µm and dragged 10 µm with μ = 0.3, the whole interface slides:

$$F_t = \mu F_n$$

`test_sliding_friction_obeys_coulomb` allows 1 %. Code_Aster's friction runs on a
first-order mesh with the base 2 mm wider than the cap (its slave face must lie inside
the master); CalculiX on the quadratic mesh without the overhang.

`tests/integration/test_contact_results.py` reads the same runs through the contact
results (ADR 0056): `test_contact_pressure_through_a_stack` asks the frictionless
interface to carry p A = 2000 N within 0.1 %, all of it in contact and sliding,
CalculiX's pressure 20 MPa at every node within 0.1 %, Code_Aster's its own Lagrange
multiplier with a mean within 1 %. ADR 0056 records CalculiX 2000.04 N (every node to
0.04 %) and Code_Aster 2000.0 N (mean 20.1 MPa). `test_a_sliding_block_slides_everywhere`
holds frictional over normal force to μ within 2 %, under 2 % of the area sticking and
the largest slip 10 µm within 20 %; both measured 0.300.
`tests/integration/test_contact_in_a_part.py` builds the stack as one STEP part of two
bodies, which the mesher joins and then parts at the contact: 2000 N within 0.1 % and
the whole face in contact (CalculiX).

## Hertz line contact

A steel cylinder, R = 10 mm, pressed on a steel block in plane strain (a 0.5 mm slice
held on both faces), half the model by symmetry, its top moved down 20 µm; the contact
zone meshed at 0.05 mm. For the force per length P that it then carries, Hertz gives

$$E^* = \frac{E}{2 (1 - \nu^2)}$$

$$a = \sqrt{\frac{4 P R}{\pi E^*}}$$

$$p_0 = \frac{2 P}{\pi a}$$

and the pressure $p(x) = p_0 \sqrt{1 - (x/a)^2}$ (Johnson, ch. 4).
`tests/integration/test_hertz.py` `test_hertz_line_contact` (CalculiX) holds the
closed zone's half-width to 1.5 elements of a, the peak to 5 % of $p_0$, and the
pressure inside 0.8 a to 10 % of $p_0$ at every node. ADR 0056 records the peak within
0.7 % and the half-width within an element.

Code_Aster 18's continuous contact fails to pair this model before its first increment,
whatever the source face or options tried, so it refuses a curved face on a face of
another shape (`unsupported`) and the checks warn `contact_nonconforming` before a run;
`test_code_aster_refuses_a_line_contact_and_the_checks_warn` checks both.

## Press fit

A steel shaft of radius b = 10 mm in a steel ring to c = 20 mm, 2 mm thick, plane
strain, a quarter by symmetry, the faces meshed coincident and the contact given an
offset of −20 µm (the interference δ). Lamé's interface pressure and the ring's hoop
stress at its bore (Timoshenko and Goodier):

$$p = \frac{E \delta (c^2 - b^2)}{2 (1 - \nu^2) b c^2}$$

$$\sigma_\theta = p \frac{c^2 + b^2}{c^2 - b^2}$$

`tests/integration/test_press_fit.py` `test_press_fit_pressure_is_lames` holds the mean
contact pressure to 3 %, the area in contact to 2 % of the whole and the hoop stress at
45° to 5 %, on CalculiX and Code_Aster. ADR 0056 records the pressure −0.32 % and
−0.20 %, the hoop stress −2.5 % and −2.3 %. The offset moves the mesh rather than using
CalculiX's `*CLEARANCE`, whose pressures came out three times Lamé's.

## Compression-only support

A steel block 100 mm long (B), 20 mm deep (D) and 50 mm tall on a foundation of
$k_n$ = 1e11 N/m³ that pushes but never pulls, loaded down by P = 20 kN at e = B/4 from
its middle. Past e = B/6 the heel lifts and a rigid footing touches over

$$c = 3 \left( \frac{B}{2} - e \right)$$

with the toe settling

$$w = \frac{2 P}{3 D k_n (B/2 - e)}$$

`tests/integration/test_compression_only.py` `test_footing_lifts_off` holds both to 2 %
on CalculiX (gaps in an NLGEOM step) and Code_Aster (`DIS_CHOC`). ADR 0037 records the
contact length −0.6 % and +0.1 %, the settlement +0.7 % and −0.05 %; CalculiX's
difference is NLGEOM's large-rotation terms. Elmer and FEniCSx refuse the support.
`test_footing_lifts_off_with_the_model_centre_below` adds a held block 1 m below, which
puts the model's centre under the foundation face: the same answers on both solvers
(measured contact 74.52 and 75.08 mm, toe 0.26853 and 0.26654 mm, 2026-11-17), where the
support's normal, turned away from the model's centre, had pointed into the footing and
the solve failed.

## Bolts under temperature

An ASTM A36 bolt clamps an aluminium 6061-T6 sleeve (bore 12 mm, outside 24 mm, 40 mm
long). A free length mismatch $\delta$ changes the bolt force by $\delta / C$, with
$C = 1/k_b + 1/k_c$ the joint's compliance; heated uniformly by $\Delta T$,

$$\delta = (\alpha_c - \alpha_b) \Delta T L$$

A beam bolt's $k_b = E_b A_b / L$ is exact (its springs and Code_Aster's beam are $EA/L$
axially); $k_c$ is the preload over the sleeve's shortening in the preload step. A
modelled bolt's $C$ comes from an adjustment run on the model ($F = d / C$); its heads
bear on the sleeve through frictionless contact. `tests/integration/test_bolt_thermal.py`
holds, measured 2026-11-17 (preload 10 kN, heating 100 K unless said):

| Case | Solver | Bolt | Closed form | Measured |
| --- | --- | --- | --- | --- |
| a, heated after the preload (rise) | CalculiX | beam | 11178.2 N | 11178.4 N |
| a | Code_Aster | beam | 11076.8 N | 11076.8 N |
| c, one material (A36 sleeve): change | CalculiX | beam | 0 | −0.011 N |
| c | Code_Aster | beam | 0 | −0.012 N |
| f, snug (1 N), rise | CalculiX, Code_Aster | beam | as a | 11178.4, 11076.8 N |
| e, then 5 kN separating: bolt force | CalculiX | beam | 23187.4 N | 23187.6 N |
| e | Code_Aster | beam | 23113.1 N | 23067.6 N (−0.2 %) |
| to ground, bore's movement | CalculiX | beam | −2.3400e-5 m | −2.3400e-5 m |
| to ground | Code_Aster | beam | −2.3400e-5 m | −2.3220e-5 m |
| transferred temperature against the body temperature | both | beam | equal | equal to six digits |
| a, heated | CalculiX | modelled | 19632.5 N | 19631.1 N |
| d, bolt +150 K, sleeve +50 K | CalculiX | modelled | 5346.0 N | 5348.4 N |
| b, cooled 40 K | CalculiX | modelled | 6147.3 N | 6146.9 N |
| b, cooled 80 K | CalculiX | modelled | 2294.4 N | 2293.4 N |
| b, cooled 250 K (opens at −103.8 K) | CalculiX | modelled | 0 | 0.17 N |
| adjustment 0.06 mm, then heated | CalculiX | modelled | 21773.8 N | 21772.1 N |
| c, one material (heads tied) | CalculiX, Code_Aster | modelled | 10000.4, 10000.5 N | unchanged |
| g, heated, heads tied | CalculiX against Code_Aster | modelled | equal | 19754.28 and 19754.20 N |
| h, modal step after preload and heat | CalculiX | beam | the joint's own modes | equal to 1e-5 |

Tolerances: beam bolts 0.1 % (Code_Aster's separating load 3 %: its LIAISON_RBE3
weights the spider unlike the load's area spread, which also leaves its sleeve 2.2 %
softer than $EA/L$), the null tests 1e-5 of the preload (beam) and 0.1 % (modelled,
the stress sampling's scatter), modelled bolts 0.5 %, the bolt to ground 1 %. Code_Aster's
frictionless modelled joint didn't converge in this sleeve, heated or not, so its
modelled cases are tied; Code_Aster refuses the modal step after a pretension (each of
its steps is its own analysis). Rigid spiders and the solid formulation are refused
under temperature: with rigid ends a joint of one material gained 13.6 % of its preload
heated 100 K, and a solid fastener lost 7.4 %.

## Weak springs

The split bar's right half held only by frictional (μ = 0.3) or frictionless contact on
the fixed left half, pressed by 1 MPa (100 N). Without springs its first solve is
singular; Puzzle adds weak springs (ADR 0080).
`tests/integration/test_weak_springs_solved.py` asks the fixed end's reaction to equal
p A within 1e-4 and the springs to carry under 1e-4 of it, with no
`weak_springs_carry` warning: frictional on CalculiX and Code_Aster, frictionless on
CalculiX. ADR 0080 records 99.9995 N of 100 with 4.4e-4 N in the springs.
`test_friction_holds_a_sliding_load_under_its_limit` (CalculiX, Code_Aster) adds a
sideways 0.1 N on the end, under the friction limit: its reaction within 1 % and the
springs under 1 % of it. It is that small because a sideways force on the bar's end
puts a moment on the 10 mm interface, and past about 1.7 N one edge lifts (10 N did,
and the body slid onto its springs). Measured (2026-11-15): 0.0994 and 0.0995 N, the
springs 7e-4 N. `test_the_answer_does_not_depend_on_the_spring_factor` (CalculiX):
the end displacement changes by under 1e-5 between factors 1e-6 and 1e-7; measured
3.2e-6. A lift-off
solved end to end is not benchmarked (it diverged on CalculiX); the warning is checked
on results in `tests/test_weak_springs.py`.

## Bolt pretension

A steel bolt, R = 5 mm, L = 50 mm, cut at mid-length; fixed at the bottom, its top on an
elastic support as stiff as the bolt ($k_s = k_b = EA/L$). Pretensioned to F = 10 kN,
then locked and pulled by P = 4 kN, the joint diagram gives

$$F_b = F + P \frac{k_b}{k_b + k_s} = F + \frac{P}{2}$$

and a shortening d instead of a force gives $F_b = d k_b k_s / (k_b + k_s) = d k_b / 2$.
`tests/integration/test_pretension.py` holds the axial force (from the stress away from
the ends) to 0.5 % in `test_pretension_force_then_locked`, `test_pretension_adjustment`,
`test_pretension_one_step` and `test_preload_step_put_in_front` (one step that tightens
and loads: Puzzle puts a preload step in front), on CalculiX and Code_Aster. ADR 0037
records, two steps: CalculiX 9998.97 then 12004.2 N, Code_Aster 9999.07 then 12002.5 N;
the adjustment +0.38 % on both. Elmer and FEniCSx refuse pretension.

`test_a_load_before_a_modal_step_leaves_its_modes`: the bolt without its pretension,
pushed or pulled by 10 kN in a static step, then a modal step. The modal step is its
own analysis, so its modes must equal the unloaded ones to 1e-5; measured (2026-11-17)
3404.0, 3404.6, 16041.2, 17135.5 and 17138.8 Hz on CalculiX and 3404.0, 3404.5,
16039.4, 17135.0, 17138.3 Hz on Code_Aster at 0 and ±10 kN alike (four asked, the last
pair kept whole). On CalculiX the elastic support's SPRINGA springs, 1e-3 of the model
long and built in the deformed state, had added F/L across: the bending pair fell to
0 Hz under the push and rose under a pull. Physical stress stiffening of the bolt at its
5 kN share would be about +0.6 % ($\sqrt{1 + T L^2/(\pi^2 E I)}$), and Puzzle doesn't
apply it unless a prestress is asked for. Code_Aster had failed the same modal step:
the fourth mode's partner fell outside the four asked for (Sturm check).

## Beam fasteners

`tests/integration/test_fasteners.py`, on CalculiX and Code_Aster, rigid and
distributing ends (ADR 0027):

- **Hanging block:** a 20 mm steel cube 50 mm below a fixed plate on one 6 mm
  fastener, under a gravity of 9.81e4 m/s². The axial force is the cube's weight within
  1e-4 and the shear under 1e-6 of it; with rigid ends the stretch is $FL/(EA)$ within
  2 %. ADR 0027 records the force exact and the stretch to 0.01 % on CalculiX and exact
  on Code_Aster.
- **Shear:** two 5 mm plates 5 mm apart, one fixed, the other pulled by P = 1 kN. The
  fastener carries P in shear within 1e-4, and by statics of the plate hanging on it the
  moment runs from 7.5 P mm to 2.5 P mm along it, within 0.1 %: exact on both.
- **Pretension:** the block tightened to F = 5 kN over a support as stiff as the bolt,
  then locked and pulled by P = 2 kN: F within 1e-4, then F + P/2 within 1 % (the
  plates' own give). ADR 0037 records 5985.1 N on both (−0.25 %), measured again
  2026-11-15.

`tests/integration/test_bolt_joints.py` (ADR 0050): two 10 mm plates and an M8 bolt
tightened to 5 kN carry 5 kN within 0.1 % whatever the ends bear on (hole walls, head
and nut annuli, a tapped thread), on both solvers; a plate held only by a bolt to
ground carries P in tension or in shear within 1e-4, with no moment at the plate and
P × 16 mm at the ground within 0.1 %; the generator's imprinted bearing ring has the
annulus's area within 0.2 % and carries the preload within 0.1 %. ADR 0050 records them
exact. Elmer and FEniCSx refuse fasteners.

## Modelled bolts

An M16 8.8 cap screw (ISO 4014) drawn from the tables through a 10 mm plate into a
tapped steel boss, preloaded to 40 kN (ADR 0077).
`tests/integration/test_solid_bolts.py` `test_the_preloaded_shank_carries_its_preload`
holds the force across the pretension section to 2 % with and without an ISO 7089
washer (CalculiX); `test_code_aster_preloads_a_modelled_bolt_on_a_first_order_mesh`
the same on Code_Aster, whose frictional contacts need first order. ADR 0077 records
39.79 kN (−0.5 %), 39.81 kN with the washer, and Code_Aster 40.31 kN (+0.8 %).

`tests/integration/test_revolved_bolts.py` meshes an M16 × 50 bolt as a body of
revolution (hex20 and wedge15 in 24 sectors). Its volume must be the solid's within
0.5 %. Pulled by a pressure on its bearing face, the axial stress in the thread's core
must match a tetrahedral mesh's within 2 % and $F/A$ of the core within 1 %
(`test_revolved_and_tetrahedral_meshes_agree`, CalculiX and Code_Aster), and within 3 %
on first-order hexahedra and prisms (`test_first_order_hexahedra_and_prisms_solve`).
ADR 0077 records 0.04 % between the meshes.

`test_the_head_fillet_raises_the_stress_as_peterson_says` (CalculiX) pulls the bolt by
its end with the head's top held, so the under-head fillet (ISO 885 minimum, D/d = 1.41,
r/d = 0.0375) is a shouldered bar's. Tipton, Sorem and Rolovic's fit, with q = D/d,

$$K_t = 0.493 + 0.48 q^{-2.43} + \left( \frac{r}{d} \right)^{-0.48} \sqrt{\frac{3.43 - 3.41 q^2 + 0.0232 q^4}{1 - 8.85 q^2 - 0.078 q^4}}$$

gives 2.82; the test allows 4 %. The test's docstring records 2.80 at r/3 elements
(2.86 at r/6). Peterson's Chart 3.4 gives 2.40 and, by its own text, can be 40 % low
for sharp fillets, so Eq. 3.5 is the reference.

## Belleville spring

A DIN 2093-style disc spring, 40 × 20.4 × 1.5 mm with a free cone height
$h_0$ = 1.15 mm, a quarter by symmetry, pressed between stiff platens through
frictionless contacts to 75 % of $h_0$ (large deflection). The Almen–László formula
(DIN 2092), with $\delta = D_e/D_i$:

$$K_1 = \frac{1}{\pi} \frac{((\delta - 1)/\delta)^2}{(\delta + 1)/(\delta - 1) - 2/\ln \delta}$$

$$F = \frac{4 E}{1 - \nu^2} \frac{t^4}{K_1 D_e^2} \frac{s}{t} \left[ \left( \frac{h_0}{t} - \frac{s}{t} \right) \left( \frac{h_0}{t} - \frac{s}{2t} \right) + 1 \right]$$

`tests/integration/test_belleville.py` `test_single_spring_follows_almen_laszlo`
(CalculiX) asks the spring to take the stroke within 5 % and the force past 0.2 $h_0$
to stay within 12 % of the formula. The test records CalculiX 9.9-10.0 % below it at
every point: the formula takes the section as turning rigidly about a neutral circle,
which over-predicts a spring this wide ($D_e/D_i \approx 2$). Code_Aster is not run:
held only by its rim contacts, its solve starts singular or fails to converge.

`test_stacked_springs_follow_the_single_one` (CalculiX) checks two exact relations
against the single spring's own curve: two springs face to face (series) take its force
at twice the deflection, within 1 %; two nested (parallel) twice its force at the same
deflection, within 5 %. Measured (2026-11-15): series 0.2 %; parallel 3.5 % at 0.2
$h_0$ falling to 1.4 % at the stroke, from the nested springs' faces in contact, which
the single spring doesn't have.

## Threads

An M16 × 2 screw pulled out of a steel boss by 40 kN, 2D axisymmetric (ADR 0076). The
reference meshes the 60° teeth, bonded on the bearing flanks; it is converged (0.25 and
0.12 mm elements give a first-thread share of 50.0 and 50.1 % and a stiffness of 1.167
and 1.166 kN/µm). The smooth model has two cylinders at the screw's minor radius with
an ISO thread on the contact. This is a meshed reference, not a published one: it
checks that springs on smooth faces stand in for real teeth.

`tests/integration/test_thread_benchmark.py`:

- `test_a_smooth_thread_shares_the_load_as_the_meshed_one` (CalculiX): the shares sum
  to the load within 2 points, the first thread within 3 points of the reference, each
  within 5, the stiffness within 5 %. ADR 0076 records 47.6 % against 50.0, threads 2-8
  within 4.7 points, 1.197 kN/µm (+2.6 %). The teeth's compliance is Yamamoto's scaled
  by one tuned factor, 0.25 (`threads.TEETH_FACTOR`); as published it gave 36.8 %.
- `test_forms_and_flanks_wedge_and_share_as_they_should` (CalculiX, FEniCSx,
  Code_Aster): the radial push of a form with flank angle α is

$$W = F \tan \alpha$$

  within 10 % for the 60° form and 15 % for buttress (7° and 45° flanks), square under
  1 % of the 60° one; per-thread loads sum to the load within 2 points. ADR 0076
  records the wedges to four digits (23094 N for ISO, 4911 and 40000 N for buttress).
- `test_the_revolved_model_shares_as_the_two_dimensional_one` (CalculiX, 3D tets): each
  share within 3 points of 2D, the wedge within 5 %, the flank pressure around the
  first thread even within 10 %. ADR 0076 records 1.3 points, 0.2 % and 8.6 %.
- `test_fenicsx_threads_match_calculix` and `test_code_aster_threads_match_calculix`:
  on one mesh, shares within 2 points and stiffness within 2 % of CalculiX's, for
  bonded, no-separation and frictionless flanks (Code_Aster also with a 0.01 mm
  clearance, whose added rise $c / \cos 30^\circ$ is checked by hand within 1 %). The
  docstrings record agreement to four digits where both are linear; on frictionless
  flanks CalculiX's NLGEOM springs sum to 103 % of the load, so its wedge is 3.4 %
  larger and the FEniCSx comparison allows 5 %.
- `test_a_beam_bolt_shares_its_thread_as_a_solid_screw` (CalculiX): a beam bolt's
  tapped end shares its load within 5 points of the solid smooth screw (1.4 points by
  ADR 0076); `test_code_aster_beam_bolt_threads_match_calculix`: within 2 points of
  CalculiX's and the axial force within 1 % of the load.

Elmer refuses threads (it writes no node-pair springs).

## Welds

`tests/integration/test_welds_solved.py` (ADR 0078):

- **Lap joint:** a 10 mm plate lapped on another, joined only by a 6 mm fillet 50 mm
  long across its end, pulled by P = 10 kN. The light weld must carry P within 1e-4
  (equilibrium) and a mean line force of P/L within 5 %, on CalculiX, Code_Aster and
  FEniCSx (`test_lap_joint_carries_the_pull`). ADR 0078 records 10000 N and 207.8 N/mm
  (+3.9 %, the peel's share) on its first build. `test_a_light_weld_passes_the_moment`
  cuts the lower plate: P within 1 % and the moment P × 10 mm within 3 %; ADR 0078
  records 98.99 of 100 N·m on all three solvers, equal to four digits.
- **Bracket welded all round:** a 20 × 40 mm block, 60 mm tall, a 6 mm fillet round its
  foot, a side load P at its top. The weld carries P and the moment P h within 1e-4.
  The elastic method (weld as lines, AISC Manual Part 8) gives the normal line force on
  the long sides

$$f = \frac{M d / 2}{I}$$

$$I = \frac{b d^2}{2} + \frac{d^3}{6}$$

  checked within 10 %; ADR 0078 records −6.3 % (the parts are not rigid, so the force
  gathers toward the corners). The instantaneous-centre strength is checked within 3 %
  of a hand calculation; ADR 0078 records 153.9 kN against 155.1 kN.
- **Modelled beads** (CalculiX, Code_Aster): the lap joint's fillet drawn as a solid
  carries P within 3 %, the line force P/L within 5 %; ADR 0078 records 88.7, 98.8 and
  101.8 % of P at 3, 6 and 12 elements across the leg (the section crosses the root's
  notch, so it is mesh-dependent). The bracket's closed bead (P within 5 %, P h within
  8 %) and an intermittent bead (P within 5 %) run on CalculiX.
- **Groove welds** (CalculiX, Code_Aster): a butt joint, CJP and PJP, carries P within
  2 % and P/L within 10 %; at the toe the structural stress is membrane P/(L t) within
  5 %. With a cap drawn, P still passes within 2 % and the cap carries a share between
  3 % and 17 %, its area's share.
- **Fatigue and structural stress** (CalculiX): AISC Appendix 3 category F on the lap
  joint reads the largest line force over the throat within 0.2 %; ASME VIII-2 5.5.5 at
  a plate-side toe carries membrane P/(L t) = 20 MPa within 1 % with bending under 2 %,
  and on a shell tee's web within 5 %. `test_a_light_weld_between_shells` (CalculiX,
  Code_Aster) carries P within 1e-4 on shell edges.

Elmer and 2D models refuse welds.

## Standards' worked examples

These unit tests run no solver: they hold Puzzle's formulas to the printed digits of
the standards' own examples.

- `tests/test_vdi2230.py`: VDI 2230 Part 1:2015 Examples B1 (M12 socket head into C45),
  B2 (M16 hex with nut, transverse load) and B5 (M20 eccentric cylinder cap), and Table
  A1's preloads and torques from M4 to M36: resiliences, cone limit diameters, load
  factors, embedding losses, working stresses and endurance limits, each to the book's
  rounding (torques within 2 %). Where the book's own arithmetic differs from its
  printed value (B5's δM 0.104 against 0.1025, its bending length 48.7 against 49.0 mm)
  the test says which it follows.
- `tests/test_weld_strength.py`: a 1/16 in E70 fillet carries 1.392 kip/in along its
  axis (AISC Manual Eq. 8-2a) and 1.5 times that across it (AISC 360 Eq. J2-5); the
  instantaneous centre reproduces 13 entries of the Manual's Table 8-4 (14th ed.) to its
  printed digits; Design Guide 27 Example 9 (stainless, R_n 183 kips, φR_n 137, R_n/Ω
  91.5); ADM 2015 Part VII Example 27 (aluminium, 51.7 k, R_n/Ω 26.5 k); Appendix 3's
  allowable ranges at 2 million cycles (category B 18.2 ksi, F 9.5 ksi). ASME VIII-2
  5.5.5 is checked against a calculation by hand.
- `tests/test_threads.py`: the forms' basic profiles against ISO 724 (M16 × 2: d2
  14.701 mm, D1 13.835 mm), ASME B1.1 (1/2-13 UNC), ISO 2904 (Tr 20 × 4) and ASME B1.5
  (1-5 Acme) within 2 µm; Yamamoto's tooth terms reproduce Liu et al.'s coefficients
  for the ISO 60° form within 0.2 %; the VDI 2230 thread compliance for an M16.

## Sources

- K. L. Johnson, *Contact Mechanics*, Cambridge University Press, 1985, ch. 4 (Hertz
  line contact).
- S. P. Timoshenko and J. N. Goodier, *Theory of Elasticity*, 3rd ed., McGraw-Hill,
  1970 (thick-walled cylinders: Lamé).
- J. O. Almen and A. László, "The uniform-section disk spring", *Transactions of the
  ASME* 58 (1936) 305-314; DIN 2092:2006, Disc springs: calculation.
- S. M. Tipton, J. R. Sorem and R. D. Rolovic, "Updated stress concentration factors
  for filleted shafts in bending and tension", *Journal of Mechanical Design* 118
  (1996) 321-327; W. D. Pilkey and D. F. Pilkey, *Peterson's Stress Concentration
  Factors*, 3rd ed., Wiley, 2008, Eq. 3.5 and Chart 3.4.
- VDI 2230 Part 1:2015, Systematic calculation of highly stressed bolted joints: joints
  with one cylindrical bolt; Examples B1, B2, B5 and Table A1.
- A. Yamamoto, *The Theory and Computation of Threads Connection*, Yokendo, Tokyo, 1980,
  as restated by Liu et al., *Engineering Structures* 315 (2024) 118486, Eqs. 5-12.
- ISO 724 (ISO metric threads, basic dimensions), ISO 2904 (trapezoidal threads), ASME
  B1.1 (unified inch threads), ASME B1.5 (Acme threads); ISO 4014, ISO 885, ISO 7089.
- AISC 360-16, Specification for Structural Steel Buildings, J2 and Appendix 3; AISC
  Steel Construction Manual, 14th ed., Part 8 and Table 8-4; AISC 370-21 and AISC Design
  Guide 27, Structural Stainless Steel, 2nd ed., Example 9.
- The Aluminum Association, Aluminum Design Manual 2015, J.2 and Part VII Example 27.
- ASME Boiler and Pressure Vessel Code, Section VIII Division 2, 5.5.5.
