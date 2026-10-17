# CalculiX solver image for Puzzle (ADR 0007).
# Uses the same conda-forge package as native development, pinned to one version.
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-calculix:2.23 containers/calculix
# Puzzle mounts the job directory at /work and runs: ccx -i <job>
FROM mambaorg/micromamba:2.0.5

ARG CALCULIX_VERSION=2.23
RUN micromamba install -y -n base -c conda-forge calculix=${CALCULIX_VERSION} \
    && micromamba clean -a -y \
    && echo "${CALCULIX_VERSION}" > /opt/conda/puzzle-solver-version

ENV PATH=/opt/conda/bin:$PATH
WORKDIR /work
