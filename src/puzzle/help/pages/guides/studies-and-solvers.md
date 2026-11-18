---
title: Studies and solvers
section: guide
order: 100
keys: kind:study, section:studies, tree:step, kind:preset, section:solver presets, kind:transfer, section:transfers, panel:jobs
summary: Say what to solve and with which solver, tune how it solves, chain studies, run them here or on another machine, and follow their progress.
adrs: 0011, 0018, 0021, 0023, 0051, 0053, 0054, 0067, 0068, 0073, 0090
---

A study is one analysis of one component: its steps (static, modal, transient...), the
solver that runs them, and how. The model stays solver-neutral, so the same component
can be solved by CalculiX, Code_Aster, Elmer, FEniCSx or another solver by changing one
field.

## How to make a study

1. Model → Studies → New study… (or right-click Studies in the tree).
2. Choose the component, the solver and the analysis: stationary, transient, modal,
   buckling, frequency domain or explicit.
3. If the component has several physics, choose which this step solves. Most solvers
   solve one physics per step; chain studies with transfers instead.
4. Name it. The form refuses a solver that can't run that analysis, saying why.

To add a step, right-click the study, Add step. A step's settings (time step, end time,
modes...) are in Properties when the step is selected; Remove step deletes it. Model →
Studies → Duplicate study… copies one under a new name.

## Choosing a solver

[What each solver does](help:reference/capabilities) lists every analysis, load and
contact each solver takes. In short:

- **CalculiX** and **Code_Aster**: structures with contact, bolts, plasticity, beams and
  shells; heat.
- **Elmer** and **FEniCSx**: heat, electrostatics, magnetics, eddy currents; structures
  without sliding contact.
- **Code_Saturne** and **OpenFOAM**: incompressible flow. **PyNite**: frames.
  **OpenCourant**: explicit dynamics (impacts, drops).

<!-- key: form:solver_shootout -->
## How to compare solvers: the shoot-out

A **shoot-out** solves one study on its own solver and on others, all on one mesh, and
compares their results.

1. Model → Run → Run shoot-out… (or right-click the study, Run shoot-out…).
2. Choose the solver to compare with, and a second if you like. Only solvers whose
   capabilities take the study's physics and steps are offered.
3. Puzzle solves each in turn. Each run is stored and listed under the study as any run
   is, and reused when nothing changed.
4. The Solver shoot-out tab in the messages shows each comparison: the difference, the
   tolerance and whether they agree.

What is compared: the study's own shoot-out metrics when it has some (a quantity, a
norm and a tolerance each, in Properties under the study's shoot-out), else each
physics' first result node by node within 1 % (relative L2: the displacement, the
temperature, the velocity), or the frequencies for a modal or buckling study. The latest
report is kept with the results. The study's own solver stays the one whose results
transfers read.

## How to run

- Model → Run → Run study (Ctrl+R), or right-click the study, Run.
- Model → Run → Run all runs every study, one after another; a study another reads
  through a transfer goes first. One that fails doesn't stop the rest.
- Model → Run → Run selected… ticks the studies to run; Stop all stops the running
  study and the ones waiting.

A run that is still current is reused, not solved again. When the model changes, the
results bar says the result is out of date, with Run again beside it.

## How to follow a solve

