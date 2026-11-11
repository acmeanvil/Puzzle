"""Puzzle's Linux machine on Windows (ADR 0068 step 5): solvers with no good Windows
build (Elmer, Code_Saturne, OpenFOAM; Code_Aster and FEniCSx faster there) run in WSL 2
distributions of Puzzle's own, without Docker.

Each solver's Linux environment is a distribution named `puzzle-<solver>`, imported
(`wsl --import`) into Puzzle's user folder from a root file system: one exported from
the solver's container image (`install_from_image`, Docker or Podman needed once, then
never), or a tarball the installer ships (`install_from_tarball`). The image's
configuration (its entrypoint, which activates OpenFOAM's or conda's environment, and
its ENV, which `wsl --exec` doesn't apply) is kept beside it (`machine.json`), so a solve
runs as it would in the container: `wsl -d puzzle-<solver> --cd /mnt/c/... --exec env
VARS... <entrypoint> <command>`, the run folder reached through /mnt (no copy), the
thread variables given per run.

`runtime_for` takes it on Windows when a solver's container can't run (no engine, or
the engine is off) and its distribution is there, before a native fallback; or always
with `PUZZLE_<SOLVER>_RUNTIME=wsl` / `PUZZLE_RUNTIME=wsl`. macOS's equivalent (Lima) is
not built yet.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from puzzle.jobs.runtime import SERIAL_BLAS, THREAD_VARIABLES, Runtime, thread_environment

PREFIX = "puzzle-"


def wsl() -> str | None:
    """wsl.exe, or None (not Windows, or WSL not installed)."""
    if os.name != "nt":
        return None
    found = shutil.which("wsl")
    if found:
        return found
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "wsl.exe"
    return str(system) if system.exists() else None


def distro_for(solver: str) -> str:
    return PREFIX + solver.replace("_", "-")


def _home() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "machine"


def _run(argv: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=timeout)


def _text(data: bytes) -> str:
    """wsl.exe's own output is UTF-16 (little endian); its programs' is UTF-8."""
    if data[:2] == b"\xff\xfe" or (len(data) > 1 and data[1:2] == b"\x00"):
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def distros() -> list[str]:
    """Every WSL distribution on this computer (Puzzle's and the user's own)."""
    exe = wsl()
    if exe is None:
        return []
    try:
        done = _run([exe, "--list", "--quiet"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return [line.strip().strip("\x00") for line in _text(done.stdout).splitlines()
            if line.strip().strip("\x00")]  # fmt: skip


_SEEN: dict = {"at": 0.0, "names": []}


def _distros_cached() -> set[str]:
    """`distros()`, at most every 30 s: runtime_for asks often."""
    import time

    if time.monotonic() - _SEEN["at"] > 30:
        _SEEN.update(at=time.monotonic(), names=distros())
    return set(_SEEN["names"])


def installed() -> dict[str, dict]:
    """Puzzle's distributions by solver, with their kept configuration. Nothing is asked
    of WSL unless Puzzle has made one (its machine.json files)."""
    home = _home()
    configs = sorted(home.glob("*/machine.json")) if home.is_dir() else []
    if not configs:
        return {}
    names = _distros_cached()
    out = {}
    for folder in (c.parent for c in configs):
        config = folder / "machine.json"
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("distro") in names:
            out[data["solver"]] = data
    return out


def linux_path(path: Path) -> str:
    """A Windows path as WSL sees it: C:\\X\\Y -> /mnt/c/X/Y."""
    p = PureWindowsPath(str(Path(path).resolve()))
    if not p.drive or not p.drive.endswith(":"):
        raise MachineError(f"'{path}' is not on a drive WSL mounts")
    rest = "/".join(p.parts[1:])
    return f"/mnt/{p.drive[0].lower()}/{rest}".rstrip("/")


class MachineError(RuntimeError):
    pass


def _import(solver: str, tarball: Path, config: dict) -> dict:
    exe = wsl()
    if exe is None:
        raise MachineError("WSL isn't installed: in an administrator terminal, `wsl --install`")
    name = distro_for(solver)
    if name in distros():
        raise MachineError(f"'{name}' exists already: remove it first (puzzle machine remove)")
    folder = _home() / solver
    folder.mkdir(parents=True, exist_ok=True)
    done = _run([exe, "--import", name, str(folder), str(tarball), "--version", "2"],
                timeout=3600)  # fmt: skip
    if done.returncode != 0:
        raise MachineError(f"wsl --import failed: {_text(done.stderr or done.stdout).strip()}")
    data = {"solver": solver, "distro": name, **config}
    (folder / "machine.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    _SEEN["at"] = 0.0  # look again
    return data


def install_from_tarball(solver: str, tarball: Path, entrypoint=(), env=()) -> dict:
    """A root file system (a tar) as the solver's distribution."""
    return _import(solver, Path(tarball), {"entrypoint": list(entrypoint), "env": list(env),
                                           "from": str(tarball)})  # fmt: skip


