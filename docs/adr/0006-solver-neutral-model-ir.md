# 0006. Solver-neutral model IR with a capability matrix

- Status: Proposed
- Date: 2026-10-17

## Context

Puzzle's central promise is that one model runs on many solvers. Solvers differ widely: input-deck solvers (CalculiX, Code_Aster, Elmer), a case-directory solver (Code_Saturne), and a code-generation target (FEniCSx).

## Decision

- The model is a typed, versioned schema (`puzzle.model`): parameters, geometry, materials, mesh recipes, components, physics features, studies and transfers.
- Quantities carry units and are converted to SI on export (pint). Many solvers are unitless, so units never reach them implicitly.
- Each solver adapter (`puzzle.adapters`) declares a capability matrix: supported physics, study step types and feature types. The GUI disables what the selected solver can't do, and `adapter.check()` reports unsupported items before a run.
- Adapters may read optional per-solver extension blocks for features with no neutral equivalent.
- Adapters register through the `puzzle.adapters` entry-point group, so third parties can add solvers without changing Puzzle.

## Consequences

- One model, many solvers, with honest feedback about gaps.
- Neutral schema design is hard: too narrow loses advanced features, too broad becomes a union of every solver. Extension blocks are the pressure valve.
- A benchmark suite must confirm that each adapter gives the reference answer before it claims a capability.

## Alternatives considered

- Pick one solver's format as the IR (for example Abaqus/CalculiX `.inp`): simple, but biased and poor for CFD and code generation.
- No IR, one GUI per solver: what exists today, and the problem Puzzle sets out to solve.
