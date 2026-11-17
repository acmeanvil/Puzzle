---
title: Flow
section: verification
order: 70
keys: verification:cfd
summary: Laminar, heated and turbulent channel flow, their 2D and axisymmetric forms, and the Code_Saturne and OpenFOAM shoot-out, against Poiseuille, the energy balance, Nu = 8.235 and Dean's correlations.
adrs: 0011, 0012, 0016, 0048, 0090
---

Flow is checked on channels, where the answer is known: plane Poiseuille flow, the heat
balance and developed profile between uniformly heated plates, and Dean's correlations
for turbulent channels. Every benchmark runs on both CFD solvers, Code_Saturne and
OpenFOAM, which also run as a shoot-out pair on one mesh. All are steady but the temperature front, a time march on Code_Saturne.

| Benchmark | Reference | Solvers | Test tolerance | Measured |
| --- | --- | --- | --- | --- |
| Laminar channel | plane Poiseuille | Code_Saturne, OpenFOAM | peak 1 %, profile 1 %, dp/dx 2 % | peak −0.23 % and −0.21 %; dp/dx −0.95 % |
| Laminar shoot-out | each other, one mesh | Code_Saturne and OpenFOAM | velocity L2 1 %, peak 0.5 %, pressure L2 2 % | 0.16 %, 0.05 %, 0.13 % |
| Heated channel | energy balance; Nu = 8.235 | Code_Saturne, OpenFOAM | 1 %; 2 % of the profile | −0.5 %; 0.03 % and 0.02 % |
| Turbulent channel, Re 1e5 | Dean (1978) | Code_Saturne, OpenFOAM; k-ε and k-ω SST | dp/dx 10 %, Uc/U 3 % | −3.2 % to −8.2 %; −0.3 % to −2.6 % |
| Turbulent heated plates, Re 2e4 | energy balance | OpenFOAM, Code_Saturne; k-ε and k-ω SST | 1 % | −0.07 %, −0.06 %; +0.10 %, −0.07 % |
| 2D plane Poiseuille | plane Poiseuille | OpenFOAM, Code_Saturne | as 3D | peak −0.10 %, −0.21 % |
| 2D axisymmetric pipe | Hagen–Poiseuille | OpenFOAM, Code_Saturne | peak 1 %, profile 1 %, dp/dy 2 % | peak −0.25 %, −0.18 % |
| 2D heated plates | energy balance; Nu = 8.235 | OpenFOAM, Code_Saturne | 1 %; 2 % | +0.23 %, +0.22 %; 0.19 %, 0.09 % |
| Temperature front, transient | Ogata and Banks (1961) | Code_Saturne | 1 % of the rise | 0.53 %, 0.50 % |

The methods are described in [Flow](help:theory/cfd); setting up a flow study is in
[Analyses](help:guides/analyses). A test whose solver isn't installed is skipped, not
passed.

## Laminar channel

Water ($\rho$ = 998.2 kg/m³, $\mu$ = 1.002e-3 Pa·s) enters a channel h = 10 mm high and
100 mm long at a uniform U = 1 mm/s, so Re = 10 on the gap. The side faces are symmetry
planes, which makes the flow two-dimensional, and it develops within a few millimetres
into plane Poiseuille flow:

$$u(y) = \frac{6 U y (h - y)}{h^2}$$

$$\frac{dp}{dx} = -\frac{12 \mu U}{h^2}$$

so the centreline speed is 1.5 U and the gradient −0.1202 Pa/m. The project is
`benchmarks/channel_flow` (structured hexahedra, 0.5 mm, 37,989 cells).

`tests/integration/test_channel_flow.py` `test_poiseuille_profile_and_pressure_gradient`
runs it on Code_Saturne and OpenFOAM and reads cell values at their centres: the peak
velocity at x = 70 mm within 1 % of 1.5 U, the profile there within 1 % (relative L2),
and the pressure gradient between x = 50 and 80 mm within 2 %. Measured (the benchmark's
README, ADR 0016):

| Quantity | Code_Saturne 9.2 | OpenFOAM v2412 | Theory |
| --- | --- | --- | --- |
| Peak velocity ÷ U | 1.4966 (−0.23 %) | 1.4969 (−0.21 %) | 1.5 |
| Profile at 70 mm, L2 | 0.21 % | | parabola |
| dp/dx, 50 to 80 mm | −0.1191 Pa/m (−0.95 %) | | −0.1202 Pa/m |

The repo records OpenFOAM's peak velocity only; its profile and gradient pass the same
tolerances.

## Shoot-out

