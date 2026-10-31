# Code_Aster solver image for Puzzle (ADR 0007, ADR 0013): the conda-forge package
# pixi pins for Linux and Windows, for platforms without a native build (macOS).
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-code-aster:18.1 containers/code_aster
# Puzzle mounts the job directory at /work and runs: run_aster <job>.export
#
# ADR 0053: the OpenMPI build runs a model split across processes (Settings > Solvers >
# Parallel processes per run). Build it with
#   docker build --build-arg CODE_ASTER_VERSION=18.1.11 --build-arg VARIANT=openmpi \
#     -t ghcr.io/acmeanvil/puzzle-code-aster:18.1-mpi containers/code_aster
FROM --platform=linux/amd64 mambaorg/micromamba:2.0.5

ARG CODE_ASTER_VERSION=18.1.10
ARG VARIANT=nompi
RUN micromamba install -y -n base -c conda-forge "code-aster=${CODE_ASTER_VERSION}=*${VARIANT}*" \
    && micromamba clean -a -y \
    && echo "${CODE_ASTER_VERSION} ${VARIANT}" > /opt/conda/puzzle-solver-version

ENV PATH=/opt/conda/bin:$PATH
# OpenMPI in a container: no ssh launcher, and the run's user isn't root.
ENV OMPI_MCA_plm=isolated OMPI_MCA_btl_vader_single_copy_mechanism=none \
    OMPI_MCA_rmaps_base_oversubscribe=yes PRTE_MCA_rmaps_default_mapping_policy=:oversubscribe
WORKDIR /work
LABEL puzzle.mpi="${VARIANT}"
