"""The job runner (ADR 0067): its daemon, solves through it here and on a fake remote
host, cancels, the queue, and a solve that outlives the Puzzle that started it."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import remote_fakes

from puzzle.errors import PuzzleError
from puzzle.jobs import pending, run_job
from puzzle.jobs.remote import RemoteRuntime
from puzzle.jobs.runtime import NativeRuntime
from puzzle.runner import client
from puzzle.runner.backends import classify

ROOT = Path(__file__).parents[1]
needs_posix = pytest.mark.skipif(os.name != "posix", reason="the runner needs POSIX")
pytestmark = needs_posix


@pytest.fixture
def runner_on(tmp_path, monkeypatch):
    """The runner on, its daemon in a short temp folder (and stopped afterwards)."""
    monkeypatch.setenv("PUZZLE_RUNNER", "on")
    monkeypatch.setenv("PUZZLE_RUNNER_IDLE", "3")
    monkeypatch.setenv("PUZZLE_RUNNER_POLL", "0.1")
    user = tmp_path / "user"  # a runner of its own: never the previous test's, shutting down
    user.mkdir()
    monkeypatch.setenv("PUZZLE_USER_DIR", str(user))
    base = user / "runner"
    yield base
    _stop(base)


def _stop(base: Path) -> None:
    if (client.runner_dir(base) / "runner.json").exists():
        try:
            with client.local(base) as c:
                c.request("shutdown")
        except Exception:  # noqa: BLE001 - already gone
            pass


def _case(tmp_path, name="run") -> Path:
    case = tmp_path / "project" / name
    case.mkdir(parents=True)
    (case / "input.txt").write_text("21\n")
    return case


def _wait(c, job, states=("queued", "running"), limit=30.0) -> dict:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        got = c.request("status", jobs=[job])["jobs"][0]
        if got["state"] not in states:
            return got
        time.sleep(0.05)
    raise AssertionError(f"job {job} still {got['state']}")


# --- the daemon ------------------------------------------------------------------------


def test_the_daemon_runs_a_job_and_outlives_its_client(tmp_path, runner_on):
    case = _case(tmp_path)
    with client.local(runner_on) as c:
        line = "echo start; sleep 0.5; echo $(( $(cat input.txt) * 2 )) > out.txt; exit 3"
        job = c.request("submit", spec={"dir": str(case), "argv": ["sh", "-c", line]})["job"]
    assert job["state"] == "running" and job["native_id"]
    with client.local(runner_on) as c:  # a new client: the job went on without the first
        got = _wait(c, job["id"])
        assert (got["state"], got["exit_code"]) == ("failed", 3)
        assert c.request("log", job=job["id"], offset=0)["text"] == "start\n"
        assert c.request("manifest", job=job["id"])["files"]["out.txt"][0] == 3
        with pytest.raises(Exception, match="may delete"):  # not a case it was given
            c.request("forget", job=job["id"], delete=True)
        c.request("forget", job=job["id"])
        assert c.request("status")["jobs"] == []
    assert (case / "out.txt").read_text() == "42\n"  # forgetting keeps the folder


def test_jobs_wait_for_cores_and_a_queued_job_cancels_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PUZZLE_RUNNER_IDLE", "3")
    monkeypatch.setenv("PUZZLE_RUNNER_POLL", "0.1")
    base = tmp_path / "rb"
    with client.local(base, cores=2) as c:
        spec = {"argv": ["sh", "-c", "sleep 1"], "resources": {"threads": 2}}
        a = c.request("submit", spec={**spec, "dir": str(_case(tmp_path, "a"))})["job"]
        b = c.request("submit", spec={**spec, "dir": str(_case(tmp_path, "b"))})["job"]
        d = c.request("submit", spec={**spec, "dir": str(_case(tmp_path, "d"))})["job"]
        assert (a["state"], b["state"], d["state"]) == ("running", "queued", "queued")
        assert c.request("cancel", job=d["id"])["job"]["state"] == "cancelled"
        _wait(c, a["id"])
        assert _wait(c, b["id"], states=("queued",))["state"] in ("running", "completed")
        assert _wait(c, b["id"])["state"] == "completed"
        c.request("shutdown")


def test_a_cancel_stops_the_job_and_its_children(tmp_path, runner_on):
    case = _case(tmp_path)
    with client.local(runner_on) as c:
        job = c.request("submit", spec={"dir": str(case), "argv": [
            "sh", "-c", "sleep 30 & echo $! > child.pid; wait"]})["job"]  # fmt: skip
        while not (case / "child.pid").exists():
            time.sleep(0.05)
        c.request("cancel", job=job["id"])
        assert _wait(c, job["id"])["state"] == "cancelled"
    child = int((case / "child.pid").read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_an_unknown_scheduler_and_a_missing_folder_are_refused(tmp_path, runner_on):
    with client.local(runner_on) as c:
        with pytest.raises(Exception, match="not a folder"):
            c.request("submit", spec={"dir": str(tmp_path / "nowhere"), "argv": ["true"]})
        with pytest.raises(Exception, match="no scheduler"):
            c.request("submit", spec={"dir": str(tmp_path), "argv": ["true"],
                                      "scheduler": "condor"})  # fmt: skip
        with pytest.raises(Exception, match="not a job id"):
            c.request("status", jobs=["../../etc"])


@pytest.mark.parametrize(("said", "code", "rc", "psij", "want"), [
    ("COMPLETED", 0, 0, "COMPLETED", "completed"),
    ("COMPLETED", 0, 2, "COMPLETED", "failed"),
    ("OUT_OF_MEMORY", 0, None, "FAILED", "out_of_memory"),
    ("TIMEOUT", 0, None, "FAILED", "walltime_exceeded"),
    ("NODE_FAIL", 0, None, "FAILED", "job_lost"),
    ("FAILED", 1, 1, "FAILED", "failed"),
    ("CANCELLED", 0, None, "CANCELED", "job_lost"),  # by someone else
    (None, None, 0, "COMPLETED", "completed"),  # no accounting: the script's own status
    (None, None, None, "COMPLETED", "job_lost"),  # nothing says how it ended
])  # fmt: skip
def test_how_a_scheduler_job_ended(said, code, rc, psij, want):
    assert classify(said, code, rc, psij, False, "")[0] == want


# --- solves through it -------------------------------------------------------------------


def test_run_job_goes_through_the_runner(tmp_path, runner_on):
    case = _case(tmp_path)
    lines = []
    job = run_job(["sh", "-c", "echo one; echo $(( $(cat input.txt) + 1 )) > out.txt; echo two"],
                  case, NativeRuntime(), on_output=lines.append)  # fmt: skip
    assert job.ok and job.outcome == "completed" and "via the job runner" in job.runtime
    assert lines[:2] == ["one", "two"]
    assert (case / "solver.log").read_text() == "one\ntwo\n"
    assert (case / "out.txt").read_text() == "22\n"
    with client.local(runner_on) as c:
        assert c.request("status")["jobs"] == []  # forgotten once home


def test_run_job_cancel_and_timeout_through_the_runner(tmp_path, runner_on):
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    job = run_job(["sh", "-c", "sleep 30"], _case(tmp_path, "a"), NativeRuntime(), cancel=stop)
    assert job.cancelled and not job.ok and job.outcome == "cancelled"
    job = run_job(["sh", "-c", "sleep 30"], _case(tmp_path, "b"), NativeRuntime(), timeout_s=0.5)
    assert job.timed_out and not job.ok


SURVIVOR = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from puzzle.jobs import pending, run_job
    from puzzle.jobs.runtime import NativeRuntime
    root, case = Path(sys.argv[1]), Path(sys.argv[2])
    ctx = pending.RunContext(root, "study-id", case)
    with pending.running(ctx):
        line = "sleep 1.5; echo done > out.txt; echo finished"
        run_job(["sh", "-c", line], case, NativeRuntime())
""")


