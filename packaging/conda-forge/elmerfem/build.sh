#!/bin/bash
set -euxo pipefail

cmake -S . -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DWITH_MPI=OFF \
    -DWITH_OpenMP=OFF \
    -DWITH_ELMERGUI=OFF \
    -DWITH_ElmerIce=OFF \
    -DUSE_MACOS_PACKAGE_MANAGER=OFF \
    -DBLAS_LIBRARIES="${PREFIX}/lib/libblas${SHLIB_EXT}" \
    -DLAPACK_LIBRARIES="${PREFIX}/lib/liblapack${SHLIB_EXT}"

cmake --build build --parallel "${CPU_COUNT}"
cmake --install build

# Elmer finds its keyword and library files from ELMER_HOME. The install prefix
# compiled into the binaries is rewritten (null-padded) when conda relocates the
# package, which breaks Fortran's fixed-length path, so set ELMER_HOME on activation.
for change in activate deactivate; do
    mkdir -p "${PREFIX}/etc/conda/${change}.d"
done
cat > "${PREFIX}/etc/conda/activate.d/elmerfem_activate.sh" <<'SH'
export ELMER_HOME_CONDA_BACKUP="${ELMER_HOME:-}"
export ELMER_HOME="${CONDA_PREFIX}"
SH
cat > "${PREFIX}/etc/conda/deactivate.d/elmerfem_deactivate.sh" <<'SH'
if [ -n "${ELMER_HOME_CONDA_BACKUP}" ]; then
    export ELMER_HOME="${ELMER_HOME_CONDA_BACKUP}"
else
    unset ELMER_HOME
fi
unset ELMER_HOME_CONDA_BACKUP
SH
