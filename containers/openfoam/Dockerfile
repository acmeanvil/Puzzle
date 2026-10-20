# OpenFOAM (ESI-OpenCFD, openfoam.com) solver image for Puzzle (ADR 0007, ADR 0016),
# from OpenCFD's Ubuntu packages at a pinned release. The same version as conda-forge's
# linux-64 `openfoam`, which Linux hosts can run natively instead.
# Build:  docker build -t ghcr.io/acmeanvil/puzzle-openfoam:2412 containers/openfoam
# Puzzle mounts the case directory at /work and runs its command through the entrypoint,
# which sources OpenFOAM's environment first.
FROM ubuntu:24.04
ARG FOAM_VERSION=2412
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://dl.openfoam.com/add-debian-repo.sh | bash \
    && apt-get update && apt-get install -y --no-install-recommends openfoam${FOAM_VERSION} \
    && rm -rf /var/lib/apt/lists/*
# OpenFOAM's bashrc reads its positional arguments as settings or files to source,
# so the command's arguments are kept aside while it is sourced.
RUN printf '#!/bin/bash\nargs=("$@")\nset --\n. /usr/lib/openfoam/openfoam%s/etc/bashrc\nexec "${args[@]}"\n' \
        "${FOAM_VERSION}" > /usr/local/bin/foam-env && chmod +x /usr/local/bin/foam-env
ENV FOAM_SIGFPE=0
ENTRYPOINT ["/usr/local/bin/foam-env"]
CMD ["simpleFoam", "-help"]
