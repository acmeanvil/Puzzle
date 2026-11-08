# 0075. Worked examples for every physics and solver

- Status: Proposed (planned: built once the design is stable and on `main`)
- Date: 2026-11-08

## Context

Puzzle ships two examples (`examples/cantilever`, `examples/bracket_assembly`) and the
benchmarks in `benchmarks/`. The maintainer asked (testing notes, 2026-11-08) for fully
worked examples for each physics and solver type, each as a single component and as a
small assembly, offered as an optional part of the installation. They wait until the
design is stable and pushed to `main`, so they don't have to be rebuilt as the schema
moves; this record keeps the plan.

## Decision

1. **What an example is:** a project folder made by its own `build.py` (as the
   cantilever's), with:
   - a README: what it shows, the steps as a user would take them in the GUI, the
     measured results, and a closed-form or published answer where there is one;
   - stored results for the solver it was built for, so it opens ready to look at;
   - a test that builds it and checks the README's numbers.
2. **Where:** `examples/<physics>/<name>/`, packaged as an optional `puzzle-examples`
   conda package that the installer (ADR 0068) offers as a tick box; Help → Examples
   opens one as a copy, never in place.
3. **The planned list.** Each runs on the solvers in its last column; a shoot-out
   between them where more than one is listed.

| Physics | Single component | Small assembly | Solvers |
| --- | --- | --- | --- |
| Linear static | Cantilever with a tip load (exists) | Bracket bolted to a base plate (exists: bonded, bolted) | CalculiX, Code_Aster, Elmer, FEniCSx |
| Contact | Hertz cylinder on a plate | Press-fit hub on a shaft (Lamé) | CalculiX, Code_Aster |
| Plasticity | Notched bar pulled past yield | Bolted lap joint, bolts yielding | CalculiX, Code_Aster |
| Modal | Free-free plate, first modes | Frame of beams and a plate | CalculiX, Code_Aster, Elmer, FEniCSx |
| Buckling | Euler column | Stiffened panel (shells and beams) | CalculiX, Code_Aster |
| Transient dynamics | Cantilever with a sudden load | Two masses on springs (bolted bracket) | CalculiX, Code_Aster, Elmer, FEniCSx |
| Harmonic response | Plate driven at its first mode | Bracket on a shaker (base excitation, once built) | CalculiX, Code_Aster, Elmer, FEniCSx |
| Steady heat | Fin with convection | Heat sink on a chip (bonded contact) | all four FE solvers |
| Transient heat | Quenched cylinder | Brake disc and pad | all four FE solvers |
| Thermal stress | Bimetal strip | Pressure vessel: thermal then structural (transfer) | all four FE solvers |
| Electrostatics | Two-layer capacitor | Comb drive | Elmer, FEniCSx |
| Magnetostatics | Wire in air | C-core electromagnet | Elmer, FEniCSx |
| Eddy currents | Skin effect in a round wire | TEAM 7 plate and coil; induction-heated billet | Elmer, FEniCSx |
| Electro-thermal | Joule-heated bus bar | Coil heating its core (losses as heat) | Elmer, FEniCSx |
| Laminar flow | Channel flow | Flow past a heated block | Code_Saturne, OpenFOAM |
| Turbulent flow | Turbulent channel (k-ε, SST) | Pipe bend | Code_Saturne, OpenFOAM |
| Conjugate heat | Heated channel | Cold plate with channels | Code_Saturne, OpenFOAM, then a structural transfer |
| 2D models | Plate with a hole (Kirsch) | Axisymmetric press fit | all four FE solvers |
| Beams and shells | Portal frame of AISC sections | Tank: shell with a stiffening ring | CalculiX, Code_Aster |
| Fatigue | Notched shaft, S-N life | Welded bracket under a duty | any structural solver |
| Optimisation | Cantilever thickness against a stress limit | Bracket hole positions | any structural solver |
| Imported fields | Pressure map from a CSV | CFD wall temperature into a structure | as the target study |
| Custom PDE | Poisson on a square | Reaction-diffusion in two bodies | FEniCSx |

4. **Each README** follows one shape: the question, the model (geometry, mesh, materials,
   loads), the GUI steps, the answer and how close it is, what to try next.

## Consequences

- New users learn each area from a working project, and the examples double as broad
  regression tests.
- About 46 projects to build and keep current: built only once the schema is stable,
  and each checked by its test so a change that breaks one is seen.
- The installer gains an optional component; the base install stays small.

## Alternatives considered

- **Build them now:** the schema still moves; each change would mean rebuilding many.
- **Benchmarks only:** they check numbers but don't teach the workflow.
- **Download on demand from a release:** possible later; a conda package fits the
  installer that exists.