Window → Solution (Ctrl+6) shows the solve as it goes: steps, increments and their
cut-backs, each iteration's residuals against their criteria, and the solver's warnings
(Code_Aster's French put in English). When a run fails, it shows a diagnosis with plain
advice, such as too-large steps, chattering contact or a body held by nothing.

A result tool whose Follow option is on is plotted while the solve runs. CalculiX tracks
all of them; Code_Aster up to four columns; FEniCSx and Elmer displacements and
temperatures.

The solver's own logs: right-click a run, Solver output…, or Results → View → Solver
output…. Errors and warnings are coloured, and Next problem jumps between them.

## Solver settings and presets

Model → Studies → Solver settings… (or right-click the study) has three layers, all
optional. An unset setting keeps the solver's default, so no result moves until you set
one.

1. **Shared settings**, translated by each solver: linear solver (direct or iterative),
   tolerance (1e-6), iterations per increment (50), output every n-th increment, and
   load stepping (increments, initial and minimum increment, fixed increments).
2. **Each solver's own**: CalculiX's equation solver and cut-backs, Code_Aster's
   method and line search, Elmer's relaxation, OpenFOAM's solvers...
3. **Expert pass-through** (Settings > Solvers at power-user level): lines placed in
   the solver's input as given. Each is placed or refused, never dropped.

Right-click a study, Save settings as preset…, to keep them; Apply settings preset puts
a preset on another study. [The solver settings](help:reference/settings) lists every
setting.

<!-- key: form:sweep_step -->
## Sweeps

A sweep runs a study once per point. Right-click a study, Materials…, to try several
materials at one material assignment in a table; a sweep of materials alone meshes
once. For parameters (a thickness at 8 and 12 mm), right-click a study, Add step →
Parametric sweep… (or Model → Studies → Add sweep step…): each parameter (a parameter
set's value, or a Number or Slider operator) is not swept, a list of values
(`8, 12 mm`), a range (from, to, how many) or a list operator's values; every
combination, or in pairs. The form shows how many points the study will run. A sweep
step's right-click Edit sweep… opens it again; sweep steps come before the solve
steps. Each
point is a stored run, grouped under "<study>: compare" in the tree. Results →
Combinations and fatigue → Compare sweep points… shows them side by side.

## Transfers between studies

A transfer passes one study's result into another: a temperature as a thermal load, a
static stress as prestress, losses as heat generation.

1. Model → Studies → New transfer… (or draw a wire from study to study in the
   schematic).
2. Choose the source study and its result, the study it goes into and its step.
3. Choose how it is used: as a load, as the starting state, or as prestress.
4. Choose how it is mapped: interpolated in the source's elements, or the nearest node.
   Two studies on one mesh just copy the values.

Run Model → Run → Run all, or run the source first.

## The job runner and remote hosts

With Settings > Solvers > Run solves through the job runner on (the default, on
macOS and Linux), each solve belongs to a small program of its own. It goes on when Puzzle
closes or the laptop sleeps. Window → Jobs (Ctrl+Shift+J) lists the project's pending
jobs: Resume picks one up (it is not solved again), Cancel stops it, Open log, Dismiss.

To solve on another machine over SSH, add it with Project → Remote hosts…, then Model →
Run → Run on… (or right-click a study). A host may run Slurm or PBS. The result is the
same as a local one and is reused either way.

On Windows the job runner is not used yet, for remote hosts too: a remote run is
followed while Puzzle is open, and comes home when it ends.

Settings > Solvers > Cores per run (automatic: 4, or one fewer than the physical cores
on smaller machines) and Parallel processes per run set what a solve may use; the
processes share those cores. Code_Aster takes both, as threads and MPI processes.

## Solvers on this computer

Settings > Solvers lists each solver and how a solve runs it here: natively from its
environment, in its container image, or missing. From the same page:

- **Build CalculiX with PARDISO now**: Intel MKL's PARDISO solver factors two to four
  times faster than SPOOLES, with the same answers. Its licence forbids shipping it
  built in, so Puzzle compiles CalculiX on your computer, in the background, from the
  environment the installer carries; it is used from then on.
- **Repair a solver environment…**: puts a solver's environment back as installed, the
  old folder restored if that fails.
- **Turn on WSL…** (Windows): solvers with no Windows build run in a Linux distribution
  of Puzzle's own, made from the solver's image.

`puzzle solvers` prints the same list in a terminal, `puzzle solvers --build` builds
CalculiX with PARDISO.

## Mistakes to avoid

- A static or transient structural study with nothing fixed is refused: Problems says
  unconstrained. Add a fixed or displacement support. Free modal studies are allowed
  on CalculiX, Elmer and FEniCSx (six modes near 0 Hz come first); Code_Aster refuses
  them.
- Running a study that has a pending job: that solves it again. Resume it in Jobs.
- A stall is not a failure: a big factorisation can be quiet for minutes.
- Load stepping on Elmer or FEniCSx, or a direct linear solver on Code_Saturne or
  OpenFOAM: refused. Leave the setting unset.
- A setting that can do nothing in its step is refused, never quietly dropped: load
  stepping outside a static structural step, a tolerance or iteration limit in a
  modal, buckling or harmonic step, output thinning where there are no increments.
- A coupling (thermal expansion, Joule heating, non-isothermal flow) on a solver that
  solves one physics per step: refused. Solve each physics in its own study and pass
  the field on with a transfer; only CalculiX solves temperature and displacement
  together in one step.
- Heat transfer in a flow with no temperature on its velocity inlet: refused on
  OpenFOAM and Code_Saturne alike. Give the inlet its temperature.

Every solver's adapter is checked feature by feature, step kind by step kind, against
its claims: it writes what it is given or refuses it with a code
(`tests/test_written_or_refused.py`, ADR 0090; `scripts/written_matrix.py` prints the
matrix).
- Changing settings on one step of a multi-step study only: set them on every solve
  step unless you mean otherwise.
- A large sweep grid is the product of its lists: check the count before running.

## For agents and scripts

The agent checks a study with [check_study](help:mcp:check_study), runs it with
[submit](help:mcp:submit), reads progress and logs with
[run_progress](help:mcp:run_progress) and [run_log](help:mcp:run_log), and changes
settings with [solver_settings](help:mcp:solver_settings) and
[solver_settings_set](help:mcp:solver_settings_set). Jobs:
[jobs_list](help:mcp:jobs_list), [job_resume](help:mcp:job_resume),
[job_cancel](help:mcp:job_cancel); hosts: [hosts_list](help:mcp:hosts_list),
[host_check](help:mcp:host_check); a shoot-out:
[solver_shootout](help:mcp:solver_shootout). In a terminal: `puzzle run` (with
`--shootout SOLVER` for a shoot-out)
([cli:run](help:cli:run)), `puzzle progress`, `puzzle log`, `puzzle jobs`,
`puzzle hosts`.

## See also

- [Analyses](help:guides/analyses)
- [Flow and convection](help:guides/fluid-flow)
- [Results](help:guides/results)
- [Optimisation and convergence](help:guides/optimisation-and-convergence)
- [Problem codes](help:reference/problems)
