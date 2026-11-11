"""Solvers built on the user's machine (ADR 0068 decision 5, ADR 0051).

CalculiX with Intel MKL's PARDISO factors contact and other large systems 2 to 4 times
faster than conda-forge's SPOOLES build, with the same answers. The GPL restricts
distributing CalculiX linked with MKL, not building it privately, so the release ships
the plain CalculiX, MKL and the compilers (the `calculix-pardiso` environment), and this
module builds `ccx` with PARDISO into that environment, where `jobs.runtime` finds it
before the plain one. It runs once in the background after installing (the GUI starts
it, `puzzle solvers --build` too); the plain CalculiX works until it is done, and a
failed build is reported and leaves it in place.

The build is Python, not make: it compiles each of CalculiX's sources (the lists in its
own Makefile.inc) with the environment's gcc and gfortran, archives them and links the
program, on Linux, Intel macOS and Windows alike (MinGW there, with MKL's single
dynamic library). The source is CalculiX's official tarball, checked by its SHA-256. A
small deck solved with the new program must say it used PARDISO before it is installed.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

ENV = "calculix-pardiso"
VERSION = "2.23"
SHA256 = "9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7"
URL = f"https://www.dhondt.de/ccx_{VERSION}.src.tar.bz2"
STALE_S = 3 * 3600  # a build marked running for longer than this has died
# CalculiX is old C: newer compilers turn these warnings into errors (clang's
# -Wincompatible-function-pointer-types is unknown to gcc 16, which refuses it).
LENIENT = ["-Wno-error=return-mismatch", "-Wno-return-mismatch",
           "-Wno-error=implicit-function-declaration", "-Wno-error=int-conversion",
           "-Wno-error=incompatible-pointer-types"]  # fmt: skip
CLANG_ONLY = ["-Wno-error=incompatible-function-pointer-types"]
# No shell runs these (the Makefile's did), so no quotes: ARCH=Linux is what CalculiX.h
# compares to pick its FORTRAN() name mangling.
DEFINES = ["-DARCH=Linux", "-DSPOOLES", "-DARPACK", "-DMATRIXSTORAGE", "-DUSE_MT=1",
           "-DPARDISO"]  # fmt: skip
# A cube of one C3D8 element, held at x = 0 and pulled at x = 1: enough for CalculiX to
# factor a system and say which solver it used.
CHECK_DECK = """*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
*ELEMENT,TYPE=C3D8,ELSET=E
1,1,2,3,4,5,6,7,8
*MATERIAL,NAME=S
*ELASTIC
210000,0.3
*SOLID SECTION,ELSET=E,MATERIAL=S
*BOUNDARY
1,1,3
4,1,3
5,1,3
8,1,3
*STEP
*STATIC
*CLOAD
2,1,1
3,1,1
6,1,1
7,1,1
*NODE PRINT,NSET=NALL
U
*END STEP
"""


@dataclass
class Status:
    """Where the PARDISO build stands: `state` is built, building, failed, ready (the
    environment is there, not built yet) or unavailable; `detail` says why, in words."""

    state: str
    detail: str
    program: str | None = None


def prefix() -> Path | None:
    """The build environment (`jobs.runtime.environment`), or None."""
    from puzzle.jobs.runtime import environment

    return environment(ENV)


def bin_dir(env: Path) -> Path:
    return env / "Library" / "bin" if os.name == "nt" else env / "bin"


def program(env: Path) -> Path:
    return bin_dir(env) / ("ccx.exe" if os.name == "nt" else "ccx")


def _state_file() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "solver-builds" / f"{ENV}.json"


def _work_dir() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "solver-builds" / ENV


def _read_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**values) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=1), encoding="utf-8")
    tmp.replace(path)


def status() -> Status:
    env = prefix()
    if env is None:
        why = ("Intel MKL isn't built for Apple silicon" if sys.platform == "darwin"
               and os.uname().machine == "arm64" else
               f"the '{ENV}' environment isn't installed")  # fmt: skip
        return Status("unavailable", why)
    exe = program(env)
    state = _read_state()
    if state.get("state") == "building" and time.time() - state.get("started", 0) < STALE_S:
        return Status("building", f"started {time.ctime(state['started'])}")
    if exe.is_file():
        again = " (the latest rebuild failed; this one is kept)" if state.get(
            "state") == "failed" else ""  # fmt: skip
        return Status("built", f"CalculiX {VERSION} with PARDISO{again}", str(exe))
    if state.get("state") == "failed":
        return Status("failed", state.get("error") or "the build failed")
    return Status("ready", "not built yet: the plain CalculiX (SPOOLES) is used")


# --- the toolchain ---------------------------------------------------------------------


@dataclass
class Toolchain:
    cc: str
    fc: str
    ar: str
    cflags: list[str]
    fflags: list[str]
    link: list[str]
    path: list[str]  # folders put first on PATH while building and checking


def _first(folders: list[Path], names: list[str]) -> str | None:
    for folder in folders:
        for name in names:
            hits = sorted(glob.glob(str(folder / name)))
            if hits:
                return hits[0]
    return None


def toolchain(env: Path) -> Toolchain:
    """The environment's compilers and libraries, as ADR 0051's build used them."""
    exe = ".exe" if os.name == "nt" else ""
    if os.name == "nt":
        lib = env / "Library"
        folders = [lib / "bin", lib / "mingw-w64" / "bin"]
    else:
        lib = env
        folders = [env / "bin"]
    if sys.platform == "darwin":
        cc_names = ["clang", "*-apple-darwin*-clang"]
    else:
        cc_names = [f"gcc{exe}", "*-conda-linux-gnu-gcc", "*-conda-linux-gnu-cc"]
    cc = _first(folders, cc_names) or os.environ.get("CC_FOR_BUILD")
    fc = _first(folders, [f"gfortran{exe}", "*-gfortran"])
    ar = _first(folders, [f"gcc-ar{exe}", "*-gcc-ar", f"ar{exe}", "*-ar", "llvm-ar"])
    ar = ar or shutil.which("ar")
    if not (cc and fc and ar):
        missing = [n for n, v in (("a C compiler", cc), ("gfortran", fc), ("ar", ar)) if not v]
        raise BuildError(f"the '{ENV}' environment has no {' or '.join(missing)}")
    lenient = LENIENT + (CLANG_ONLY if "clang" in Path(cc).name else [])
    includes = [f"-I{lib / 'include' / 'spooles'}", f"-I{lib / 'include'}"]
    cflags = ["-w", *lenient, "-O2", "-fopenmp", *includes, *DEFINES]
    fflags = ["-w", "-O2", "-fopenmp", "-cpp", "-fallow-argument-mismatch"]
    spooles = [str(lib / "lib" / "spoolesMT.a"), str(lib / "lib" / "spooles.a")]
    if os.name == "nt":
        # MKL's single dynamic library picks Intel threading itself; ARPACK is
        # conda-forge's MinGW build; CalculiX's own threads are pthreads.
        link = [*spooles, f"-L{lib / 'mingw-w64' / 'lib'}", "-larpack", f"-L{lib / 'lib'}",
                "-lmkl_rt", "-lpthread", "-lm"]  # fmt: skip
    else:
        link = [f"-L{lib / 'lib'}", f"-Wl,-rpath,{lib / 'lib'}", *spooles, "-larpack",
                "-llapack", "-lblas", "-lmkl_intel_lp64", "-lmkl_intel_thread", "-lmkl_core",
                "-liomp5", "-lpthread", "-lm"]  # fmt: skip
        if sys.platform != "darwin":
            link.append("-ldl")
    return Toolchain(cc, fc, ar, cflags, fflags, link, [str(f) for f in folders])


class BuildError(RuntimeError):
    pass


# --- the build -------------------------------------------------------------------------


def _fetch(work: Path, say) -> Path:
    import hashlib

    tarball = work / f"ccx_{VERSION}.src.tar.bz2"
    if not tarball.is_file():
        say(f"downloading CalculiX {VERSION}'s source from dhondt.de")
        tmp = tarball.with_suffix(".part")
        try:
            with urllib.request.urlopen(URL, timeout=120) as r, open(tmp, "wb") as out:
                shutil.copyfileobj(r, out)
        except OSError as exc:
            why = (
                f"couldn't download CalculiX's source ({exc}); the plain CalculiX stays "
                "in use, and the build is tried again later"
            )
            raise BuildError(why) from None
        tmp.replace(tarball)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != SHA256:
        tarball.unlink()
        raise BuildError(f"CalculiX's source failed its checksum ({digest})")
    return tarball


def _unpack(tarball: Path, work: Path) -> Path:
    tree = work / "CalculiX"
    if tree.exists():
        shutil.rmtree(tree)
    with tarfile.open(tarball, "r:bz2") as tar:
        tar.extractall(work, filter="data")
    src = tree / f"ccx_{VERSION}" / "src"
    if os.name == "nt":
        # Two main files call _set_output_format at file scope under __WIN32 (for old
        # MSVCRT's three-digit exponents); UCRT prints two already and MinGW refuses it.
        for c in src.glob("*.c"):
            text = c.read_text(encoding="latin-1")
            if "\n_set_output_format(_TWO_DIGIT_EXPONENT);" in text:
                c.write_text(text.replace("\n_set_output_format(_TWO_DIGIT_EXPONENT);", "\n"),
                             encoding="latin-1")  # fmt: skip
    return src


def sources(src: Path) -> tuple[list[str], list[str]]:
    """CalculiX's Fortran and C sources, from its Makefile.inc (SCCXF, SCCXC): those that
    exist, each object once. 2.23 lists mafillmm both as .f and .c with only the .f
    there; make never notices, since both give mafillmm.o."""
    text = src.joinpath("Makefile.inc").read_text(encoding="latin-1").replace("\\\n", " ")
    lists = {}
    for name in ("SCCXF", "SCCXC"):
        m = re.search(rf"^{name}\s*=\s*(.*)$", text, re.M)
        if not m:
            raise BuildError(f"CalculiX's Makefile.inc has no {name}")
        lists[name] = m.group(1).split()
    seen: set[str] = set()
    out = {}
    for name in ("SCCXF", "SCCXC"):
        kept = []
        for f in lists[name]:
            stem = Path(f).stem
            if stem in seen or not (src / f).is_file():
                continue
            seen.add(stem)
            kept.append(f)
        out[name] = kept
    return out["SCCXF"], out["SCCXC"]


def _env(tools: Toolchain) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*tools.path, env.get("PATH", "")])
    return env


def _run(argv: list[str], cwd: Path, env: dict, log) -> None:
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          errors="replace")  # fmt: skip
    log.write(" ".join(argv) + "\n" + done.stdout + done.stderr)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-6:]
        raise BuildError(f"{Path(argv[0]).name} failed on {argv[-1]}: " + " | ".join(tail))


def _compile(src: Path, tools: Toolchain, jobs: int, say, log) -> Path:
    fortran, c = sources(src)
    main = f"ccx_{VERSION}.c"
    env = _env(tools)
    work = [([tools.fc, *tools.fflags, "-c", f], f) for f in fortran]
    work += [([tools.cc, *tools.cflags, "-c", f], f) for f in [*c, main]]
    total, done = len(work), 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_run, argv, src, env, log) for argv, _ in work]
        for f in futures:
            f.result()
            done += 1
            if done % 100 == 0 or done == total:
                say(f"compiled {done} of {total} files")
    objects = [Path(n).with_suffix(".o").name for n in [*fortran, *c]]
    archive = src / "ccx.a"
    archive.unlink(missing_ok=True)
    # In slices: Windows' command line holds 32 767 characters.
    for i in range(0, len(objects), 200):
        _run([tools.ar, "rcs", "ccx.a", *objects[i : i + 200]], src, env, log)
    out = src / ("ccx.exe" if os.name == "nt" else "ccx")
    say("linking ccx")
    _run([tools.fc, "-fopenmp", "-O2", "-o", out.name, Path(main).with_suffix(".o").name,
          "ccx.a", *tools.link], src, env, log)  # fmt: skip
    return out


def check(exe: Path, tools: Toolchain | None = None, workdir: Path | None = None) -> str:
    """Solve a one-element deck with `exe` and return its log; raises BuildError unless
    it says PARDISO factored the system."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(workdir or tmp)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cube.inp").write_text(CHECK_DECK, encoding="ascii", newline="\n")
        env = _env(tools) if tools else dict(os.environ)
        env["OMP_NUM_THREADS"] = "1"
        done = subprocess.run([str(exe), "cube"], cwd=folder, env=env, capture_output=True,
                              text=True, errors="replace", timeout=300)  # fmt: skip
        text = done.stdout + done.stderr
    if done.returncode != 0 or "pardiso" not in text.lower():
        tail = " | ".join(text.strip().splitlines()[-4:])
        raise BuildError(f"the new ccx didn't solve with PARDISO: {tail}")
    return text


