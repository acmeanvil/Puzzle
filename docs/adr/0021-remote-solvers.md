# 0021. Run solvers remotely: SSH hosts, HPC schedulers, later cloud batch

- Status: Accepted; SSH hosts and Slurm/PBS built (2026-11-04)
- Date: 2026-10-22

## Context

Solvers run out of process (ADR 0007), natively or in Docker/Podman (`puzzle.jobs`), with the runtime chosen per solver. Large models outgrow a workstation, and most of Puzzle's solvers scale with MPI:

| Solver | Parallelism |
| --- | --- |
| Code_Aster | MPI |
| Elmer | MPI |
| FEniCSx | MPI |
| Code_Saturne | MPI |
| OpenFOAM | MPI, with `decomposePar` |
| CalculiX | threads (OpenMP; PARDISO or PaStiX where built) |

Users have three kinds of remote machines: a lab workstation reached by SSH; a cluster with Slurm or PBS, usually with Apptainer rather than Docker; and cloud batch services. All six solvers are open source, so licences don't limit where they run.

## Decision (proposed)

1. **A `RemoteRuntime` beside the native and container runtimes**, behind the same job interface: write the input, run, stream the log, read the results. A remote job:
   - stages the case directory (rsync or SFTP);
   - runs the same command the adapter gives, natively in a remote pixi environment or in a container (Docker, Podman or Apptainer, from the images in `containers/`);
   - streams the log back;
   - fetches only what the adapter's `read_results` needs.
2. **Hosts are user settings, not project data:** name, address, user, the runtime there, and the cores and memory to use. They live in `<user data dir>/hosts.toml`. Credentials come from the SSH agent or the OS keychain, never a file in a project. A study chooses a host by name, and its run key stays the same: results are reusable wherever they were computed.
3. **Schedulers are a layer on the SSH runtime.** Slurm first, then PBS:
   - a job script is generated from the study's resources (ranks, threads, memory, wall time) and submitted;
   - the job is polled, and can be cancelled;
   - the job id is stored in the run record, so a GUI restart reattaches to a running job instead of losing it.
4. **Parallel settings are solver-neutral** where they can be: `ranks` and `threads` in the study's resources. Each adapter maps them to its own form (`mpirun -n`, `OMP_NUM_THREADS`, OpenFOAM's `decomposeParDict`, Code_Aster's `mpi_nbcpu`) and refuses what it cannot do.
5. **Cloud batch comes later**, after the SSH and scheduler path is proven (AWS Batch, Azure Batch, or Kubernetes Jobs running the same containers). It is a separate decision, because of costs, accounts and data residency.

## Consequences

- Remote runs reuse adapters unchanged: the runtime boundary (ADR 0007) was drawn for this.
- Moving data dominates for large meshes and results: the transfer back happens once, after the solve, and is reported with its size and time.
- Failure modes multiply (network, quotas, queue limits): each becomes a `PuzzleError` with a code the GUI and agents can act on (`host_unreachable`, `queue_rejected`, `walltime_exceeded`).
- Agents may run on remote hosts only within the autonomy budget (ADR 0015). Remote time can cost money, so the budget gains core-hours.

## Decided (2026-10-22, maintainer)

- **Environments.** Solver environments on remote hosts come from pixi first (portable, user space); site modules may follow as needed.
- **Results.** Results are returned whole to the local result store after solving, so a remote run reads back like a local one. The remote case may be cleaned up afterwards.

## Open questions

- Which clusters and schedulers are in reach for the first test? Not settled yet; a Slurm test container in CI is the fallback.

## Alternatives considered

- Hand users an exported case to run themselves: no tooling, but no tracking, run keys or result import.
- A Puzzle server daemon on the remote host: more control (progress, partial results), but something to install and secure on every host; it can come later if SSH proves too thin.

## Built (2026-11-04): SSH hosts and schedulers

