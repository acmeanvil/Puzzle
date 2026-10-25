# 0016. Add OpenFOAM as the second CFD solver, paired with Code_Saturne

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-20

## Context

ADR 0012 left CFD with one solver, Code_Saturne, so CFD studies had no shoot-out partner (ADR 0011). Every other main physics has two or three. OpenFOAM was turned down there because Code_Saturne fits the MED toolchain better. That still holds for fluid-structure coupling with Code_Aster, but it doesn't stop OpenFOAM from serving as an independent check, and OpenFOAM has the larger user community.

There are two OpenFOAM lines: ESI-OpenCFD (openfoam.com, vYYMM) and the OpenFOAM Foundation (openfoam.org, numbered releases). Both are GPL-3.0. conda-forge packages the ESI line for linux-64 only (v2412). OpenCFD publishes Ubuntu packages, which build a container for macOS and Windows in under a minute.

## Decision

1. Puzzle adds an `openfoam` adapter for the ESI line, pinned to v2412. It runs natively from conda-forge on Linux, and in `containers/openfoam` (OpenCFD's Ubuntu packages) elsewhere.
2. The adapter takes the same features, values and settings as the Code_Saturne adapter, so one study runs on either solver without changes:
   - steady incompressible flow with simpleFoam (SIMPLEC);
   - laminar, k-epsilon or k-omega SST with wall functions;
   - temperature as a passive scalar (the `scalarTransport` function object), with fixed temperatures and wall heat fluxes;
   - velocity inlet, pressure outlet, wall and symmetry.
3. Meshes go in as Gmsh 2.2 through `gmshToFoam`, then `foamDictionary` sets the patch types. Results are read from OpenFOAM's ASCII field files and mapped to Puzzle's elements by cell centre. Pressure is stored in Pa (OpenFOAM's is p/ρ), like Code_Saturne's.
4. Code_Saturne stays the primary CFD solver and the route to Code_Aster coupling. OpenFOAM is its shoot-out partner first. Each new CFD capability should land in both adapters with a shared benchmark.
5. Shoot-outs now run: `puzzle.shootout.run_shootout` runs the primary solver, then each shoot-out solver on the same mesh, and evaluates the study's comparison metrics.

## Measured (2026-10-20, 0.5 mm hexahedra, 37,989 cells)

| Benchmark | Code_Saturne 9.2 | OpenFOAM v2412 | Exact |
| --- | --- | --- | --- |
| Laminar channel: peak velocity ÷ U | 1.4966 | 1.4969 | 1.5 |
| Heated channel: bulk dT/dx (K/m) | 109.5 | 109.53 | 110.08 (energy balance) |
| Heated channel: profile error | 0.03% | 0.02% | — |
| Turbulent channel, Re 1e5, k-epsilon: dp/dx, Uc/U | −7.0%, −0.3% | −6.5%, −1.6% | Dean (1978) |
| Turbulent channel, k-omega SST: dp/dx, Uc/U | −3.2%, −1.9% | −8.2%, −2.6% | Dean (1978) |

The two solvers agree cell by cell on the laminar channel (shoot-out, same mesh): velocity relative L2 0.16%, peak 0.05%, pressure relative L2 0.13%. With wall functions they differ by a few per cent (the wall-function formulations and the k-epsilon variants differ: Code_Saturne's is the linear-production one), which is the spread a turbulent shoot-out tolerance must allow.

## Consequences

- Every CFD benchmark can run as a shoot-out pair, so adapter translation bugs show up as disagreements.
- A sixth adapter to maintain, and a second image. The ESI release cadence is twice a year; the pin moves when conda-forge does.
- OpenFOAM's wider model range (multiphase, compressible, dynamic meshes) is reachable later through the same adapter, but each addition needs a benchmark first.
- Convection walls, transient runs and mass-flow inlets are refused for now; Code_Saturne still has them.

## Alternatives considered

- OpenFOAM Foundation line: not on conda-forge, so no native Linux path through pixi.
- SU2 as the second CFD solver: strong for compressible aerodynamics, weaker for the incompressible and thermal cases Puzzle benchmarks first.
- No second CFD solver (ADR 0012 as it stood): leaves CFD as the only physics without an independent check.
