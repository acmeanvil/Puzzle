"""Install a built installer somewhere disposable and check Puzzle works (ADR 0068).

    pixi run -e packaging python scripts/smoke_installer.py INSTALLER [--prefix DIR]
                                                            [--shortcuts] [--keep]

1. Installs in batch mode, asking nothing: `.sh` with `-b -p`, `.exe` with `/S /D=`,
   `.pkg` with `installer -target CurrentUserHomeDirectory` (into ~/Puzzle, CI only).
   Shortcuts only with `--shortcuts`, so a run on your own machine adds no menu entry.
2. Runs the installed `puzzle --help`, imports the GUI, sketch and boolean pieces.
3. Solves the cantilever example's Tip load with CalculiX, with nothing on PATH or
   PUZZLE_SOLVER_PATH pointing at it, so the installed Puzzle must find its own
   `envs/calculix` (`jobs.runtime.environment`), and reads the result back.
4. Checks each solver environment the installer holds answers (dolfinx, Netgen, MMG,
   the scripts and runner Pythons), each started with the environment Puzzle gives
   solvers (`NativeRuntime.environment`, so Windows' activation scripts too).
5. Solves the Tip load again with the installed FEniCSx.

The example is copied with `git archive`, never touched in place.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


TIMES: list[tuple[str, float]] = []  # each step and its seconds, printed at the end
_started: list[float] = []


def step(i: int, n: int, what: str) -> None:
    now = time.monotonic()
    if _started and TIMES:
        TIMES[-1] = (TIMES[-1][0], now - _started[-1])
    _started.append(now)
    TIMES.append((what, 0.0))
    print(f"[progress] {i}/{n} {what}", flush=True)


def install(installer: Path, prefix: Path, shortcuts: bool) -> Path:
    kind = installer.suffix
    if kind == ".sh":
        argv = ["bash", str(installer), "-b", "-p", str(prefix)] + ([] if shortcuts else ["-m"])
    elif kind == ".exe":
        # NSIS wants /D= last and unquoted, spaces and all, so this is one string.
        argv = (f'"{installer}" /S /NoShortcuts={0 if shortcuts else 1} '
                f"/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /D={prefix}")  # fmt: skip
    elif kind == ".pkg":
        argv = ["installer", "-pkg", str(installer), "-target", "CurrentUserHomeDirectory"]
        prefix = Path.home() / "Puzzle"
    else:
        raise SystemExit(f"not an installer: {installer}")
    print("+", argv if isinstance(argv, str) else " ".join(argv), flush=True)
    done = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    print(done.stdout[-5000:], done.stderr[-5000:], sep="\n", flush=True)
    if done.returncode:
        for log in sorted(prefix.glob("*.log")) if prefix.is_dir() else []:
            print(f"--- {log}", log.read_text(errors="replace", encoding="utf-8")[-5000:], sep="\n")
        if kind == ".pkg":  # installer says only that a script failed; its log says which
            log = Path("/var/log/install.log")
            if log.exists():
                tail = [ln for ln in log.read_text(errors="replace", encoding="utf-8").splitlines()
                        if "Puzzle" in ln or "install" in ln.lower()][-80:]  # fmt: skip
                print("--- /var/log/install.log", *tail, sep="\n")
        print(shutil.disk_usage(prefix.parent if prefix.parent.exists() else Path.home()))
        raise SystemExit(f"the installer failed ({done.returncode})")
    return prefix


def uninstall(prefix: Path) -> None:
    """The Windows installer registers an uninstaller; run it so nothing stays listed."""
    exe = prefix / "Uninstall-Puzzle.exe"
    if WINDOWS and exe.exists():
        subprocess.run(f'"{exe}" /S _?={prefix}', check=False)


def clean_env() -> dict[str, str]:
    """An environment that knows nothing of this checkout's solvers or Python."""
    keep = ("HOME", "USER", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
            "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "TMPDIR",
            "PROGRAMDATA", "USERNAME", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE")  # fmt: skip
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PATH"] = os.pathsep.join(
        ["C:\\Windows\\System32", "C:\\Windows"] if WINDOWS else ["/usr/bin", "/bin"]
    )
    env["PUZZLE_RUNNER"] = "off"  # one solve, here and now
    if os.environ.get("PUZZLE_VIEW_NO_RENDER"):  # CI without OpenGL: ParaView's data only
        env["PUZZLE_VIEW_NO_RENDER"] = os.environ["PUZZLE_VIEW_NO_RENDER"]
    return env


