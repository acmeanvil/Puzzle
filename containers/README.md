# Solver container images

One image per solver and version (ADR 0007). Puzzle mounts each job's directory at `/work`, runs the solver command there as the current user, and reads the results back from the same directory.

| Solver | Directory | Default image |
| --- | --- | --- |
| CalculiX | [calculix/](calculix/Dockerfile) | `ghcr.io/acmeanvil/puzzle-calculix:2.23` |
| Elmer | [elmer/](elmer/Dockerfile) | `ghcr.io/acmeanvil/puzzle-elmer:26.2` |

Status: the CalculiX image builds and passes the integration smoke test (`PUZZLE_RUNTIME=docker pytest tests/integration`). The Elmer image builds and ElmerSolver 26.2 and ElmerGrid start; it has no solver test yet (that comes with the Elmer adapter), and it is built without MUMPS because Debian trixie doesn't package ParMETIS. No images are published yet, so build them locally:

```sh
docker build -t ghcr.io/acmeanvil/puzzle-calculix:2.23 containers/calculix
docker build -t ghcr.io/acmeanvil/puzzle-elmer:26.2 containers/elmer
```

## Choosing how solvers run

| Variable | Effect |
| --- | --- |
| `PUZZLE_RUNTIME` | `auto` (default: native if installed, else a local container image), `native`, `docker` or `podman` |
| `PUZZLE_<SOLVER>_RUNTIME` | The same, for one solver |
| `PUZZLE_<SOLVER>_IMAGE` | Use another image, e.g. `PUZZLE_CALCULIX_IMAGE=local/ccx:dev` |
| `PUZZLE_SOLVER_PATH` | Extra directories searched for native solver executables |

The long-term goal is to ship Puzzle itself as a prepackaged container (or set of containers) so users install it with minimal effort. How that is packaged is still open.
