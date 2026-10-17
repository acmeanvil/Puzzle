# 0013. Platforms and packaging: pixi environments, native solvers first, containers for the gaps

- Status: Accepted
- Date: 2026-10-17

## Context

Puzzle targets Linux, macOS (Apple silicon and, for now, Intel) and Windows. It depends on native libraries that pip handles poorly (Qt, VTK, OpenCASCADE, Gmsh) and on solvers with very different packaging. On macOS and Windows, containers need a Linux virtual machine, which costs memory, adds slow file sharing for large results, and is one more thing users must install.

conda-forge solver packages, checked 2026-10-17:

| Solver | linux-64 | linux-aarch64 | osx-64 | osx-arm64 | win-64 |
| --- | --- | --- | --- | --- | --- |
| CalculiX 2.23 | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gmsh 4.15 | ✓ | ✓ | ✓ | ✓ | ✓ |
| FEniCSx 0.11 | ✓ | ✓ | ✓ | ✓ | ✓ |
| Code_Aster 18.1 | ✓ | — | — | — | ✓ |
| Elmer | — | — | — | — | — |
| Code_Saturne | — | — | — | — | — |

## Decision

- **One package manager:** pixi with conda-forge (`pixi.toml`, `pixi.lock` committed). Platforms: linux-64, linux-aarch64, osx-64, osx-arm64, win-64. Installers for end users will be built from these environments.
- **The GUI and core always run natively**, never in a container.
- **One pixi environment per native solver** (`calculix`, `fenicsx`, `code-aster`). Solvers run as separate processes (ADR 0007), and solving them together causes clashes: FEniCSx and Code_Aster need different MPI builds on Windows.
- **Containers only for the gaps:** Elmer until it is on conda-forge, Code_Saturne everywhere but Linux, Code_Aster on macOS and Linux ARM. Images are built for amd64 and arm64. Podman is the recommended engine (open source on all three systems); Docker, Colima and OrbStack also work. On Windows, WSL2 is a possible further runtime.
- **Runtime chosen per solver** (`PUZZLE_RUNTIME`, `PUZZLE_<SOLVER>_RUNTIME`; default `auto`): native when the solver is installed, else a container whose image is present, else a clear "not installed" message. Each run records which runtime it used.
- **Elmer goes to conda-forge:** Puzzle prepares an `elmerfem` recipe so Elmer becomes native everywhere.
- **CI** runs on Ubuntu, macOS Intel, macOS ARM and Windows through pixi.

## Consequences

- Most users never need a container runtime; only Code_Saturne users on macOS or Windows, and Code_Aster users on macOS, do.
- Solver versions are pinned once, in the lock file, for native runs, CI and the conda-forge-based images alike.
- Intel Mac support is kept for now. Apple has said macOS 26 is the last release for Intel and GitHub is retiring Intel macOS runners, so reassess soon.
- Code_Aster images for arm64 need a source build, since conda-forge has no linux-aarch64 package.

## Alternatives considered

- Containers for everything: simplest to describe, but a VM and slow file sharing on two of three platforms.
- pip wheels only: no Qt/VTK/OCCT/solver story on Windows and macOS.
- One shared environment for all solvers: fails to solve on Windows and couples unrelated upgrades.
