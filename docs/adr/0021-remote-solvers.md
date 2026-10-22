# 0021. Run solvers remotely: SSH hosts, HPC schedulers, later cloud batch

- Status: Proposed
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
- Moving data dominates for large meshes and results: results are fetched lazily (only fields shown or queried), and the remote case can be kept for re-reads.
- Failure modes multiply (network, quotas, queue limits): each becomes a `PuzzleError` with a code the GUI and agents can act on (`host_unreachable`, `queue_rejected`, `walltime_exceeded`).
- Agents may run on remote hosts only within the autonomy budget (ADR 0015). Remote time can cost money, so the budget gains core-hours.

## Open questions

- Which clusters and schedulers are in reach for the first test (a Slurm test container in CI is possible)?
- Should solver environments on HPC come from pixi (portable, user space) or from site modules?
- Should results be stored back into the local Delta store whole, or referenced remotely until needed?

## Alternatives considered

- Hand users an exported case to run themselves: no tooling, but no tracking, run keys or result import.
- A Puzzle server daemon on the remote host: more control (progress, partial results), but something to install and secure on every host; it can come later if SSH proves too thin.