def test_a_solve_outlives_puzzle_and_is_picked_up(tmp_path, runner_on):
    """A Puzzle that stops mid-solve leaves the job running and recorded; the next one
    attaches to it, reads its whole log and brings it home."""
    root, case = tmp_path / "project", _case(tmp_path)
    puzzle = subprocess.Popen([sys.executable, "-c", SURVIVOR, str(root), str(case)],
                              env=os.environ.copy())  # fmt: skip
    end = time.monotonic() + 20
    while not pending.entries(root) and time.monotonic() < end:
        time.sleep(0.05)
    puzzle.kill()  # Puzzle stops; the solve doesn't
    puzzle.wait()
    (entry,) = pending.entries(root)
    assert entry["where"] == "this computer" and entry["study"] == "study-id"
    lines = []
    ctx = pending.RunContext(root, "study-id", case)
    with pending.running(ctx, {entry["folder"]: entry["job"]}):
        job = run_job(["never", "run"], case, NativeRuntime(), on_output=lines.append)
    assert job.ok and (case / "out.txt").read_text() == "done\n"
    assert "finished" in lines and any("picked up" in x for x in lines)
    assert pending.entries(root) == []


CCX = shutil.which("ccx", path=str(ROOT / ".pixi" / "envs" / "calculix" / "bin"))


