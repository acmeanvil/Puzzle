import sys
import threading
from pathlib import Path

import pytest

from puzzle.jobs import (
    ContainerRuntime,
    NativeRuntime,
    SolverNotFound,
    run_job,
    runtime_for,
)
from puzzle.jobs.runtime import DEFAULT_IMAGES

PY = Path(sys.executable)


def _script(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_solver.py"
    script.write_text(body)
    return [PY.name, str(script)]


@pytest.fixture
def native() -> NativeRuntime:
    return NativeRuntime(search_path=str(PY.parent))


def test_successful_run_streams_and_logs(tmp_path, native):
    seen: list[str] = []
    cmd = _script(tmp_path, "print('step 1'); print('step 2')")
    result = run_job(cmd, tmp_path, native, on_output=seen.append)
    assert result.ok
    assert seen == ["step 1", "step 2"]
    assert result.log.read_text() == "step 1\nstep 2\n"
    assert result.runtime == "native"


def test_failure_keeps_the_return_code(tmp_path, native):
    cmd = _script(tmp_path, "import sys; print('diverged'); sys.exit(3)")
    result = run_job(cmd, tmp_path, native)
    assert result.returncode == 3
    assert not result.ok
    assert "diverged" in result.log.read_text()


def test_cancel_stops_the_process(tmp_path, native):
    cancel = threading.Event()
    cmd = _script(tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(60)")
    result = run_job(cmd, tmp_path, native, on_output=lambda _: cancel.set(), cancel=cancel)
    assert result.cancelled
    assert not result.ok
    assert result.duration_s < 30


def test_timeout_stops_the_process(tmp_path, native):
    cmd = _script(tmp_path, "import time; time.sleep(60)")
    result = run_job(cmd, tmp_path, native, timeout_s=0.5)
    assert result.timed_out
    assert result.duration_s < 30


def test_missing_solver_is_reported(tmp_path):
    with pytest.raises(SolverNotFound, match="definitely-not-a-solver"):
        NativeRuntime(search_path="").wrap(["definitely-not-a-solver"], tmp_path)


def test_container_runtime_mounts_the_workdir(tmp_path):
    argv = ContainerRuntime(image="img:1", engine="podman").wrap(["ccx", "-i", "job"], tmp_path)
    assert argv[:3] == ["podman", "run", "--rm"]
    assert f"{tmp_path.resolve()}:/work" in argv
    assert argv[-4:] == ["img:1", "ccx", "-i", "job"]


def test_runtime_selection(monkeypatch):
    monkeypatch.setenv("PUZZLE_RUNTIME", "docker")
    monkeypatch.setenv("PUZZLE_CALCULIX_IMAGE", "local/ccx:dev")
    assert runtime_for("calculix") == ContainerRuntime(image="local/ccx:dev", engine="docker")
    monkeypatch.setenv("PUZZLE_RUNTIME", "native")
    assert isinstance(runtime_for("calculix"), NativeRuntime)
    monkeypatch.setenv("PUZZLE_ELMER_RUNTIME", "podman")
    assert runtime_for("elmer") == ContainerRuntime(image=DEFAULT_IMAGES["elmer"], engine="podman")
    monkeypatch.setenv("PUZZLE_RUNTIME", "vm")
    with pytest.raises(ValueError):
        runtime_for("calculix")


def test_availability_checks(tmp_path):
    assert NativeRuntime(search_path=str(PY.parent)).available(PY.name)
    assert not NativeRuntime(search_path="").available("definitely-not-a-solver")
    assert not ContainerRuntime(image="x", engine="definitely-not-an-engine").available("ccx")


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script stand-in")
def test_auto_prefers_native_then_falls_back(monkeypatch, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ccx = fake_bin / "ccx"
    ccx.write_text("#!/bin/sh\n")
    ccx.chmod(0o755)
    monkeypatch.delenv("PUZZLE_RUNTIME", raising=False)
    monkeypatch.setenv("PUZZLE_SOLVER_PATH", str(fake_bin))
    assert runtime_for("calculix") == NativeRuntime(search_path=str(fake_bin))

    monkeypatch.setenv("PUZZLE_SOLVER_PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no solver and no container engine
    assert isinstance(runtime_for("calculix"), NativeRuntime)  # reports the solver as missing
