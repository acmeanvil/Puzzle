# Code_Saturne solver image for Puzzle (ADR 0007, ADR 0013), built from source at a
# pinned release. conda-forge doesn't package Code_Saturne and Debian dropped it.
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-code-saturne:9.2 containers/code_saturne
# Puzzle mounts the case directory at /work and runs: code_saturne run ...
#
# Minimal build: no GUI, no MPI, no MED/CGNS/HDF5. Meshes come in as Gmsh .msh 2.2,
# which the Code_Saturne preprocessor reads natively.
ARG CS_TAG=v9.2.0

FROM debian:trixie-slim AS build
ARG CS_TAG
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran python3 python3-dev python3-setuptools autoconf automake libtool \
        bison flex git ca-certificates zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${CS_TAG} https://github.com/code-saturne/code_saturne.git /src
WORKDIR /src
RUN ./sbin/bootstrap
RUN mkdir /build && cd /build \
    && /src/configure --prefix=/opt/code_saturne --disable-gui --without-mpi \
        --without-med --without-cgns --without-hdf5 --disable-static \
    && make -j2 && make install  # more jobs can exhaust Docker Desktop's VM memory

FROM debian:trixie-slim
ARG CS_TAG
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 libgfortran5 build-essential zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/code_saturne /opt/code_saturne
ENV PATH=/opt/code_saturne/bin:$PATH \
    PYTHONPATH=/opt/code_saturne/lib/python3.13/site-packages
RUN echo "${CS_TAG}" > /etc/puzzle-solver-version
WORKDIR /work
