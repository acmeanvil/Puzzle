---
title: How Puzzle is verified
section: verification
order: 10
keys: verification:index
summary: What the benchmarks check, where their answers come from, how close each solver must come, and how to run them yourself.
adrs: 0011, 0083
---

A result is only worth what its checks are worth. Every feature Puzzle claims for a
solver is backed by a benchmark: a model whose answer is known from a closed form, a
published reference or a standard's worked example, solved by Puzzle and compared with
a stated tolerance. A solver's capability matrix lists a feature only once its benchmark
passes; until then the adapter refuses it.

## What a benchmark is

- **A known answer.** A closed-form solution (a cantilever's tip deflection, Lamé's
  thick cylinder, Hertz contact, the series solution of transient conduction), a
  published benchmark (NAFEMS LE1, TEAM problem 7), or a worked example from a standard
  or handbook (VDI 2230, the AISC Manual, Shigley). Never a snapshot of an earlier run.
- **A tolerance stated in the test.** It allows for the mesh and the element, not for
  the method being wrong: a second-order solid on a closed form is usually held to 1 %
  or better, a contact pressure to a few per cent, a turbulence model to what the model
  itself can give.
- **Every solver that claims the feature.** Most tests run once per solver
  (`tests/solvers.py` `over(...)`), so CalculiX, Code_Aster, Elmer and FEniCSx are
  checked against the same answer, and often against each other on one mesh: the
  shoot-out (ADR 0011).
- **Measured values recorded.** Where a benchmark's README, its ADR or the project notes
  record what each solver gave, the pages here quote them; otherwise they say the test
  passes within its tolerance.

## The pages

- [Structural statics](help:verification/structural): beams, plates, shells, 2D
  models, element choices, local axes, body and remote loads, symmetry, plasticity.
- [Dynamics, buckling and explicit](help:verification/dynamics): modes, transient and
  harmonic response, buckling loads, prestress, explicit wave and impact problems.
- [Heat transfer and transfers](help:verification/heat): conduction, transient heat,
  temperature-dependent materials, heat generation, fields passed between studies and
  imported from files.
- [Electromagnetics](help:verification/electromagnetics): the capacitor, a wire in
  air, eddy currents, coils, TEAM 7.
- [Contact, bolts, threads and welds](help:verification/contact-and-joints): Hertz,
  press fits, frictional contact, pretension, modelled bolts, threads, welds, and the
  standards' worked examples.
- [Flow](help:verification/cfd): laminar, heated and turbulent channels on Code_Saturne
  and OpenFOAM.
- [Frames and design codes](help:verification/frames-and-design): frame analysis,
  P-Delta, member checks, seismic design, and AISC, ADM and stainless examples.
- [Meshing, convergence and studies](help:verification/meshing-and-studies): mesh
  convergence, error estimates, adaptive meshing, optimisation, result tools.

## Running them yourself

The benchmarks are in Puzzle's source tree, in `tests/integration/` (solves) and
`tests/` (formulas against worked examples). From a checkout, with the solvers' pixi
environments installed:

```sh
pixi run -e dev pytest tests/integration/test_nafems_le1.py   # one benchmark
pixi run -e dev pytest tests/integration -k hertz             # by name
pixi run -e dev check                                         # everything, with CalculiX
```

A test whose solver is not installed here is skipped, not failed, so a skip means
"not checked on this machine". Container solvers (Elmer, Code_Saturne, OpenFOAM, and
others where their image is present) run with Docker or Podman. Two projects are kept
as runnable benchmarks with their measured tables: `benchmarks/nafems_le1` and
`benchmarks/channel_flow`.

## Limits

- A benchmark checks the method on its model, not your model: the mesh, the supports and
  the material data of a real part are yours to check
  ([Mesh convergence](help:theory/mesh-convergence),
  [Error estimates](help:theory/error-estimates)).
- Tolerances are per benchmark. A feature that agrees to 0.1 % on a smooth beam can be
  off by more at a corner, a contact edge or a singularity.
- A solver refused for a feature has no benchmark for it: Puzzle says so before writing
  input rather than run it unchecked.

## Sources

- NAFEMS, *The Standard NAFEMS Benchmarks*, TNSB Rev. 3, 1990: LE1 and the linear
  elastic tests.
- P. J. Roache, *Verification and Validation in Computational Science and Engineering*,
  Hermosa Publishers, 1998: verification against known answers.