The benchmark study lists OpenFOAM as Code_Saturne's shoot-out partner (ADR 0011, 0016):
both run on one hexahedral mesh and their cell values are compared directly, by the
study's own metrics: velocity relative L2 within 1 %, its largest value within 0.5 %,
pressure relative L2 within 2 %.
`tests/integration/test_cfd_shootout.py` `test_channel_flow_shootout` checks both runs
share one mesh and every metric passes; it needs both solvers and is skipped otherwise.
ADR 0016 records velocity L2 0.16 %, peak 0.05 %, pressure L2 0.13 %.

## Heated channel

Air at 15 mm/s and 20 °C between plates h = 10 mm apart, 100 mm long, each plate adding
q = 10 W/m² (Re = 10), the side faces symmetry planes, 0.5 mm hexahedra, time-marched
to steady with a 2 s pseudo time step. Where the flow is fully developed the axial
gradient is uniform, axial conduction into and out of a slice cancels, and the bulk
(velocity-weighted) temperature rises at exactly

$$\frac{dT_b}{dx} = \frac{2 q}{\rho c_p U h}$$

With $\eta = y/h$, Poiseuille flow under a uniform wall flux has the developed profile
(Nu = 8.235 on the hydraulic diameter 2h; Shah and London)

$$T(\eta) - T(1/2) = \frac{12 q h}{k} \left( \frac{\eta^3}{6} - \frac{\eta^4}{12} - \frac{\eta}{12} + \frac{5}{192} \right)$$

`tests/integration/test_heated_channel.py` `test_heated_channel` (Code_Saturne,
OpenFOAM) holds the bulk gradient between 40 and 80 mm to 1 % of the energy balance and
the profile at 60 mm to 2 % of its wall-to-centre range. Near the inlet heat conducts
back out through the fixed-temperature inlet (the Péclet number is about 7), and near
the outlet's zero-gradient condition the balance does not hold, so those are left out.
ADR 0016 records the bulk gradient 109.5 K/m (Code_Saturne) and 109.53 K/m (OpenFOAM)
against 110.08 K/m (−0.5 %), and the profile error 0.03 % and 0.02 %.

## Turbulent channel

Water at a mean 5 m/s in a 20 mm channel 1 m long (Re = 1e5 on the full height), side
faces symmetry planes, 2 mm cells putting the first cells at $y^+$ ≈ 200, where wall
functions belong. Dean's correlations for plane channels:

$$C_f = 0.073 Re^{-1/4}$$

$$\frac{dp}{dx} = -\frac{C_f \rho U^2}{H}$$

$$\frac{U_c}{U} = 1.28 Re^{-0.0116}$$

`tests/integration/test_turbulent_channel.py` `test_turbulent_channel` runs k-ε and
k-ω SST with wall functions on Code_Saturne and OpenFOAM, and holds the pressure
gradient between 0.6 and 0.9 m to 10 % and the centreline ratio at 0.85 m to 3 %.
Measured (the test's docstring, ADR 0016):

| Model | Code_Saturne 9.2: dp/dx, Uc/U | OpenFOAM v2412: dp/dx, Uc/U |
| --- | --- | --- |
| k-ε | −7.0 %, −0.3 % | −6.5 %, −1.6 % |
| k-ω SST | −3.2 %, −1.9 % | −8.2 %, −2.6 % |

The two codes' wall functions and k-ε variants differ (Code_Saturne's is the
linear-production one), which is why they differ from each other by a few per cent. At
Re = 13,300 ($Re_\tau$ ≈ 390, air) both under-predicted the friction by 13-16 % on 2 mm
and 1.25 mm meshes alike: wall functions need a developed log layer, so the benchmark is
run at Re = 1e5.

## Turbulent heated plates

Air at 30 m/s between plates h = 10 mm apart, 100 mm long (Re = 2e4), both walls
adding q = 1000 W/m², a 2D planar region on 0.5 mm quadrilaterals ($y^+$ ≈ 30 at the
first cell), k-ε and k-ω SST with wall functions, 2000 iterations. Whatever the
profiles, the bulk temperature rises at $2 q / (ho c_p U h)$.
`tests/integration/test_turbulent_heated_channel.py`
`test_turbulent_heated_plates_balance_energy` takes the bulk over 20 mm bands of cells
about x = 40 and 80 mm (each cell weighted by u times its area) and holds the slope
between them to 1 % of the balance (ADR 0090 OF-1). Measured 2026-11-17, slope over the
balance:

| Model | OpenFOAM v2412 | Code_Saturne 9.2 |
| --- | --- | --- |
| k-ε | 0.9993 | 1.0010 |
| k-ω SST | 0.9994 | 0.9993 |

