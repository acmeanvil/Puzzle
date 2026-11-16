---
title: Structural dynamics
section: theory
order: 90
keys: theory:structural-dynamics
summary: How Puzzle finds a structure's natural modes, its response in time to a changing load, and its steady response to a load at one frequency.
adrs: 0041, 0079
---

A static solve assumes the load changes slowly next to the structure's own vibration.
When it doesn't (a shock, a sudden load, a rotating unbalance, a shaker table), inertia
and damping matter. Structural dynamics answers three questions: at which frequencies
does the structure ring (modal), how does it move in time after a load arrives
(transient), and how large is its steady response to a load at a given frequency
(harmonic, a Frequency domain step).

## The equation of motion

After meshing, every structural dynamics problem is the same set of equations, one per
degree of freedom:

$$\mathbf{M}\ddot{\mathbf{u}} + \mathbf{C}\dot{\mathbf{u}} + \mathbf{K}\mathbf{u} = \mathbf{F}(t)$$

$\mathbf{M}$ is the mass matrix (from each material's density, which is why dynamic
steps need one), $\mathbf{K}$ the stiffness, $\mathbf{C}$ the damping and $\mathbf{F}$
the loads. Without damping or load, the free vibrations solve the eigenproblem

$$\mathbf{K}\phi_n = \omega_n^2 \mathbf{M}\phi_n$$

which gives each mode's shape $\phi_n$ and angular frequency
$\omega_n = 2\pi f_n$. A Modal step returns these, one result step per mode.

## Damping

Puzzle's damping is Rayleigh's, proportional to mass and stiffness:

$$\mathbf{C} = \alpha \mathbf{M} + \beta \mathbf{K}$$

`rayleigh_alpha` is $\alpha$ (1/s) and `rayleigh_beta` is $\beta$ (s). In mode $n$ this
gives the damping ratio

$$\zeta_n = \frac{\alpha}{2\omega_n} + \frac{\beta\omega_n}{2}$$

so mass damping falls off with frequency and stiffness damping grows with it. To get a
ratio $\zeta$ in the first mode from mass damping alone, set $\alpha = 2\zeta\omega_1$;
the benchmarks do this for 5 %. One study has one pair (`common.rayleigh`); two dynamic
steps with different pairs are refused.

Code_Aster names the two the other way round: its `AMOR_ALPHA` multiplies the
stiffness and `AMOR_BETA` the mass. Puzzle writes them crosswise for you
(`code_aster/comm.py`). CalculiX takes them as `*DAMPING` with each material, Elmer as
`Rayleigh Damping Alpha/Beta` in the Material block.

A modal `damping_ratio` (the same $\zeta$ in every mode) only means something when the
response is built from modes, so it needs `method` modal; a direct solve with one is
refused with the reason (`common.check_dynamics`).

## Transient response: Newmark and HHT

A direct transient steps the equation of motion in time. Newmark's method updates
displacement and velocity from the accelerations at both ends of a step:

$$\mathbf{u}_{n+1} = \mathbf{u}_n + \Delta t\,\dot{\mathbf{u}}_n + \Delta t^2 \left( \left( \frac{1}{2} - \beta_N \right) \ddot{\mathbf{u}}_n + \beta_N \ddot{\mathbf{u}}_{n+1} \right)$$

$$\dot{\mathbf{u}}_{n+1} = \dot{\mathbf{u}}_n + \Delta t \left( (1 - \gamma) \ddot{\mathbf{u}}_n + \gamma \ddot{\mathbf{u}}_{n+1} \right)$$

Puzzle uses the average acceleration rule, $\beta_N = 1/4$ and $\gamma = 1/2$: it is
unconditionally stable, second-order accurate and adds no numerical damping. It does
stretch periods slightly: for a mode with $\omega\Delta t$ small,

$$\frac{\Delta T}{T} \approx \frac{(\omega \Delta t)^2}{12}$$