def _install(built: Path, target: Path) -> None:
    """Put the new program in place. Windows won't overwrite a program that is running
    (a solve may be), but lets it be renamed: the old one moves aside first, and is
    removed when it no longer runs."""
    tmp = target.with_name(target.name + ".new")
    shutil.copy2(built, tmp)
    old = target.with_name(target.stem + ".old" + target.suffix)
    with contextlib.suppress(OSError):  # still running from an earlier swap: next time
        old.unlink(missing_ok=True)
    if target.exists() and os.name == "nt":
        with contextlib.suppress(OSError):
            target.replace(old)
    tmp.replace(target)


def build(say=print, jobs: int | None = None) -> Status:
    """Build CalculiX with PARDISO into its environment (module docstring). Progress goes
    to `say`; the outcome is kept for `status` (a failure's reason too)."""
    env = prefix()
    if env is None:
        return status()
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    work = _work_dir()
    work.mkdir(parents=True, exist_ok=True)
    _write_state(state="building", started=time.time(), pid=os.getpid())
    try:
        tools = toolchain(env)
        src = _unpack(_fetch(work, say), work)
        with open(work / "build.log", "w", encoding="utf-8") as log:
            out = _compile(src, tools, jobs, say, log)
        say("checking it solves with PARDISO")
        check(out, tools)
        _install(out, program(env))
    except BuildError as exc:
        _write_state(state="failed", finished=time.time(), error=str(exc))
        say(f"the build failed: {exc}")
        return Status("failed", str(exc))  # an earlier build, if any, stays in use
    except Exception as exc:  # anything else: report it, keep the plain CalculiX
        _write_state(state="failed", finished=time.time(), error=f"{type(exc).__name__}: {exc}")
        raise
    _write_state(state="built", finished=time.time())
    say(f"CalculiX {VERSION} with PARDISO installed: {program(env)}")
    return status()


