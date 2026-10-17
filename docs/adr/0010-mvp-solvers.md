# 0010. MVP solvers are CalculiX and Elmer; Elmer is the core multiphysics and electromagnetics solver

- Status: Proposed
- Date: 2026-10-17

## Context

The MVP must prove that the model IR (ADR 0006) really is solver-neutral, which needs at least two solvers with different input styles. Electromagnetics is a first-class goal for Puzzle, not a later add-on, and Elmer is the open-source solver that covers it alongside structural and thermal analysis.

What Elmer offers, from its Models Manual (January 2026 edition) and source (release 26.2):

| Physics | Elmer module | Notes |
| --- | --- | --- |
| Electrostatics | StatElecSolve | Capacitance and field energy |
| Static current conduction | StatCurrentSolve | Joule heating |
| Magnetic fields, 3D | MagnetoDynamics (WhitneyAVSolver, WhitneyAVHarmonicSolver, MagnetoDynamicsCalcFields) | A-V formulation, edge elements, transient and frequency domain, rigid motion |
| Magnetic fields, 2D and axisymmetric | MagnetoDynamics2D, MagnetoDynamics2DHarmonic | Lagrange elements, Joule losses for harmonic fields |
| Coils and circuits | CoilSolver, CircuitsAndDynamics | Coil current distribution; circuit coupling |
| High-frequency waves | VectorHelmholtz, EMWaveSolver | Frequency and time domain |
| Bonded interfaces | Mortar boundary conditions | Ties nonconformal meshes for elasticity, heat and magnetodynamics |
| Elasticity with contact | ElasticSolve with "Apply Contact BCs" | Stick, slip and friction exist in the source, documented only by test cases |

Elmer's contact support is documented almost only through its test cases (`fem/tests/Contact*`, `fem/tests/Mortar*`); the Models Manual's elasticity chapters don't cover it.

## Decision

Ship the MVP with two adapters:

- **Elmer** is a core solver from Phase 1. It is Puzzle's electromagnetics solver and also covers structural, thermal and coupled problems (Joule heating, thermal expansion).
- **CalculiX** covers structural and thermal analysis with Abaqus-style `.inp` decks, familiar to Ansys and Abaqus users.

Phase 1 physics on Elmer: electrostatics, electric currents, and magnetic fields (stationary and frequency domain, 3D and 2D axisymmetric), plus Joule heating coupled to heat transfer. Transient magnetics, circuits and wave problems follow in Phase 2.

Elmer is used for bonded interfaces only. Its mechanical contact types are listed as experimental: off by default, available behind an opt-in for users who want to try them. General mechanical contact runs on CalculiX and Code_Aster. When an electromagnetic problem also needs contact, Elmer solves the fields and a transfer passes the electromagnetic forces to a CalculiX contact study.

| Puzzle contact | Elmer | Status |
| --- | --- | --- |
| bonded, conformal | Shared nodes; no contact condition | Supported |
| bonded, nonconformal | Mortar BC with `Contact Type = tie` | Supported |
| frictionless | `Contact Type = slide` | Experimental |
| frictional | `Contact Type = friction` with friction coefficient | Experimental |
| rough | `Contact Type = stick` | Experimental |
| no separation | None | Not supported |

Elmer's mortar projectors also tie heat transfer and magnetodynamics across nonconformal interfaces, so a nonconformal bonded interface can carry all physics.

## Consequences

- Two very different input formats test the IR early.
- Electromagnetic studies have one solver until the FEniCSx adapter lands (ADR 0012); it is the second EM solver for shoot-outs (ADR 0011).
- The Elmer adapter picks modules by physics and dimension: WhitneyAV for 3D magnetics (edge elements only work in 3D), MagnetoDynamics2D for planar and axisymmetric, StressSolve for elasticity.
- Assemblies that need sliding or frictional contact run their structural study on CalculiX, fed by transfers from Elmer where fields are involved.
- Promoting an experimental Elmer contact type to supported needs benchmark cases that pass.
- Nonlinear analysis with large deformation waits for Code_Aster in Phase 3.

## Alternatives considered

- Code_Aster first: most capable for nonlinear structural work, but hardest to install, and it isn't an electromagnetics solver.
- One solver only: would not test the solver-neutral claim.
- CalculiX only for structures, Elmer only for electromagnetics: splits physics by solver and prevents comparing them on shared problems.
