---
title: Seismic loads by ASCE 7
section: theory
order: 180
keys: theory:seismic
summary: How Puzzle turns a site's design spectrum into earthquake loads on a frame, by the equivalent lateral force procedure or a modal response spectrum combined by CQC, and checks the story drifts, all by ASCE 7-16.
adrs: 0079
---

An earthquake shakes a building's base. The building's mass resists the motion, and the
inertia forces load the frame. ASCE 7 replaces that dynamic problem with design loads.
The equivalent lateral force (ELF) procedure applies one set of story forces. Modal
response spectrum analysis loads each vibration mode by the design spectrum and
combines the modes. Both are reduced by the system's ductility R, and the story drifts
are then amplified by $C_d$ to estimate the real displacements.

## The design spectrum (11.4.6)

You give the site's $S_{DS}$ and $S_{D1}$ (in g) and the long-period transition $T_L$.
With $T_s = S_{D1} / S_{DS}$ and $T_0 = 0.2 T_s$, the spectral acceleration at period T
(Figure 11.4-1) is:

$$S_a = S_{DS} \left( 0.4 + 0.6 \frac{T}{T_0} \right) \quad \mathrm{for} \quad T < T_0$$

$$S_a = S_{DS} \quad \mathrm{for} \quad T_0 \leq T \leq T_s$$

$$S_a = \frac{S_{D1}}{T} \quad \mathrm{for} \quad T_s < T \leq T_L$$

$$S_a = \frac{S_{D1} T_L}{T^2} \quad \mathrm{for} \quad T > T_L$$

## The equivalent lateral force procedure (12.8)

**The period.** The approximate period is $T_a = C_t h_n^x$ (12.8-7), with $h_n$ the
height of the highest level above the base. $C_t$ and x come from Table 12.8-2 in SI:
0.0724 and 0.8 for steel moment frames, 0.0466 and 0.9 for concrete moment frames,
0.0731 and 0.75 for eccentrically braced and buckling-restrained frames, and 0.0488 and
0.75 otherwise. A computed period (the modal one, or the one you give as `period`) is
used, but no more than $C_u T_a$. $C_u$ comes from Table 12.8-1, interpolated: 1.7 at
$S_{D1} \leq 0.1$ down to 1.4 at 0.3 and above.

**The seismic coefficient.** With R the response modification coefficient and $I_e$
the importance factor:

$$C_s = \frac{S_{DS}}{R / I_e}$$

It is capped at $S_{D1} / (T R / I_e)$ for $T \leq T_L$ (12.8-3) or
$S_{D1} T_L / (T^2 R / I_e)$ beyond (12.8-4). It is at least
$\max(0.044 S_{DS} I_e, 0.01)$ (12.8-5). Where $S_1 \geq 0.6$ g it is also at least
$0.5 S_1 / (R / I_e)$ (12.8-6). Puzzle reports which equation governed.

**Base shear and its distribution.** $V = C_s W$, with W the effective seismic weight.
Each level x takes the share:

$$C_{vx} = \frac{w_x h_x^k}{\sum_i w_i h_i^k}$$

$$F_x = C_{vx} V$$

k is 1 for $T \leq 0.5$ s, 2 for $T \geq 2.5$ s, and linear between (12.8-12). The
story shear below each level is the sum of the forces above it.

**The weight W.** `frames/seismic_frame.py` gives each frame node half of each
attached member's self weight, $\rho A g L$. It adds the gravity loads of the design's
`mass_cases` with their factors: D at 1 by default, a storage live load at 0.25 if you
list it. Nodes are grouped into levels by elevation above the base, the lowest held
node. Weight at the base isn't in W. Puzzle writes the ELF forces as `force` loads in
case Ex or Ey, spread over each level's nodes in proportion to their weight.

## Modal response spectrum analysis (12.9.1)

A modal step with `response_spectrum` loads each mode n by the spectrum at its own
period. For a mode with participation factor $\Gamma_n$ in the direction, circular
frequency $\omega_n$ and shape $\phi_n$, the modal response is:

$$q_n = \frac{\Gamma_n S_a(T_n) g I_e}{R \omega_n^2}$$

The response is $q_n \phi_n$. On PyNite each mode's equivalent static loads,
$M \phi_n \Gamma_n S_a g I_e / R$, are solved directly.

**CQC (12.9.1.3).** The modes' peaks don't happen at once, so they are combined by the
complete quadratic combination, component by component:

$$R = \sqrt{\sum_i \sum_j \rho_{ij} R_i R_j}$$

For equal damping ζ (default 0.05, `damping`) and $r = \omega_j / \omega_i$, Der
Kiureghian's correlation coefficient is:

$$\rho_{ij} = \frac{8 \zeta^2 (1 + r) r^{1.5}}{(1 - r^2)^2 + 4 \zeta^2 r (1 + r)^2}$$

Well-separated modes have ρ near 0, and the rule becomes the square root of the sum of
squares. Close modes add.

**Scaling (12.9.1.4).** The modal base shear $V_t$ is the CQC of each mode's
$m_{eff} S_a g I_e / R$, or of its base reactions on PyNite. If $V_t$ is below
`scale_to_elf` × V (1.0 by default, as 7-16 requires), forces and stresses are scaled
up by $V / V_t$. V is the ELF base shear at the period of the mode with the most mass
in that direction, capped at $C_u T_a$. Displacements are not scaled by that factor.
Where $C_s$ came from 12.8-6 and $V_t < C_s W$, they are scaled by $C_s W / V_t$
(12.9.1.4.2).

**Signs.** CQC gives magnitudes. So a combination with Ex or Ey becomes two steps,
"(+E)" and "(−E)": the static loads plus and minus the factor times the envelope.

## Story drift (12.8.6, 12.12)

At each level's centre of mass (the nodes' displacements weighted by their seismic
weight), the design displacement is:

$$\delta_x = \frac{C_d \delta_{xe}}{I_e}$$

The story drift is $\Delta = \delta_x - \delta_{x-1}$. It is checked against
$\Delta_a$, the Table 12.12-1 ratio times the story height. The ratio depends on the
structure type and risk category: 0.020, 0.015 or 0.010 for "other" structures in risk
categories I/II, III and IV.

## How Puzzle does it

`frames/seismic.py` holds the pure procedures: `spectrum`, `seismic_coefficient`,
`elf`, `cqc` and `drifts`. `frames/seismic_frame.py` builds levels and weights from
the model and writes the ELF loads. PyNite runs the modal analysis and the spectrum
itself (`adapters/pynite`). On CalculiX and Code_Aster, `puzzle/spectra.py` combines
the modes they return, which carry participation factors and effective masses. The
parameters live in `Study.seismic`: $S_{DS}$, $S_{D1}$, $S_1$, $T_L$, R, $C_d$, $I_e$, risk
category, structure types, `mass_cases`, `scale_to_elf` and `damping`.

Set them in Model → Studies → Seismic design…, write the ELF loads with Model →
Studies → ELF loads…, and read the numbers with MCP [seismic_report](help:mcp:seismic_report) (or
[seismic_elf](help:mcp:seismic_elf)). Then combine with
[load combinations](help:mcp:load_combinations) and check members as in
[Frame member design](help:theory/frame-design). See
[Beams, shells and frames](help:guides/beams-shells-frames).

## Limits

- ASCE 7-16 only; 7-22's multi-period spectra are not built.
- Only the ELF procedure and modal response spectrum analysis are built. There is no
  check of when ELF is permitted (Table 12.6-1), no irregularity checks, and no
  accidental torsion.
- Directions x and y are taken one at a time. There is no 100 %/30 % combination of
  orthogonal effects (12.5).
- The redundancy factor ρ and the vertical effect $E_v = 0.2 S_{DS} D$ are yours to
  include in the combinations.
- On CalculiX and Code_Aster the ELF shear for scaling takes the frame's levels, as
  PyNite does (`spectra.elf_levels`): each member's weight half to each end node, the
  supports' half left out (12.7.2). A model with no frame is one level: W is the total
  mass times g and the height its z extent.
- Modes must capture most of the mass. Check `mass_participation`; ASCE 7 asks for
  enough modes to reach 100 % (12.9.1.1), and too few modes understate $V_t$.
- Drift uses elastic displacements from a linear analysis. P-Delta effects on drift
  (12.8.7) are not checked.

## Sources

- ASCE/SEI 7-16, Minimum Design Loads and Associated Criteria for Buildings and Other
  Structures: 11.4.6 (Figure 11.4-1), 12.8 (Eqs. 12.8-2 to 12.8-7, 12.8-11, 12.8-12;
  Tables 12.8-1 and 12.8-2), 12.8.6, 12.9.1 (12.9.1.3, 12.9.1.4), Table 12.12-1.
- E. L. Wilson, A. Der Kiureghian and E. P. Bayo, "A replacement for the SRSS method
  in seismic analysis," Earthquake Engineering and Structural Dynamics 9, 1981 (the
  CQC rule and Der Kiureghian's correlation coefficient for equal damping).
- A. K. Chopra, Dynamics of Structures (modal participation and effective modal mass;
  the cantilever's modes used by `tests/integration/test_seismic_frames.py`).