@pytest.mark.skipif(CCX is None, reason="CalculiX not installed")
def test_a_study_resumed_after_puzzle_stopped(tmp_path, runner_on, monkeypatch):
    """The session's resume: the study's run continued in its own folder, its results
    stored as from an ordinary run."""
    monkeypatch.setenv("PUZZLE_SOLVER_PATH", str(Path(CCX).parent))
    project = tmp_path / "cantilever"
    shutil.copytree(ROOT / "examples" / "cantilever", project,
                    ignore=shutil.ignore_patterns("results", ".puzzle", "versions"))  # fmt: skip
    script = textwrap.dedent(f"""
        import os, time
        from pathlib import Path
        from puzzle.commands import Session
        import puzzle.jobs.runner as r
        real = r._through_runner
        def slow(service, command, *a, **k):  # the solve waits, so Puzzle can be stopped
            ccx = {CCX!r}
            line = " ".join([ccx, *command[1:]])
            return real(service, ["sh", "-c", "sleep 2; exec " + line], *a, **k)
        r._through_runner = slow
        Session(Path({str(project)!r})).submit({{"command": "run_study", "study": "Tip load"}})
    """)
    puzzle = subprocess.Popen([sys.executable, "-c", script], env=os.environ.copy())
    end = time.monotonic() + 60
    while not pending.entries(project) and time.monotonic() < end:
        time.sleep(0.1)
    puzzle.kill()
    puzzle.wait()
    from puzzle.commands import Session

    s = Session(project)
    (entry,) = s.pending_jobs()
    out = s.resume(entry["job"])
    assert out.status == "applied" and out.data["resumed"] == entry["job"]
    deflection = out.data["summary"][0]["fields"]["displacement"]
    assert deflection["max"] > 0
    assert s.pending_jobs() == []
    history = [json.loads(x) for x in (project / "history.jsonl").read_text().splitlines()]
    assert history[-1]["result"]["resumed"] == entry["job"]


# --- on a remote host ----------------------------------------------------------------------

needs_rsync = pytest.mark.skipif(not Path("/usr/bin/rsync").exists(), reason="no rsync")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    return remote_fakes.install(tmp_path, monkeypatch)


@needs_rsync
def test_a_remote_job_runs_there_and_comes_home_whole(tmp_path, fakes, runner_on):
    bin_dir, home = fakes
    case = _case(tmp_path)
    rt = RemoteRuntime(remote_fakes.host(bin_dir, cores=3, python=sys.executable), "calculix")
    lines = []
    job = run_job(["sh", "-c", 'echo "threads $OMP_NUM_THREADS"; '
                   'echo $(( $(cat input.txt) * 2 )) > answer.txt; echo done'],
                  case, rt, on_output=lines.append)  # fmt: skip
    assert job.ok, (case / "solver.log").read_text()
    assert "threads 3" in lines and "done" in lines
    assert (case / "answer.txt").read_text().strip() == "42"
    assert "done" in (case / "solver.log").read_text()  # the log as streamed
    assert not (home / rt.remote_dir(case)).exists()  # deleted there once home
    assert list(home.glob(".puzzle-runner/runner-*/main.py"))  # the runner was copied
    assert not list(tmp_path.glob("project/.fetch-*"))


