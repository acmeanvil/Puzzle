"""The job runner with a batch scheduler (ADR 0067 phase 3): jobs submitted, followed and
cancelled by PSI/J on a fake Slurm or PBS (tests/remote_fakes.py), how each ended read
from the scheduler, and a runner restarted under a running job picking it up again.

The runner runs under the pixi `runner` environment's Python 3.10 and takes PSI/J from
its own bundle, as on a cluster's login node.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
import remote_fakes

from puzzle.errors import PuzzleError
from puzzle.jobs import run_job
from puzzle.jobs.remote import RemoteRuntime
from puzzle.runner import build, client

ROOT = Path(__file__).parents[1]
RUNNER_ENV = ROOT / ".pixi" / "envs" / "runner"
PYTHON = RUNNER_ENV / "bin" / "python3"
pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="the runner needs POSIX"),
    pytest.mark.skipif(not PYTHON.exists() or build.vendor_dir() is None,
                       reason="no pixi runner environment (pixi install -e runner)"),
    pytest.mark.skipif(not Path("/usr/bin/rsync").exists(), reason="no rsync"),
]  # fmt: skip

ENDED = ("completed", "failed", "cancelled", "walltime_exceeded", "out_of_memory", "job_lost")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """The fake host and scheduler, and the runner on with a quick queue poll."""
    bin_dir, home = remote_fakes.install(tmp_path, monkeypatch)
    monkeypatch.setenv("PUZZLE_RUNNER", "on")
    monkeypatch.setenv("PUZZLE_RUNNER_IDLE", "3")
    monkeypatch.setenv("PUZZLE_RUNNER_POLL", "0.1")
    monkeypatch.setenv("PUZZLE_RUNNER_QUEUE_POLL", "1")
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("PUZZLE_USER_DIR", str(user))
    yield bin_dir, home
    for info in home.glob(".puzzle-runner/p*/runner.json"):  # the host's runner stopped
        _kill(json.loads(info.read_text())["pid"])


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)


def _case(tmp_path, name="run") -> Path:
    case = tmp_path / "project" / name
    case.mkdir(parents=True)
    (case / "input.txt").write_text("21\n")
    return case


def _runtime(bin_dir, scheduler="slurm", **host) -> RemoteRuntime:
    host.setdefault("walltime", "00:00:30")
    host.setdefault("python", str(PYTHON))
    return RemoteRuntime(remote_fakes.host(bin_dir, scheduler=scheduler, **host), "calculix")


def _scheduler_files(home: Path) -> Path:
    (folder,) = home.glob(".puzzle-runner/p*/psij")
    return folder


# --- Slurm --------------------------------------------------------------------------------


def test_a_slurm_job_is_submitted_by_psij_streamed_and_comes_home_whole(tmp_path, fakes,
                                                                        monkeypatch):  # fmt: skip
    bin_dir, home = fakes
    monkeypatch.setenv("FAKE_PENDING_S", "1.5")  # it waits in the queue first
    monkeypatch.setenv("PSIJ_BATCH_KEEP_FILES", "1")  # PSI/J's own files kept, to look at
    case = _case(tmp_path)
    rt = _runtime(bin_dir)
    lines = []
    job = run_job(["sh", "-c", "echo solving; sleep 1; echo $(( $(cat input.txt) * 2 )) > "
                   "answer.txt; echo done"], case, rt, on_output=lines.append)  # fmt: skip
    assert job.ok and job.outcome == "completed", (case / "solver.log").read_text()
    assert "solving" in lines and "done" in lines
    assert (case / "answer.txt").read_text().strip() == "42"
    assert not (home / rt.remote_dir(case)).exists()  # deleted there once home
    jobs = home / ".fake-jobs"
    submitted = Path((jobs / "1000.sub").read_text().strip())
    assert submitted.parent == _scheduler_files(home) / "slurm"  # PSI/J's script, not ours
    script = (jobs / "1000.script").read_text()
    assert f'--chdir="{home / rt.remote_dir(case)}"' in script and "--time=0:0:30" in script
    assert "/bin/sh puzzle-solve.sh" in script
    # PSI/J's exit code file beside our own exit status (kept here by PSIJ_BATCH_KEEP_FILES)
    assert (submitted.parent / "1000.ec").read_text().strip() == "0"


def test_psij_cleans_up_after_itself(tmp_path, fakes):
    """Without PSIJ_BATCH_KEEP_FILES nothing is left in PSI/J's folder but its launcher."""
    bin_dir, home = fakes
    job = run_job(["sh", "-c", "echo 7 > answer.txt"], _case(tmp_path), _runtime(bin_dir))
    assert job.ok
    left = [p.name for p in (_scheduler_files(home) / "slurm").iterdir()]
    assert left == []


