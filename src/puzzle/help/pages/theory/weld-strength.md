---
title: Weld strength
section: theory
order: 70
keys: theory:weld-strength
summary: How Puzzle turns a weld's solved line force into a code check (AISC 360 and 370, the Aluminum Design Manual) and a fatigue life (AISC Appendix 3, ASME VIII-2 structural stress).
adrs: 0078
---

A fillet weld fails through its throat, the narrowest section from the root to the
weld's face. The codes check it by a force per length of weld against the throat's
strength, not by a peak stress at the toe, which a finite element mesh can make as large
as it likes. So Puzzle's welds give a line force at each point of the weld, and the
checks below read that.

## The line force from the model

A light weld (the default, `formulation` line) is a connection, not a meshed bead. Its
legs lie on the two parts' faces. At each node along its path (a station), the nodes of
each leg nearby are fitted by a rigid motion, the way an RBE3 spreads a load, and six
springs join the two fits at one point: three in translation, of $E$ times the station's
share of the path, and three in rotation from the pairs' spread across the legs
(`adapters/weld_pairs.py`). A rigid motion stretches none of them, so the weld passes
force and moment exactly, and a fillet resists turning about its own line as a bead
does.

After the solve `post/weld_fields.py` gives at each station the line force $q$ (N/m) on
the other part, in the weld's frame: along the path, normal to the throat's plane, and
across the path in it; and the twist, the moment per length about the path. Measured
against hand statics (`tests/integration/test_welds_solved.py`): a lap joint's mean line
force is P/L +3.9 %; a bracket welded all round under a moment balances exactly with the
twist, about 7 % of the moment being the fillets' twist, which the elastic method
leaves out, so its normal line force is 6.8 % under the elastic method's $M c / I$.

## The throat and its strength

The effective throat of a fillet with legs $l_1$ and $l_2$ (0.707 $w$ for equal legs):

$$t_e = \frac{l_1 l_2}{\sqrt{l_1^2 + l_2^2}}$$

A partial-penetration groove weld's throat is its depth as given (AISC Table J2.1's
reductions for the process and groove angle are the user's to apply). By AISC 360-16
J2.4 the nominal strength per length is $F_{nw} t_e$ with $F_{nw} = 0.60 F_{EXX}$, the
filler's classification strength. Where the force crosses the weld at an angle θ to its
axis the weld is stronger, and with `directional` on Puzzle uses Eq. J2-5:

$$\frac{R_n}{l} = 0.60 F_{EXX}\left(1.0 + 0.50\sin^{1.5}\theta\right) t_e$$

The utilisation is $|q| / (\phi R_n/l)$ for LRFD with φ = 0.75, and
$|q| / ((R_n/l)/\Omega)$ for ASD with Ω = 2.00, at every station; the largest governs.
Use loads factored for the method you read.

$F_{EXX}$ is the weld's filler (a material with a tensile strength, such as the library's
Filler metal group), else the base metal's match: E70XX (482.6 MPa) for carbon and
low-alloy steel. Stainless steel is checked by AISC 370-21 J2 (the same $F_{nw}$, φ and
Ω) with E308L (517.1 MPa), E316L (482.6 MPa) or E2209 (689.5 MPa) chosen by the base
metal's name, as Design Guide 27's matching table. A complete-penetration groove weld is
not checked: its strength is the base metal's (J2.5).

## Aluminium welds

Aluminium follows the Aluminum Design Manual 2015, J.2. A fillet's strength per length is
the lesser of the weld metal on the throat and the heat-affected base metal on the leg:

$$\frac{R_n}{l} = \min\left(0.6 \cdot 0.85\,F_{tuw,filler}\,t_e,\ 0.6\,F_{tuw,base}\,l_{leg}\right)$$

