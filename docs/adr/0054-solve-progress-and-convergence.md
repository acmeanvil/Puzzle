# 0054. Show every solve's progress and convergence, live and afterwards

- Status: Accepted
- Date: 2026-10-30

## Context

A run shows nothing but its solver's log, streamed line by line into the Output panel (`jobs.runner` → `on_output`), and one timing line at the end (ADR 0053). There is no progress bar, no convergence plot and no record of how a run converged once it is over. Ansys (Solution Information: force convergence, substep and bisection marks, result trackers), COMSOL (progress window, convergence plot, probes while solving) and Nastran (.f06/.log epsilon, memory estimate) all show these, and engineers use them to tell a slow run from a stuck one and a hard model from a broken one.

On 2026-10-30 this cost real time. A Code_Aster run hung for 11 hours with nothing to show it, and the bolted bracket's frictional contact failed four ways before the log, read by hand, showed the cause: friction multipliers going singular after six cut-backs. A watchdog built outside Puzzle for that day's runs (progress bars, a sparkline, stall warnings, toasts) proved the value; this ADR brings the same thing into Puzzle, driven by the solvers' own convergence data.

Every solver already reports enough:

| Solver | Progress | Convergence |
| --- | --- | --- |
| CalculiX | `.sta`: step, increment, attempt, step and total time | `.cvg`: per iteration, residual force, correction, contact elements |
| Code_Aster | `Instant de calcul`, cut-back level | the Newton table: RESI_GLOB_RELA/MAXI, contact and friction columns |
| Elmer | time-step lines | `ComputeChange` nonlinear change, linear solver residuals |
| FEniCSx | `study.py` is ours | can print structured lines: Newton and PETSc KSP residuals |
| OpenFOAM | `Time = …` | `Initial residual` per field and iteration |
| Code_Saturne | time step | the listing's residual table per equation |

## Decision

1. **Typed progress events, solver-neutral** (`puzzle.progress`): `Stage` (mesh, write, solve, read, store), `Step` (k of n, time t of T), `Increment` (number, attempt, size, cut-back), `Iteration` (kind nonlinear, linear or contact; residuals and their criteria by name: force, displacement, contact, friction, a field's), `Converged`, `Diverged`, `Message` (level, text, translated), `Resource` (equations, degrees of freedom, memory, cores) and `Tracked` (a result tracker's value). Each adapter has a `progress` parser that turns its log lines and side files (CalculiX's `.sta` and `.cvg`, read as they grow) into events; FEniCSx's `study.py` prints them directly as tagged JSON lines. `run_study` gains `on_event` beside `on_output`. A parser never blocks or changes a run: an unparsed line is only a log line.
2. **The Solution panel**, a tab beside Output that comes forward when a run starts (Window → Solution):
   - the stage strip with each stage's time (ADR 0053's timings), and a progress bar from the step's time over its end time (or steps done for static and modal), with time left;
   - the **convergence plot**: each residual against its criterion on a log scale over cumulative iteration, criteria dashed, converged increments, cut-backs and new steps marked;
   - **time against increment**, so repeated cut-backs show as a stair that flattens;
   - **result trackers** (below) as value-over-time plots;
   - messages: warnings and errors from the log, Code_Aster's French in English (`progress.translate`), each linked to its log line;
   - resources: equations, memory, cores and processes;
   - a **stall warning** when no event arrives for a time (Settings > Solvers > Stall warning, 10 min by default) and a stop button; the run keeps going unless stopped.
3. **Result trackers in the first phase.** A `ResultProbe` (ADR 0032) marked `track = true` is followed while the solve runs: a point's displacement or temperature, a selection's maximum, a reaction force or a contact's status. CalculiX reads them from the `.frd` it writes each increment; Code_Aster prints them per increment in its convergence table (`SUIVI_DDL`) and keeps them in an observation table (`OBSERVATION`); Elmer through a `SaveScalars` solver, FEniCSx from `study.py`, OpenFOAM through a `probes` function object, Code_Saturne through its probes. A one-increment linear solve has nothing to track and says so.
4. **History kept with the run.** The events are stored with the run's results (a `progress` table in the results store, by run id), so a finished, failed or stopped run still shows its convergence. The CLI gets `puzzle progress RUN` (a summary, or `--plot` to a PNG), MCP `run_progress`, and `puzzle run` prints a one-line live summary instead of the whole log (`--log` for the old output).
5. **Diagnosis** (the Advisor, ADR 0040): known patterns in the events become plain advice. A singular pivot on a Lagrange multiplier means redundant constraints (a contact node also tied or held); one on a physical degree of freedom, a rigid-body motion; cut-backs to the limit, a load step too large or a model held only by friction; residuals that oscillate, contact chattering (try a smaller penalty, or ramping). Each pattern has a test built from a recorded log.
6. **Order:** CalculiX and Code_Aster first (events, panel, trackers, history), where nonlinear contact needs it most; then FEniCSx and Elmer; then OpenFOAM and Code_Saturne residuals; the diagnoses grow with each. This comes before the ADR 0050 bolt manager.

## Consequences

