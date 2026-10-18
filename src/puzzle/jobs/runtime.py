"""Where a solver command runs: on the host, or inside a container image.

Selection (ADR 0013), per solver:
1. `PUZZLE_<SOLVER>_RUNTIME`, else `PUZZLE_RUNTIME`: "auto" (default), "native",
   "docker" or "podman".
2. "auto" runs natively when the solver's executable is found, otherwise in its
   container image when an engine (podman, then docker) has that image locally,
   otherwise natively (which reports the solver as missing).
3. For containers, `PUZZLE_<SOLVER>_IMAGE` overrides the default image.
4. For native runs, `PUZZLE_SOLVER_PATH` (os.pathsep-separated) is searched before PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# Images built from containers/<solver>/Dockerfile. Not published yet.
DEFAULT_IMAGES = {
    "calculix": "ghcr.io/acmeanvil/puzzle-calculix:2.23",
    "elmer": "ghcr.io/acmeanvil/puzzle-elmer:26.2",
    "code_aster": "ghcr.io/acmeanvil/puzzle-code-aster:18.1",
    "code_saturne": "ghcr.io/acmeanvil/puzzle-code-saturne:9.2",
}

# The executable that proves a solver is installed natively.
EXECUTABLES = {
    "calculix": "ccx",
    "elmer": "ElmerSolver",
    "code_aster": "run_aster",
    "code_saturne": "code_saturne",
}

CONTAINER_WORKDIR = "/work"
ENGINES = ("podman", "docker")


class SolverNotFound(RuntimeError):
    pass


class Runtime(ABC):
    @abstractmethod
    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        """The full argv that runs `command` with `workdir` as its working directory."""

    @abstractmethod
    def describe(self) -> str:
        """A short label recorded with each run, e.g. the image name."""

    @abstractmethod
    def available(self, executable: str) -> bool:
        """Whether `executable` can run here (on PATH, or the image is present locally)."""

    def environment(self, argv: list[str]) -> dict[str, str] | None:
        """Environment variables for the process; None inherits Puzzle's own."""
        return None


@dataclass(frozen=True)
class NativeRuntime(Runtime):
    search_path: str | None = None

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        exe = shutil.which(command[0], path=self._path())
        if exe is None:
            raise SolverNotFound(
                f"'{command[0]}' not found on PUZZLE_SOLVER_PATH or PATH; install the solver "
                "or set PUZZLE_RUNTIME=docker to use its container image"
            )
        return [exe, *command[1:]]

    def describe(self) -> str:
        return "native"

    def available(self, executable: str) -> bool:
        return shutil.which(executable, path=self._path()) is not None

    def environment(self, argv: list[str]) -> dict[str, str] | None:
        """Activate the solver's conda/pixi environment, if it lives in one.

        Puzzle runs solver binaries directly. Without activation, Windows can't find
        the environment's DLLs (exit 0xC0000135), and some solvers can't find their
        data files, so put the environment's directories first on PATH and set
        CONDA_PREFIX, as `conda activate` would.
        """
        prefix = conda_prefix(Path(argv[0]))
        if prefix is None:
            return None
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([*map(str, env_bin_dirs(prefix)), env.get("PATH", "")])
        env["CONDA_PREFIX"] = str(prefix)
        if (prefix / "share" / "elmersolver").is_dir():
            # Relocated conda Elmer can't find its files from the compiled-in prefix.
            env["ELMER_HOME"] = str(prefix)
        return env

    def _path(self) -> str:
        parts = [self.search_path, os.environ.get("PATH")]
        return os.pathsep.join(p for p in parts if p)


def conda_prefix(executable: Path) -> Path | None:
    """The conda/pixi environment containing `executable` (the folder with conda-meta)."""
    for parent in list(executable.resolve().parents)[:3]:
        if (parent / "conda-meta").is_dir():
            return parent
    return None


def env_bin_dirs(prefix: Path) -> list[Path]:
    """The directories `conda activate` puts on PATH for an environment."""
    if os.name == "nt":
        subdirs = ["", "Library/mingw-w64/bin", "Library/usr/bin", "Library/bin", "Scripts", "bin"]
    else:
        subdirs = ["bin"]
    return [prefix / d for d in subdirs]


@dataclass(frozen=True)
class ContainerRuntime(Runtime):
    image: str
    engine: str = "docker"

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        argv = [self.engine, "run", "--rm", "-v", f"{workdir.resolve()}:{CONTAINER_WORKDIR}"]
        argv += ["-w", CONTAINER_WORKDIR]
        if hasattr(os, "getuid"):  # keep output files owned by the user
            # That user has no home directory in the image; some solvers need one.
            argv += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
        return [*argv, self.image, *command]

    def describe(self) -> str:
        return f"{self.engine}:{self.image}"

    def available(self, executable: str) -> bool:
        if shutil.which(self.engine) is None:
            return False
        probe = [self.engine, "image", "inspect", self.image]
        return subprocess.run(probe, capture_output=True).returncode == 0


def runtime_for(solver: str) -> Runtime:
    key = solver.upper()
    kind = os.environ.get(f"PUZZLE_{key}_RUNTIME") or os.environ.get("PUZZLE_RUNTIME", "auto")
    native = NativeRuntime(search_path=os.environ.get("PUZZLE_SOLVER_PATH"))
    if kind == "native":
        return native
    image = os.environ.get(f"PUZZLE_{key}_IMAGE") or DEFAULT_IMAGES.get(solver)
    if kind in ENGINES:
        if image is None:
            raise SolverNotFound(f"no container image known for {solver}")
        return ContainerRuntime(image=image, engine=kind)
    if kind == "auto":
        executable = EXECUTABLES.get(solver)
        if executable is None or native.available(executable):
            return native
        for engine in ENGINES:
            container = ContainerRuntime(image=image, engine=engine) if image else None
            if container and container.available(executable):
                return container
        return native
    raise ValueError(f"PUZZLE_RUNTIME must be auto, native, docker or podman, not '{kind}'")