def run(argv: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    print("+", " ".join(argv), flush=True)
    done = subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    print(done.stdout[-3000:], done.stderr[-3000:], sep="\n", flush=True)
    if done.returncode:
        raise SystemExit(f"failed ({done.returncode}): {' '.join(argv)}")
    return done.stdout


# Run an environment's Python with the environment Puzzle gives solvers
# (`NativeRuntime.environment`: PATH, CONDA_PREFIX and on Windows the activation scripts).
AS_PUZZLE = """
import subprocess, sys
from puzzle.jobs.runtime import NativeRuntime
argv = [sys.argv[1], "-c", sys.argv[2]]
sys.exit(subprocess.run(argv, env=NativeRuntime().environment(argv)).returncode)
"""
# The copied cantilever's study switched to FEniCSx, as the GUI would (a command).
TO_FENICSX = """
import sys
from pathlib import Path
from puzzle.commands import Session
Session(Path(sys.argv[1])).submit({"command": "edit", "object": "Tip load",
                                   "ops": [{"op": "set", "path": "solver", "value": "fenicsx"}]})
"""


PARAVIEW = """
import subprocess, sys
from pathlib import Path
import pyvista as pv
from puzzle.post import paraview_link
folder = Path(sys.argv[1])
grid = pv.ImageData(dimensions=(5, 3, 3)).cast_to_unstructured_grid()
grid.point_data["Temperature [K]"] = grid.points[:, 0]
script = paraview_link.write(folder, grid, paraview_link.View(array="Temperature [K]"))
exe = paraview_link.find()
assert exe is not None, "no ParaView in the installation"
batch = [p for p in exe.parent.glob("pvbatch*")]
assert batch, f"no pvbatch beside {exe}"
done = subprocess.run([str(batch[0]), str(script)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=600,
                      env=paraview_link.environment(batch[0]))  # as Puzzle starts it
print(done.stdout[-1500:], done.stderr[-1500:])
assert done.returncode == 0, done.returncode
assert "Traceback" not in done.stdout + done.stderr
print("ParaView ran Puzzle's view:", exe)
"""


def example(dest: Path) -> Path:
    data = subprocess.run(["git", "archive", "HEAD", "examples/cantilever"], cwd=ROOT,
                          capture_output=True, check=True).stdout  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "examples" / "cantilever"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # the CI console's cp1252 can't print all of it
        stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("installer", type=Path)
    p.add_argument("--prefix", type=Path, help="where to install (default: a temp folder)")
    p.add_argument("--shortcuts", action="store_true", help="make the menu entries too")
    p.add_argument("--keep", action="store_true", help="leave the installation there")
    args = p.parse_args(argv)
    # resolve(): Windows' temp folder can come as a short name (RUNNER~1), and the
    # installer refuses a destination with a "~" in it.
    work = Path(tempfile.mkdtemp(prefix="puzzle-smoke-")).resolve()
    prefix = args.prefix or work / "Puzzle"
    n = 5
    try:
        step(1, n, f"installing {args.installer.name}")
        prefix = install(args.installer.resolve(), prefix, args.shortcuts)
        env = clean_env()
        env["PUZZLE_USER_DIR"] = str(work / "user")  # no settings of yours
        python = prefix / ("python.exe" if WINDOWS else "bin/python")
        puzzle = prefix / ("Scripts/puzzle.exe" if WINDOWS else "bin/puzzle")

        step(2, n, "puzzle --help and imports")
        run([str(puzzle), "--help"], env)
        run([str(python), "-c", "import puzzle.gui.theme, py_slvs.slvs, manifold3d, OCP, gmsh;"
             " print('imports ok')"], env)  # fmt: skip
        if "arm64" not in args.installer.name:  # no conda-forge hidapi there yet (ADR 0069)
            run([str(python), "-c", "import hid; print('3D mouse: hidapi lists',"
                 " len(hid.enumerate()), 'devices')"], env)  # fmt: skip
        # ParaView (ADR 0070): a view written by the installed Puzzle, run off screen.
        run([str(python), "-c", PARAVIEW, str(work / "paraview")], env)
        if args.shortcuts:
            menu = prefix / "Menu" / "puzzle-fem.json"
            assert menu.exists(), menu

        step(3, n, "solving the cantilever with the installed CalculiX")
        project = example(work / "copy")
        run([str(puzzle), "run", str(project), "--study", "Tip load"], env)
        out = run([str(puzzle), "runs", str(project)], env)
        assert "Tip load" in out, out

        step(4, n, "each solver environment answers, started as Puzzle starts solvers")
        envs = prefix / "envs"
        found = sorted(e.name for e in envs.iterdir()) if envs.is_dir() else []
        print("environments:", ", ".join(found))
        for name, code in {
            "fenicsx": "import dolfinx; print('dolfinx', dolfinx.__version__)",
            "netgen": "import netgen; print('netgen ok')",
            "scripts": "import numpy, scipy, polars; print('scripts ok')",
            "runner": "import psij; print('psij ok')",
        }.items():
            if name in found:
                exe = envs / name / ("python.exe" if WINDOWS else "bin/python")
                run([str(python), "-c", AS_PUZZLE, str(exe), code], env)
        if "mmg" in found:  # conda-forge's Windows mmgsuite uses bin\, not Library\bin
            dirs = [envs / "mmg" / d for d in ("bin", "Library/bin")]
            assert any(f for d in dirs for f in d.glob("mmg3d*")), dirs

        step(5, n, "solving the cantilever with the installed FEniCSx")
        if "fenicsx" in found:
            run([str(python), "-c", TO_FENICSX, str(project)], env)
            out = run([str(puzzle), "run", str(project), "--study", "Tip load"], env)
            assert "fenicsx" in out and "0.0015" in out, out
        step(n, n, f"done: {args.installer.name} works")
        files = (f for f in prefix.rglob("*") if f.is_file() and not f.is_symlink())
        size = sum(f.stat().st_size for f in files)
        print(f"installed size: {size / 1e9:.2f} GB")
        for what, seconds in TIMES[:-1]:
            print(f"  {seconds:7.1f} s  {what}")
    finally:
        if not args.keep:
            uninstall(prefix)
            shutil.rmtree(work, ignore_errors=True)
            if args.installer.suffix == ".pkg" and not args.prefix:
                shutil.rmtree(prefix, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
