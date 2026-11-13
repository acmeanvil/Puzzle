# FEniCSx image for Puzzle (ADR 0007): the locked `fenicsx` pixi environment for
# linux-64, so the same dolfinx, PETSc (MUMPS, SLEPc) and OpenMPI as on Linux and macOS.
# On Windows it is the only way to get PETSc: conda-forge builds none for win-64, so
# the native Windows dolfinx falls back to SciPy (single-threaded SuperLU).
#
# Built from the repository root, so the lock file is the one Puzzle runs with:
#   docker build -f containers/fenicsx/Dockerfile -t ghcr.io/acmeanvil/puzzle-fenicsx:0.11 .
# (Dockerfile.dockerignore beside this file keeps the context to the pixi files.)
#
# Puzzle mounts the job directory at /work and runs `python study.py` there. Forms that
# dolfinx compiles are kept in /cache, a named volume Puzzle mounts, so they are
# compiled once rather than on every solve.
ARG PIXI_VERSION=v0.81.0

FROM ubuntu:24.04 AS build
ARG PIXI_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://pixi.sh/install.sh | PIXI_VERSION=${PIXI_VERSION} PIXI_HOME=/usr/local bash
WORKDIR /app
COPY pixi.toml pixi.lock pyproject.toml ./
RUN pixi install -e fenicsx --frozen
# The environment's activation (PETSc, MPI and compiler variables) as a script.
RUN pixi shell-hook -e fenicsx -s bash > /activate.sh

FROM ubuntu:24.04
COPY --from=build /app/.pixi/envs/fenicsx /app/.pixi/envs/fenicsx
COPY --from=build /activate.sh /activate.sh
RUN printf '#!/bin/bash\nsource /activate.sh\nexec "$@"\n' > /entrypoint.sh \
    && chmod 755 /entrypoint.sh \
    && mkdir -m 1777 /cache \
    && /bin/bash -c 'source /activate.sh && python -c "import dolfinx; print(dolfinx.__version__)"' \
       > /etc/puzzle-solver-version
# Windows and macOS engines run as root; OpenMPI refuses that unless told.
ENV XDG_CACHE_HOME=/cache \
    OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
LABEL puzzle.petsc="yes"
WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
