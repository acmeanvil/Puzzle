import os
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
    assert Path(argv[0]).stem == "podman" and argv[1:3] == ["run", "--rm"]
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
    from puzzle.jobs import runtime

    monkeypatch.setattr(runtime, "ENGINE_DIRS", [])
    monkeypatch.setattr(runtime, "pixi_solver_dirs", lambda: [])
    assert isinstance(runtime_for("calculix"), NativeRuntime)  # reports the solver as missing


def test_native_runs_activate_the_solver_environment(tmp_path):
    prefix = tmp_path / "env"
    (prefix / "conda-meta").mkdir(parents=True)
    bindir = prefix / ("Library/bin" if sys.platform == "win32" else "bin")
    bindir.mkdir(parents=True)
    exe = bindir / ("solver.exe" if sys.platform == "win32" else "solver")
    exe.write_text("")
    env = NativeRuntime().environment([str(exe)])
    assert env["CONDA_PREFIX"] == str(prefix.resolve())
    assert env["PATH"].split(os.pathsep)[0].startswith(str(prefix.resolve()))
    assert NativeRuntime().environment([str(tmp_path / "loose-binary")]) is None


def test_a_flood_of_output_is_capped(tmp_path, native, monkeypatch):
    """A solver printing a warning per node must not fill the disk or memory."""
    from puzzle.jobs import runner

    monkeypatch.setattr(runner, "MAX_LOG_BYTES", 10_000)
    argv = _script(tmp_path, "for i in range(200000):\n    print('WARNING zero coefficient', i)\n")
    result = run_job(argv, tmp_path, native)
    assert result.ok
    assert result.log.stat().st_size < 20_000
    assert runner.log_tail(result.log)[-1].startswith("[puzzle: log truncated")


def test_pixi_solver_envs_are_found_without_activation(tmp_path, monkeypatch):
    """Puzzle started from .pixi/envs/dev without `pixi run` still finds the CalculiX
    environment beside it (no PUZZLE_SOLVER_PATH)."""
    from puzzle.jobs import runtime

    envs = tmp_path / ".pixi" / "envs"
    (envs / "dev").mkdir(parents=True)
    calculix = envs / "calculix" / ("Library/bin" if os.name == "nt" else "bin")
    calculix.mkdir(parents=True)
    monkeypatch.setattr(runtime.sys, "prefix", str(envs / "dev"))
    assert runtime.pixi_solver_dirs() == [calculix]
    monkeypatch.setattr(runtime.sys, "prefix", str(tmp_path / "venv"))
    assert runtime.pixi_solver_dirs() == []


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script stand-in")
def test_an_engine_off_path_is_found_and_put_on_its_path(monkeypatch, tmp_path):
    """Docker Desktop on macOS: `docker` lives in the app, not on an app's PATH."""
    from puzzle.jobs import runtime

    app = tmp_path / "Docker.app" / "bin"
    app.mkdir(parents=True)
    docker = app / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")  # `image inspect` succeeds: the image is here
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(runtime, "ENGINE_DIRS", [str(app)])
    container = ContainerRuntime(image="img:1", engine="docker")
    assert container.available("run_aster")
    assert container.wrap(["run_aster"], tmp_path)[0] == str(docker)
    assert container.environment([])["PATH"].split(os.pathsep)[0] == str(app)
    monkeypatch.delenv("PUZZLE_RUNTIME", raising=False)
    monkeypatch.setattr(runtime, "pixi_solver_dirs", lambda: [])
    assert runtime_for("code_aster") == ContainerRuntime(
        image=DEFAULT_IMAGES["code_aster"], engine="docker"
    )
