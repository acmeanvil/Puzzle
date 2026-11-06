# 0067. Hand every solve to a job runner, the same here and on remote hosts

- Status: Proposed
- Date: 2026-11-05

## Context

Puzzle hands a solve to a `Runtime` (ADR 0007, 0021): native, a container, or a remote
host over SSH. `run_job` starts the process, reads its log line by line, parses progress
(ADR 0054) and waits. `solve.py` does not know which runtime it got, so the boundary is
already in the right place. What sits behind it is too thin:

- **A solve lives only as long as the Puzzle that started it.** `run_job` is a blocking
  loop in the GUI's (or CLI's) process. The study's state "running" is only in the GUI's
  memory (`project/status.py`). Close Puzzle, let the laptop sleep, or lose the network,
  and a remote run is orphaned: its `ssh` session ends, the driver gets SIGHUP, and a
  Slurm job goes on with nobody to fetch its results. Nothing reattaches.
- **The remote job's state is tracked in shell.** `puzzle-run.sh` follows the scheduler
  with `squeue`/`sacct`/`qstat` and turns their text into an exit code. The review of ADR
  0021 found that this mistakes a failed `squeue` for the end of the job, reads an
  out-of-memory or node-failed job as a success, and lets text be injected through the job
  script's directives. Each is fixable in shell, but the logic belongs in a language with
  tests.
- **No one place manages solves.** Each run is one thread in one Puzzle. There is no
  queue across studies, no limit on how many solves share a machine's cores, and no view
  of "what is running on the lab machine now" from a second Puzzle, the CLI or an agent.
- **Logs cross the network whole.** Progress is parsed here, so a remote solver's log is
  streamed in full (capped at 256 MB) to be read line by line.

## Decision (proposed)

1. **A job runner, `puzzle-runner`: a small program that owns solves.** It takes a case
   folder, a command, a runtime (native, Docker, Podman, Apptainer), resources (processes,
   threads, memory, GPUs, walltime) and a scheduler (none, Slurm, PBS). It runs the job,
   keeps its state on disk (`<runner dir>/jobs/<id>/`: `job.json`, `solver.log`,
   `events.jsonl`), and answers questions about it. It is detached from whoever submitted
   the job: a solve outlives Puzzle, the SSH session and a sleeping laptop.
2. **The same runner here and there.** Locally Puzzle starts one in the background (a Unix
   socket in the user data folder, or a named pipe on Windows, owner-only). On a remote
   host it is started over SSH as `puzzle-runner serve`, speaking JSON lines on stdin and
   stdout. No ports are opened, and SSH stays the only authentication. Puzzle talks to both
   through one client, so "local" is just a host called `this computer`.
3. **One interface for Puzzle: submit, watch, fetch, cancel.** `Runtime` becomes an
   executor with `submit(case) → job`, `events(job, since)`, `fetch(job)`, `cancel(job)`
   and `jobs()`. `run_job` is rewritten over it, so `solve.py` and the adapters do not
   change. The in-process path stays as a fallback (tests, a machine where the runner
   can't start).
4. **Progress is parsed where the solver runs.** The runner holds the progress parsers
   (ADR 0054) and sends typed events. The log stays on the host and is fetched on demand,
   or whole on failure.
5. **Schedulers in Python, with tests.** Slurm and PBS states are read from `sacct`/`qstat`
   output into one set of states: queued, running, completed, failed, out of memory, node
   failed, timed out, cancelled. A failed query is retried, never taken as the end of the
   job. Directives are validated against each scheduler's grammar. Using PSI/J
   (ExaWorks' portable job interface, which already covers Slurm, PBS, LSF and Flux) under
   this layer is to be evaluated before writing our own.
6. **Queues and limits per host.** A runner starts jobs only while their cores, memory and
   GPUs fit what the host offers (`hosts.toml`), so several solves with different solvers
   can be queued at once and run side by side. With a scheduler, the scheduler decides and
   the runner only follows.
7. **Runs are recorded when submitted, not when finished.** A run record gains the host
   and the runner's job id, with state `submitted`/`running`. When Puzzle opens a project,
   it asks each host's runner about the jobs it is waiting for, reattaches to those still
   running, and fetches those that finished while it was away. The cache by run key is
   unchanged.
8. **One place to manage solves.** A Jobs panel (Window → Jobs) lists every host's jobs:
   queued and running, solver, study, elapsed time, the progress line, core-hours. It can
   cancel, fetch, open the log and reattach. Also CLI `puzzle jobs`, MCP `jobs_list` and
   `job_cancel` within the autonomy budget (ADR 0015).
9. **Installing it is Puzzle's job.** The runner is a subpackage that imports only the
   standard library and the progress parsers: no Qt, Gmsh, OCC, VTK or Delta Lake. On first
   contact with a host, Puzzle copies it there as one zipapp and checks its version on
   every connection. A host with pixi may install it as an environment instead. No
   administrator rights are needed.

## Consequences

- Solves survive Puzzle closing, so long or queued remote runs become practical, and an
  agent or the CLI can see and manage what a GUI started.
- The state machine of a job moves out of shell into tested Python. The scheduler bugs
  found in the ADR 0021 review go away with it.
- Local runs gain the same queue and limits: three studies started at once no longer
  fight for the same cores.
- New things to secure and version: a runner on every host, a protocol between two
  versions of Puzzle, and a local socket. The runner runs only what the user's own SSH
  account could already run, and refuses clients of a different protocol version.
- `run_job`'s synchronous shape hides the change from `solve.py`, but the GUI has to learn
  that a run can belong to a job submitted in an earlier session.
- More to test: the runner gets its own suite with fake schedulers (`tests/remote_fakes.py`
  already has fake ssh, sbatch and qsub).

## Phases

1. The runner locally: submit, watch, fetch and cancel behind `run_job`; jobs survive the
   GUI; run records carry the job id; reattach on open.
2. Remote over SSH: the zipapp copied and version-checked, JSON lines over stdio,
   progress parsed there, logs on demand.
3. Schedulers in Python (or through PSI/J), the per-host queue and limits, GPUs.
4. The Jobs panel, CLI and MCP tools, and the skill `remote-runs` updated.

## Open questions

- Whether PSI/J is a good fit (licence, dependencies, Windows) or a thin layer of our own
  is simpler.
- Whether the local runner should be on by default, or only when the user asks for runs
  that outlive Puzzle.
- How long a host keeps a finished job's case before cleaning it up.

## Alternatives considered

- **Fix the shell driver and stop there.** That fixes today's bugs (and is done now, ahead
  of this decision), but solves still die with Puzzle and nothing manages several at once.
- **`nohup`/`tmux` around today's driver.** That keeps a remote job alive, but leaves its
  state in files only shell understands, and gives no queue, limits or reattach.
- **HTCondor, Dask, Ray or Parsl as the job layer.** They are capable, but each needs a
  server or a cluster set up, and they are heavy for a single workstation. Parsl's and
  PSI/J's scheduler code may still be used under the runner.
- **A web service on the host.** It means a port to open, TLS and accounts to look after.
  SSH already gives authentication.
