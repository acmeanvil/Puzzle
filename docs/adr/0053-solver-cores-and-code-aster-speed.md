# 0053. Cores per run, parallel processes, and a faster Code_Aster

- Status: Accepted
- Date: 2026-10-30

## Context

After ADR 0051 (PARDISO for CalculiX) the maintainer asked for the same care on the other solvers, Code_Aster first, and for a user-selectable number of cores used by default. Code_Aster was run with `P ncpus 1`, `P mpi_nbcpu 1`, `P memory_limit 4096` and `P time_limit 3600` in its export: one core whatever the machine (run_aster sets OpenMP's threads from `ncpus`, ignoring `OMP_NUM_THREADS`), 4 GB for MUMPS, and a one-hour limit that would stop a long contact run. Its contact steps were ramped over 10 increments, as CalculiX's were before ADR 0039's full-load start.

## Decision

1. **Settings > Solvers:**
   - **Cores per run:** "Physical cores (n)" by default, or any number from 1 to the logical cores (`puzzle.machine`: `physical_cores`, `logical_cores`, `memory_bytes`, read from the operating system without new dependencies). Finite element assembly and sparse factoring gain little from a second thread per core.
   - **Parallel processes per run** (MPI), 1 by default, up to the physical cores. The cores are shared out: each process gets cores ÷ processes threads (`jobs.runtime.threads_per_process`), so a run never oversubscribes the machine.
   - Adapters say whether they can split (`Adapter.parallel_processes`, `processes()`); each run's timing line says what it used ("4 cores", "2 processes x 2 threads").
2. **Code_Aster's export** comes from them (`code_aster.comm.resources`): `ncpus` = threads per process, `mpi_nbcpu` = processes, `memory_limit` = 75 % of the runtime's memory per process (Docker Desktop's VM, not the Mac, when run in a container: `Runtime.memory`), `time_limit` a week (Puzzle stops runs itself).
3. **MUMPS options** as Code_Aster solver settings, written only when set: acceleration (Code_Aster's choice, full rank, full rank faster, low rank, low rank faster), the low-rank tolerance, the ordering (METIS, SCOTCH, PORD, AMD, AMF, QAMD) and where the factors are kept (in memory, on disk). They are refused with another linear solver, and a tolerance without low rank.
4. **Parallel runs:** an MPI build (conda-forge's `openmpi` variant; the image `puzzle-code-aster:18.1-mpi`, labelled `puzzle.mpi=openmpi`) runs `mpi_nbcpu` processes; a sequential build always runs one (`code_aster.mpi_build`).
5. **Contact:** a contact-only static step starts at the full load (`DEFI_LIST_INST`), and a failed increment is cut in four, up to five times, instead of ending the run. Plasticity, large deflection and compression-only gaps keep the 10-step ramp.

## Measured

Intel Mac, 4 cores (8 threads); Code_Aster 18.1 in Docker Desktop (8 CPUs, 8 GB VM). Solve time in seconds. Every run gave the same displacement and stress to all printed digits (LE1 σyy max 93.23 MPa).

| Run | LE1 | Bonded bracket (53,367 nodes) |
| --- | --- | --- |
| 1 core (as before this ADR) | 12.6 | 24.4 |
| 2 cores | 11.1 | 19.0 |
| 4 cores (physical, the new default) | 11.3 | 18.6 |
| 8 threads (hyperthreads) | 20.7 | 38.8 |
| MPI image, 1 process × 4 threads | | 20.2 |
| MPI image, 2 processes × 2 threads | | 19.9 |
| MPI image, 4 processes × 1 thread | 14.8 | 23.1 |
| MUMPS full rank faster (FR+) | | 20.7 |
| METIS ordering | | 24.9 |
| Factors in memory | | 30.4 |
| Low rank, tolerance 1e-9, 1 core | | 35.4 |
| Low rank (LR or LR+), 4 cores | | hung (11 h at 400 % CPU; 10 min limit for LR+) |

- **Physical cores are the right default:** hyperthreads double the time. Past two cores these models gain little; most of the time is assembly and Code_Aster's start-up, not factoring.
- **MPI** pays nothing at this size on four cores, so the sequential image stays the default and the MPI one an option for large models.
- **MUMPS's own choices win:** none of the options beat the defaults here, so none is set by default.
- **Low rank hangs MUMPS with more than one OpenMP thread** (in this build), so a low-rank run is given one thread.

The bolted bracket (first order, 3,332 nodes, two pretensioned beam bolts, a 120 × 80 mm seat) had only been written for Code_Aster before; running it found:

- **Bolt holes on a contact face:** each hole's edge shares 12 nodes with the seat, tied to the bolt by `LIAISON_RBE3` and in contact too, which made the friction multipliers singular (LAGS_F1). Those faces are now left out of the zone (`SANS_GROUP_NO`).
- **Frictionless, at full load in one increment: 16.8 s**, so the start at the full load (decision 5) works with bolts and pretension.
- **Frictional (μ = 0.2) does not converge:** the friction multipliers go singular at seat nodes away from the holes with Newton on friction (the default), with a fixed point on friction, and with fixed points on both contact and friction (Newton oscillates; the step is cut six times). CalculiX solves the same model (penalty contact, 246 s with PARDISO), so the bolted example stays a CalculiX study; ADR 0039's Code_Aster friction limits stand.

## Consequences

- Every solver run uses the physical cores by default, and a user can give a run fewer (to keep working) or the hyperthreads too.
- Code_Aster runs that hit 4 GB or an hour no longer slow down out of core or stop.
- Settings that change MUMPS's accuracy (low rank) are opt-in and say so.

## Alternatives considered

- **Every logical core by default** (what Puzzle did for CalculiX): hyperthreads share each core's floating point units and memory bandwidth, so they rarely help a solve and can slow it.
- **Processes multiplying threads** (each process taking every core): oversubscribes the machine; sharing the cores keeps the total what the user chose.
