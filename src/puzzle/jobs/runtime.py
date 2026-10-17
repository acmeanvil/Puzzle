"""Where a solver command runs: on the host, or inside a container image.

Selection, in order:
1. `PUZZLE_RUNTIME` = "native", "docker" or "podman" (default: "native").
2. For containers, `PUZZLE_<SOLVER>_IMAGE` overrides the default image.
3. For native runs, `PUZZLE_SOLVER_PATH` (os.pathsep-separated) is searched before PATH.
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# Images built from containers/<solver>/Dockerfile. Not published yet.
DEFAULT_IMAGES = {
    "calculix": "ghcr.io/acmeanvil/puzzle-calculix:2.23",
    "elmer": "ghcr.io/acmeanvil/puzzle-elmer:26.2",
}

CONTAINER_WORKDIR = "/work"


class SolverNotFound(RuntimeError):
    pass


class Runtime(ABC):
    @abstractmethod
    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        """The full argv that runs `command` with `workdir` as its working directory."""

    @abstractmethod
    def describe(self) -> str:
        """A short label recorded with each run, e.g. the image name."""


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

    def _path(self) -> str:
        parts = [self.search_path, os.environ.get("PATH")]
        return os.pathsep.join(p for p in parts if p)


@dataclass(frozen=True)
class ContainerRuntime(Runtime):
    image: str
    engine: str = "docker"

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        argv = [self.engine, "run", "--rm", "-v", f"{workdir.resolve()}:{CONTAINER_WORKDIR}"]
        argv += ["-w", CONTAINER_WORKDIR]
        if hasattr(os, "getuid"):  # keep output files owned by the user
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return [*argv, self.image, *command]

    def describe(self) -> str:
        return f"{self.engine}:{self.image}"


def runtime_for(solver: str) -> Runtime:
    kind = os.environ.get("PUZZLE_RUNTIME", "native")
    if kind == "native":
        return NativeRuntime(search_path=os.environ.get("PUZZLE_SOLVER_PATH"))
    if kind in ("docker", "podman"):
        override = os.environ.get(f"PUZZLE_{solver.upper()}_IMAGE")
        image = override or DEFAULT_IMAGES.get(solver)
        if image is None:
            raise SolverNotFound(f"no container image known for {solver}")
        return ContainerRuntime(image=image, engine=kind)
    raise ValueError(f"PUZZLE_RUNTIME must be native, docker or podman, not '{kind}'")