@needs_rsync
def test_a_fetch_that_does_not_match_leaves_the_hosts_copy(tmp_path, fakes, runner_on,
                                                           monkeypatch):  # fmt: skip
    bin_dir, home = fakes
    case = _case(tmp_path)
    rt = RemoteRuntime(remote_fakes.host(bin_dir, python=sys.executable), "calculix")
    real = RemoteRuntime._copy

    def damaged(self, workdir, remote, up):  # the copy back loses a byte
        moved = real(self, workdir, remote, up)
        if not up:
            (workdir / "answer.txt").write_text("4")
        return moved

    monkeypatch.setattr(RemoteRuntime, "_copy", damaged)
    with pytest.raises(PuzzleError) as err:
        run_job(["sh", "-c", "echo 42 > answer.txt"], case, rt)
    assert err.value.code == "transfer_failed"
    assert (home / rt.remote_dir(case) / "answer.txt").read_text() == "42\n"  # kept there
    assert not (case / "answer.txt").exists()  # nothing half-moved here


def test_the_runner_preference_and_tests_default(monkeypatch):
    from puzzle.jobs import service
    from puzzle.settings import PREFERENCES

    assert {p.key: p.default for p in PREFERENCES}["solvers.runner"] is True
    monkeypatch.setenv("PUZZLE_RUNNER", "off")
    assert service.service_for(NativeRuntime()) is None


def _project_with_pending(tmp_path, follower=None) -> tuple[Path, str, str]:
    from puzzle.commands import Session
    from puzzle.model.schema import Study

    root = tmp_path / "cantilever"
    shutil.copytree(ROOT / "examples" / "cantilever", root,
                    ignore=shutil.ignore_patterns("results", ".puzzle", "versions"))  # fmt: skip
    study = next(o for o in Session(root).project.objects.values()
                 if isinstance(o, Study) and o.name == "Tip load")  # fmt: skip
    folder = root / ".puzzle" / "runs" / "tip-1"
    folder.mkdir(parents=True)
    ctx = pending.RunContext(root, str(study.id), folder)
    pending.add(ctx, "20261105-000000-abcdef", "this computer", folder, {})
    if follower is not None:
        data = json.loads((root / pending.FILE).read_text())
        data["jobs"][0]["follower"] = {
            "pid": follower,
            "machine": socket.gethostname(),
        }
        (root / pending.FILE).write_text(json.dumps(data))
    return root, str(study.id), "20261105-000000-abcdef"


def test_a_job_another_puzzle_follows_is_not_picked_up_twice(tmp_path):
    from puzzle.commands import Session

    other = subprocess.Popen(["sleep", "30"])
    try:
        root, _, job = _project_with_pending(tmp_path, follower=other.pid)
        (entry,) = pending.entries(root)
        assert pending.followed(entry)
        with pytest.raises(PuzzleError) as err:
            Session(root).resume(job)
        assert err.value.code == "busy"
    finally:
        other.kill()
        other.wait()
    assert not pending.followed(entry)  # its follower is gone: free to pick up


def test_an_agents_resume_is_a_proposal_that_picks_the_job_up(tmp_path):
    from puzzle.commands import Session
    from puzzle.commands.session import Actor

    root, study, job = _project_with_pending(tmp_path)
    s = Session(root)
    s.submit({"command": "set_autonomy", "level": "propose"})
    out = s.resume(job, Actor("agent", "test-agent"))
    assert out.status == "proposed"
    (proposal,) = [p for p in s.proposals() if p["id"] == out.proposal]
    assert proposal["commands"][0]["resume"] == job  # accepting it picks this job up
    assert proposal["commands"][0]["study"] == study
