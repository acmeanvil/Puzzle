"""Solves handed to a runner and not yet brought home (ADR 0067): `.puzzle/jobs.json`.

A run records its job here when it is submitted and removes it when its results are in.
An entry left behind is a solve that went on after Puzzle stopped: opening the project
shows it, and resuming re-enters the same run (same study, same run folder) attached to
the job instead of submitting it again. The file is the project's working state, like
`.puzzle/runs`: never versioned, archived or hashed.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

FILE = ".puzzle/jobs.json"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class RunContext:
    """What a run is, set around `run_study` by the session, so `run_job` can record it."""

    root: Path
    study: str  # the study's id
    workdir: Path  # the run's top folder
    host: str | None = None


CONTEXT: ContextVar[RunContext | None] = ContextVar("puzzle_run_context", default=None)
#: While resuming: a job folder (resolved, as text) → the runner's job id to attach to.
ATTACH: ContextVar[dict[str, str]] = ContextVar("puzzle_attach", default={})  # noqa: B039


def _path(root: Path) -> Path:
    return Path(root) / FILE


def entries(root: Path) -> list[dict]:
    try:
        data = json.loads(_path(root).read_text())
    except (OSError, ValueError):
        return []
    return [e for e in data.get("jobs", []) if isinstance(e, dict) and e.get("job")]


def _write(root: Path, jobs: list[dict]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".jobs-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump({"jobs": jobs}, f, indent=1)
    os.replace(tmp, path)


def add(ctx: RunContext, job: str, where: str, folder: Path, label: dict) -> None:
    with _LOCK:
        jobs = [e for e in entries(ctx.root) if e["job"] != job]
        jobs.append({"job": job, "where": where, "folder": str(Path(folder).resolve()),
                     "workdir": str(Path(ctx.workdir).resolve()), "study": ctx.study,
                     "host": ctx.host, "submitted": time.time(), "label": label,
                     "follower": _me()})  # fmt: skip
        _write(ctx.root, jobs)


def _me() -> dict:
    return {"pid": os.getpid(), "machine": socket.gethostname()}


def follow(root: Path, job: str) -> None:
    """This process follows `job` now (it picked the job up)."""
    with _LOCK:
        jobs = entries(root)
        for e in jobs:
            if e["job"] == job:
                e["follower"] = _me()
        _write(root, jobs)


def followed(entry: dict) -> bool:
    """Whether another live process on this machine is following the job, so picking it
    up here would make two clients fetch and store the same run."""
    who = entry.get("follower") or {}
    if who.get("machine") != socket.gethostname() or who.get("pid") in (None, os.getpid()):
        return False
    try:
        os.kill(int(who["pid"]), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, OverflowError):
        return True
    return True


def remove(root: Path, job: str) -> None:
    with _LOCK:
        jobs = entries(root)
        kept = [e for e in jobs if e["job"] != job]
        if len(kept) != len(jobs):
            _write(root, kept)


def attached(folder: Path) -> str | None:
    return ATTACH.get().get(str(Path(folder).resolve()))


@contextlib.contextmanager
def running(ctx: RunContext, attach: dict[str, str] | None = None):
    token = CONTEXT.set(ctx)
    token2 = ATTACH.set(dict(attach or {}))
    try:
        yield
    finally:
        CONTEXT.reset(token)
        ATTACH.reset(token2)
