# 0051. Build CalculiX with PARDISO where Intel MKL exists, and prefer it

- Status: Accepted; built on Linux, Intel macOS and Windows (2026-11-10)
- Date: 2026-10-30

## Context

The maintainer asked for anything that speeds up solves or the I/O around them. Every run now reports its time per stage. On the bolted bracket (`examples/bracket_assembly`, 20,724 nodes, frictional contact, two beam bolts), mesh, write, read and store together took 2.9 s; the solve took 935 s. So the I/O was not the problem; factoring the equations was.

conda-forge's CalculiX 2.23 links SPOOLES only (`SOLVER=PARDISO` errors there). Frictional contact makes the system unsymmetric, and SPOOLES factors it slowly and on few cores (263 % CPU on 8). CalculiX also supports Intel MKL's PARDISO and PaStiX, if compiled in.

## Decision

1. **A pixi environment, `calculix-pardiso`** (linux-64, osx-64 and win-64, where conda-forge has MKL), with the compilers, MKL, ARPACK and SPOOLES. `pixi run -e dev build-calculix` runs `puzzle solvers --build` (`puzzle.solver_builds`; until 2026-11-10 a shell script, `scripts/build_calculix_pardiso.sh`):
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

## Windows (2026-11-10)

conda-forge has MinGW gcc and gfortran 16 for win-64, MKL, and MinGW builds of ARPACK and SPOOLES, so Windows builds the same way. The differences: MKL's single dynamic library (`-lmkl_rt`, Intel threading chosen by itself) in place of the separate threaded libraries; ARPACK from `Library/mingw-w64`; two of CalculiX's main files call `_set_output_format` at file scope under `__WIN32` (for old MSVCRT's three-digit exponents), which MinGW's headers refuse, so those lines are dropped (UCRT prints two digits already); gcc 16 refuses clang's `-Wno-error=incompatible-function-pointer-types`. `ccx.exe` goes in the environment's `Library/bin`, which the dev environment's Windows `PUZZLE_SOLVER_PATH` now lists first.

The build moved from the shell script to Python (`puzzle.solver_builds`) so that the installed app can run it on every system without bash or make (ADR 0068 step 4): it compiles the files Makefile.inc lists in parallel, archives them, links, and installs only after the new program has solved a one-element deck with PARDISO.

Measured on Windows 11 (the same decks, cores per run automatic): every CalculiX benchmark (`test_benchmarks.py`, `test_nafems_le1.py`) passes on it, and its log says "Factoring the system of equations using the symmetric pardiso solver".

| Deck | SPOOLES | PARDISO | Answers |
| --- | --- | --- | --- |
| Bracket, two beam bolts (frictional seat and heads) | 271 s | 161 s (1.7×) | max deflection and bolt forces identical |
| Bracket, two modelled bolts (ADR 0077) | 8089 s | 2521 s (3.2×) | max deflection 0.2405 mm and bolt forces (4941.8, 4963.5 N) identical |

## Consequences

- Contact and other large CalculiX runs are 2 to 4 times faster on Intel Linux and Intel Macs, with no change to results or decks.
- The build is an opt-in step (a few minutes) until a conda package exists. The next step is a conda-forge variant (or Puzzle's own channel package) built the same way, so `pixi install` brings it.
- **Apple silicon** has no PARDISO this way: MKL isn't built for osx-arm64. PaStiX, also supported by CalculiX, is the candidate there.
- The Docker image for CalculiX (Linux) can be built the same way.
- MKL is proprietary but freely redistributable (Intel Simplified Software License); CalculiX stays GPL. The build is local and links them on the user's machine; a published package must check the licences' combination first.

## Alternatives considered

- **Tune the contact only** (done too: balanced stiffness, a full-load start, 37.6 → 15.6 min). It sets the iteration count; PARDISO cuts the cost of each iteration, so the two multiply.
- **CalculiX's iterative solvers:** not for unsymmetric contact systems.
- **PaStiX:** a GPU-capable, open alternative, but harder to build (it needs PaStiX 6 with CalculiX's patches); kept for Apple silicon.