the weld metal's term × 1.36 with `directional` where the force runs mostly across the
weld (Table J.2.2's note on transverse loading), φ = 0.75 and Ω = 1.95. $F_{tuw,base}$
is each part's [welded tensile strength](help:property:welded_tensile_strength); the
default filler is ER4043 (165 MPa). ADM groove welds aren't checked yet.

## Eccentric weld groups: the instantaneous centre

A weld group loaded off its centroid turns about some point, its instantaneous centre
(IC). The elements far from it deform most; the Manual's tables (Part 8) find the IC
where all the elements' forces balance the load, with each element's force-deformation
curve from its angle θ to the force:

$$\Delta_m = 0.209\,(\theta + 2)^{-0.32}\,w$$

$$\Delta_u = 1.087\,(\theta + 6)^{-0.65}\,w \leq 0.17\,w$$

$$F_{nw,i} = 0.60 F_{EXX}\left(1.0 + 0.50\sin^{1.5}\theta_i\right) f(p_i)$$

$$f(p) = \left[p\,(1.9 - 0.9\,p)\right]^{0.3}$$

with $p_i = \Delta_i/\Delta_{m,i}$, and the deformations $\Delta_i$ in proportion to the
distance from the IC, the critical element (least $\Delta_u/r$) at its $\Delta_u$.
`weld_strength.ic_capacity` searches first along the line through the centroid normal to
the load, then in 2D (MINPACK) when the group isn't symmetric; a load through the
centroid falls back to every element deforming alike, and a pure moment turns about the
centroid (`ic_moment_capacity`). It reproduces the 14th edition Manual's Table 8-4 to its
printed digits. Puzzle applies it to a fillet group whose stations lie in a plane, with
the solved force and moment about the centroid as the load, and says so when the group
is also bent out of its plane, which the IC method leaves out.

## Fatigue

Two methods, both from the line force's range between two steps (or from zero).

AISC 360-16 Appendix 3 gives the allowable stress range for $n$ cycles by the detail's
category, from Table A-3.1's constant $C_f$ and threshold $F_{TH}$:

$$F_{SR} = 6900\left(\frac{C_f}{n}\right)^{0.333} \geq F_{TH}$$

in MPa (Eq. A-3-1M), and for category F, shear on a fillet's throat,
$F_{SR} = 690\,(1.5/n)^{0.167} \geq 55$ MPa (Eq. A-3-2M). Category F (the default) reads
$|\Delta q|/t_e$ at every station. Categories A to E′ read the largest principal stress
range in the base metal at the weld's footprint nodes, which depends on the mesh as any
hot-spot reading does.

ASME VIII-2 5.5.5 uses the structural stress at the toe, which is mesh-insensitive when
it comes from nodal forces, as the weld's line forces are: membrane $f/t$ and bending
$6m/t^2$ through the attachment's thickness $t$, $f$ the line force into the attachment
and $m$ the twist. The range is made equivalent

$$\Delta S_{ess} = \frac{\Delta\sigma}{t_{ess}^{(2 - m_{ss})/(2 m_{ss})}\,I^{1/m_{ss}}\,f_M}$$

with $m_{ss}$ = 3.6, $t_{ess}$ the thickness held between 16 and 150 mm, the bending
ratio $R_b = |\Delta\sigma_b| / (|\Delta\sigma_m| + |\Delta\sigma_b|)$ in

$$I^{1/m_{ss}} = \frac{1.23 - 0.364 R_b - 0.17 R_b^2}{1.007 - 0.306 R_b - 0.178 R_b^2}$$

and $f_M = (1 - R)^{1/m_{ss}}$ for a high tensile mean (else 1). The life is

$$N = \frac{f_I}{f_E}\left(\frac{f_{MT}\,C}{\Delta S_{ess}}\right)^{1/h}$$

from Table 3-F.2M ($C$ and $h$ for steel or aluminium), on the lower 99 % curve (−3σ)
unless another basis is chosen. $\Delta\sigma$ is the elastic range times
$E/(1 - \nu^2)$, or Neuber's rule on the cyclic curve when its $K_{css}$ and $n_{css}$
are given. `side` plate reads the toe on the other part from that plate's own section.

## How Puzzle does it

Welds are made by the agent or with [weld_add](help:mcp:weld_add) (see
[Bolts and welds](help:guides/bolts-and-welds)). The J2 check and the IC run with every
weld result ([weld_results](help:mcp:weld_results), Window → Welds); fatigue on request
([weld_fatigue](help:mcp:weld_fatigue), `method` aisc or asme). Code: `weld_strength.py`
(the formulas), `post/weld_fields.py` (line forces, checks, fatigue),
`weld_generator.py` (welds from picked edges, and modelled beads, whose line force comes
from the section through the throat: 98.8 % of P at six elements across it).

## Limits

- Not checked: the base metal along the weld (AISC J4), Table J2.4's minimum sizes, and
  a CJP groove's base metal; they need the plates' thicknesses.
- The directional factor is the code's for linear welds and groups loaded in plane
  through their centre. Elsewhere leave `directional` off, or read the IC result.
- The line force is an elastic distribution, read station by station; the IC method's
  strength comes from the elements' load-deformation curves up to the critical one's
  ultimate deformation, so the two can differ on an eccentric group.
- Light welds join the legs' fits rigidly in their plane; stresses in the plates right
  at the weld are not a bead's. Model the bead (a modelled weld) where they matter.
- Toe categories A to E′ read a hot spot from the mesh: refine and compare, or use the
  ASME structural stress.

## Sources

- AISC 360-16, Specification for Structural Steel Buildings: J2.2 (effective areas),
  J2.4 and Eq. J2-5 (fillet strength and the directional factor), Table J2.5, J2.5;
  Appendix 3, Table A-3.1 and Eqs. A-3-1M, A-3-2M.
- AISC 360-10 J2.4(b) and the AISC Steel Construction Manual, 14th ed., Part 8 and
  Table 8-4 (the instantaneous centre; the test reproduces its coefficients).
- AISC 370-21, Specification for Structural Stainless Steel Buildings, J2; AISC Design
  Guide 27, Structural Stainless Steel, 2nd ed., Example 9 and its filler table.
- The Aluminum Association, Aluminum Design Manual 2015, J.2, Tables A.3.3, A.3.6 and
  J.2.2; Part VII Example 27.
- ASME Boiler and Pressure Vessel Code, Section VIII Division 2 (2023), 5.5.5 (Eqs.
  5.54-5.68) and Annex 3-F.2, Table 3-F.2M.
- AWS D1.1 (the matching E70 filler; the same fatigue categories), AWS A5.4/A5.9
  (stainless fillers).