@pytest.mark.parametrize(("env", "command", "host", "outcome", "code"), [
    ({"FAKE_SACCT_STATE": "OUT_OF_MEMORY|0:125"}, ["true"], {}, "out_of_memory", None),
    ({}, ["sh", "-c", "sleep 20"], {"walltime": "00:00:02"}, "walltime_exceeded", None),
    ({"FAKE_SACCT_STATE": "NODE_FAIL|0:0"}, ["true"], {}, "job_lost", None),
    ({}, ["sh", "-c", "echo bad; exit 3"], {}, "failed", 3),
    ({"FAKE_NO_ACCOUNTING": "1"}, ["sh", "-c", "exit 3"], {}, "failed", 3),
    ({"FAKE_NO_ACCOUNTING": "1"}, ["true"], {}, "completed", 0),
    ({"FAKE_SACCT_LAG": "3"}, ["sh", "-c", "exit 5"], {}, "failed", 5),  # sacct catching up
    ({"FAKE_SQUEUE_FORGETS": "1"}, ["sh", "-c", "exit 6"], {}, "failed", 6),  # gone at once
])  # fmt: skip
def test_how_a_slurm_job_ended(tmp_path, fakes, monkeypatch, env, command, host, outcome,
                               code):  # fmt: skip
    """The outcome is the scheduler's word (sacct), else the job script's own exit
    status; never success unless something says it exited 0."""
    bin_dir, _home = fakes
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    job = run_job(command, _case(tmp_path), _runtime(bin_dir, **host))
    assert job.outcome == outcome, job.message
    assert job.ok == (outcome == "completed")
    if code is not None:
        assert job.returncode == code
    if outcome in ("out_of_memory", "walltime_exceeded", "job_lost"):
        assert job.message and job.returncode != 0


def test_a_queue_that_stops_answering_is_waited_out(tmp_path, fakes, monkeypatch):
    """PSI/J fails every job once squeue has failed a few times running; the runner
    follows them again instead, so a busy controller doesn't end a solve."""
    bin_dir, _home = fakes
    monkeypatch.setenv("PUZZLE_RUNNER_QUEUE_ERRORS", "1")
    monkeypatch.setenv("FAKE_SQUEUE_FAILS", "5")
    job = run_job(["sh", "-c", "sleep 4; echo 7 > answer.txt"], _case(tmp_path),
                  _runtime(bin_dir))  # fmt: skip
    assert job.ok and job.outcome == "completed", job.message


def test_a_cancel_stops_the_scheduler_job(tmp_path, fakes):
    bin_dir, home = fakes
    case = _case(tmp_path)
    rt = _runtime(bin_dir, keep_case=True)
    stop = threading.Event()
    remote = home / rt.remote_dir(case)

    def cancel_once_running() -> None:
        end = time.monotonic() + 30
        while not (remote / "solver.pid").exists() and time.monotonic() < end:
            time.sleep(0.1)
        stop.set()

    threading.Thread(target=cancel_once_running, daemon=True).start()
    job = run_job(["sh", "-c", "echo $$ > solver.pid; sleep 60"], case, rt, cancel=stop)
    assert job.cancelled and job.outcome == "cancelled" and not job.ok
    jobs = home / ".fake-jobs"
    assert "scancel 1000" in (jobs / "cancels").read_text()
    assert (jobs / "1000.rc").exists()  # the job ended there
    with pytest.raises(ProcessLookupError):  # and its solver with it
        os.kill(int((remote / "solver.pid").read_text()), 0)


