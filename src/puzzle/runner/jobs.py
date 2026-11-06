"""Jobs on disk: one JSON record per job, written atomically, and a case's manifest."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from puzzle.runner import FINAL

#: Files of the runner's own in a case folder, left out of its manifest.
OWN = frozenset({"puzzle-solve.sh", "puzzle-job.rc", "puzzle-job.rc.tmp"})


@dataclass
class Job:
    """A job and what is known about it. `spec` is what the client asked for:

    - `dir`: the case folder on this machine (the job's working directory);
    - `argv` and `env` (run as they are), or `script` (shell lines, run by `sh`);
    - `scheduler`: none, slurm, pbs or lsf;
    - `resources`: processes, threads, memory_gb, gpus, walltime, partition, account;
    - `owned`: the runner may delete `dir` when the client says it has the results;
    - `label`: free words for people (project, study, solver).
    """

    id: str
    spec: dict
    state: str = "queued"
    submitted: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    exit_code: int | None = None
    message: str = ""
    native_id: str = ""  # the scheduler's job id, or the process id
    cancel_requested: bool = False

    @property
    def final(self) -> bool:
        return self.state in FINAL

    @property
    def cores(self) -> int:
        r = self.spec.get("resources") or {}
        return max(1, int(r.get("processes") or 1)) * max(1, int(r.get("threads") or 1))

    @property
    def memory_gb(self) -> float:
        return float((self.spec.get("resources") or {}).get("memory_gb") or 0.0)

    @property
    def log(self) -> Path:
        return Path(self.spec["dir"]) / self.spec.get("log", "solver.log")

    def public(self) -> dict:
        """What a client is told: the record without the environment (it can be large
        and hold the user's variables)."""
        d = asdict(self)
        d["spec"] = {k: v for k, v in self.spec.items() if k != "env"}
        d["elapsed"] = (self.ended or time.time()) - self.started if self.started else 0.0
        return d


class Store:
    """`jobs/<id>.json` under the runner's folder; every write atomic."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def new_id(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)

    def save(self, job: Job) -> None:
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".job-", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(job), f)
            os.replace(tmp, self.dir / f"{job.id}.json")

    def load(self, job_id: str) -> Job | None:
        path = self.dir / f"{_safe_id(job_id)}.json"
        try:
            return Job(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def all(self) -> list[Job]:
        with self.lock:
            jobs = [self.load(p.stem) for p in sorted(self.dir.glob("*.json"))]
        return [j for j in jobs if j is not None]

    def remove(self, job_id: str) -> None:
        with self.lock:
            (self.dir / f"{_safe_id(job_id)}.json").unlink(missing_ok=True)


def _safe_id(job_id: str) -> str:
    """A job id names a file: refuse anything that could point elsewhere."""
    if not job_id or not all(c.isalnum() or c in "-_" for c in job_id):
        raise ValueError(f"not a job id: {job_id!r}")
    return job_id


def manifest(folder: Path, skip: frozenset[str] = OWN | {"solver.log"}) -> dict[str, list]:
    """Every file under `folder` (relative POSIX path → [size, sha256]), so a client can
    check a fetched copy before it deletes the original. Links are not followed."""
    out: dict[str, list] = {}
    folder = Path(folder)
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(folder).as_posix()
            if rel in skip or path.is_symlink():
                continue
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out[rel] = [path.stat().st_size, h.hexdigest()]
    return out
