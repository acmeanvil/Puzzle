# 0011. Solver shoot-out: run one study on several solvers and compare

- Status: Proposed (future feature; the schema hook exists now)
- Date: 2026-10-17

## Context

Open-source solvers differ in formulation, element technology and defaults. Users moving from commercial tools need evidence that a result is trustworthy. Running the same study on two independent solvers and comparing the answers is a strong, cheap check, and it reuses the same machinery as Puzzle's benchmark suite.

## Decision

- A study may carry a `shootout`: one or more extra solvers plus comparison metrics.
- The study's own `solver` stays primary. Its results are the study's results, and transfers always read them. Shoot-out runs feed only a comparison report.
- Every solver gets the same mesh, so fields can be compared node by node when element types match, and by mapping otherwise.
- Metrics per quantity: maximum absolute difference, relative L2 difference, or relative difference of a scalar (eigenfrequencies, reaction forces, field energy), each with a tolerance. The report shows pass or fail per metric and plots the difference field.
- A shoot-out is only runnable when every listed solver passes the capability check for the study.
- Run keys cover the study setup without the solver choice, plus the solver and the sweep point. Adding a shoot-out never stales the primary result, and a shoot-out solver's runs survive a swap of the primary solver.

## Consequences

- Accuracy tracking becomes routine instead of a separate verification project.
- Disagreements surface translation bugs in adapters as well as solver differences.
- Compute cost multiplies by the number of solvers; shoot-outs are opt-in per study.
- A shoot-out needs at least two solvers with the capability: CalculiX, Code_Aster and Elmer for structural and thermal studies, Elmer and FEniCSx for electromagnetics (ADR 0012).

## Alternatives considered

- Duplicate the study per solver: works today, but loses the link between the runs and clutters the schematic.
- Benchmark suite only: verifies solvers on reference problems, not on the user's actual model.
