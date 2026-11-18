# 0011. Solver shoot-out: run one study on several solvers and compare

- Status: Accepted (2026-10-25: built and in use); implemented: `puzzle.shootout.run_shootout`, 2026-10-20
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
- A shoot-out needs at least two solvers with the capability: CalculiX, Code_Aster and Elmer for structural and thermal studies, Elmer and FEniCSx for electromagnetics (ADR 0012), Code_Saturne and OpenFOAM for CFD (ADR 0016).

## Alternatives considered

- Duplicate the study per solver: works today, but loses the link between the runs and clutters the schematic.
- Benchmark suite only: verifies solvers on reference problems, not on the user's actual model.

## Amended (2026-11-18, ADR 0090 package 16)

The shoot-out had no way in from any client (ADR-7): `run_shootout` was
called only by two integration tests. Now it is a run command, `run_shootout`, in the
session (the autonomy level, the budget per fresh solve, the history entry, the version
before the run), behind:

- the GUI: Model → Run → Run shoot-out… and a study's right-click (`gui/solver_shootout.py`:
  the solvers whose capabilities take the study, in one form; the comparison as the
  Solver shoot-out tab);
- the CLI: `puzzle run PROJECT --study NAME --shootout [SOLVER ...]`;
- MCP: `solver_shootout(study, solvers)`.

`shootout.shootout_report` runs it: the solvers named for this run (else the study's
`shootout.solvers`), the study's metrics, else `shootout.default_metrics` (each solved
physics' first output node by node, relative L2 within 1 %; eigenfrequencies for modal
and buckling steps). Each solver's run is stored and reused as any run; the report is
kept as `results/shootout/<study id>.json`. Tests: `tests/test_solver_shootout.py`.
Not built: the difference field, and comparison by mapping when meshes or element types
differ (the solvers share one mesh, so ids match); both are on ADR 0072's list.