- **Hosts** (`puzzle/hosts.py`) live in `<user data dir>/hosts.toml`. Each has a name, an address (an ssh alias, host name or IP), a user and a port, and the runtime there:
  - `native`, the pixi environments in `envs_dir`, with the same names as here;
  - `docker`, `podman` or `apptainer`, with Puzzle's images, or a `.sif` named in `images`.

  A host also has `work_dir`, `cores`, `memory_gb`, a scheduler (`none`, `slurm` or `pbs`) with its partition, account and default walltime, and `keep_case`. Puzzle runs the system's `ssh` with BatchMode and a connect timeout, so credentials stay with the user's agent, keys and `~/.ssh/config`. A host that would ask for a password is reported as unreachable.

  `hosts.check` reaches a host in one round trip and lists its cores, the solvers there and whether its scheduler answers.
- **The runtime** (`jobs/remote.py` `RemoteRuntime`) sits behind the same job interface. `Runtime` gained `prepare`, `finish` and `cancel` hooks, which do nothing for native and container runs. A remote job:
  1. writes a driver (`puzzle-run.sh`) and, with a scheduler, a job script into the case;
  2. copies the case to `<work_dir>/<case>-<hash>` there (rsync, else scp);
  3. runs `ssh host sh …/puzzle-run.sh`, whose output is the solver's, streamed into the log and the progress parser as it comes;
  4. fetches the case back whole, even after a failure, so the log says why, and removes it there unless it is kept;
  5. logs each copy's size and time.

  A program from a local pixi environment (FEniCSx's Python) becomes the same environment's program there. Thread variables come from the run's resources or the host's cores.
- **Schedulers.** The job script carries the resources as directives:
  - Slurm: `--ntasks`, `--cpus-per-task`, `--mem`, `--time`, `--partition`, `--account`;
  - PBS: `select=1:ncpus:mpiprocs:mem`, `walltime`, `-q`, `-A`.

  The driver submits the job, keeps its id in `puzzle-job.id`, follows its output until it leaves the queue, and exits with its exit code. It says what went wrong on `[puzzle]` lines, which `remote.failure` turns into the codes `queue_rejected`, `walltime_exceeded` and `host_unreachable`; `transfer_failed` comes from the copy.
- **Cancel.** Cancelling stops the far end first: `scancel`/`qdel` for a queued job, or the driver's whole process group (the driver records it when it starts). Then the local ssh stops. `run_job` calls it once.
- **Studies.** `Study.remote` (`RemoteRun`: host, processes, threads, memory_gb, walltime, partition, account) is kept out of the content hashes, so a run key never depends on where it runs; a test checks this. `run_study(..., host=)` and `RunStudy.host` run a study there once. `run_study` sets the host for every runtime its adapters ask for, through a context variable (`jobs.runtime.on_host`), so Elmer's MPI check and Code_Aster's memory see the host. Studies it takes transfers from run where their own `remote` says. A stored run's runtime reads `lab (native)` or `cluster (slurm)`.
- **Agents.** A remote run counts against the budget at act_in_budget in core-hours too (`Budget.core_hours`, default 2), from processes × threads or the host's cores. Agents list and check hosts (MCP `hosts_list`, `host_check`) but never add them.
- **Clients.**
  - CLI: `puzzle hosts list|add|check|remove` and `puzzle run --host`.
  - GUI: Project → Remote hosts… (`gui/hosts_dialog.py`) and a study's Run on… (one Edit of `remote`; queue fields only for a host with a scheduler).
  - The skill `remote-runs`.
- **Tests** (`tests/test_remote.py`). `tests/remote_fakes.py` stands in for ssh (a new session, as sshd starts one), rsync and Slurm/PBS (jobs in the background, walltime kills, rejected partitions) on this machine. The scripts, staging, streaming, fetching, cancelling and coded failures are therefore tested for real, and the cantilever runs on the fake host with CalculiX from its pixi envs, matching a local run.

Not done yet:
- Reattaching to a queued job after a GUI restart. The job id is kept in the run folder, but `run_study` waits for its job.
- Code_Aster's MPI build check on a host. It assumes no MPI there, so it runs on one process.
- A test against a real Slurm (a Slurm container in CI is the fallback).
- Cloud batch (decision 5), which needs a decision of its own.