OpenFOAM's temperature is a passive scalar with diffusivity $
u/Pr + 
u_t/Pr_t$, and
under wall functions $
u_t$ is not zero on the wall face; with the wall gradient set to
$q/k$ the same case gave 1.56 (k-ε) and 1.47 (SST) times the balance, so the gradient is
now $q / (k + ho c_p 
u_t / Pr_t)$ face by face (`exprMixed`).

## 2D and axisymmetric flow

A 2D model is solved as its mesh extruded one cell thick by the adapter: planar faces
`empty` on OpenFOAM and symmetry on Code_Saturne, an axisymmetric model as a 5° wedge
(ADR 0048). `tests/integration/test_cfd_2d.py`, on OpenFOAM and Code_Saturne:

- `test_plane_poiseuille`: the laminar channel as a 2D region (10 mm gap, 50 mm long,
  Re 10) with the 3D benchmark's tolerances, peak and profile 1 %, dp/dx 2 %, and no
  velocity across the layer (under 1e-6 U).
- `test_hagen_poiseuille_axisymmetric`: a pipe R = 5 mm, 50 mm long, axisymmetric about
  Y, the same water and speed:

$$u(r) = 2 U \left( 1 - \frac{r^2}{R^2} \right)$$

$$\frac{dp}{dy} = -\frac{8 \mu U}{R^2}$$

  peak 2 U within 1 %, profile 1 %, gradient 2 %.
- `test_heated_plates_2d`: the heated channel in 2D, bulk gradient 1 % and profile 2 %.

Measured (ADR 0048):

| Case | Exact | OpenFOAM | Code_Saturne |
| --- | --- | --- | --- |
| Plane Poiseuille, peak u/U | 1.5 | −0.10 % | −0.21 % |
| Plane Poiseuille, dp/dx | −0.12024 Pa/m | −0.34 % | −0.45 % |
| Hagen–Poiseuille, peak u/U | 2.0 | −0.25 % | −0.18 % |
| Hagen–Poiseuille, dp/dy | −0.32064 Pa/m | +0.22 % | +0.17 % |
| Heated plates, bulk dT/dx | 110.081 K/m | +0.23 % | +0.22 % |
| Heated plates, Nu 8.235 profile | | 0.19 % | 0.09 % |

Code_Saturne's steady algorithm runs away on a wedge, so without a pseudo time step it
time-marches at 0.5 h/U there.

## A temperature front in plug flow

`tests/integration/test_cfd_transient.py` `test_a_temperature_front_in_plug_flow`
(Code_Saturne; OpenFOAM's adapter runs steady flow only): a 2D channel 200 mm long and
10 mm wide whose walls are symmetry planes, so the flow is uniform at U = 5 mm/s. Air
starts at 20 °C and the inlet is held at 30 °C from t = 0. Along the channel the
temperature then follows Ogata and Banks' solution on a half-line, with
$\alpha = k / (\rho c_p)$ and $s = 2 \sqrt{\alpha t}$:

$$\frac{T - T_0}{T_1 - T_0} = \frac{1}{2} \left( \mathrm{erfc} \frac{x - U t}{s} + e^{U x / \alpha} \mathrm{erfc} \frac{x + U t}{s} \right)$$

Cells are 1 mm, steps 50 ms (Courant 0.25), first order in time, and results come back
at 0, 10 and 20 s, one step per output time. The test checks the start (the physics'
initial temperature, to EnSight's single precision), the velocity (U to 1e-3) and the
temperature everywhere within 1 % of the rise.

Measured (Code_Saturne 9.2, 2026-11-15): the largest error 0.53 % of the rise at 10 s
and 0.50 % at 20 s.

## Sources

- A. Ogata and R. B. Banks, *A Solution of the Differential Equation of Longitudinal
  Dispersion in Porous Media*, U.S. Geological Survey Professional Paper 411-A, 1961.
- R. K. Shah and A. L. London, *Laminar Flow Forced Convection in Ducts*, Academic
  Press, 1978: plane Poiseuille flow, Nu = 8.235 between plates with uniform heat flux.
- R. B. Dean, "Reynolds number dependence of skin friction and other bulk flow
  variables in two-dimensional rectangular duct flow", *Journal of Fluids Engineering*
  100 (1978) 215-223.
- B. E. Launder and D. B. Spalding, "The numerical computation of turbulent flows",
  *Computer Methods in Applied Mechanics and Engineering* 3 (1974) 269-289: k-ε and
  wall functions.
- F. R. Menter, "Two-equation eddy-viscosity turbulence models for engineering
  applications", *AIAA Journal* 32 (1994) 1598-1605: k-ω SST.
