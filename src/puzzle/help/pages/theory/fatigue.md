---
title: Fatigue
section: theory
order: 80
keys: theory:fatigue
summary: How Puzzle's fatigue tool turns stresses into a life, a damage and a safety factor at every node, by stress life (Shigley) or strain life, with mean-stress corrections, notch factors and Miner's rule.
adrs: 0026
---

A part that carries a load many times can crack at a stress well below its strength.
Fatigue analysis asks how many cycles of a given loading a part survives, or by what
factor the loads could rise before it fails within its design life. Puzzle answers at
every node, from the stresses of one or more solved load cases, the way the Ansys
Fatigue Tool does.

## From a load case to an amplitude and a mean

Each block of the duty is a kind of cycle and how many of it there are per duty:

- **fully reversed**: the case's stress and its reverse, so the amplitude is the case's
  stress and the mean is zero;
- **zero-based**: the case's stress and nothing, so the amplitude and the mean are each
  half of it;
- **range**: between the two cases of a range combination, the amplitude half its range
  and the mean its mean.

A block's `scale` multiplies both. The stress tensors become numbers by the chosen
measure: signed von Mises (the default; von Mises given the sign of the principal stress
of largest magnitude, so compression doesn't count as tension), the maximum principal
stress, or one component. The amplitude takes the measure's magnitude, the mean keeps
its sign.

## The mean-stress correction

A tensile mean shortens life. Each correction finds the completely reversed stress
$\sigma_{rev}$ that is as damaging as the amplitude $\sigma_a$ with the mean $\sigma_m$,
by reading the criterion's line through $(\sigma_m, \sigma_a)$ on the amplitude axis.
Modified Goodman, the default:

$$\sigma_{rev} = \frac{\sigma_a}{1 - \sigma_m/S_{ut}}$$

Gerber divides by $1 - (\sigma_m/S_{ut})^2$, ASME-elliptic by
$\sqrt{1 - (\sigma_m/S_y)^2}$, Soderberg by $1 - \sigma_m/S_y$, and Smith-Dolan
multiplies by $(1 + \sigma_m/S_{ut})/(1 - \sigma_m/S_{ut})$. A compressive mean gets no
benefit ($\sigma_{rev} = \sigma_a$), and a mean that alone reaches the criterion's limit
gives zero life.

## Stress life: Shigley's S-N line

With no test data, the endurance limit of a steel specimen is estimated from its
ultimate strength, $S'_e = 0.5\,S_{ut}$ up to 1400 MPa and 700 MPa above; a measured
$S'_e$ replaces it (and is needed for other metals). The part's endurance limit applies
Marin's factors:

$$S_e = k_a k_b k_c k_d k_e k_f\,S'_e$$

- surface $k_a = a\,S_{ut}^b$ ($S_{ut}$ in MPa; machined 4.51 and −0.265, ground 1.58
  and −0.085, hot-rolled 57.7 and −0.718, as-forged 272 and −0.995; polished 1);
- size $k_b = (d/7.62)^{-0.107}$ for 2.79 to 51 mm and $1.51\,d^{-0.157}$ above (d in
  mm), with the effective diameter 0.370 d for a round that doesn't rotate and
  $0.808\sqrt{hb}$ for a rectangle in bending; 1 for axial loading or no size effect
  (the default);
- load $k_c = 1$ (see Limits);
- temperature $k_d$, Shigley's polynomial in °F from 70 to 1000 °F;
- reliability $k_e = 1 - 0.08\,z_a$ (50 % by default, $k_e$ = 1; 99 % gives 0.814);
- miscellaneous $k_f$ (plating, corrosion: the `misc_factor`, 1 by default).

The S-N line runs from $f S_{ut}$ at $10^3$ cycles to $S_e$ at $10^6$:

$$S_f = a N^b$$

$$a = \frac{(f S_{ut})^2}{S_e}$$

$$b = -\frac{1}{3}\log\frac{f S_{ut}}{S_e}$$

where $f$ comes from $\sigma'_F = S_{ut} + 345$ MPa (0.9 below 70 kpsi, 483 MPa). Below
$10^3$ cycles the line is $S_{ut} N^{(\log f)/3}$, and at or below $S_e$ the life is
infinite. The life model can instead read the material's own S-N curve (the Material
builder's Fatigue tab), log-log between its points: infinite below its lowest stress,
its first segment extended above its highest (with a warning).

## Strain life

For lives where plastic strain matters, the strain-life model reads the equivalent
strain amplitude $\varepsilon_a$ against Coffin-Manson-Basquin,

$$\varepsilon_a = \frac{\sigma'_f}{E}(2N)^b + \varepsilon'_f (2N)^c$$

inverted for $N$ by bisection, or Langer's form (NUREG/CR-6909),
$\ln N = A - B \ln(100\,\varepsilon_a - C)$. No mean-stress correction is applied in
strain life.

## Notches the mesh doesn't resolve

Where the mesh resolves a stress raiser, its stresses already carry it: leave Kf at 1.
Where it doesn't (a thread root, a small fillet not modelled), the fatigue notch factor
multiplies the amplitude:

$$K_f = 1 + q\,(K_t - 1)$$

given directly, or from $K_t$ and the notch radius $r$ by Neuber's notch sensitivity
$q = 1/(1 + \sqrt{a}/\sqrt{r})$, $\sqrt{a}$ a cubic in $S_{ut}$ (kpsi) for bending and
axial or for torsion, $r$ in inches. Neuber's constant is fitted for steels of 345 to
1720 MPa, and the tool warns outside that. Kf goes on the mean too only if asked
(`notch_on_mean`; Shigley puts it on the alternating stress). A modelled bolt's thread,
drawn smooth, takes its own Kf (its type's `thread_kf`, 3 by default) on its thread face.

## Miner's rule and the results

Each block's life $N_i$ at each node, for the material assigned there, adds up by
Miner's rule:

$$D = \sum_i \frac{n_i}{N_i}$$

- **Life** is $1/D$: repetitions of the duty to failure (cycles, for one block of one
  cycle), shown as $10^{12}$ where it is infinite.
- **Damage** is $D$ times the design life (repetitions of the duty; $10^6$ by default):
  above 1 the part fails within it.
- **Safety factor** is the factor on every stress that makes the life equal the design
  life, found by bisection between 1/15 and 15 and capped at 15, as Ansys does.

## How Puzzle does it

The fatigue tool is a derived result (Results → Combinations and fatigue → New fatigue
tool…, see [Results](help:guides/results)), evaluated from stored runs on demand
(`post/fatigue_tool.py` over `materials/fatigue.py`). It needs nodal stresses, a
material at every node with the strengths its choices use
([tensile strength](help:property:tensile_strength) for Goodman, Gerber, Smith-Dolan and
the S-N line; [yield strength](help:property:yield_strength) for ASME-elliptic and
Soderberg), and for strain life a stored strain field. Besides life, damage and safety
factor it stores the alternating and mean stress as the life model saw them (× Kf).

## Limits

- High-cycle, proportional loading only. Where a range's principal directions differ
  from its mean's by more than 15° at the critical node, the result says the loading
  looks non-proportional and is an estimate; critical-plane methods are out of scope.
- Puzzle takes $k_c = 1$ for every load and lets axial and torsional stresses in through
  the stress measure. Shigley's combined-loading method divides the axial part by
  0.85; for mostly axial loading the tool's stress is about 15 % lower (1 − 0.85)
  than that method's, so its life is longer.
- $S'_e = 0.5\,S_{ut}$ and Neuber's constants are for steels. For aluminium and other
  metals with no true endurance limit, use a measured S-N curve.
- The size factor isn't limited at the top of Shigley's range (254 mm).
- Miner's rule ignores load order. A safety factor of 15 means 15 or more, and one of
  1/15 means at most that.
- Lives come from the nodal stress the solver gives, so a coarse mesh at a stress
  raiser gives a long life; check the mesh where the life is least
  ([mesh convergence](help:theory/mesh-convergence)).

## Sources

- R. G. Budynas and J. K. Nisbett, Shigley's Mechanical Engineering Design, 10th ed.,
  ch. 6: Eq. 6-8 ($S'_e$), Eqs. 6-10 to 6-17 (the S-N line), Eq. 6-18 and Tables 6-2
  and 6-5 (Marin factors), Eqs. 6-20, 6-27 and 6-29 (size, temperature, reliability),
  Eqs. 6-32, 6-34 and 6-35 (Kf and Neuber's notch sensitivity), Miner's rule (Eq.
  6-58); `tests/test_fatigue_formulas.py` reproduces Examples 6-3, 6-6, 6-7 and 6-15.
- NUREG/CR-6909, Effect of LWR coolant environments on the fatigue life of reactor
  materials (Langer's strain-life form).
- Ansys Mechanical, Fatigue Tool (the per-node life, damage and safety factor, and the
  cap of 15).
