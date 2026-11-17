---
title: Frame member design
section: theory
order: 170
keys: theory:frame-design
summary: How Puzzle checks each frame member against its design code (AISC 360, AISC 370 or the Aluminum Design Manual) from the forces of a solved run, and how the load combinations, P-Delta and the Direct Analysis Method feed it.
adrs: 0079
---

A frame solve gives forces along every member: axial force, two shears, torsion and two
bending moments. A member check asks whether the member can carry them by its design
code: will it yield, buckle as a column, buckle sideways as a beam, or crumple locally?
Each check gives a ratio of demand to available strength; 1.0 or less passes. Puzzle
reports every limit state with its clause, and the one that governs.

## The idea: limit states and their factors

Each limit state has a nominal strength $R_n$ from the code's equations. The available
strength depends on the method chosen in Member design:

$$R_{avail} = \phi R_n \quad \mathrm{(LRFD)}$$

$$R_{avail} = R_n / \Omega \quad \mathrm{(ASD)}$$

The demand must come from the matching load combinations: factored (LRFD) or service
level (ASD). Under AISC 360 most limit states use φ 0.90 and Ω 1.67. Tensile rupture
uses 0.75 and 2.00. Shear yielding of a rolled I-shape's web, G2.1(a), uses 1.00 and
1.50.

## Steel by AISC 360-16

`frames/aisc360.py` covers doubly symmetric I-shapes, rectangular and round HSS, and
solid rectangles and rounds. Channels, tees and angles are refused, and so is an I
whose web is noncompact in flexure (F4, F5).

**Tension (D2).** Yielding on the gross area, $F_y A_g$, and rupture on the effective
net area, $F_u A_e$. You give the ratio $A_e / A_g$ as `net_area_ratio`, because
Puzzle doesn't know the connection's holes or shear lag.

**Compression (E3, E4, E7).** Flexural buckling about each axis uses the elastic
buckling stress $F_e = \pi^2 E / (L_c / r)^2$ (E3-4). The critical stress is:

$$F_{cr} = 0.658^{F_y / F_e} F_y \quad \mathrm{when} \quad F_y / F_e \leq 2.25$$

$$F_{cr} = 0.877 F_e \quad \mathrm{otherwise}$$

For a doubly symmetric I, torsional buckling (E4-2) is checked too:

$$F_e = \left( \frac{\pi^2 E C_w}{L_{cz}^2} + G J \right) \frac{1}{I_x + I_y}$$

Slender elements reduce the area by E7's effective widths, with $c_1$, $c_2$ of 0.22
and 1.49 for flanges, 0.18 and 1.31 for webs, and 0.20 and 1.38 for HSS walls. Round
HSS use E7-7. The least strength over the cases governs.

**Flexure (F2-F11).** For an I bent about its strong axis (F2), the plastic moment
$M_p = F_y Z_x$ holds up to the unbraced length $L_p = 1.76 r_y \sqrt{E / F_y}$. Between
$L_p$ and $L_r$ the strength falls linearly toward $0.7 F_y S_x$. Past $L_r$ it is
elastic lateral-torsional buckling:

$$F_{cr} = \frac{C_b \pi^2 E}{(L_b / r_{ts})^2} \sqrt{1 + 0.078 \frac{J c}{S_x h_o} \left( \frac{L_b}{r_{ts}} \right)^2}$$

