# 0052. GPUs for meshing and solving: researched, not pursued yet (wishlist)

- Status: Accepted (as a wishlist: no work planned)
- Date: 2026-10-30

## Context

The maintainer asked what GPUs could do for meshing and solving, alongside the CPU work (ADR 0051's PARDISO CalculiX, threads and parallel runs for Code_Aster). This records what was found so it isn't researched again.

- **Precision:** finite element and CFD solves need double precision. Consumer GPUs run it 32 to 64 times slower than single precision; Apple's GPUs (Metal) have none; the maintainer's Mac has an Intel Iris GPU, which runs no CUDA. Real gains need data-centre NVIDIA cards (A100, H100) on Linux.
- **Meshing:** Gmsh and Netgen have no GPU paths. Gmsh's gain is CPU threads (its HXT 3D algorithm and `General.NumThreads`).
- **Solvers:**

| Solver | GPU path | Verdict |
| --- | --- | --- |
| CalculiX | none in the official code; its PaStiX solver option can offload factoring to NVIDIA GPUs | experimental builds; large models, Linux and NVIDIA only |
| Code_Aster | none practical: MUMPS is CPU only; PETSc's GPU backends aren't in its usual paths | no |
| FEniCSx | PETSc's GPU backends (CUDA/HIP vectors and matrices, GPU multigrid) for iterative solves; assembly on the CPU | workable on Linux with NVIDIA and a CUDA PETSc; the first to try |
| Elmer | experimental at most | no |
| OpenFOAM | add-ons that swap in GPU linear solvers | possible later |
| Code_Saturne | partial GPU offload in recent versions; maturity unchecked | to check later |

NVIDIA's GPU sparse direct solver (cuDSS) is not used by any of these yet.

## Decision

No GPU work now. CPU parallelism comes first (ADR 0051, 0053): it helps every user on every machine.

**Wishlist,** in order, when a user has Linux with a data-centre NVIDIA GPU:
1. FEniCSx: a CUDA PETSc environment and GPU options (`vec_type cuda`, `mat_type aijcusparse`, GPU multigrid) as a solver preset, benchmarked on LE1 at a large mesh;
2. CalculiX with GPU PaStiX, built like ADR 0051's PARDISO;
3. recheck Code_Saturne's GPU offload, OpenFOAM's GPU linear solver add-ons, and cuDSS adoption.

## Consequences

- Nothing changes now; this ADR is the reference when GPUs come up again.
- Any GPU option must be benchmarked like any other capability (CLAUDE.md), and stays optional: most users have no suitable GPU.

## Alternatives considered

- **Start GPU work now:** no suitable hardware here, and CPU parallelism gives more to more users first.
