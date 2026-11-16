---
title: Bolted joints
section: theory
order: 50
keys: theory:bolted-joints
summary: How a preloaded bolt shares a working load with the parts it clamps (VDI 2230), and how Puzzle models a bolt in a finite element model.
adrs: 0037, 0050, 0077
---

A tightened bolt and the parts it clamps act as two springs: the bolt is stretched, the
parts are squeezed. When a working load pulls the joint apart, the bolt feels only a
share of it, and the rest goes into unloading the parts. This note explains how that
share, the preload and the tightening torque are worked out by VDI 2230, and how a bolt
is put into a finite element model so the solver gives its forces directly.

## The joint as two springs

Each spring is described by its resilience δ (its give, m/N: the inverse of a stiffness).
The bolt's resilience is the sum of its pieces in series:

$$\delta_S = \delta_{SK} + \sum_i \delta_i + \delta_{Gew} + \delta_G + \delta_M$$

- the head, $\delta_{SK} = l_{SK} / (E_S A_N)$, with $l_{SK} = 0.5\,d$ for a hexagon
  head and $0.4\,d$ for a socket head cap screw;
- each shank cylinder, $\delta_i = l_i / (E_S A_i)$;
- the free loaded thread, $\delta_{Gew} = l_{Gew} / (E_S A_{d3})$, on the minor section;
- the engaged thread, $\delta_G = 0.5\,d / (E_S A_{d3})$;
- the nut or tapped hole, $\delta_M = l_M / (E_M A_N)$, with $l_M = 0.4\,d$ for a nut and
  $0.33\,d$ for a tapped thread.

Here $A_N = \pi d^2/4$, $A_{d3} = \pi d_3^2/4$, and the thread's sizes come from the
basic profile: $d_2 = d - 0.649519\,P$, $d_3 = d - 1.226869\,P$.

The clamped parts carry the bolt's force through a cone that spreads from the bearing
face under the head (diameter $d_W$) into the plates. Its half-angle depends on the
clamp length $l_K$ and the parts' outside diameter $D_A$, with $\beta_L = l_K/d_W$ and
$y = D_A/d_W$. For a bolt with a nut (two cones, $w = 1$):

$$\tan\varphi = 0.362 + 0.032 \ln(\beta_L/2) + 0.153 \ln y$$

and for a tapped hole (one cone, $w = 2$):

$$\tan\varphi = 0.348 + 0.013 \ln\beta_L + 0.193 \ln y$$

The cone reaches $D_{A,Gr} = d_W + w\,l_K \tan\varphi$. If the parts are wider than that,
the whole clamp length is cones; if they are narrower than $d_W$, a sleeve; between,
cones then a sleeve. For the cones alone, with $d_h$ the hole:

$$\delta_P = \frac{2 \ln\left[\frac{(d_W + d_h)(d_W + w l_K \tan\varphi - d_h)}{(d_W - d_h)(d_W + w l_K \tan\varphi + d_h)}\right]}{w E_P \pi d_h \tan\varphi}$$

## The load factor

With both resiliences known, the bolt's share of an axial working load $F_A$ is the load
factor Φ. For a load put in under the head and nut:

$$\Phi_K = \frac{\delta_P + (w - 1)\delta_M}{\delta_S + \delta_P}$$

