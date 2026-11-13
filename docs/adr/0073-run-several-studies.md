# 0073. Run several studies one after another

- Status: Accepted; built
- Date: 2026-11-08

## Context

A project usually holds several studies (a thermal and the structural one it feeds, load
cases, solver variants), and each had to be started by hand, waiting for the last to end.
The maintainer asked (testing notes, 2026-11-08) for Run All, a way to select studies,
Run Selected and Stop All in the Studies right-click, with this behaviour: one study at a
time, in sequence, and a failure goes on to the next.

The job runner (ADR 0067) already queues local jobs for cores and memory, but it holds
solver jobs, not studies: a study's run also meshes, maps transfers and reads results in
Puzzle, and it should be possible to stop the rest of a list without touching the runner.

## Decision

1. **One shared order and loop** (`puzzle/run_queue.py`): `in_order(project, ids)` is
   the studies asked for (every study by default), a study another reads through a
   transfer before that one, otherwise the project's order; `run_all` runs them one by
   one, records a failure and goes on, and stops before the next when told to;
   `summary` gives one line ("3 studies: 2 finished, 1 failed (Modal)").
2. **GUI.** The Studies section's right-click and the Model menu: **Run all**, **Run
   selected…** (a list of the studies with tick boxes, the last choice ticked again) and
   **Stop all** (greyed when nothing is queued). The window keeps the queue: each study
   starts when the last one ends (`_start_run`'s `then`), shown as any run is (the
   Solution panel, the tree's running state, a toast each); at the end a toast and the
   status bar give the summary. Stop all cancels the running study and clears the rest.
   Studies asked for while the queue runs are added to its end.
3. **CLI.** `puzzle run PROJECT --all`, or `--study` given several times, runs them the
   same way; the exit status is 1 when any failed.
4. **Agents** run studies with `run_study` one at a time already (runs never go in a
   batch, ADR 0015), so they need nothing new; the skill says the order to use.

## Consequences

- A thermal-then-structural chain runs in the right order with one action, and a failed
  load case doesn't cost the rest of an overnight list.
- Only one study runs at a time from the queue; the runner's own queue (ADR 0067) still
  decides cores and memory inside each solve.
- A run started by hand while the queue runs is not part of it (it runs beside it, as
  before).

## Alternatives considered

- **Several studies at once:** the machine's cores are already shared inside one solve
  (ADR 0053); running them side by side would compete for the same cores and memory.
  Later, with solve helpers on other computers (ADR 0072), parallel runs make sense.
- **Queue in the job runner:** it would outlive Puzzle, but the runner holds solver jobs,
  not studies (meshing, transfers, reading results happen in Puzzle).
- **Stop on the first failure:** the maintainer asked for the next study to run instead.
