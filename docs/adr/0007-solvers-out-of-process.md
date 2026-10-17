# 0007. Solvers run out of process, in containers

- Status: Proposed
- Date: 2026-10-17

## Context

Open-source solvers are hard to install (Code_Aster), change behaviour between releases, and are mostly GPL. Users will also want to run large jobs on clusters.

## Decision

- Every solver runs as a separate process, never linked into Puzzle.
- The default runtime is a container image per solver and version (Docker or Podman on desktops, Apptainer on HPC). A native executable on PATH is also accepted.
- A job runner abstraction covers local, container and Slurm execution, with streamed logs, cancel and resume.
- Each run records solver name, version and image digest with its results (ADR 0008).

## Consequences

- Reproducible runs with pinned solver versions.
- Solver licences stay separate from Puzzle's.
- Container images are large; offer native mode for users who already have solvers installed.

## Alternatives considered

- Linking solvers as libraries: licence entanglement and crash coupling.
- Native installs only: the main source of user frustration in today's tools.