A load put in nearer the interface loads the bolt less, by the load introduction factor
$n$ (VDI 2230 Table 2, joint types SV 1 to SV 6, read against $a_k/h$ and $l_A/h$):
$\Phi_n = n\,\Phi_K$. The bolt then carries $F_{SA} = \Phi_n F_A$ more, and the clamp
load at the interface drops by $(1 - \Phi_n) F_A$. The parts are usually stiffer than
the bolt, so Φ is small (VDI's Example B1: $\Phi_K$ = 0.161, $\Phi_n$ = 0.056): a
preloaded bolt sees little of the load and of its fluctuation, which is why preload
matters for fatigue. Once the interface opens, the bolt takes all of any further load.

## Preload and torque

The permissible assembly preload uses 90 % ($\nu = 0.9$) of the bolt's 0.2 % proof
stress under the combined tension and thread torsion of tightening, at the smallest
thread friction $\mu_G$:

$$\sigma_{Mzul} = \frac{\nu R_{p0.2}}{\sqrt{1 + 3\left[\frac{3}{2}\frac{d_2}{d_0}\left(\frac{P}{\pi d_2} + 1.155\mu_G\right)\right]^2}}$$

and $F_{Mzul} = \sigma_{Mzul} A_0$, where $d_0$ is the smallest section ($d_S$, the stress
diameter, for a bolt threaded to the head). The torque that gives a preload $F_M$ is

$$M_A = F_M \left(0.16 P + 0.58 d_2 \mu_G + \frac{D_{Km}}{2}\mu_K\right)$$

with $D_{Km} = (d_W + d_h)/2$ the mean diameter of the head's friction face. Dividing,
$K = M_A / (F_M d)$ is the short-form nut factor of $T = K F d$.

Two more effects reduce the preload. Embedding of the surfaces (VDI Table 5, by the
roughness $R_z$: the thread, each bearing face and each inner interface) loses
$F_Z = f_Z / (\delta_S + \delta_P)$. The tightening method's scatter, the tightening
factor $\alpha_A$ (Table A8: 1.1 for ultrasonic elongation control up to 4 for an impact
wrench), means the least preload is $F_{Mzul} / \alpha_A$.

In service the bolt's equivalent stress is
$\sigma_{red} = \sqrt{\sigma_z^2 + 3(k_\tau \tau)^2}$ with $k_\tau = 0.5$, and its
thread's endurance limit (a stress amplitude) is
$\sigma_{ASV} = 0.85\,(150/d + 45)$ N/mm² with $d$ in mm, for threads rolled before heat
treatment; rolled after, $(2 - F_{Sm}/F_{0.2})\,\sigma_{ASV}$.

## How Puzzle does it

`vdi2230.py` holds the guideline's formulas in SI and `vdi2230.check` puts them together
for one concentric bolt: both resiliences, Φ, $F_{Mzul}$ and its torque, embedding, and
with an axial load the bolt's load, its equivalent stress against $R_{p0.2}$, and the
fatigue safety against $\sigma_{ASV}$ for a load cycling from 0 to $F_A$ (amplitude
$\Phi F_A / 2A_S$). With $\alpha_A$ it adds the least preload and the residual clamp load
$F_{Mzul}/\alpha_A - (1 - \Phi)F_A - F_Z$. What isn't given is defaulted and named in its
notes: grade 10.9, $\mu_G = \mu_K = 0.12$, $R_z$ 16 µm, one inner interface, $n = 1$,
$d_W$ from the size's head table, $d_h$ an ISO 273 medium hole, $D_A = 3\,d_W$. It is
reached through [bolt_vdi2230](help:mcp:bolt_vdi2230); the functions for the eccentric
case ($\Phi^*_{en}$, $I_{Bers}$, Eq. 188's bending stress) are there but `check` doesn't
use them.

A bolt type's preload (see [Bolts and welds](help:guides/bolts-and-welds)) is a force, a
share of the yield load $\sigma_y A_s$ (70 % by default), or a torque through
$F = T/(K d)$ with $K = 0.2$ by default. The bolt table's stress is
$|N|/A_s + M/Z$ with $Z = \pi d^3/32$ on the stress area's diameter, against the grade's
proof and yield strengths.

In the model a bolt is one of three things.

- **A beam bolt** (the default): a two-node Timoshenko beam written exactly. Each end's
  spider ties the hole's nodes to a node at the bolt's middle, rigidly or by an RBE3
  average (distributing ends), and six springs join the two middle nodes in the bolt's
  frame: $EA/L$, $GJ/L$, $EI/L$ twice, and in shear $12EI / (L^3 (1 + \phi))$ with
  $\phi = 12EI/(G A_s L^2)$, $J = 2I$ and $A_s = \kappa A$, $\kappa = 6(1 + \nu)/(7 + 6\nu)$
  (`adapters/common.py`, `beam_bolt_stiffness`). The bolt's forces are its end faces'
  reactions summed: exact by equilibrium.
- **A solid bolt**: a small meshed round bar between the holes, its end faces rigid.
- **A modelled bolt** (ADR 0077): the bolt, nut and washers drawn from the fastener
  tables as bodies of revolution, the thread a smooth cylinder at $d_3/2$, with a section
  across the shank; its loads are read from the stress across that section. Measured: a
  40 kN pretension reads 39.79 kN at the section.

A pretension cuts the bolt. On a beam bolt, the axial spring's end is separated from its
node by an opening along the axis, the first degree of freedom of a reference node; on a
meshed section (a [bolt pretension](help:feature:bolt_pretension) feature), the mesh is
cut into twin nodes with $u_{twin} - u = n\,u_{ref}$, because CalculiX's own pretension
section left tetrahedra joined at their edges. The pretension force acts on the
reference node in its step; in later steps the opening is locked, so the bolt keeps the
length it reached and takes the working load as a real bolt does. On CalculiX and
Code_Aster a preload-only step goes first by default, so the preload is in before the
loads.

## Limits

- `check` is VDI 2230 for one cylindrical bolt loaded concentrically along its axis. It
  doesn't do R11 (the length of engagement; tapped joints default to 1.5 d), the slip
  check under transverse load, or an eccentric joint's opening.
- The cone formulas assume parts of one modulus around a single bolt. Closely spaced
  bolts share their cones; the FE model, not $\delta_P$, is the better guide there.
- $K$ and the friction coefficients scatter widely; a torque-specified preload is
  uncertain by $\alpha_A$, which the FE model doesn't know. Run the least and largest
  preloads if it matters.
- A beam bolt's spider makes the hole's rim rigid (rigid ends) or moves it on average
  (distributing ends): stresses right at the hole are not the real bearing stresses. Use
  a modelled bolt where those matter.
- The bolt table's stress is a nominal one on the stress area. It is not the thread
  root's peak, which [fatigue](help:theory/fatigue) treats with a notch factor.

## Sources

- VDI 2230 Part 1:2015, Systematic calculation of highly stressed bolted joints: joints
  with one cylindrical bolt. Section 5.1.1, Eqs. 20-31 (the bolt's resilience); 5.1.2
  (the clamped parts' cone and sleeve); 5.3 and Table 2 (the load factor and n);
  Table 5 (embedding); Eq. 127 (tightening torque); Eq. 144 (the permissible assembly
  preload); Eqs. 170-174 (the thread's endurance limit); Tables A1, A5, A8, A9; worked
  Examples B1, B2 and B5, which `tests/test_vdi2230.py` reproduces.
- R. G. Budynas and J. K. Nisbett, Shigley's Mechanical Engineering Design, 10th ed.,
  ch. 8 (the torque coefficient $K$, Eq. 8-27).
- G. R. Cowper, The shear coefficient in Timoshenko's beam theory, Journal of Applied
  Mechanics 33 (1966) 335-340 (κ for a round section).
- ISO 273 (clearance holes), ISO 898-1 (property classes), ISO 4014/4017/4032 (bearing
  diameters $d_W$).
