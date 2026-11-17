"""No container outlives Stop or a timeout (ADR 0090, BE-3), with a real engine: a
`sleep` in a solver's image, started as `run_job` starts a solve, cancelled or timed
out, then the engine asked whether its container still runs. Skipped without an engine
or the image.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

from puzzle.jobs import runtime, warm
from puzzle.jobs.runner import run_job
from puzzle.jobs.runtime import DEFAULT_IMAGES, ContainerRuntime, find_engine

IMAGE = DEFAULT_IMAGES["calculix"]


def _engine():
    for engine in ("docker", "podman"):
        rt = ContainerRuntime(image=IMAGE, engine=engine)
        if find_engine(engine) and rt.available("sleep"):
            return rt
    pytest.skip(f"no container engine with {IMAGE}")


def _running(rt, name: str) -> bool:
    exe = find_engine(rt.engine)
    out = subprocess.run([exe, "ps", "-q", "--filter", f"name={name}"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=60).stdout  # fmt: skip
    return bool(out.strip())


def _named(work) -> str:
    end = time.monotonic() + 60
    while not (work / runtime.CONTAINER_NAME).exists():
        if time.monotonic() > end:
            raise TimeoutError("the container never started")
        time.sleep(0.1)
    return (work / runtime.CONTAINER_NAME).read_text(encoding="utf-8").strip()


def _gone(rt, name: str, within: float = 15) -> bool:
    end = time.monotonic() + within
    while _running(rt, name):
        if time.monotonic() > end:
            return False
        time.sleep(0.5)
    return True


def test_stop_stops_the_container(tmp_path):
    rt = _engine()
    cancel, seen = threading.Event(), {}

    def press_stop():
        seen["name"] = _named(tmp_path)
        time.sleep(3)  # it runs a while first
        seen["was_running"] = _running(rt, seen["name"])
        cancel.set()

    threading.Thread(target=press_stop, daemon=True).start()
    job = run_job(["sleep", "120"], tmp_path, rt, cancel=cancel)
    assert job.cancelled and seen["was_running"]
    assert _gone(rt, seen["name"]), "the container outlived Stop"


def test_a_timeout_stops_the_container(tmp_path):
    rt = _engine()
    seen = {}
    threading.Thread(target=lambda: seen.setdefault("name", _named(tmp_path)),
                     daemon=True).start()  # fmt: skip
    job = run_job(["sleep", "120"], tmp_path, rt, timeout_s=6)
    assert job.timed_out
    assert _gone(rt, seen["name"]), "the container outlived the timeout"


def test_puzzle_exiting_stops_its_containers(tmp_path):
    """What atexit does when Puzzle exits mid-run: every container it started, killed."""
    rt = _engine()
    done = threading.Event()

    def solve():
        run_job(["sleep", "120"], tmp_path, rt)
        done.set()

    threading.Thread(target=solve, daemon=True).start()
    name = _named(tmp_path)
    time.sleep(3)
    assert _running(rt, name)
    runtime.stop_all_containers()
    assert _gone(rt, name) and done.wait(30)


def test_a_warm_solve_is_stopped_inside_its_container(tmp_path):
    rt = _engine()
    cancel, seen = threading.Event(), {}
    folder = tmp_path / "point-1"
    folder.mkdir()
    with warm.batch(tmp_path) as pool:
        hot = warm.warm(rt)
        assert isinstance(hot, warm.WarmRuntime)

        def press_stop():
            end = time.monotonic() + 60
            while not (folder / warm.PID).exists() and time.monotonic() < end:
                time.sleep(0.1)
            time.sleep(2)
            cancel.set()

        threading.Thread(target=press_stop, daemon=True).start()
        job = run_job(["sleep", "120"], folder, hot, cancel=cancel)
        assert job.cancelled
        (name,) = pool.names.values()
        exe = find_engine(rt.engine)
        listing = (
            "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null; "
            "echo; done"
        )  # the image has no ps
        time.sleep(1)
        top = subprocess.run([exe, "exec", name, "sh", "-c", listing], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout  # fmt: skip
        seen["top"] = top
    assert "sh -c" in seen["top"]  # the listing works: it sees its own shell
    assert "sleep 120" not in seen["top"], "the exec'd solve outlived Stop"
    assert _gone(rt, name)
