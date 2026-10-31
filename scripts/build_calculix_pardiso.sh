#!/usr/bin/env bash
# Build CalculiX 2.23 with Intel MKL's PARDISO into the pixi `calculix-pardiso`
# environment (run it as `pixi run -e calculix-pardiso build-calculix`).
#
# conda-forge's CalculiX has SPOOLES only. PARDISO factors the same systems 2 to 4 times
# faster with identical answers (the bolted bracket: 935 s -> 246 s, frictional contact;
# the bonded one 15 s -> 7 s). CalculiX picks PARDISO by itself when it is built in, so
# Puzzle's decks don't change; `jobs.runtime` looks in this environment before
# `calculix`. MKL exists for linux-64, osx-64 (Intel Macs) and win-64; this script
# covers the first two.
set -euo pipefail

: "${CONDA_PREFIX:?run through pixi: pixi run -e calculix-pardiso build-calculix}"
VERSION=2.23
SHA256=9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7
URL="https://www.dhondt.de/ccx_${VERSION}.src.tar.bz2"
WORK="${PIXI_PROJECT_ROOT:-$PWD}/.pixi/build/calculix-pardiso"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"

mkdir -p "$WORK"
cd "$WORK"
if [ ! -f "ccx_${VERSION}.src.tar.bz2" ]; then
    curl -sSfL -o "ccx_${VERSION}.src.tar.bz2" "$URL"
fi
if command -v sha256sum >/dev/null; then
    got=$(sha256sum "ccx_${VERSION}.src.tar.bz2" | cut -d' ' -f1)
else
    got=$(shasum -a 256 "ccx_${VERSION}.src.tar.bz2" | cut -d' ' -f1)
fi
if [ "$got" != "$SHA256" ]; then
    echo "checksum mismatch for ccx_${VERSION}.src.tar.bz2: $got" >&2
    exit 1
fi
rm -rf CalculiX
tar xjf "ccx_${VERSION}.src.tar.bz2"
cd "CalculiX/ccx_${VERSION}/src"

E="$CONDA_PREFIX"
case "$(uname)" in
    Darwin) CC="$E/bin/clang"; EXTRA_LIBS="" ;;
    *) CC="$E/bin/gcc"; [ -x "$CC" ] || CC="${CC_FOR_BUILD:-gcc}"; EXTRA_LIBS="-ldl" ;;
esac
FC="$E/bin/gfortran"
# CalculiX is old C: newer compilers turn these warnings into errors.
LENIENT="-Wno-error=return-mismatch -Wno-return-mismatch -Wno-error=implicit-function-declaration \
-Wno-error=int-conversion -Wno-error=incompatible-function-pointer-types \
-Wno-error=incompatible-pointer-types"

cat > Makefile_puzzle <<EOF
CFLAGS = -w $LENIENT -O2 -fopenmp -I$E/include/spooles -I$E/include -DARCH="Linux" \
 -DSPOOLES -DARPACK -DMATRIXSTORAGE -DUSE_MT=1 -DPARDISO
FFLAGS = -w -O2 -fopenmp -cpp -fallow-argument-mismatch
CC = $CC
FC = $FC
.c.o :
	\$(CC) \$(CFLAGS) -c \$<
.f.o :
	\$(FC) \$(FFLAGS) -c \$<
include Makefile.inc
SCCXMAIN = ccx_${VERSION}.c
OCCXF = \$(SCCXF:.f=.o)
OCCXC = \$(SCCXC:.c=.o)
OCCXMAIN = \$(SCCXMAIN:.c=.o)
LIBS = -L$E/lib -Wl,-rpath,$E/lib $E/lib/spoolesMT.a $E/lib/spooles.a -larpack -llapack -lblas \
 -lmkl_intel_lp64 -lmkl_intel_thread -lmkl_core -liomp5 -lpthread -lm $EXTRA_LIBS
ccx: \$(OCCXMAIN) ccx.a
	\$(FC) -fopenmp -O2 -o \$@ \$(OCCXMAIN) ccx.a \$(LIBS)
ccx.a: \$(OCCXF) \$(OCCXC)
	ar r \$@ \$?
EOF

PATH="$E/bin:$PATH" make -j"$JOBS" -f Makefile_puzzle ccx > build.log 2>&1 || {
    tail -30 build.log >&2
    exit 1
}
install -m 755 ccx "$E/bin/ccx"
echo "CalculiX $VERSION with PARDISO installed: $E/bin/ccx"
