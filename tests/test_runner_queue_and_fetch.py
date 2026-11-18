"""The job runner's queue and fetch, on any system (ADR 0067; ADR 0090 package 16):

- ADR-55: local jobs start in the order they came, so a small later job never passes an
  earlier large one for ever (the queue had `continue`d past it); and an unscheduled
  remote job claims only the memory its run asks for, not the host's whole limit.
- ADR-54: a failed or stopped job whose files can't be fetched stays pending, so its
  logs can be fetched again or the job dismissed; it had been forgotten on the host.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from puzzle.runner.daemon import Runner
from puzzle.runner.jobs import Job


class Started:
    """A backend that starts what it is given and never ends it."""

    def __init__(self):
        self.started = []

    def start(self, job):
        job.state, job.native_id, job.started = "running", f"p{len(self.started)}", time.time()
        self.started.append(job.id)

    def poll(self, job):
        pass

    def attach(self, job):
        pass


def _job(runner, name, threads, at, scheduler="none"):
    job = Job(id=name, spec={"dir": str(runner.root), "scheduler": scheduler,
                             "resources": {"threads": threads}}, submitted=at)  # fmt: skip
    runner.store.save(job)
    return job


def test_local_jobs_start_in_order(tmp_path):
    runner = Runner(tmp_path, cores=4)
    backend = Started()
    runner.backend = lambda job: backend
    _job(runner, "a", 3, 1.0)  # running
    _job(runner, "b", 4, 2.0)  # waits for all four cores
    _job(runner, "c", 1, 3.0)  # would fit beside a, but b came first
    _job(runner, "q", 8, 4.0, scheduler="slurm")  # a scheduler's: never held by the queue
    runner.tick()
    assert backend.started == ["a", "q"]
    runner.tick()
    assert backend.started == ["a", "q"]  # c still behind b


def test_an_unscheduled_remote_job_asks_only_its_runs_memory():
    from puzzle.jobs.service import RemoteService

    host = SimpleNamespace(scheduler="none", memory_gb=64.0, walltime="", partition="",
                           account="")  # fmt: skip
    runtime = SimpleNamespace(resources=SimpleNamespace(processes=1, memory_gb=0.0,
                                                        walltime="", partition="", account=""),
                              remote_dir=lambda w: "/r", solve_lines=lambda c: ["x"],
                              threads=lambda: 2)  # fmt: skip
    service = RemoteService.__new__(RemoteService)
    service.runtime, service.host = runtime, host
    assert service.spec(["x"], Path("w"))["resources"]["memory_gb"] == 0.0
    host.scheduler = "slurm"  # a scheduler's request keeps the host's default
    assert service.spec(["x"], Path("w"))["resources"]["memory_gb"] == 64.0


def test_a_failed_job_whose_fetch_fails_stays_pending(tmp_path, monkeypatch):
    from puzzle.jobs import pending, runner

    removed = []
    monkeypatch.setattr(pending, "remove", lambda root, job: removed.append(job))
    monkeypatch.setattr(pending, "add", lambda *a, **k: None)

    class Service:
        log_is_local = False
        poll_s = 0.0
        where = "lab"

        def prepare(self, command, workdir, say):
            pass

        def submit(self, command, workdir, label):
            return "job-1"

        def log(self, job, offset):
            return "", offset

        def status(self, job):
            return {"state": "failed", "exit_code": 2, "elapsed": 1.0}

        def finish(self, job, workdir, say, *, ok):
            raise OSError("connection reset by the host")

        def cancel(self, job):
            pass

    said = []
    rt = SimpleNamespace(describe=lambda: "lab")
    ctx = pending.RunContext(tmp_path, "study", tmp_path)
    with pending.running(ctx):
        result = runner._through_runner(Service(), ["x"], tmp_path, rt, on_output=said.append,
                                        cancel=None, timeout_s=None, parser=None,
                                        emit=lambda e: None)  # fmt: skip
    assert result.returncode == 2
    assert removed == []  # kept: Window → Jobs can fetch it again or dismiss it
    assert any("stays in" in line for line in said)