In the cantilever benchmark, 60 steps per first period left only about 9.6 steps per
period of the second mode, which came out 3.6 % long, and the peak read 2.5 % low. That
is the time step, not the solver: take about 20 steps per period of the highest mode
that matters (0.8 % elongation).

HHT-α (`hht_alpha`, from −1/3 to 0) damps the high modes numerically, which helps when
they are mesh noise. With $\alpha \leq 0$ the equation is balanced at a point inside
the step,

$$\mathbf{M}\ddot{\mathbf{u}}_{n+1} + (1+\alpha)\left( \mathbf{C}\dot{\mathbf{u}}_{n+1} + \mathbf{K}\mathbf{u}_{n+1} \right) - \alpha \left( \mathbf{C}\dot{\mathbf{u}}_n + \mathbf{K}\mathbf{u}_n \right) = \mathbf{F}(t_{n+1} + \alpha \Delta t)$$

with $\beta_N = (1-\alpha)^2/4$ and $\gamma = 1/2 - \alpha$. At $\alpha = 0$ it is
Newmark's average acceleration again. Puzzle's default is 0 on every solver, so the
response isn't damped by the scheme. CalculiX's own default (−0.05) is overridden; under
`geometric_nonlinearity` CalculiX and Code_Aster keep −0.05, because undamped Newmark
lost convergence on a strip swinging to large rotation (ADR 0082).

### The first step after a sudden load

A load without an amplitude acts at full value from t = 0. The scheme then needs the
acceleration at t = 0, which follows from the equation of motion:

$$\mathbf{M}\ddot{\mathbf{u}}_0 = \mathbf{F}(0) - \mathbf{K}\mathbf{u}_0 - \mathbf{C}\dot{\mathbf{u}}_0$$

FEniCSx solves this consistent acceleration first (`study.py` `solve_transient`), and
CalculiX and Code_Aster give the same history. Elmer's Bossak scheme (written with
Bossak's alpha 0, which is Newmark's average acceleration) starts from zero
acceleration instead. Its first step after a sudden load then moves about half as far
(−1.17e-5 m against −2.43e-5 m in the benchmark), and its history lags early on: up to
3.5 % of the peak at 0.22 of the first period with 5 % damping, 1.3 % on average. The
gap shrinks with the time step. A load ramped by an amplitude starts from zero force
and shows no lag (within 0.35 % on all four solvers). Elmer refuses `hht_alpha`: its
alpha method is Bossak's, which isn't HHT.

## Modal superposition

With `method` modal (CalculiX only), the response is built from the first `modes` modes
(default 20). Each mode is a single oscillator,

$$\ddot{q}_n + 2\zeta_n\omega_n\dot{q}_n + \omega_n^2 q_n = \phi_n^T \mathbf{F}(t)$$

with $\phi_n$ normalised to unit modal mass, and
$\mathbf{u} = \sum \phi_n q_n$. It is cheap, and the only way to give a
damping ratio per mode, but it leaves out whatever the modes not kept would carry: a
load that excites high modes, or a local stress near a load, needs more modes or the
direct method. Puzzle writes a `*FREQUENCY, STORAGE=YES` step (tagged as a helper)
before `*MODAL DYNAMIC`.

## Harmonic response

A Frequency domain step on solid mechanics assumes every load and every response
varies as $e^{i\omega t}$, so the time derivatives become factors of $i\omega$:

$$\left( \mathbf{K} + i\omega\mathbf{C} - \omega^2\mathbf{M} \right)\mathbf{U} = \mathbf{F}$$

$\mathbf{U}$ is complex: its size is the amplitude, its angle the phase against the
load. FEniCSx (`solve_harmonic`), Code_Aster (`DYNA_VIBRA`, `TYPE_CALCUL='HARM'`,
`BASE_CALCUL='PHYS'`) and Elmer (one scan per frequency) solve this at each frequency;
CalculiX has no direct harmonic solve, so it superposes modes (`*STEADY STATE
DYNAMICS`, one step per listed frequency). Without damping the response at a natural
frequency is infinite: include a frequency near one only with damping.

