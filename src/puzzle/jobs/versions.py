"""Which solver a run used (ADR 0072 section 1: the solver version and image digest kept
with each run, ADR 0007, 0013).

- In a container: the image's id (`docker image inspect`, the digest of what ran) and
  the version Puzzle's images write (`/etc/puzzle-solver-version` or
  `/opt/conda/puzzle-solver-version`), read once per image per session.
- Natively: the solver package's version from its environment's `conda-meta` (CalculiX
  2.23, code-aster 18.1.10 ...); a CalculiX built here with PARDISO says so.
- In Puzzle's Linux machine: the image its distribution was made from.

Never raises: a run whose solver can't be named is stored without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# The conda package that holds each solver, for a native run's version.
PACKAGES = {"calculix": "calculix", "code_aster": "code-aster", "elmer": "elmerfem",
            "fenicsx": "fenics-dolfinx", "openfoam": "openfoam",
            "code_saturne": "code_saturne"}  # fmt: skip
_SEEN: dict[str, tuple[str | None, str | None]] = {}


def identify(solver: str) -> tuple[str | None, str | None]:
    """(version, image digest) of the solver a run of `solver` uses here now."""
    try:
        from puzzle.jobs.runtime import runtime_for

        rt = runtime_for(solver)
        key = f"{solver}|{rt.describe()}"
        if key not in _SEEN:
            _SEEN[key] = _identify(solver, rt)
        return _SEEN[key]
    except Exception:  # noqa: BLE001 - naming the solver never fails a run
        return None, None


def _identify(solver: str, rt) -> tuple[str | None, str | None]:
    from puzzle.jobs.machine import WslRuntime
    from puzzle.jobs.runtime import ContainerRuntime, NativeRuntime

    if isinstance(rt, ContainerRuntime):
        return _container(rt)
    if isinstance(rt, WslRuntime):
        from puzzle.jobs import machine

        found = machine.installed().get(solver, {})
        return (f"from {found['from']}" if found.get("from") else None), None
    if isinstance(rt, NativeRuntime):
        return _native(solver, rt), None
    return None, None


def _container(rt) -> tuple[str | None, str | None]:
    from puzzle.jobs.runtime import find_engine

    exe = find_engine(rt.engine)
    if exe is None:
        return None, None
    env = rt.environment([exe])
    inspect = subprocess.run([exe, "image", "inspect", "-f", "{{.Id}}", rt.image],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env=env, timeout=60)  # fmt: skip
    digest = inspect.stdout.strip() if inspect.returncode == 0 else None
    read = subprocess.run([exe, "run", "--rm", "--entrypoint", "sh", rt.image, "-c",
                           "cat /etc/puzzle-solver-version /opt/conda/puzzle-solver-version "
                           "2>/dev/null; true"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=120)  # fmt: skip
    version = read.stdout.strip().splitlines()[0] if read.stdout.strip() else None
    return version, digest


def _native(solver: str, rt) -> str | None:
    from puzzle.jobs.runtime import EXECUTABLES, conda_prefix

    if solver == "fenicsx":
        from puzzle.adapters.fenicsx.case import fenicsx_python

        exe = fenicsx_python()
    else:
        name = EXECUTABLES.get(solver)
        found = shutil.which(name, path=rt._path()) if name else None
        exe = Path(found) if found else None
    if exe is None:
        return None
    prefix = conda_prefix(Path(exe))
    if prefix is None:
        return None
    if prefix.name == "calculix-pardiso":  # ADR 0051: built here, not a package
        from puzzle import solver_builds

        return f"{solver_builds.VERSION} (PARDISO, built here)"
    package = PACKAGES.get(solver)
    for meta in sorted((prefix / "conda-meta").glob(f"{package}-*.json")) if package else []:
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("name") == package:
            return str(data.get("version"))
    return None
