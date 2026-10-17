# Elmer solver image for Puzzle (ADR 0007), built from source at a pinned release.
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-elmer:26.2 containers/elmer
# Puzzle mounts the job directory at /work and runs ElmerGrid, then ElmerSolver.
# MUMPS is off: Debian's MUMPS needs ParMETIS, which trixie doesn't package. Elmer's
# iterative solvers and bundled UMFPACK cover direct and iterative solves meanwhile.
ARG ELMER_TAG=release-26.2

FROM debian:trixie-slim AS build
ARG ELMER_TAG
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran cmake git ca-certificates \
        libblas-dev liblapack-dev libopenmpi-dev openmpi-bin \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${ELMER_TAG} https://github.com/ElmerCSC/elmerfem.git /src
RUN cmake -S /src -B /build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/elmer \
        -DWITH_MPI=ON -DWITH_Mumps=OFF -DWITH_ElmerIce=OFF -DWITH_ELMERGUI=OFF \
    && cmake --build /build -j"$(nproc)" \
    && cmake --install /build

FROM debian:trixie-slim
ARG ELMER_TAG
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgfortran5 libblas3 liblapack3 libopenmpi40 openmpi-bin \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/elmer /opt/elmer
ENV PATH=/opt/elmer/bin:$PATH \
    LD_LIBRARY_PATH=/opt/elmer/lib/elmersolver
RUN echo "${ELMER_TAG}" > /etc/puzzle-solver-version
WORKDIR /work
