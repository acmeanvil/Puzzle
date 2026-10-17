# Code_Aster solver image for Puzzle (ADR 0007, ADR 0013): the conda-forge package
# pixi pins for Linux and Windows, for platforms without a native build (macOS).
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-code-aster:18.1 containers/code_aster
# Puzzle mounts the job directory at /work and runs: run_aster <job>.export
FROM --platform=linux/amd64 mambaorg/micromamba:2.0.5

ARG CODE_ASTER_VERSION=18.1.10
RUN micromamba install -y -n base -c conda-forge code-aster=${CODE_ASTER_VERSION} \
    && micromamba clean -a -y \
    && echo "${CODE_ASTER_VERSION}" > /opt/conda/puzzle-solver-version

ENV PATH=/opt/conda/bin:$PATH
WORKDIR /work
