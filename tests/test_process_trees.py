"""Stop means stop (ADR 0090, BE-3): a cancelled or timed-out native solve takes its
whole process tree with it, and a container run is named so its container can be killed
(the `docker run` client forwards no signal on Windows). With a real engine:
tests/integration/test_container_stop.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from puzzle.jobs import runtime
from puzzle.jobs.runner import run_job
from puzzle.jobs.runtime import ContainerRuntime, NativeRuntime

# A solve that starts a child which outlives it unless the tree is stopped: the child's
# pid goes into child.pid, then both sleep.
PARENT = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open("child.pid", "w").write(str(child.pid))
print("started", flush=True)
time.sleep(120)
"""


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32")
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie is gone in all but name
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
        return state != "Z"
    except OSError:
        return True


class Plain(NativeRuntime):
    """This Python, with no environment activation (fast, and the same everywhere)."""

    def environment(self, argv):
        return None


def _child(work: Path) -> int:
    end = time.monotonic() + 30
    while not (work / "child.pid").exists():
        if time.monotonic() > end:
            raise TimeoutError("the stand-in solve never started its child")
        time.sleep(0.05)
    time.sleep(0.1)
    return int((work / "child.pid").read_text(encoding="utf-8"))


def test_a_stop_ends_the_solves_children_too(tmp_path):
    (tmp_path / "solve.py").write_text(PARENT, encoding="utf-8")
    cancel = threading.Event()
    seen = {}

    def press_stop():
        seen["child"] = _child(tmp_path)
        cancel.set()

    threading.Thread(target=press_stop, daemon=True).start()
    started = time.monotonic()
    job = run_job([sys.executable, "solve.py"], tmp_path, Plain(), cancel=cancel)
    assert job.cancelled and time.monotonic() - started < 60
    end = time.monotonic() + 10
    while alive(seen["child"]) and time.monotonic() < end:
        time.sleep(0.1)
    assert not alive(seen["child"]), "the solve's child outlived the stop"


def test_a_timeout_ends_the_tree_too(tmp_path):
    (tmp_path / "solve.py").write_text(PARENT, encoding="utf-8")
    job = run_job([sys.executable, "solve.py"], tmp_path, Plain(), timeout_s=3)
    assert job.timed_out
    child = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    end = time.monotonic() + 10
    while alive(child) and time.monotonic() < end:
        time.sleep(0.1)
    assert not alive(child)


def test_a_container_run_is_named_and_its_container_killed(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(runtime, "find_engine", lambda engine: engine)
    monkeypatch.setattr(runtime.subprocess, "run",
                        lambda argv, **kw: killed.append(argv) or subprocess.CompletedProcess(
                            argv, 0))  # fmt: skip
    rt = ContainerRuntime(image="img:1", engine="docker")
    argv = rt.wrap(["ccx", "job"], tmp_path)
    name = argv[argv.index("--name") + 1]
    assert name.startswith("puzzle-run-") and (tmp_path / runtime.CONTAINER_NAME).exists()
    assert f"{runtime.CONTAINER_LABEL}=1" in argv
    rt.cancel(tmp_path)
    assert killed == [["docker", "kill", name], ["docker", "rm", "-f", name]]
    assert not (tmp_path / runtime.CONTAINER_NAME).exists()
    # Puzzle exiting mid-run (atexit): every container still running is stopped.
    killed.clear()
    (tmp_path / "b").mkdir()
    other = rt.wrap(["ccx", "job"], tmp_path / "b")
    runtime.stop_all_containers()
    assert ["docker", "kill", other[other.index("--name") + 1]] in killed


def test_a_warm_runtime_outside_its_batch_stops_as_a_plain_run(tmp_path, monkeypatch):
    from puzzle.jobs import warm

    calls = []
    monkeypatch.setattr(runtime, "stop_container", lambda engine, w: calls.append(w))
    rt = warm.WarmRuntime(image="img:1", engine="docker", pool=None)
    rt.cancel(tmp_path)  # no pid file: it ran as `docker run`
    assert calls == [tmp_path]


def test_the_warm_solve_keeps_its_container_alive_while_it_runs(tmp_path):
    from puzzle.jobs import warm

    pool = warm.Pool(tmp_path)
    pool.names[("docker", "img:1", ())] = "c"
    pool.entrypoints[("docker", "img:1", ())] = []
    pool.container = lambda rt: "c"
    argv = warm.WarmRuntime(image="img:1", engine="docker", pool=pool).wrap(["sleep", "1"],
                                                                         tmp_path)  # fmt: skip
    script = argv[argv.index("-c") + 1]
    assert "while kill -0 $$" in script and f"touch {warm.ALIVE}" in script
    assert ">/dev/null 2>&1 </dev/null &" in script  # so `docker exec` returns with the solve


def test_a_wsl_solve_is_stopped_inside_its_distribution(tmp_path, monkeypatch):
    from puzzle.jobs import machine

    shells = []
    rt = machine.WslRuntime("elmer", "puzzle-elmer")
    monkeypatch.setattr(machine, "linux_path", lambda p: "/mnt/c/run")
    monkeypatch.setattr(rt.__class__, "_shell", lambda self, script: shells.append(script))
    argv = rt.wrap(["ElmerSolver", "case.sif"], tmp_path)
    assert argv[argv.index("-c") + 1].startswith(f"echo $$ > {machine.PID}")
    rt.cancel(tmp_path)
    assert f"cat '/mnt/c/run/{machine.PID}'" in shells[0] and "kill -TERM $pids" in shells[0]
