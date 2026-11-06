"""The project's jobs (ADR 0067 phase 4): rows from the pending file and the runners,
cancel and dismiss, the CLI's `puzzle jobs` and the MCP tools."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from puzzle.jobs import overview, pending, run_job
from puzzle.jobs.runtime import NativeRuntime
from puzzle.model.schema import Study
from puzzle.runner import client

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the runner needs POSIX")


@pytest.fixture
def runner_on(tmp_path, monkeypatch):
    """The runner on, its daemon in a short temp folder of its own (stopped after)."""
    monkeypatch.setenv("PUZZLE_RUNNER", "on")
    monkeypatch.setenv("PUZZLE_RUNNER_IDLE", "3")
    monkeypatch.setenv("PUZZLE_RUNNER_POLL", "0.1")
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("PUZZLE_USER_DIR", str(user))
    base = user / "runner"
    yield base
    if (client.runner_dir(base) / "runner.json").exists():
        try:
            with client.local(base) as c:
                c.request("shutdown")
        except Exception:  # noqa: BLE001 - already gone
            pass


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "p"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle",
                                                                  "versions"))  # fmt: skip
    return root


def _study_id(root: Path) -> str:
    from puzzle.project.store import load_project

    return str(next(s.id for s in load_project(root).of_type(Study) if s.name == "Tip load"))


def _solve(root: Path, line: str) -> tuple[threading.Thread, list]:
    """A solve through the runner in a thread, recorded as the study's pending job."""
    case = root / ".puzzle" / "runs" / "r1"
    case.mkdir(parents=True)
    got: list = []
    ctx = pending.RunContext(root, _study_id(root), case)

    def go():
        with pending.running(ctx):
            got.append(run_job(["sh", "-c", line], case, NativeRuntime()))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    end = time.monotonic() + 20
    while not pending.entries(root) and time.monotonic() < end:
        time.sleep(0.05)
    assert pending.entries(root), "the job was never recorded"
    return t, got


