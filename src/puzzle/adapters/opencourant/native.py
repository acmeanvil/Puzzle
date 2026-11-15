"""OpenCourant run natively from its release (ADR 0085 step 3), when no container runs.

The release zip for this platform is fetched on first use into
`<user data dir>/solvers/opencourant/<release>/`, checked against its SHA-256 (pinned
here with the release) and unpacked there; never in the installer (the AGPL allows it,
but 80-330 MB for a solver many won't use). `PUZZLE_OPENCOURANT_PATH` names a build of
the user's own instead (a folder holding `exec/`).

The driver (`python -m puzzle.adapters.opencourant.native`, run by the native runtime
in the run folder) does what `run.sh` does in the image: the Starter, the Engine (under
`mpiexec` with processes > 1, where one is on the path), then the frames to VTK and the
history to CSV, with OpenCourant's environment set (OPENCOURANT_PATH, RAD_CFG_PATH,
RAD_H3D_PATH, the stack size, its libraries on PATH or LD_LIBRARY_PATH). Its output goes
to stdout, so the progress parser reads the Engine's cycle lines as in the container.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "latest-20261011"
URL = "https://github.com/OpenCourant/OpenCourant/releases/download/{release}/{name}"
ZIPS = {  # platform -> (file, SHA-256), from the release's downloads page
    "win64": ("OpenCourant_win64.zip",
              "6d0ea8b43b68aa12f0a2787b339384a554978cbb65a5d3dbaf1e40598f02e7e6"),
    "linux64": ("OpenCourant_linux64.zip",
                "6cb419677ca36d73acb96218cc0cf5e4f7d50494d9c7812ba27935e3c0f8ec94"),
    "linuxa64": ("OpenCourant_linuxa64.zip",
                 "ded9814e2460767353d591c89695ba1f76251291041943ab0ad73070a6c865f7"),
}  # fmt: skip
SUFFIX = {"win64": "_win64", "linux64": "_linux64_gf", "linuxa64": "_linuxa64_gf"}
STACK = "400m"


def platform_key() -> str | None:
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "win64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux64"
    if sys.platform.startswith("linux") and machine in ("aarch64", "arm64"):
        return "linuxa64"
    return None


def home() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "solvers" / "opencourant" / RELEASE


def root() -> Path | None:
    """The OpenCourant folder to run (it holds exec/), or None when none is here."""
    own = os.environ.get("PUZZLE_OPENCOURANT_PATH")
    if own:
        return Path(own) if (Path(own) / "exec").is_dir() else None
    folder = home() / "OpenCourant"
    return folder if (folder / "exec").is_dir() else None


def available() -> bool:
    """Whether a native run can go ahead: installed, or fetchable for this platform."""
    return root() is not None or platform_key() is not None


def fetch(say=print, url: str | None = None, digest: str | None = None) -> Path:
    """Download this platform's release, check its SHA-256, unpack it; the folder."""
    key = platform_key()
    if key is None:
        raise RuntimeError(
            f"OpenCourant has no release for {sys.platform} "
            f"{platform.machine()}: use its container (Docker or Podman)"
        )
    name, pinned = ZIPS[key]
    url = url or URL.format(release=RELEASE, name=name)
    digest = digest or pinned
    target = home()
    target.mkdir(parents=True, exist_ok=True)
    say(f"Fetching OpenCourant {RELEASE} ({name}) from its release, once…")
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        archive = Path(tmp) / name
        h = hashlib.sha256()
        request = urllib.request.Request(url, headers={"User-Agent": "puzzle"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            while chunk := response.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != digest:
            raise RuntimeError(f"{name}'s SHA-256 is {h.hexdigest()}, not the release's "
                               f"{digest}: not unpacked")  # fmt: skip
        unpacked = Path(tmp) / "unpacked"
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():  # nothing outside the folder (no .. or absolute)
                dest = (unpacked / member).resolve()
                if not dest.is_relative_to(unpacked.resolve()):
                    raise RuntimeError(f"{name} holds a path outside it: {member}")
            z.extractall(unpacked)
        folder = unpacked / "OpenCourant"
        if not (folder / "exec").is_dir():
            raise RuntimeError(f"{name} has no OpenCourant/exec folder")
        if key != "win64":
            for exe in (folder / "exec").iterdir():
                exe.chmod(exe.stat().st_mode | 0o755)
        final = target / "OpenCourant"
        if final.exists():
            shutil.rmtree(final)
        folder.rename(final)
    say(f"OpenCourant {RELEASE} is in {final}")
    return final


def environment(folder: Path, base: dict | None = None) -> dict[str, str]:
    """OpenCourant's variables (its INSTALL.md) over `base`."""
    key = platform_key() or "linux64"
    env = dict(os.environ if base is None else base)
    env["OPENCOURANT_PATH"] = str(folder)
    env["RAD_CFG_PATH"] = str(folder / "hm_cfg_files")
    plat = "win64" if key == "win64" else key
    env["RAD_H3D_PATH"] = str(folder / "extlib" / "h3d" / "lib" / plat)
    env.setdefault("OMP_STACKSIZE", STACK)
    env.setdefault("KMP_STACKSIZE", STACK)
    libs = [folder / "extlib" / "hm_reader" / plat, folder / "extlib" / "h3d" / "lib" / plat]
    if key == "win64":
        libs.append(folder / "extlib" / "intelOneAPI_runtime" / "win64")
        env["PATH"] = os.pathsep.join([*map(str, libs), env.get("PATH", "")])
    else:
        env["LD_LIBRARY_PATH"] = os.pathsep.join([*map(str, libs), env.get("LD_LIBRARY_PATH", "")])
    return env


def _run(argv: list[str], env: dict, cwd: Path, quiet: bool = False) -> int:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.STDOUT if not quiet else subprocess.DEVNULL,
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    """Run the deck in the current folder (its run.json says the name, precision and
    processes); fetch OpenCourant first if it isn't here."""
    workdir = Path.cwd()
    job = json.loads((workdir / "run.json").read_text(encoding="utf-8"))
    name, processes = job["name"], int(job.get("processes", 1))
    sp = "_sp" if job.get("precision") == "single" else ""
    folder = root()
    if folder is None:
        folder = fetch(lambda s: print(s, flush=True))
    key = platform_key() or "linux64"
    exe = folder / "exec"
    suffix = SUFFIX[key] + (".exe" if key == "win64" else "")
    env = environment(folder)
    starter = exe / f"starter{SUFFIX[key]}{sp}{'.exe' if key == 'win64' else ''}"
    rc = _run([str(starter), "-i", f"{name}_0000.rad", "-np", str(processes)], env, workdir)
    if rc != 0:
        return rc
    mpiexec = shutil.which("mpiexec", path=env.get("PATH"))
    if processes > 1 and mpiexec:
        mpi = "_impi" if key == "win64" else "_ompi"
        engine = exe / f"engine{SUFFIX[key]}{mpi}{sp}{'.exe' if key == 'win64' else ''}"
        extra = ["-localonly"] if key == "win64" else []
        command = [mpiexec, *extra, "-np", str(processes), str(engine)]
    else:
        if processes > 1:
            print(f"no mpiexec on the path: the Engine runs as one process "
                  f"(the model was split in {processes})", flush=True)  # fmt: skip
        command = [str(exe / f"engine{SUFFIX[key]}{sp}{'.exe' if key == 'win64' else ''}")]
    rc = _run([*command, "-i", f"{name}_0001.rad"], env, workdir)
    if rc != 0:
        return rc
    anim = exe / f"anim_to_vtk{suffix}"
    for frame in sorted(workdir.glob(f"{name}A[0-9][0-9][0-9]")):
        with (workdir / f"{frame.name}.vtk").open("wb") as out:
            subprocess.run([str(anim), frame.name], cwd=workdir, env=env, stdout=out, check=True)
    history = workdir / f"{name}T01"
    if history.exists():
        _run([str(exe / f"th_to_csv{suffix}"), history.name], env, workdir, quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
