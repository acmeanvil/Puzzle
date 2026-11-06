# 0067. Hand every solve to a job runner, the same here and on remote hosts

- Status: Accepted (2026-11-05); built (2026-11-05)
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

## Decision

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

## Built (2026-11-05)

- **The runner** (`src/puzzle/runner/`, standard library only, Python 3.10+) is a daemon of
  its own: one per user and protocol version (`<dir>/p1/`), jobs as JSON records written
  atomically, JSON lines over a Unix socket (owner-only; a short path in the temp folder
  when the folder's is too long for a socket). Requests: hello (protocol checked), submit,
  status, log (by byte offset, so only new text crosses), cancel, manifest, forget (the
  case deleted only when the client marked it owned and the job has ended). It stops after
  15 minutes with nothing running and no client.
- **Jobs without a scheduler** are its own processes, each in a session of its own (a
  cancel stops the solver's children too; SIGKILL after 10 s). Each runs `puzzle-solve.sh`,
  which writes `solver.log` and its own exit status (`puzzle-job.rc`), so how a job ended
  survives the runner stopping. They queue for the runner's cores (this computer's physical
  cores, a host's `cores`) and memory; a scheduler queues its own.
- **Schedulers through PSI/J** (Slurm, PBS; LSF wired but untested). Found while building:
  - PSI/J's executors package imports its local executor, which needs psutil, so the
    runner loads PSI/J without it;
  - its Slurm script always exits 0, so sacct's exit code says nothing about the solve:
    only `puzzle-job.rc` (else PSI/J's `.ec`) does, and a job counts as a success only
    when one of them says 0;
  - one squeue line it can't parse (a reason with spaces, an unknown reason) or a PBS id
    with no history fails every job's poll, so the runner parses both tolerantly;
  - a queue that keeps failing makes PSI/J fail every job: the runner reattaches instead,
    for up to 10 minutes;
  - sacct can lag behind the queue, so a state that hasn't ended is asked again for up to
    60 s;
  - PSI/J leaves its scripts and outputs behind, so the runner removes them.
  Polling every `PUZZLE_RUNNER_QUEUE_POLL` s (10), giving up after
  `PUZZLE_RUNNER_QUEUE_ERRORS` failures in a row (12).
- **On a host**, `build.bundle()` packs the runner and PSI/J's pure-Python tree (from the
  pixi `runner` environment) into one archive, named by its hash. Puzzle sends it over
  SSH once, unpacks it into a temporary folder and moves that into place. It then runs
  `python3 -I -S main.py connect` (the host's `python`, else its pixi environments' or
  the first Python 3.10+), isolated so the host's own packages never shadow the bundle's.
  Without the `runner` environment the bundle holds the runner alone, which refuses
  scheduler jobs (`unsupported`).
- **Puzzle's side.** `run_job` hands the solve to `service.service_for(runtime)`:
  `LocalService` runs it in the run folder itself, and `RemoteService` stages it with rsync.
  Either way it submits, follows the log and state, and cancels. A remote case comes back
  into a temporary folder, is checked against the runner's manifest, and each file is
  moved into the run folder; only then is the host's copy deleted (`transfer_failed`
  otherwise, with the host's copy kept). The timeout and the core-hours count running
  time only, not time in a queue (ADR 0021's leftover). The runner's endings become
  codes: `out_of_memory`, `walltime_exceeded`, `job_lost`.
- **Pending jobs and resuming.** A run's job is recorded in `.puzzle/jobs.json` when it is
  submitted and removed when its results are in. The file is left out of Save as copies
  and archives, and each entry names the process following it. `Session.resume(job)` (a
  `run_study` command with `resume`, so it is proposed at "propose" and picked up on
  accepting) re-enters the same run folder attached to the job, with input written to a
  scratch folder so a running job's files are left alone; finished points and upstream
  studies are reused. A job another live Puzzle follows is refused (`busy`).
- **Managing them.** The Jobs panel (Window → Jobs: study, solver, where, state, elapsed,
  last line; Resume, Resume all, Cancel, Open log, Dismiss) and, on opening a project with
  pending jobs, a toast offering to pick them up. Also CLI `puzzle jobs`, MCP `jobs_list`,
  `job_resume` and `job_cancel` (agents at act only), and the skill `job-runner`.
- **Tests:**
  - `tests/test_runner.py`: the daemon, the queue, cancels, solves here and on a fake
    host, a damaged fetch, a solve that outlives Puzzle, a CalculiX study resumed, and
    followers and proposals;
  - `test_runner_schedulers.py`: Slurm and PBS through PSI/J from the bundle under Python
    3.10, eight Slurm endings, a failing queue, cancel, a restarted runner reattaching;
  - `test_jobs_overview.py`, `test_jobs_panel.py`.
  Tests default to `PUZZLE_RUNNER=off`.

### Not done yet

- Progress is still parsed by Puzzle from the log as it arrives (decision 4). The log
  crosses by offset, so nothing is sent twice, but the whole log does cross.
- LSF has no fake scheduler, so no tests. GPUs are passed to PSI/J but not limited in the
  runner's own queue.
- Windows runs solves in-process: the runner needs Unix sockets and process groups.
- Cancel and dismiss from the Jobs panel are working state, not history entries.

## Decided (2026-11-05, maintainer)

- **PSI/J, behind a clean interface.** PSI/J (MIT, pure Python, on conda-forge) drives the
  batch schedulers. Read before deciding:
  - It calls `sbatch`/`squeue` itself, so it runs beside the scheduler: inside the runner
    on the host, never in the GUI.
  - It reads states from `squeue` only. A job that has left the queue counts as completed,
    with the exit code from a file its job script writes. So the runner still asks `sacct`
    for the final state (out of memory, node failure, timeout) where accounting is on.
  - It reattaches to a job by its scheduler id (`attach`), which is what runs that outlive
    Puzzle need.
  - Its local executor needs psutil (compiled) and a SIGCHLD handler, so the runner starts
    unscheduled jobs itself: a detached process that writes its own exit status.
  - It pins `packaging<=25`, which Puzzle's environment is past. The runner is therefore
    always its own process. On a host it runs from one bundle that carries PSI/J and its
    pure-Python dependencies (built from the pixi `runner` environment, Python 3.10, so
    older cluster Pythons can run it). Puzzle never imports PSI/J. PSI/J loads its plugins
    and templates from files, so the bundle is a folder unpacked from one archive, not a
    zipapp.
- **On by default, selectable.** Settings > Solvers > "Run solves through the job runner"
  (`solvers.runner`, on). Off, solves run inside Puzzle as before and stop with it.
- **A finished job comes home whole, then leaves the host.** The case is fetched into a
  temporary folder beside the run folder, checked against the runner's manifest (every
  file's size and hash), and each file moved into the run folder (an atomic rename per
  file). Only then is the runner told to delete it there. A failed or partial fetch leaves the remote case alone, to be fetched
  again.

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
