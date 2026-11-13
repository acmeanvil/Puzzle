# Solver container images

One image per solver and version (ADR 0007). Puzzle mounts each job's directory at `/work`, runs the solver command there as the current user, and reads the results back from the same directory.

| Solver | Directory | Default image |
| --- | --- | --- |
| CalculiX | [calculix/](calculix/Dockerfile) | `ghcr.io/acmeanvil/puzzle-calculix:2.23` |
| Elmer | [elmer/](elmer/Dockerfile) | `ghcr.io/acmeanvil/puzzle-elmer:26.2` |
| Code_Aster | [code_aster/](code_aster/Dockerfile) | `ghcr.io/acmeanvil/puzzle-code-aster:18.1` (macOS; Linux and Windows run it natively) |
| Code_Saturne | [code_saturne/](code_saturne/Dockerfile) | `ghcr.io/acmeanvil/puzzle-code-saturne:9.2` (built from source; no conda-forge or current distro package) |
| OpenFOAM | [openfoam/](openfoam/Dockerfile) | `ghcr.io/acmeanvil/puzzle-openfoam:2412` (OpenCFD's Ubuntu packages; conda-forge has linux-64 only) |
| FEniCSx | [fenicsx/](fenicsx/Dockerfile) | `ghcr.io/acmeanvil/puzzle-fenicsx:0.11` (the locked linux-64 `fenicsx` pixi environment: PETSc with MUMPS and SLEPc, which conda-forge has no Windows build of) |

Status: the CalculiX image builds and passes the integration smoke test (`PUZZLE_RUNTIME=docker pytest tests/integration`). The Elmer image builds and ElmerSolver 26.2 and ElmerGrid start; it has no solver test yet (that comes with the Elmer adapter), and it is built without MUMPS because Debian trixie doesn't package ParMETIS. No images are published yet, so build them locally:

```sh
docker build -t ghcr.io/acmeanvil/puzzle-calculix:2.23 containers/calculix
docker build -t ghcr.io/acmeanvil/puzzle-elmer:26.2 containers/elmer
docker build -t ghcr.io/acmeanvil/puzzle-code-aster:18.1 containers/code_aster
docker build -t ghcr.io/acmeanvil/puzzle-code-saturne:9.2 containers/code_saturne   # ~10 min
docker build -t ghcr.io/acmeanvil/puzzle-openfoam:2412 containers/openfoam
docker build -f containers/fenicsx/Dockerfile -t ghcr.io/acmeanvil/puzzle-fenicsx:0.11 .   # from the repo root: it reads pixi.lock
```

On Windows, "auto" tries the images of FEniCSx, Elmer and Code_Aster before a native install (`CONTAINER_FIRST` in `src/puzzle/jobs/runtime.py`, with the measurements): the Windows FEniCSx has no PETSc, the Windows Elmer build crashed on 19 of 31 integration tests, and Code_Aster ran 10-23 % faster in its image. CalculiX stays native (its image was about 1.4x slower). Without the image or a running engine, the native install is used as before. Docker on Windows runs containers as root, so the images that launch OpenMPI allow it (`OMPI_ALLOW_RUN_AS_ROOT`).

## Choosing how solvers run

| Variable | Effect |
| --- | --- |
| `PUZZLE_RUNTIME` | `auto` (default: native if installed, else a local container image), `native`, `docker` or `podman` |
| `PUZZLE_<SOLVER>_RUNTIME` | The same, for one solver |
| `PUZZLE_<SOLVER>_IMAGE` | Use another image, e.g. `PUZZLE_CALCULIX_IMAGE=local/ccx:dev` |
| `PUZZLE_SOLVER_PATH` | Extra directories searched for native solver executables |

The long-term goal is to ship Puzzle itself as a prepackaged container (or set of containers) so users install it with minimal effort. How that is packaged is still open.