Both branches are multiplied by $C_b$ and capped at $M_p$. Puzzle uses the tabulated
$r_x$, $r_y$ and $r_{ts}$ where the section table has them, as the Manual does. Flange
local buckling (F3) is checked for noncompact and slender flanges. Weak-axis bending is
F6, rectangular HSS F7 (including F7.4's lateral-torsional buckling of tall boxes),
round HSS F8, and solid bars F11.

**The moment gradient factor (F1-1).** A beam whose moment varies along its unbraced
length is stronger than one under uniform moment. Puzzle reads $C_b$ from the moment
diagram:

$$C_b = \frac{12.5 M_{max}}{2.5 M_{max} + 3 M_A + 4 M_B + 3 M_C}$$

$M_A$, $M_B$ and $M_C$ are the absolute moments at the quarter, middle and three-quarter
points, interpolated between stations along the member.

**Shear (G2, G4, G5, G6).** Webs of rolled I-shapes by G2.1(a), others by G2.1(b) with
$k_v = 5.34$. Flanges under weak-axis shear by G6. Rectangular HSS walls by G4, with the
flat width taken as the outside size less $3t$. Round HSS by G5, with $L_v$ taken as half
the member. Solid bars have no Chapter G check.

**Torsion and combined forces (H1, H3).** HSS torsion is checked by H3.1; torsion of
I-shapes (Design Guide 9) is not. The interaction ratio is H1-1:

$$\frac{P_r}{P_c} + \frac{8}{9} \left( \frac{M_{rx}}{M_{cx}} + \frac{M_{ry}}{M_{cy}} \right) \leq 1 \quad \mathrm{when} \quad P_r / P_c \geq 0.2$$

$$\frac{P_r}{2 P_c} + \frac{M_{rx}}{M_{cx}} + \frac{M_{ry}}{M_{cy}} \leq 1 \quad \mathrm{otherwise}$$

Where HSS torsion exceeds $0.2 T_c$, H3-6 is checked as well.

## Stainless and aluminium

The code follows the material's group (`design.code_for`): Carbon Steel and Low Alloy
Steel by AISC 360, Stainless Steel by AISC 370-21 (`frames/aisc370.py`), Aluminum by the
Aluminum Design Manual 2015 (`frames/adm.py`). `MemberDesign.code` can name one
directly.

- **AISC 370.** Column curves A, B and C from Table E3.1 replace E3's single curve. F2
  adds a third length $L_y$, and $\beta$ factors by alloy family from Table F2.1. The
  family is duplex when the material's name says duplex, 2101, 2205, 2304 or 2507, and
  austenitic otherwise. Local buckling uses effective section moduli. Shear's $C_{v1}$
  may reach 1.2 (strain hardening). H1-1 is the same as 360's.
- **ADM.** Buckling constants come from Table B.4.1 or B.4.2 by temper, read from the
  material's name. Elements are checked by B.5.4 and B.5.5. Members use E.2 to E.4,
  F.2 to F.4, G.2 to G.5 and a linear H.1-1:
  $P_r/P_c + M_{rx}/M_{cx} + M_{ry}/M_{cy} \leq 1$. The factors are for building-type
  structures. Tension yielding is φ 0.90, Ω 1.65, and rupture φ 0.75, Ω 1.95. The
  governing limit state is picked for the method asked, since rupture's factors differ
  from yielding's.

## Second-order analysis and the Direct Analysis Method

The strengths above assume the forces come from an analysis that already includes
second-order effects. Two step settings on a PyNite study provide that:

- `second_order` runs PyNite's P-Delta analysis (`analyze_PDelta`), each combination on
  its own because superposition no longer holds. P-δ along a member comes from the
  elements the mesh splits it into.
- `direct_analysis` (AISC 360 Chapter C) runs P-Delta with E and G multiplied by 0.8
  ($\tau_b = 1$). It also adds notional lateral loads $N_i = 0.003 Y_i$ at each node:
  C2.2b's $0.002 Y_i$ and C2.3(c)'s $0.001 \alpha Y_i$ ($\alpha = 1$, LRFD), the price of
  keeping $\tau_b = 1$ for members whose $\alpha P_r / P_{ns}$ passes 0.5 instead of
  checking each. $Y_i$ is that combination's factored gravity load at the node (point
  loads, line
  loads and self weight half to each end, plate pressures a quarter to each corner).
  Each combination is repeated for each
  `notional_directions` entry, x and y in both senses by default.