def _bundle(tmp_path) -> list[str]:
    """The runner as a host unpacks it, run as Puzzle runs it there."""
    _digest, data = build.bundle()
    folder = tmp_path / "bundle"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(folder, filter="data")
    assert (folder / "psij").is_dir()
    return [str(PYTHON), "-I", "-S", str(folder / "main.py")]


def _wait(c, job, states=("queued", "running"), limit=30.0) -> dict:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        got = c.request("status", jobs=[job])["jobs"][0]
        if got["state"] not in states:
            return got
        time.sleep(0.1)
    raise AssertionError(f"job {job} still {got['state']}")


def test_a_restarted_runner_picks_up_its_scheduler_job(tmp_path, fakes, monkeypatch):
    """The runner killed while its Slurm job runs: the next one attaches to the job by
    its id (PSI/J's attach) and still says how it ended."""
    bin_dir, home = fakes
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    command, base, case = _bundle(tmp_path), tmp_path / "rb", _case(tmp_path)
    spec = {"dir": str(case), "scheduler": "slurm", "resources": {"walltime": "00:00:30"},
            "script": ["sleep 4", "echo done > out.txt", "sh -c 'exit 4'"]}  # fmt: skip
    with client.local(base, command) as c:
        job = c.request("submit", spec=spec)["job"]
        assert job["native_id"] == "1000"
        _wait(c, job["id"], states=("queued",))
        pid = c.info["pid"]
    os.kill(pid, signal.SIGKILL)  # the runner dies; the job goes on
    os.waitpid(pid, 0)  # this test started it (client.local), so reaps it
    assert not (home / ".fake-jobs" / "1000.rc").exists()  # still running
    with client.local(base, command) as c:
        assert c.info["pid"] != pid
        got = _wait(c, job["id"])
        assert (got["state"], got["exit_code"], got["native_id"]) == ("failed", 4, "1000")
        c.request("shutdown")
    assert (case / "out.txt").read_text() == "done\n"
    left = [p.name for p in (client.runner_dir(base) / "psij" / "slurm").iterdir()]
    assert left == []  # the first runner's submit script too, found by the job's id


# --- PBS ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("env", "command", "host", "outcome"), [
    ({}, ["sh", "-c", "echo solving; echo 7 > answer.txt"], {}, "completed"),
    ({"FAKE_PBS_EXIT": "-27"}, ["true"], {}, "out_of_memory"),
    ({}, ["sh", "-c", "sleep 20"], {"walltime": "00:00:02"}, "walltime_exceeded"),
    ({"FAKE_NO_HISTORY": "1"}, ["sh", "-c", "exit 3"], {}, "failed"),  # no job history
])  # fmt: skip
def test_a_pbs_job_through_psij(tmp_path, fakes, monkeypatch, env, command, host, outcome):
    bin_dir, home = fakes
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    case = _case(tmp_path)
    lines = []
    job = run_job(command, case, _runtime(bin_dir, "pbs", **host), on_output=lines.append)
    assert job.outcome == outcome, job.message
    if outcome == "completed":
        assert job.ok and "solving" in lines
        assert (case / "answer.txt").read_text().strip() == "7"
        script = (home / ".fake-jobs" / "1000.script").read_text()
        assert "#PBS -l walltime=0:0:30" in script
    elif outcome == "failed":
        assert job.returncode == 3


# --- without PSI/J --------------------------------------------------------------------------


def test_a_runner_without_psij_refuses_a_scheduler_job(tmp_path, fakes, monkeypatch):
    bin_dir, _home = fakes
    monkeypatch.setattr(build, "vendor_dir", lambda: None)  # a bundle of the runner alone
    rt = _runtime(bin_dir, python=sys.executable)
    with pytest.raises(PuzzleError) as err:
        run_job(["true"], _case(tmp_path), rt)
    assert err.value.code == "unsupported" and "PSI/J" in str(err.value)
