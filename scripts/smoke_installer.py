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
4. Checks each solver environment the installer holds answers (`ccx`, dolfinx, Netgen,
   MMG, the scripts and runner Pythons).

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


def step(i: int, n: int, what: str) -> None:
    print(f"[progress] {i}/{n} {what}", flush=True)


def install(installer: Path, prefix: Path, shortcuts: bool) -> Path:
    kind = installer.suffix
    if kind == ".sh":
        argv = ["bash", str(installer), "-b", "-p", str(prefix)] + ([] if shortcuts else ["-m"])
    elif kind == ".exe":
        argv = [str(installer), "/S", f"/NoShortcuts={0 if shortcuts else 1}",
                "/InstallationType=JustMe", f"/D={prefix}"]  # fmt: skip
    elif kind == ".pkg":
        argv = ["installer", "-pkg", str(installer), "-target", "CurrentUserHomeDirectory"]
        prefix = Path.home() / "Puzzle"
    else:
        raise SystemExit(f"not an installer: {installer}")
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)
    return prefix


def clean_env() -> dict[str, str]:
    """An environment that knows nothing of this checkout's solvers or Python."""
    keep = ("HOME", "USER", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
            "SYSTEMROOT", "SystemRoot", "COMSPEC", "LANG", "TMPDIR")  # fmt: skip
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PATH"] = os.pathsep.join(
        ["C:\\Windows\\System32", "C:\\Windows"] if WINDOWS else ["/usr/bin", "/bin"]
    )
    env["PUZZLE_RUNNER"] = "off"  # one solve, here and now
    return env


def run(argv: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    print("+", " ".join(argv), flush=True)
    done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True)
    print(done.stdout[-3000:], done.stderr[-3000:], sep="\n", flush=True)
    if done.returncode:
        raise SystemExit(f"failed ({done.returncode}): {' '.join(argv)}")
    return done.stdout


def example(dest: Path) -> Path:
    data = subprocess.run(["git", "archive", "HEAD", "examples/cantilever"], cwd=ROOT,
                          capture_output=True, check=True).stdout  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "examples" / "cantilever"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("installer", type=Path)
    p.add_argument("--prefix", type=Path, help="where to install (default: a temp folder)")
    p.add_argument("--shortcuts", action="store_true", help="make the menu entries too")
    p.add_argument("--keep", action="store_true", help="leave the installation there")
    args = p.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="puzzle-smoke-"))
    prefix = args.prefix or work / "Puzzle"
    n = 4
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
        if args.shortcuts:
            menu = prefix / "Menu" / "puzzle-fem.json"
            assert menu.exists(), menu

        step(3, n, "solving the cantilever with the installed CalculiX")
        project = example(work / "copy")
        run([str(puzzle), "run", str(project), "--study", "Tip load"], env)
        out = run([str(puzzle), "runs", str(project)], env)
        assert "Tip load" in out, out

        step(4, n, "each solver environment answers")
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
                run([str(exe), "-c", code], env)
        if "mmg" in found:
            bin_dir = envs / "mmg" / ("Library/bin" if WINDOWS else "bin")
            assert any(bin_dir.glob("mmg3d*")), bin_dir
        print(f"[progress] {n}/{n} done: {args.installer.name} works", flush=True)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
            if args.installer.suffix == ".pkg" and not args.prefix:
                shutil.rmtree(prefix, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