def should_build() -> bool:
    """Whether to build in the background now: the environment is there, nothing is
    built or building, an earlier failure is a day old (a download may work now), and
    the preference allows it."""
    from puzzle.settings import current

    if not current().get("solvers.build_pardiso"):
        return False
    s = status()
    if s.state == "ready":
        return True
    if s.state == "failed":
        return time.time() - _read_state().get("finished", 0) > 24 * 3600
    return False


def as_dict(s: Status) -> dict:
    return asdict(s)


# --- the Solvers page (ADR 0068 decision 6) ----------------------------------------------


@dataclass
class SolverRow:
    """One solver as the Solvers page shows it: `how` is native, container or missing;
    `where` the program, image or reason; `note` anything else (PARDISO's state)."""

    solver: str
    name: str
    how: str
    where: str
    note: str = ""


def solvers() -> list[SolverRow]:
    """Every installed adapter's solver and how a solve would run it here now."""
    from puzzle.adapters import SOLVER_NAMES, available_adapters
    from puzzle.jobs.runtime import EXECUTABLES, ContainerRuntime, SolverNotFound, runtime_for

    rows = []
    for solver in sorted(available_adapters()):
        name = SOLVER_NAMES.get(solver, solver)
        try:
            runtime = runtime_for(solver)
        except (SolverNotFound, ValueError) as exc:
            rows.append(SolverRow(solver, name, "missing", str(exc)))
            continue
        if isinstance(runtime, ContainerRuntime):
            rows.append(SolverRow(solver, name, "container",
                                  f"{runtime.engine} image {runtime.image}"))  # fmt: skip
            continue
        if solver == "fenicsx":
            from puzzle.adapters.fenicsx.case import fenicsx_python

            python = fenicsx_python()
            where = str(python) if python else "no Python with dolfinx found"
            rows.append(SolverRow(solver, name, "native" if python else "missing", where))
            continue
        exe = EXECUTABLES.get(solver)
        found = shutil.which(exe, path=runtime._path()) if exe else None
        if found is None:
            rows.append(SolverRow(solver, name, "missing",
                                  f"'{exe}' not found, and no container image"))  # fmt: skip
            continue
        note = ""
        if solver == "calculix":
            s = status()
            uses = Path(found).resolve() == Path(s.program).resolve() if s.program else False
            note = ("PARDISO" if uses else
                    f"SPOOLES; PARDISO: {s.state} ({s.detail})")  # fmt: skip
        rows.append(SolverRow(solver, name, "native", found, note))
    return rows


if __name__ == "__main__":  # python -m puzzle.solver_builds
    result = build()
    print(result.state, "-", result.detail)
    sys.exit(0 if result.state == "built" else 1)
