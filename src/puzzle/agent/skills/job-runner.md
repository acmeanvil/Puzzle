---
name: job-runner
description: solves that outlive Puzzle (ADR 0067): pending jobs after a restart, picking them up, cancelling, how a job ended (out of memory, walltime, lost), and the runner preference
adr: 0067
---
Read this when a project opens with solves still out, when the user asks what is
running (here or on a host), when a run ended `out_of_memory`, `walltime_exceeded` or
`job_lost`, or when a run fails with "job runner on …".

With Settings > Solvers > "Run solves through the job runner" on (the default; POSIX
only), every solve is handed to Puzzle's job runner: a small program of its own, on this
computer or on the remote host (skill `remote-runs`). The solve belongs to the runner, so
it goes on when Puzzle closes, the laptop sleeps or the connection drops. Each project
keeps the jobs whose results are not in yet (`.puzzle/jobs.json`, working state, never
versioned or archived).

## Steps

1. `jobs_list()` lists the project's pending jobs: study, solver, where ("this computer"
   or a host's name), state, elapsed, the log's last line, the job's id. It asks only this
   computer's runner; `jobs_list(live=true)` also asks each remote host (slow: SSH).
2. A job that is `Finished`, `Failed` or still `Running` with nobody following it:
   `job_resume(job)` (its id or its study's name) picks it up. It is the same run, in the
   same run folder: Puzzle attaches to the job (it is not solved again), reads its whole
   log, brings the case home and stores the results as from any run. Finished sweep points
   and upstream studies are reused. Resume finished jobs first: their results are waiting.
3. A job you were asked to stop: `job_cancel(job)` (autonomy `act` or `act_in_budget`).
   The entry stays until it is resumed (which then reports the cancel) or the user
   dismisses it in the Jobs panel (Window → Jobs).
4. Tell the user what you found and did: which jobs, where, how each ended.

## How a job ended

| State | What it means | What to do |
| --- | --- | --- |
| Finished / Failed | the solver's own exit (Failed: its exit code ≠ 0) | read the log as for any run (skill `solve-progress`) |
| Out of memory (`out_of_memory`) | the scheduler stopped it for memory | ask for more memory (the study's remote memory) or a coarser mesh |
| Ran past its walltime (`walltime_exceeded`) | the scheduler's time limit | a longer walltime, or fewer steps |
| Lost (`job_lost`) | ended by the host (node failure, preemption, a cancel outside Puzzle) or no record says how | run it again; tell the user if it repeats |
| Unknown | the runner can't be reached, or doesn't know the job | `jobs_list(live=true)`; check the host (`host_check`) |

## Tools and arguments

- `jobs_list(live=false)` → `{jobs: [{job, study, solver, where, state, code, elapsed, last_line, ...}]}`.
- `job_resume(job)` → the run's outcome (`data.resumed` = the job id), or a proposal at
  autonomy `propose`; accepting it picks the job up.
- `job_cancel(job)`.

## Mistakes to avoid

- Don't `run_study` a study that has a pending job: that solves it again. Resume it.
- A job another Puzzle on this computer is following is refused (`busy`): leave it.
- Local jobs queue when the runner's cores are taken: two studies each asking for all the
  cores run one after the other. That is not a hang; the state says Queued.
- A run's timeout counts its running time only, not time in a queue.
- With the preference off, solves run inside Puzzle and stop when it closes; nothing is
  pending then.
- Results come home only after every file of the case matches the host's copy; then the
  host deletes it. A `transfer_failed` leaves the host's copy: resume to fetch it again.
