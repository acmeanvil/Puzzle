# 0012. Solver line-up: CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-17

## Context

Every adapter costs translation work, a container image, benchmark cases and long-term maintenance. Early design notes listed eight or more solvers. Puzzle needs a set that covers structural, thermal, electromagnetic, custom-PDE and CFD work, with at least two solvers for each main physics so solver shoot-outs (ADR 0011) are possible.

## Decision

Puzzle supports five solvers. All five adapters exist with their capability matrices; translation is implemented phase by phase.

| Solver | Role in Puzzle | Input | Phase |
| --- | --- | --- | --- |
| CalculiX | Structural and thermal, general contact | `.inp` deck | 1 |
| Elmer | Electromagnetics, multiphysics, structural and thermal; bonded interfaces only | `.sif` + ElmerGrid mesh | 1 |
| Code_Aster | Nonlinear structural: friction contact, plasticity, large deformation; harmonic response | `.comm` + `.export`, MED | 3 |
| FEniCSx | Custom PDEs (equation-based modelling) and the second electromagnetics solver | Generated dolfinx script | 3 |
| Code_Saturne | CFD: incompressible flow, turbulence, non-isothermal flow | Case directory + `setup.xml`, MED | 4 |

Shoot-out pairs this line-up allows: CalculiX, Code_Aster and Elmer for structural and thermal studies; Elmer and FEniCSx for electromagnetics. CFD has one solver (ADR 0016 adds OpenFOAM as its partner).

Not supported for now: MYSTRAN, FreeFEM, GetDP and Palace. OpenFOAM was on this list until ADR 0016. The adapter plug-in API (ADR 0006) lets any of them be added later without changing Puzzle.

## Consequences

- A smaller adapter set gets each adapter fully benchmarked sooner.
- FEniCSx carries two jobs, so its adapter needs both a template library (EM, heat, elasticity) and a path for user-written UFL forms.
- Nastran-format input is not supported; users bringing Nastran decks need an import path through the model IR instead.
- CFD has no shoot-out partner until a second CFD solver is added.
- Code_Aster and Code_Saturne both use MED and come from the same ecosystem, which eases fluid-structure coupling later.

## Alternatives considered

- MYSTRAN for Nastran users: dropped for now; CalculiX and Code_Aster cover its structural range.
- FreeFEM for custom PDEs: overlaps with FEniCSx, which also serves electromagnetics.
- GetDP or Palace as the second EM solver: FEniCSx fills that role and is already in the line-up for custom PDEs.
- OpenFOAM for CFD: larger community, but Code_Saturne fits the MED-based toolchain and pairs naturally with Code_Aster.
