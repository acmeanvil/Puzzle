"""Warm containers for a batch of solves (ADR 0072 section 1, the maintainer's first
priority there).

A container solve is normally its own `docker run --rm`: start-up costs about half a
second a solve, more for small models, which a sweep, an optimisation or a convergence
series pays at every point. Inside `batch(root)` the first container solve of each
image starts one container that stays up, with `root` (the batch's folder, holding
every point's run folder) mounted at /batch, and every solve of that image is
`docker exec`'d into it, in its own folder there, with the thread variables of its own.

- The image's entrypoint (OpenFOAM's environment, FEniCSx's activation) runs in front
  of each exec'd command, as `docker run` would run it.
- The container keeps itself alive only while used: it exits when nothing has touched
  /tmp/.puzzle-alive for `IDLE_S`, so a Puzzle that dies leaves nothing behind; the
  batch's end removes it at once.
- Cancel: the exec'd solve writes its pid into its folder (`.puzzle-pid`), and
  `WarmRuntime.cancel` kills it inside the container (killing `docker exec` itself
  would leave the solve running).
- A solve whose folder isn't under the batch's root runs as before.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from puzzle.jobs.runtime import (
    SERIAL_BLAS,
    THREAD_VARIABLES,
    ContainerRuntime,
    find_engine,
    thread_environment,
)

MOUNT = "/batch"
IDLE_S = 300
ALIVE = "/tmp/.puzzle-alive"
PID = ".puzzle-pid"
# Sleeps until nothing has touched ALIVE for IDLE_S (busybox and GNU stat both take -c).
KEEPER = (f"touch {ALIVE}; while [ $(( $(date +%s) - $(stat -c %Y {ALIVE}) )) -lt {IDLE_S} ]; "
          "do sleep 5; done")  # fmt: skip

_BATCH: contextvars.ContextVar = contextvars.ContextVar("puzzle_warm_batch", default=None)


def _run(argv: list[str], env=None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)  # fmt: skip


@dataclass
class Pool:
    """The batch's running containers, one per (engine, image, caches)."""

    root: Path
    names: dict[tuple, str] = field(default_factory=dict)
    entrypoints: dict[tuple, list[str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def container(self, rt: ContainerRuntime) -> str | None:
        """The running container for `rt`'s image, started now if need be; None when it
        can't be (the run then goes as a plain `docker run`)."""
        key = (rt.engine, rt.image, rt.caches)
        exe = find_engine(rt.engine) or rt.engine
        env = rt.environment([exe])
        with self.lock:
            name = self.names.get(key)
            if name is not None and _running(exe, name, env):
                return name
            entry = _entrypoint(exe, rt.image, env)
            if entry is None:
                return None
            name = f"puzzle-warm-{uuid.uuid4().hex[:10]}"
            argv = [exe, "run", "-d", "--rm", "--name", name, "-v",
                    f"{self.root}:{MOUNT}"]  # fmt: skip
            for volume, mount in rt.caches:
                argv += ["-v", f"{volume}:{mount}"]
            if hasattr(os, "getuid"):
                argv += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
            argv += ["--entrypoint", "sh", rt.image, "-c", KEEPER]
            try:
                done = _run(argv, env)
            except (OSError, subprocess.SubprocessError):
                return None
            if done.returncode != 0:
                return None
            self.names[key], self.entrypoints[key] = name, entry
            return name

    def stop(self) -> None:
        """Every container of the batch removed now (each would stop when idle anyway)."""
        with self.lock:
            for (engine, _, _), name in self.names.items():
                exe = find_engine(engine) or engine
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    _run([exe, "rm", "-f", name], timeout=60)
            self.names.clear()


def _running(exe: str, name: str, env) -> bool:
    try:
        done = _run([exe, "inspect", "-f", "{{.State.Running}}", name], env, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


def _entrypoint(exe: str, image: str, env) -> list[str] | None:
    """The image's own entrypoint (a list, empty for none), or None if the image can't
    be read."""
    try:
        done = _run([exe, "image", "inspect", "-f", "{{json .Config.Entrypoint}}", image], env)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        value = json.loads(done.stdout.strip() or "null")
    except ValueError:
        return []
    return list(value or [])


@contextlib.contextmanager
def batch(root: Path):
    """Container solves inside, in folders under `root`, share one running container per
    image (module docstring). Nested batches use the outermost."""
    if _BATCH.get() is not None:
        yield _BATCH.get()
        return
    Path(root).mkdir(parents=True, exist_ok=True)
    pool = Pool(Path(root).resolve())
    token = _BATCH.set(pool)
    try:
        yield pool
    finally:
        _BATCH.reset(token)
        pool.stop()


def current() -> Pool | None:
    return _BATCH.get()


@dataclass(frozen=True)
class WarmRuntime(ContainerRuntime):
    """A container solve exec'd into the batch's running container."""

    pool: Pool | None = field(default=None, compare=False, hash=False)

    def _place(self, workdir: Path) -> tuple[str, str] | None:
        if self.pool is None:
            return None
        try:
            rel = Path(workdir).resolve().relative_to(self.pool.root)
        except ValueError:
            return None  # not under the batch: a plain run
        name = self.pool.container(self)
        if name is None:
            return None
        return name, f"{MOUNT}/{rel.as_posix()}".rstrip("/")

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        placed = self._place(workdir)
        if placed is None:
            return super().wrap(command, workdir)
        name, inside = placed
        exe = find_engine(self.engine) or self.engine
        argv = [exe, "exec", "-w", inside]
        threads = thread_environment()
        for var in (*THREAD_VARIABLES, *SERIAL_BLAS):
            argv += ["-e", f"{var}={threads[var]}"]
        entry = self.pool.entrypoints.get((self.engine, self.image, self.caches), [])
        script = f'touch {ALIVE}; echo $$ > {PID}; exec "$@"'
        return [*argv, name, "sh", "-c", script, "sh", *entry, *command]

    def describe(self) -> str:
        return f"{self.engine}:{self.image} (warm)"

    def cancel(self, workdir: Path) -> None:
        """Stop the exec'd solve inside the container: its pid from its folder."""
        placed = self._place(workdir) if (Path(workdir) / PID).exists() else None
        if placed is None:
            return
        name, inside = placed
        exe = find_engine(self.engine) or self.engine
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _run([exe, "exec", "-w", inside, name, "sh", "-c",
                  f"kill -TERM $(cat {PID}) 2>/dev/null; true"], timeout=30)  # fmt: skip


def warm(rt):
    """`rt` as a warm runtime when a batch is on and it is a container one."""
    pool = _BATCH.get()
    if pool is None or not isinstance(rt, ContainerRuntime) or isinstance(rt, WarmRuntime):
        return rt
    return WarmRuntime(image=rt.image, engine=rt.engine, caches=rt.caches, pool=pool)


def batched(fn):
    """`fn(project, x, root, workdir, ...)` run as one batch in `workdir`: optimisation,
    convergence and adaptivity series, each point a solve."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        workdir = kwargs.get("workdir", args[3] if len(args) > 3 else None)
        if workdir is None:
            return fn(*args, **kwargs)
        with batch(Path(workdir)):
            return fn(*args, **kwargs)

    return wrapper
