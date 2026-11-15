---
title: Studies and solvers
section: guide
order: 100
keys: kind:study, section:studies, tree:step, kind:preset, section:solver presets, kind:transfer, section:transfers, panel:jobs
summary: Say what to solve and with which solver, tune how it solves, chain studies, run them here or on another machine, and follow their progress.
adrs: 0011, 0018, 0021, 0023, 0054, 0067
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

A **shoot-out** runs the same study on further solvers on one mesh and compares them by
the study's metrics. List the extra solvers in the study's Properties. The study's own
solver stays the one whose results transfers read.

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

## Sweeps

A sweep runs a study once per point. Right-click a study, Materials…, to try several
materials at one material assignment in a table; a sweep of materials alone meshes
once. Parameter sweeps (a thickness at 8 and 12 mm) are made through the agent. Each
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

Settings > Solvers > Cores per run (automatic: 4, or one fewer than the physical cores
on smaller machines) and Parallel processes per run set what a solve may use.

## Mistakes to avoid

- A static or transient structural study with nothing fixed is refused: Problems says
  unconstrained. Add a fixed or displacement support. Free modal studies are allowed.
- Running a study that has a pending job: that solves it again. Resume it in Jobs.
- A stall is not a failure: a big factorisation can be quiet for minutes.
- Load stepping on Elmer or FEniCSx, or a direct linear solver on Code_Saturne or
  OpenFOAM: refused. Leave the setting unset.
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
[host_check](help:mcp:host_check). In a terminal: `puzzle run`
([cli:run](help:cli:run)), `puzzle progress`, `puzzle log`, `puzzle jobs`,
`puzzle hosts`.

## See also

- [Analyses](help:guides/analyses)
- [Results](help:guides/results)
- [Optimisation and convergence](help:guides/optimisation-and-convergence)
- [Problem codes](help:reference/problems)
