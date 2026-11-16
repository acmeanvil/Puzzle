---
title: Stress linearization
section: theory
order: 20
keys: theory:linearization
summary: Splits the stress along a line through a wall into membrane, bending and peak parts, with their equivalent stresses and the ASME VIII-2 elastic checks.
adrs: 0032, 0042
---

Pressure-vessel rules do not judge the peak stress at a point. They ask how the stress
varies through a wall: the average carries the load (membrane), the straight-line part
bends the wall (bending), and what is left is a local peak that matters for fatigue but
not for collapse. Stress linearization finds those three parts along a line drawn from
one surface of the wall to the other, the stress classification line (SCL).

## The idea

Take one stress component $\sigma(x)$ along the line, with $x$ from 0 at the start to
$t$, the line's length, at the end.

- The **membrane** part is its mean through the wall.
- The **bending** part is the straight line with the same first moment about the
  wall's middle: equal and opposite at the two surfaces, zero at the middle.
- The **peak** part is the rest: total less membrane and bending.

A wall in pure tension has only membrane stress; a beam in pure bending only bending;
a notch or a thermal skin adds peak.

## The equations

Each of the six components is linearized on its own (ASME VIII-2 Annex 5-A):

$$\sigma_m = \frac{1}{t} \int_0^t \sigma(x) dx$$

$$\sigma_b = \frac{6}{t^2} \int_0^t \sigma(x) \left(\frac{t}{2} - x\right) dx$$

$\sigma_b$ is the bending stress at the start; at the end it is $-\sigma_b$. The peak is
what is left at each end:

$$\sigma_p = \sigma - \sigma_m \mp \sigma_b$$

The linearized stress along the line, drawn in the plot, is
$\sigma_m + \sigma_b (1 - 2x/t)$.

The integrals use Simpson's rule over evenly spaced points, $h$ apart:

$$\int_0^t f dx \approx \frac{h}{3}\left(f_0 + 4 f_1 + 2 f_2 + \ldots + 4 f_{n-2} + f_{n-1}\right)$$

It is exact for integrands up to cubic: the membrane of a cubic stress, the bending of
a quadratic one.

## Axes and which components bend

The components can be given in the global axes or in the line's own (`line_frame`):

- **n**, through the wall, along the line;
- **h**, the hoop direction: the one given, else the global axis most across the line
  (on a 2D model, the axis out of the plane), made square to n;
- **t** = h × n, the meridional direction.

With every component bending (Bending: all), the split is as above for all six. With
ASME bending (Bending: normal, line axes only), bending is kept only for the meridional
and hoop normal stresses, as Annex 5-A (5-A.4.1.2) asks: the stress along the line and
the shears get membrane and peak only, the linear part of them going to the peak.

## Equivalent stresses

For each part (membrane, bending, membrane + bending, peak, total) at both ends, Puzzle
gives the six components, the principal stresses $S_1 \geq S_2 \geq S_3$, the von Mises
stress (VIII-2 since 2007) and the stress intensity of the older rules, Tresca's:

$$S = S_1 - S_3$$

The equivalents do not depend on the axes, only on which components carry bending. The
tool's value is the larger membrane + bending equivalent of the two ends, of the kind
chosen.

## The checks

Given an allowable stress S, Puzzle applies VIII-2 5.2.2.4's elastic checks with the
chosen equivalent:

- primary membrane: $P_m \leq S$ (general) or $P_L \leq 1.5 S$ (local, when the
  membrane is set to local);
- primary membrane + bending: $P_L + P_b \leq 1.5 S$;
- primary + secondary: $P_L + P_b + Q \leq 3 S$.

Each check gives the value, the limit and their ratio. Which load is primary and which
is secondary is the engineer's call: the numbers are the linearized stresses of the run
read.

## How Puzzle does it

A result tool of type Linearized stress (`post/probes.py` `linearized`, the arithmetic
in `post/linearize.py`) reads the run's nodal stress at evenly spaced points along a
straight line, by VTK interpolation on the undeformed mesh. The count is made odd for
Simpson's rule (the form's default is 47 points through the wall, as Ansys uses). The
two end points are read a hair inside the surfaces; a line that leaves the model
anywhere is refused, so draw it across one wall, surface to surface.

The settings are on the tool: Equivalent (von Mises or Tresca), Components (global or
the line's axes), Hoop direction, Bending (all or the ASME normal ones), Allowable and
Membrane (general or local). The breakdown is the Linearization tab in Messages. See
[Results](help:guides/results), and [probes](help:mcp:probes) for agents.

Checked against a Lamé thick cylinder (hoop membrane $p r_i / t$ to 1e-4) and, on a
CalculiX cantilever across its depth, bending within 2 % of $Mc/I$ with no membrane.

## Limits

- The line is straight and must cross the wall roughly square to both surfaces.
  Annex 5-A's guidance on where to place it still applies.
- The input is the solver's nodal stress, averaged at the nodes (see
  [Stress results](help:theory/stress-results)). Near a notch, the peak part depends on
  the mesh; membrane and bending converge much faster.
- The checks compare the membrane + bending of one run. VIII-2's $P_L + P_b + Q$ limit
  is on the range over the operating cycle: combine the cases into a range first
  ([Stress results](help:theory/stress-results)), then linearize the range.
- $S_{PL} = 1.5 S$ and $S_{PS} = 3 S$ here. Where Part 5 allows a limit from the yield
  strength instead, compare by hand.
- Elastic analysis only: the checks are not the limit-load or elastic-plastic methods
  of Part 5.

## Sources

- ASME Boiler and Pressure Vessel Code, Section VIII, Division 2, Part 5: 5.2.2.4
  (protection against plastic collapse, elastic stress analysis) and Annex 5-A
  (linearization of stress results for stress classification; 5-A.4.1.2 on which
  components get bending).
- J. L. Hechmer and G. L. Hollinger, *3D Stress Criteria Guidelines for Application*,
  WRC Bulletin 429, Welding Research Council, 1998.
- Ansys Mechanical APDL Command Reference: PRSECT (linearized stresses along a path;
  47 points).