def install_from_image(solver: str, image: str | None = None, engine: str = "docker",
                       say=print) -> dict:  # fmt: skip
    """The solver's container image exported once and imported as its distribution:
    Docker or Podman is needed for this, never afterwards."""
    from puzzle.jobs.runtime import DEFAULT_IMAGES, find_engine

    image = image or DEFAULT_IMAGES.get(solver)
    exe = find_engine(engine)
    if image is None or exe is None:
        raise MachineError(f"no {engine} or no image for {solver}")
    inspect = _run([exe, "image", "inspect", "-f", "{{json .Config}}", image], timeout=120)
    if inspect.returncode != 0:
        raise MachineError(f"{image} isn't here: {_text(inspect.stderr).strip()}")
    config = json.loads(_text(inspect.stdout) or "{}")
    created = _run([exe, "create", image], timeout=300)
    if created.returncode != 0:
        raise MachineError(f"couldn't create a container of {image}")
    container = _text(created.stdout).strip()
    tarball = _home() / f"{solver}.tar"
    tarball.parent.mkdir(parents=True, exist_ok=True)
    try:
        say(f"exporting {image}'s file system")
        done = _run([exe, "export", container, "-o", str(tarball)], timeout=3600)
        if done.returncode != 0:
            raise MachineError(f"export failed: {_text(done.stderr).strip()}")
        say(f"importing it as {distro_for(solver)}")
        return _import(solver, tarball, {"entrypoint": config.get("Entrypoint") or [],
                                         "env": config.get("Env") or [],
                                         "workdir": config.get("WorkingDir") or "",
                                         "from": image})  # fmt: skip
    finally:
        _run([exe, "rm", "-f", container], timeout=120)
        tarball.unlink(missing_ok=True)


def remove(solver: str) -> None:
    exe = wsl()
    name = distro_for(solver)
    if exe is not None and name in distros():
        _run([exe, "--unregister", name], timeout=600)
    folder = _home() / solver
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    _SEEN["at"] = 0.0


@dataclass(frozen=True)
class WslRuntime(Runtime):
    """A solve in Puzzle's Linux machine (module docstring)."""

    solver: str
    distro: str
    entrypoint: tuple[str, ...] = ()
    env: tuple[str, ...] = field(default=())

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        exe = wsl() or "wsl"
        threads = thread_environment()
        given = [f"{k}={threads[k]}" for k in (*THREAD_VARIABLES, *SERIAL_BLAS)]
        return [exe, "-d", self.distro, "--cd", linux_path(workdir), "--exec", "env",
                "HOME=/tmp", *self.env, *given, *self.entrypoint, *command]  # fmt: skip

    def describe(self) -> str:
        return f"wsl:{self.distro}"

    def available(self, executable: str) -> bool:
        return self.distro in distros()


def runtime(solver: str) -> WslRuntime | None:
    """The solver's machine runtime, when its distribution is installed here."""
    found = installed().get(solver)
    if found is None:
        return None
    return WslRuntime(solver, found["distro"], tuple(found.get("entrypoint") or ()),
                      tuple(found.get("env") or ()))  # fmt: skip