def _row_when(root, test, limit=20.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        rows = overview.jobs(root)
        if rows and test(rows[0]):
            return rows[0]
        time.sleep(0.05)
    raise AssertionError(overview.jobs(root))


def test_rows_from_the_pending_file_and_the_local_runner(project, runner_on):
    t, got = _solve(project, "echo starting; echo working hard; sleep 1.5; echo all done")
    row = _row_when(project, lambda r: r.last_line == "working hard")
    assert (row.study, row.solver, row.where, row.state) == (
        "Tip load", "calculix", "this computer", "Running")  # fmt: skip
    assert row.elapsed is not None and row.elapsed >= 0 and row.active
    t.join(20)
    assert got and got[0].ok
    assert overview.jobs(project) == []  # home: no longer pending


def test_a_pending_entry_without_a_runner_is_unknown(project, runner_on):
    ctx = pending.RunContext(project, _study_id(project), project / "r")
    pending.add(ctx, "20261105-000000-abcdef", "this computer", project / "r", {})
    pending.add(ctx, "20261105-000001-abcdef", "lab", project / "r", {})
    here, there = overview.jobs(project)
    assert here.state == overview.UNREACHABLE and here.elapsed is None
    assert there.where == "lab" and there.state == overview.NOT_ASKED
    there = overview.jobs(project, live=True, hosts={})[1]  # not one of the user's hosts
    assert there.state == overview.UNREACHABLE
    out = overview.dismiss(project, here.job)
    assert not out["forgotten"] and "not running" in out["note"]
    assert [e["job"] for e in pending.entries(project)] == [there.job]


def test_a_cancel_from_elsewhere_ends_the_run_following_it(project, runner_on):
    """The Jobs panel (or the CLI) cancels a job another thread follows: that run ends
    as cancelled and takes its entry home with it."""
    t, got = _solve(project, "echo started; sleep 30")
    row = _row_when(project, lambda r: r.code == "running")
    assert overview.cancel(project, row.job) in ("Cancelled", "Running")
    t.join(20)
    assert got and got[0].outcome == "cancelled" and not got[0].ok
    assert overview.jobs(project) == []


def test_dismiss_cancels_a_running_job_and_forgets_it(project, runner_on):
    case = project / ".puzzle" / "runs" / "r2"
    case.mkdir(parents=True)
    with client.local(runner_on) as c:  # a job whose Puzzle has gone: nobody follows it
        job = c.request("submit", spec={"dir": str(case), "argv": ["sh", "-c", "sleep 30"]})
    ctx = pending.RunContext(project, _study_id(project), case)
    pending.add(ctx, job["job"]["id"], "this computer", case, {})
    (row,) = overview.jobs(project)
    assert row.state == "Running"
    out = overview.dismiss(project, row.job)
    assert out["cancelled"] and out["forgotten"] and not out["deleted"]
    assert overview.jobs(project) == []
    with client.local(runner_on) as c:
        assert c.request("status")["jobs"] == []
    assert case.is_dir()  # the run folder here is kept


def test_find_and_resume_order(project):
    ctx = pending.RunContext(project, _study_id(project), project / "a")
    pending.add(ctx, "20261105-000000-aaaaaa", "this computer", project / "a", {})
    assert [e["job"] for e in overview.find(project, "Tip load")] == ["20261105-000000-aaaaaa"]
    assert overview.find(project, "20261105-000000-aaaaaa")[0]["study"] == ctx.study
    from puzzle.errors import PuzzleError

    with pytest.raises(PuzzleError) as err:
        overview.find(project, "Nope")
    assert err.value.code == "not_found"

    def row(job, code, t, workdir):
        return overview.Row(job, "s", "", "", "this computer", "", code, None, "", t, "",
                            workdir, None)  # fmt: skip

    rows = [row("a", "running", 1, "w1"), row("b", "completed", 2, "w2"),
            row("c", "completed", 3, "w2"), row("d", "", 0, "w3")]  # fmt: skip
    assert [r.job for r in overview.resume_order(rows)] == ["b", "d", "a"]
    assert overview.words(None) == "—" and overview.words(75) == "1 min 15 s"


# --- the CLI -----------------------------------------------------------------------------


def test_puzzle_jobs_lists_cancels_and_dismisses(project, runner_on, capsys):
    from puzzle.cli import main

    assert main(["jobs", str(project)]) == 0
    assert "No solves are with a job runner" in capsys.readouterr().out
    case = project / ".puzzle" / "runs" / "r3"
    case.mkdir(parents=True)
    with client.local(runner_on) as c:
        job = c.request("submit", spec={"dir": str(case), "argv": [
            "sh", "-c", "echo hello there; sleep 30"]})["job"]["id"]  # fmt: skip
    pending.add(pending.RunContext(project, _study_id(project), case), job, "this computer",
                case, {})  # fmt: skip
    time.sleep(0.3)
    assert main(["jobs", str(project)]) == 0
    out = capsys.readouterr().out
    assert job in out and "Tip load" in out and "Running" in out
    assert main(["jobs", str(project), "--json"]) == 0
    (data,) = json.loads(capsys.readouterr().out)
    assert data["job"] == job and data["state"] == "Running" and data["study"] == "Tip load"
    assert main(["jobs", str(project), "--cancel", "Tip load"]) == 0  # by the study's name
    assert capsys.readouterr().out.startswith(f"{job}: ")
    assert main(["jobs", str(project), "--dismiss", job]) == 0
    assert "Dismissed" in capsys.readouterr().out
    assert pending.entries(project) == []
    assert main(["jobs", str(project), "--cancel", "nothing"]) == 1


# --- MCP ---------------------------------------------------------------------------------


def _call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return result.content[0].text


def _error(server, tool, **arguments) -> dict:
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as err:
        asyncio.run(server.call_tool(tool, arguments))
    return json.loads(str(err.value).split(": ", 1)[1])


def test_mcp_jobs_tools(project, runner_on):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session

    server = build_server(project, "test")
    assert _call(server, "jobs_list") == {"jobs": []}
    ctx = pending.RunContext(project, _study_id(project), project / "a")
    pending.add(ctx, "20261105-000000-aaaaaa", "this computer", project / "a", {})
    (row,) = _call(server, "jobs_list")["jobs"]
    assert row["study"] == "Tip load" and row["state"] == overview.UNREACHABLE
    # propose (the default): a resume is a run, so it becomes a proposal
    assert _call(server, "job_resume", job="Tip load")["status"] == "proposed"
    assert _error(server, "job_cancel", job="Tip load")["code"] == "not_allowed"
    Session(project).submit({"command": "set_autonomy", "level": "advise"})
    assert _error(server, "job_resume", job="Tip load")["code"] == "not_allowed"
    Session(project).submit({"command": "set_autonomy", "level": "act_in_budget"})
    # at act the cancel reaches the runner, which isn't running here
    assert _error(server, "job_cancel", job="Tip load")["code"] == "invalid"
    assert _error(server, "job_resume", job="nothing")["code"] == "not_found"