`common.harmonic_fields` stores three fields per frequency: `displacement_amplitude`
(the size, per component), `displacement_phase` (degrees) and `displacement`, the real
response at the instant the largest component peaks:

$$\mathbf{u}_{shown} = \mathrm{Re}\left( \mathbf{U} e^{-i\varphi_{peak}} \right)$$

At a resonance the real part alone is near zero (the response is 90° behind the load),
so the deformed view would show nothing; drawn at its peak it shows the shape.

## Response spectra

A Modal step with `response_spectrum` combines each mode's peak response from a design
spectrum instead of a time history (`puzzle/spectra.py`, on CalculiX and Code_Aster).
Each mode's response is its shape times its participation factor and the spectrum's
acceleration at its period; the modes are combined by the complete quadratic
combination (CQC), whose correlation between modes $i$ and $j$ at equal damping is
Der Kiureghian's. The spectrum, the scaling to the equivalent lateral force and the
drift are in [Seismic design](help:theory/seismic).

## How Puzzle does it

- Steps: `transient` on solid mechanics (`time_step`, `end_time`, `method`, `modes`,
  `hht_alpha`) and `frequency_domain` (`frequencies` as text, or `start_frequency`,
  `end_frequency`, `points`, `spacing`); see [Choosing an analysis](help:guides/analyses).
- CalculiX: `*DYNAMIC, DIRECT, ALPHA=0` with the given increment (without DIRECT it
  picked its own increments and the response came out half what it should be).
- Code_Aster: `DYNA_NON_LINE` with `SCHEMA='NEWMARK'`, or HHT when `hht_alpha` is set.
- FEniCSx: the stiffness and mass assembled once, held dofs eliminated, the effective
  matrix factored once by SciPy's sparse LU.
- Elmer: StressSolve with Bossak, Rayleigh damping in the Material.
- Loads follow an [amplitude](help:guides/loads-and-supports) in time; results are one
  step per output time, or one per frequency.

## Limits

- Linear: small deflection and elastic materials, except CalculiX's and Code_Aster's
  large-deflection transients (ADR 0082).
- Rayleigh damping fits a ratio at two frequencies at most; between and beyond them it
  is only an approximation of real damping.
- Modal superposition is CalculiX's alone; Code_Aster, FEniCSx and Elmer refuse it.
- No base excitation, frequency-dependent loads or stresses in harmonic steps yet.
- Impact and wave propagation over microseconds belong to
  [explicit dynamics](help:theory/explicit-dynamics).

## Sources

- N. M. Newmark, "A method of computation for structural dynamics", Journal of the
  Engineering Mechanics Division, ASCE, 85 (EM3), 1959, 67-94.
- H. M. Hilber, T. J. R. Hughes, R. L. Taylor, "Improved numerical dissipation for time
  integration algorithms in structural dynamics", Earthquake Engineering and Structural
  Dynamics 5, 1977, 283-292.
- W. L. Wood, M. Bossak, O. C. Zienkiewicz, "An alpha modification of Newmark's
  method", International Journal for Numerical Methods in Engineering 15, 1980,
  1562-1566.
- T. J. R. Hughes, The Finite Element Method: Linear Static and Dynamic Finite Element
  Analysis, Prentice-Hall 1987 (Dover 2000): Newmark's accuracy and period error.
- A. K. Chopra, Dynamics of Structures: Rayleigh damping, modal superposition,
  harmonic response.
- A. Der Kiureghian, "A response spectrum method for random vibration analysis of MDF
  systems", Earthquake Engineering and Structural Dynamics 9, 1981, 419-435.
- The benchmark: the cantilever's Euler-Bernoulli modes (βL = 1.875, 4.694, 7.855, ...),
  `tests/integration/test_dynamics.py`.