With direct analysis, effective length factors stay at 1 (C3), and the member checks
use the full E. For a frame with stainless members, AISC 370 C2.3 applies: the
stiffness factor is 0.70, and the notional loads are $0.004 Y_i$ from the stainless
members' and plates' loads (0.003 from the carbon steel ones); a node shared by both
takes the mean weighted by the lengths of the members meeting there.

## Load combinations

`frames/combinations.py` writes ASCE 7-16's combinations from the cases the loads use:
LRFD by 2.3.1 (1.4D, 1.2D + 1.6L + 0.5(Lr or S or R), ..., 0.9D + 1.0E) or ASD by 2.4.1.
It leaves out any combination whose wind, seismic or leading roof load the frame
doesn't have. Wind and seismic act both ways, and Ex and Ey go in combinations of their
own. E is the load effect as given: $E_v = 0.2 S_{DS} D$ and ρ are yours to include.
On PyNite each combination is solved as a load combination. On the finite-element
solvers each becomes a static step with the factored loads (`puzzle/combine.py`).

## How Puzzle does it

`frames/design.py` `member_checks` reads each member's forces from a stored run.
PyNite gives nine stations per element (`member_stations`). The other solvers give
`beam_forces` at the nodes. Every static or combination step is checked at every
station. Strong-axis flexure is checked per unbraced segment: the member is split into
equal segments of about `unbraced_length`, each with $C_b$ from its own diagram. Where
$L_b$ runs past the member, $C_b$ is 1.0, which F1 always allows. Deflection is the
largest distance of a displaced node from the line through the displaced ends, checked
against `deflection_limit` (L/δ). The checks are evaluated on demand and never stored.

Set the code, LRFD or ASD, lengths, K factors, $C_b$, `net_area_ratio` and
`deflection_limit` in Model → Beams and shells → Member design…. Read the results in the
Members panel, with the CLI's [members](help:cli:members), or with MCP
[member_checks](help:mcp:member_checks). See
[Beams, shells and frames](help:guides/beams-shells-frames).

## Limits

- Channels, tees, angles and double angles are not checked (E4 with a shear centre
  off the centroid, F9, F10). Noncompact or slender I webs in flexure (F4, F5),
  stiffened webs and tension field action (G2.2) aren't checked either.
- There is no B1/B2 amplification (Appendix 8). Forces from a first-order run are
  checked as they are, so use `direct_analysis` for a frame where stability matters.
- Direct analysis keeps $\tau_b = 1$; the reduction for $\alpha P_r / P_y > 0.5$ is not
  applied. Notional loads go in every combination, which is conservative. C2.2b(4) would
  allow gravity-only combinations alone where $\Delta_2 / \Delta_1 \leq 1.7$.
- Unbraced segments are equal divisions of the member. Bracing at uneven points needs
  the member split at the braces or $C_b$ given.
- Welded aluminium members (heat-affected zones), torsion on stainless and aluminium,
  and AISC 370's continuous strength method are not built.
- The checks are only as good as the forces: the K factors, lengths and releases are
  yours to set.

## Sources

- AISC 360-16, Specification for Structural Steel Buildings, Chapters B (Table B4.1),
  C (C2.2b, C2.3), D2, E3, E4, E7 (Table E7.1), F1-F8, F11, G2, G4, G5, G6, H1, H3.
- AISC Steel Construction Manual, 15th ed., Table 3-2 (W-shape flexural values, used
  by `tests/test_aisc360.py`).
- AISC 370-21, Specification for Structural Stainless Steel Buildings, Table E3.1,
  Table F2.1, C2.3; AISC Design Guide 27, Structural Stainless Steel,
  2nd ed., Chapter 12 (examples used by
  `tests/test_aisc370.py`).
- The Aluminum Association, Aluminum Design Manual 2015, Part I Specification
  (Tables B.4.1-B.4.3, B.5.1; Chapters D-H) and Part VII examples (`tests/test_adm.py`).
- ASCE/SEI 7-16, Minimum Design Loads and Associated Criteria for Buildings and Other
  Structures, 2.3.1 and 2.4.1.
