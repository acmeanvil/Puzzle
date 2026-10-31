# 0051. Build CalculiX with PARDISO where Intel MKL exists, and prefer it

- Status: Accepted
- Date: 2026-10-30

## Context

The maintainer asked for anything that speeds up solves or the I/O around them. Every run now reports its time per stage. On the bolted bracket (`examples/bracket_assembly`, 20,724 nodes, frictional contact, two beam bolts), mesh, write, read and store together took 2.9 s; the solve took 935 s. So the I/O was not the problem; factoring the equations was.

conda-forge's CalculiX 2.23 links SPOOLES only (`SOLVER=PARDISO` errors there). Frictional contact makes the system unsymmetric, and SPOOLES factors it slowly and on few cores (263 % CPU on 8). CalculiX also supports Intel MKL's PARDISO and PaStiX, if compiled in.

## Decision

1. **A pixi environment, `calculix-pardiso`** (linux-64 and osx-64, where conda-forge has MKL), with the compilers, MKL, ARPACK and SPOOLES. `pixi run -e calculix-pardiso build-calculix` runs `scripts/build_calculix_pardiso.sh`:
   - it downloads CalculiX's official source from dhondt.de and checks its SHA-256;
   - it compiles with `-DPARDISO` (plus SPOOLES, ARPACK and the multithreaded build) against MKL's threaded libraries;
   - it installs `ccx` into that environment.
2. **Puzzle prefers it.** `jobs.runtime` lists `calculix-pardiso` before `calculix` (`PIXI_SOLVER_ENVS`, and the dev environment's `PUZZLE_SOLVER_PATH`). An environment installed but not built has no `ccx`, so the search falls through to conda-forge's.
3. **Decks don't change.** CalculiX picks PARDISO by itself when it is built in, symmetric or unsymmetric. The solver setting "CalculiX's choice" already says so, and SPOOLES can still be chosen per study.

## Measured (4 cores (8 threads), Intel Mac, the same decks)

| Deck | SPOOLES | PARDISO | Answers |
| --- | --- | --- | --- |
| Bracket, bolted (frictional contact, 44 contact iterations) | 935 s | 246 s (3.8×) | displacement identical; stress within 1e-13 |
| Bracket, bonded (53,367 nodes, linear) | 15.1 s | 7.2 s (2.1×) | — |

Every integration benchmark is rerun on the PARDISO build; see the commit that added this ADR.

## Consequences

- Contact and other large CalculiX runs are 2 to 4 times faster on Intel Linux and Intel Macs, with no change to results or decks.
- The build is an opt-in step (a few minutes) until a conda package exists. The next step is a conda-forge variant (or Puzzle's own channel package) built the same way, so `pixi install` brings it.
- **Apple silicon and Windows** have no PARDISO this way yet: MKL isn't built for osx-arm64, and Windows needs an MSVC/MinGW build. PaStiX, also supported by CalculiX, is the candidate for Apple silicon.
- The Docker image for CalculiX (Linux) can be built the same way.
- MKL is proprietary but freely redistributable (Intel Simplified Software License); CalculiX stays GPL. The build is local and links them on the user's machine; a published package must check the licences' combination first.

## Alternatives considered

- **Tune the contact only** (done too: balanced stiffness, a full-load start, 37.6 → 15.6 min). It sets the iteration count; PARDISO cuts the cost of each iteration, so the two multiply.
- **CalculiX's iterative solvers:** not for unsymmetric contact systems.
- **PaStiX:** a GPU-capable, open alternative, but harder to build (it needs PaStiX 6 with CalculiX's patches); kept for Apple silicon.
