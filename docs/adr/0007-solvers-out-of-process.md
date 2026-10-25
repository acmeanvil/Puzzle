# 0007. Solvers run out of process, in containers

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-17

## Context

Open-source solvers are hard to install (Code_Aster), change behaviour between releases, and are mostly GPL. Users will also want to run large jobs on clusters.

## Decision

- Every solver runs as a separate process, never linked into Puzzle.
- The default runtime is a container image per solver and version (Docker or Podman on desktops, Apptainer on HPC). A native executable on PATH is also accepted.
- A job runner abstraction covers local, container and Slurm execution, with streamed logs, cancel and resume.
- Each run records solver name, version and image digest with its results (ADR 0008).
- Where conda-forge packages a solver (CalculiX, Code_Aster), its image installs the same pinned package used for native development and CI, so container and native runs match. Elmer is built from source at a release tag.
- Images live in `containers/`. Runtime selection: `PUZZLE_RUNTIME` (native, docker, podman), `PUZZLE_<SOLVER>_IMAGE`, `PUZZLE_SOLVER_PATH`.
- Packaging Puzzle itself is open; the goal is a prepackaged container so users install with minimal effort.

## Consequences

- Reproducible runs with pinned solver versions.
- Solver licences stay separate from Puzzle's.
- Container images are large; offer native mode for users who already have solvers installed.

## Alternatives considered

- Linking solvers as libraries: licence entanglement and crash coupling.
- Native installs only: the main source of user frustration in today's tools.