- A run can be told slow from stuck from broken while it runs, and a failed run explains itself afterwards; the agent reads the same events instead of guessing from log tails.
- Each adapter takes on a parser that must follow its solver's output format: parsers are tested on recorded logs from the benchmarks, so a solver upgrade that changes a format fails a test rather than the panel.
- The results store gains a table, so the store format moves on (ADR 0008's `stale_store` path handles older stores).
- Trackers make Code_Aster's and Elmer's decks a little longer when used, and CalculiX's `.frd` is read while still being written (only whole increment blocks are read).

## Alternatives considered

- **Keep the raw log only:** what we have; it hid an 11-hour hang and needed an expert to read a singular friction multiplier.
- **Plot from the log in the GUI alone, no events:** every client (CLI, agent, GUI) would parse logs separately, and nothing would be kept with the run.
- **Each solver's own monitor** (CalculiX's `.cvg` with its own plotting, foamMonitor, Code_Saturne's GUI): six different tools and looks, none in Puzzle, none comparable in a shoot-out.
- **Trackers in a later phase:** the maintainer wants them first; they are what tells a converging contact model from one drifting toward a wrong answer.

## Built

Phase 1 (2026-10-31): CalculiX and Code_Aster.

- **Events** (`puzzle.progress`): `Stage`, `Step`, `Increment`, `Iteration`, `Converged`, `Diverged`, `Message`, `Resource` and `Tracked`, as frozen dataclasses; `Recorder` stamps each with the seconds since the run started, writes it to the run folder's `progress.jsonl` as it comes (a killed run keeps its history) and passes it to `on_event`. `State` folds them into what a progress bar and a status line read (`fraction`, `line`); `summary` is what the CLI, MCP and the agent get. `Parser.safe_feed` and `safe` swallow a parser's own errors: a run never stops for one.
- **CalculiX** (`adapters/calculix/progress.py`) reads its log only. The log carries what `.sta` and `.cvg` hold (step, increment, attempt, step time; per iteration the largest residual force over the time-averaged force, the largest correction over the increment, the contact elements), and a log reaches Puzzle from a container as well as from a native run. Criteria are CalculiX's R_n = 0.005 and C_n = 0.01, or the deck's `*CONTROLS, PARAMETERS=FIELD`; step periods and `** puzzle step` tags come from the deck. The recorded bolted bracket matches its `.cvg` (14 280 % then 27 750 % force residual; 108 185 then 79 649 contact elements).
- **Code_Aster** (`adapters/code_aster/progress.py`): `Instant de calcul` and its cut-back level, the Newton table with its columns named from its own header (contact, friction and geometry loops, RESI_GLOB_RELA/MAXI, contact pressure, `SUIVI_DDL` trackers), the boxed alarms and errors (`<A>`, `<E>`, `<F>`, `<S>`, `<EXCEPTION>`, `<SolverError>`), `<Erreur>`/`<Action>` lines and zero pivots; `progress.translate` puts the known phrases into English and leaves the rest.
- **`run_study(on_event=, cancel=)`** and `Session.submit(on_event=, cancel=)`: stage events around the solver, each job's parser from `Adapter.progress(input_file, step, of)` (None for the solvers not done yet), the job runner feeding it each line and polling it when quiet, a stop (code `stopped`), the events kept in the results store's new `progress` table (one row per run; older stores just have no history, so the store format did not move) and in the run folder for failed and stopped runs.
- **Trackers** (`puzzle.trackers`): `ResultProbe.track`; point (nearest node), selection (max, min, mean) and reaction (summed) probes of displacement, temperature or reaction force. CalculiX's parser reads whole result blocks from the growing `.frd`; Code_Aster gets `SUIVI_DDL` columns (displacement components over a selection, nonlinear static steps) and reports their values at each converged increment. What can't be followed gets an info message and still works on the finished result. The Results ribbon's selection and reaction forms ask Follow: after the solve, or while solving too.
- **The Solution panel** (`gui/solution_panel.py`), a tab beside Output that comes forward when a run starts (Window → Solution, Ctrl+6): the progress bar with time left, the stages with their times, the convergence plot (each residual over its criterion, log scale, criterion line at 1, converged increments, cut-backs and new steps marked), time against increment (cut-backs drawn as a red stair), trackers against step time, messages in English (double-click finds the line in Output), equations, memory and cores, a stall warning (Settings > Solvers > Stall warning, 10 min by default) and Stop. Selecting a stored run shows its history.
- **CLI**: `puzzle run` prints one live status line (rewritten in place on a terminal; `--log` for the whole log); `puzzle progress PROJECT --study/--run/--folder [--plot PNG] [--json]`.
- **MCP** `run_progress(study, run=None)`: the newest attempt (failed, stopped or running included) with its diagnoses; the agent skill `solve-progress`.
- **Diagnoses** (`progress.diagnose`), shown by the Advisor's Run step for a failed last attempt: `friction_singular` (a pivot on LAGS_F), `redundant_constraints` (other Lagrange multipliers), `rigid_body` (a physical degree of freedom), `cutbacks` (three or more before giving up), `chattering` (a residual turning up and down for ten iterations or more), `iterations`. The bolted bracket's frictional failure of 2026-10-30 gives the first three, as read by hand that day.
- **Tests**: `tests/test_progress.py` on recorded logs in `tests/data/progress` (the bracket's contact on both solvers, the plastic bar on both); `tests/integration/test_progress_live.py` solves the plastic bar on both, checking ten converged increments, the stages and a tracked tip displacement equal to strain × L × t at every increment (and on CalculiX the root reaction against the closed-form stress).

Left for later phases: FEniCSx and Elmer events, then OpenFOAM and Code_Saturne residuals; Code_Aster point and reaction trackers (a node group per point, `FORC_NODA` sums) and `OBSERVATION` tables; translating more of Code_Aster's advice text.
