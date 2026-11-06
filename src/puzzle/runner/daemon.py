"""The runner daemon: jobs, their backends, the queue, and a socket clients talk to.

Requests and answers are JSON objects, one per line: `{"id": 1, "op": "submit", ...}` →
`{"id": 1, "ok": true, ...}` or `{"id": 1, "ok": false, "code": ..., "error": ...}`.

- `hello` {protocol} → {protocol, version, pid, cores, memory_gb}
- `submit` {spec} → {job}: queued, then started when its cores and memory fit (jobs
  without a scheduler) or at once (a scheduler queues them itself)
- `status` {jobs?} → {jobs: [record, ...]} (all when no ids are given)
- `log` {job, offset, limit?} → {text, offset, size}: the log from `offset` on
- `cancel` {job}
- `manifest` {job} → {files: {path: [size, sha256]}} of a finished job's folder
- `forget` {job, delete?} → the record removed; with `delete`, the case folder too,
  only for a folder the client marked `owned` and only once the job has ended
- `shutdown` (tests): stop when no job is running
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

from puzzle.runner import PROTOCOL, VERSION
from puzzle.runner.backends import ProcessBackend, PsijBackend
from puzzle.runner.jobs import Job, Store, manifest

IDLE_S = float(os.environ.get("PUZZLE_RUNNER_IDLE", 900))  # stop after this long unused
POLL_S = float(os.environ.get("PUZZLE_RUNNER_POLL", 1.0))
QUEUE_POLL_S = float(os.environ.get("PUZZLE_RUNNER_QUEUE_POLL", 10.0))  # squeue/qstat every
QUEUE_ERRORS = int(os.environ.get("PUZZLE_RUNNER_QUEUE_ERRORS", 12))  # failed polls tolerated
LOG_LIMIT = 1 << 20  # at most this much log per answer


class RunnerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def socket_path(root: Path) -> Path:
    """The socket: in the runner's folder, unless that path is too long for a Unix socket
    (about 104 bytes on macOS); then a short one in the temp folder, named for the folder."""
    path = Path(root) / "runner.sock"
    if len(str(path)) < 100:
        return path
    tag = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"puzzle-runner-{os.getuid()}-{tag}.sock"


class Runner:
    def __init__(self, root: Path, cores: int = 0, memory_gb: float = 0.0):
        self.root = Path(root)
        self.store = Store(self.root)
        self.cores = cores or os.cpu_count() or 1
        self.memory_gb = memory_gb
        self.backends: dict[str, object] = {"none": ProcessBackend()}
        self.lock = threading.RLock()
        for job in self.store.all():  # picked up after a restart
            if not job.final and job.native_id:
                with contextlib.suppress(Exception):
                    self.backend(job).attach(job)

    def backend(self, job: Job):
        name = job.spec.get("scheduler") or "none"
        if name not in self.backends:
            if name not in ("slurm", "pbs", "lsf"):
                raise RunnerError("invalid", f"no scheduler called '{name}'")
            try:
                self.backends[name] = PsijBackend(name, self.root, QUEUE_POLL_S, QUEUE_ERRORS)
            except ImportError as exc:
                raise RunnerError("unsupported", f"this runner has no PSI/J ({exc}): it "
                                  "can't use a scheduler") from None  # fmt: skip
        return self.backends[name]

    # --- requests ----------------------------------------------------------------

    def submit(self, spec: dict) -> Job:
        folder = Path(spec.get("dir") or "").expanduser()
        if spec.get("dir") and not folder.is_absolute():  # as ssh and rsync read it: from home
            folder = Path.home() / folder
        spec = {**spec, "dir": str(folder)}
        if not folder.is_dir():
            raise RunnerError("invalid", f"the case folder '{folder}' is not a folder here")
        if not (spec.get("argv") or spec.get("script")):
            raise RunnerError("invalid", "a job needs argv or a script")
        with self.lock:
            job = Job(self.store.new_id(), spec)
            self.backend(job)  # refuse an unknown scheduler now, not later
            self.store.save(job)
        self.tick()
        return self.store.load(job.id) or job

    def job(self, job_id: str) -> Job:
        try:
            job = self.store.load(job_id)
        except ValueError as exc:
            raise RunnerError("invalid", str(exc)) from None
        if job is None:
            raise RunnerError("not_found", f"no job {job_id}")
        return job

    def cancel(self, job_id: str) -> Job:
        with self.lock:
            job = self.job(job_id)
            if job.final:
                return job
            if job.state == "queued" and not job.native_id:
                job.state, job.ended, job.cancel_requested = "cancelled", time.time(), True
            else:
                self.backend(job).cancel(job)
            self.store.save(job)
            return job

    def log(self, job_id: str, offset: int, limit: int = LOG_LIMIT) -> dict:
        job = self.job(job_id)
        try:
            size = job.log.stat().st_size
            with job.log.open("rb") as f:
                f.seek(max(0, offset))
                data = f.read(max(0, min(limit, LOG_LIMIT)))
        except FileNotFoundError:
            return {"text": "", "offset": offset, "size": 0}
        return {"text": data.decode("utf-8", "replace"), "offset": offset + len(data),
                "size": size}  # fmt: skip

    def manifest(self, job_id: str) -> dict:
        job = self.job(job_id)
        if not job.final:
            raise RunnerError("busy", f"job {job_id} has not ended")
        return manifest(Path(job.spec["dir"]))

    def forget(self, job_id: str, delete: bool = False) -> None:
        with self.lock:
            job = self.job(job_id)
            if not job.final:
                raise RunnerError("busy", f"job {job_id} has not ended: cancel it first")
            folder = Path(job.spec["dir"])
            if delete:
                if not job.spec.get("owned") or not (folder / "puzzle-solve.sh").exists():
                    raise RunnerError("not_allowed", f"'{folder}' is not a case this runner "
                                      "may delete")  # fmt: skip
                shutil.rmtree(folder, ignore_errors=True)
            self.store.remove(job_id)

    # --- the queue -----------------------------------------------------------------

    def tick(self) -> None:
        """Follow running jobs, then start queued ones that fit."""
        with self.lock:
            jobs = self.store.all()
            for job in jobs:
                if job.final or not job.native_id:
                    continue
                before = (job.state, job.exit_code, job.started)
                with contextlib.suppress(Exception):
                    self.backend(job).poll(job)
                if (job.state, job.exit_code, job.started) != before:
                    self.store.save(job)
            local = [j for j in jobs if (j.spec.get("scheduler") or "none") == "none"]
            busy = [j for j in local if j.state == "running"]
            cores = sum(j.cores for j in busy)
            memory = sum(j.memory_gb for j in busy)
            for job in sorted((j for j in jobs if j.state == "queued" and not j.native_id),
                              key=lambda j: j.submitted):  # fmt: skip
                scheduled = (job.spec.get("scheduler") or "none") != "none"
                fits = (not busy or (cores + job.cores <= self.cores and (
                    not self.memory_gb or memory + job.memory_gb <= self.memory_gb)))  # fmt: skip
                if not scheduled and not fits:
                    continue
                try:
                    self.backend(job).start(job)
                except Exception as exc:  # noqa: BLE001 - a job that can't start fails, said why
                    job.state, job.ended, job.message = "failed", time.time(), f"{exc}"
                if not scheduled and job.state == "running":
                    busy.append(job)
                    cores, memory = cores + job.cores, memory + job.memory_gb
                self.store.save(job)

    def active(self) -> bool:
        return any(not j.final for j in self.store.all())

    def handle(self, request: dict) -> dict:
        op = request.get("op")
        if op == "hello":
            if request.get("protocol") != PROTOCOL:
                raise RunnerError("version", f"this runner speaks protocol {PROTOCOL}, not "
                                  f"{request.get('protocol')}")  # fmt: skip
            return {"protocol": PROTOCOL, "version": VERSION, "pid": os.getpid(),
                    "cores": self.cores, "memory_gb": self.memory_gb}  # fmt: skip
        if op == "submit":
            return {"job": self.submit(request.get("spec") or {}).public()}
        if op == "status":
            ids = request.get("jobs")
            jobs = [self.job(i) for i in ids] if ids else self.store.all()
            return {"jobs": [j.public() for j in jobs]}
        if op == "log":
            return self.log(request["job"], int(request.get("offset") or 0),
                            int(request.get("limit") or LOG_LIMIT))  # fmt: skip
        if op == "cancel":
            return {"job": self.cancel(request["job"]).public()}
        if op == "manifest":
            return {"files": self.manifest(request["job"])}
        if op == "forget":
            self.forget(request["job"], bool(request.get("delete")))
            return {}
        raise RunnerError("invalid", f"no request called '{op}'")


def serve(root: Path, cores: int = 0, memory_gb: float = 0.0) -> None:
    """Run the daemon for `root` until it has been idle for IDLE_S (no job running, no
    client connected); a second daemon for the same folder exits at once."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / "runner.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    runner = Runner(root, cores, memory_gb)
    path = socket_path(root)
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        server.bind(str(path))
    finally:
        os.umask(old)
    server.listen()
    server.settimeout(POLL_S)
    info = {"pid": os.getpid(), "socket": str(path), "protocol": PROTOCOL, "version": VERSION}
    tmp = root / "runner.json.tmp"
    tmp.write_text(json.dumps(info))
    os.replace(tmp, root / "runner.json")
    state = {"clients": 0, "last": time.time(), "stop": False}

    def client(conn: socket.socket) -> None:
        state["clients"] += 1
        try:
            with conn, conn.makefile("rwb") as stream:
                for raw in stream:
                    state["last"] = time.time()
                    try:
                        request = json.loads(raw)
                        if request.get("op") == "shutdown":
                            state["stop"] = True
                            answer = {}
                        else:
                            answer = runner.handle(request)
                        answer = {"id": request.get("id"), "ok": True, **answer}
                    except RunnerError as exc:
                        answer = {"id": request.get("id"), "ok": False, "code": exc.code,
                                  "error": str(exc)}  # fmt: skip
                    except Exception as exc:  # noqa: BLE001 - said to the client, not fatal
                        answer = {"ok": False, "code": "invalid", "error": f"{exc}"}
                    stream.write(json.dumps(answer).encode() + b"\n")
                    stream.flush()
        except OSError:
            pass
        finally:
            state["clients"] -= 1
            state["last"] = time.time()

    try:
        while True:
            with contextlib.suppress(socket.timeout):
                conn, _ = server.accept()
                threading.Thread(target=client, args=(conn,), daemon=True).start()
            runner.tick()
            active = runner.active()
            if active or state["clients"]:
                state["last"] = time.time()
            if state["stop"] and not active:
                break
            if not active and not state["clients"] and time.time() - state["last"] > IDLE_S:
                break
    finally:
        server.close()
        path.unlink(missing_ok=True)
        (root / "runner.json").unlink(missing_ok=True)
        lock.close()
