"""A project's solves that are with a job runner (ADR 0067 phase 4): one row each.

`jobs(root)` merges the project's pending entries (`pending.entries`, written when a run
is submitted, removed when its results are home) with what the runners they name say
about them. The local runner is asked only if one is running (nothing is started to
answer); without one, its job records on disk are read. Remote hosts are asked only
with `live=True`, since each one is an SSH connection (seconds): the GUI does that on
an explicit Refresh, never on its timer.

`cancel` and `dismiss` go through the runner too. A cancelled job stays pending until
it is resumed (the run then ends as cancelled and the entry goes) or dismissed.
Resuming is a run, so it is the session's (`Session.resume`); `resume_order` says in
which order to pick several up: finished ones first, their results are waiting.
"""

from __future__ import annotations

import contextlib
import json
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from puzzle.errors import PuzzleError
from puzzle.jobs import pending

HERE = "this computer"

#: The runner's states, in words for people.
STATES = {
    "queued": "Queued",
    "running": "Running",
    "completed": "Finished",
    "failed": "Failed",
    "out_of_memory": "Out of memory",
    "walltime_exceeded": "Ran past its walltime",
    "job_lost": "Lost",
    "cancelled": "Cancelled",
}
UNREACHABLE = "Unknown — not reachable"
NOT_ASKED = "Unknown — not asked yet"
GONE = "Unknown — the runner no longer knows it"
FINAL = ("completed", "failed", "out_of_memory", "walltime_exceeded", "job_lost", "cancelled")
TAIL_BYTES = 4096


@dataclass
class Row:
    """One pending job. `job` is the runner's id: internal, never shown in the GUI."""

    job: str
    study: str  # the study's name ("" when it is gone from the project)
    study_id: str
    solver: str  # the study's solver key (the GUI shows its label)
    where: str  # "this computer" or a host's name
    state: str  # in words (STATES, or one of the Unknown ones)
    code: str  # the runner's state ("" when unknown)
    elapsed: float | None  # seconds running, None when unknown
    last_line: str
    submitted: float
    folder: str
    workdir: str
    host: str | None
    message: str = ""  # what the runner said about how it ended

    @property
    def ended(self) -> bool:
        return self.code in FINAL

    @property
    def active(self) -> bool:
        return self.code in ("queued", "running")

    def to_dict(self) -> dict:
        return asdict(self) | {"ended": self.ended}


# --- talking to runners ------------------------------------------------------------


def local_base() -> Path:
    from puzzle.jobs.service import local_base as base

    return base()


def _local_client(base: Path | None = None):
    """A client of the local runner if one is running, else None (none is started)."""
    from puzzle.runner.client import Client, runner_dir

    info = runner_dir(base or local_base()) / "runner.json"
    if not info.exists():
        return None
    try:
        path = json.loads(info.read_text())["socket"]
    except (OSError, ValueError, KeyError):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(path)
    except OSError:
        s.close()
        return None
    stream = s.makefile("rwb")

    def close() -> None:
        stream.close()
        s.close()

    try:
        return Client(stream, stream, close)
    except Exception:  # noqa: BLE001 - a runner of another protocol, or going away
        close()
        return None


def _remote_client(host):
    """A client of the runner on `host` over SSH (the runner copied there first)."""
    from puzzle.jobs.service import deploy, remote_command
    from puzzle.runner.client import over
    from puzzle.runner.daemon import RunnerError

    digest = deploy(host)
    try:
        return over(host.ssh_argv(remote_command(host, digest)), host.name)
    except RunnerError as exc:
        from puzzle.jobs.service import coded

        raise coded(exc, host.name) from None


def _on_disk(job: str, base: Path | None = None) -> dict | None:
    """The local runner's record of a job, read from its folder (no runner needed)."""
    from puzzle.runner.client import runner_dir
    from puzzle.runner.jobs import Job

    path = runner_dir(base or local_base()) / "jobs" / f"{job}.json"
    if not all(c.isalnum() or c in "-_" for c in job):
        return None
    try:
        return Job(**json.loads(path.read_text())).public()
    except (OSError, ValueError, TypeError):
        return None


def _hosts(hosts):
    if hosts is not None:
        return hosts
    from puzzle import hosts as h

    try:
        return h.load()
    except PuzzleError:
        return {}


def _tail(text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return lines[-1][:300] if lines else ""


def _file_tail(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            return _tail(f.read().decode(errors="replace"))
    except OSError:
        return ""


def _log_tail(client, job: str) -> str:
    with contextlib.suppress(Exception):
        size = client.request("log", job=job, offset=0, limit=1).get("size") or 0
        got = client.request("log", job=job, offset=max(0, size - TAIL_BYTES), limit=TAIL_BYTES)
        return _tail(got.get("text") or "")
    return ""


def _ask(client, ids: list[str]) -> dict[str, dict]:
    """The runner's records of these jobs (one request; ones it no longer has missing)."""
    try:
        every = client.request("status")["jobs"]
    except Exception:  # noqa: BLE001 - said as unreachable by the caller
        return {}
    wanted = set(ids)
    return {j["id"]: j for j in every if j.get("id") in wanted}


# --- the rows ------------------------------------------------------------------------


def _studies(root: Path) -> dict[str, tuple[str, str]]:
    from puzzle.model.schema import Study
    from puzzle.project.store import load_project

    try:
        project = load_project(Path(root))
    except Exception:  # noqa: BLE001 - the rows still show, without names
        return {}
    return {str(s.id): (s.name, s.solver) for s in project.of_type(Study)}


def _row(entry: dict, studies: dict, record: dict | None, state: str, tail: str) -> Row:
    name, solver = studies.get(entry.get("study") or "", ("", ""))
    code = (record or {}).get("state") or ""
    return Row(
        job=entry["job"], study=name, study_id=entry.get("study") or "", solver=solver,
        where=entry.get("where") or HERE, state=state if not code else STATES.get(code, code),
        code=code, elapsed=(record or {}).get("elapsed") if record else None,
        last_line=tail, submitted=float(entry.get("submitted") or 0.0),
        folder=entry.get("folder") or "", workdir=entry.get("workdir") or "",
        host=entry.get("host"), message=(record or {}).get("message") or "",
    )  # fmt: skip


def jobs(root: Path, *, hosts=None, live: bool = False, base: Path | None = None) -> list[Row]:
    """The project's pending jobs with what their runners say, oldest first.

    `live` asks remote hosts too (SSH: slow); `hosts` (name → Host) stands in for the
    user's hosts.toml; `base` for the local runner's folder (tests)."""
    entries = sorted(pending.entries(Path(root)), key=lambda e: e.get("submitted") or 0)
    if not entries:
        return []
    studies = _studies(root)
    rows: list[Row] = []
    here = [e for e in entries if (e.get("where") or HERE) == HERE]
    if here:
        client = _local_client(base)
        try:
            records = _ask(client, [e["job"] for e in here]) if client is not None else {}
            for e in here:
                rec = records.get(e["job"])
                state = UNREACHABLE
                if rec is None:  # the runner stopped (or forgot it): what its folder says
                    rec = _on_disk(e["job"], base)
                    if rec is not None and rec.get("state") not in FINAL:
                        rec, state = None, UNREACHABLE
                    elif rec is None and client is not None:
                        state = GONE
                tail = _file_tail(Path(e.get("folder") or "") / "solver.log")
                rows.append(_row(e, studies, rec, state, tail))
        finally:
            if client is not None:
                client.close()
    there: dict[str, list[dict]] = {}
    for e in entries:
        if (e.get("where") or HERE) != HERE:
            there.setdefault(e["where"], []).append(e)
    known = _hosts(hosts) if live and there else {}
    for where, group in there.items():
        if not live:
            rows += [_row(e, studies, None, NOT_ASKED, "") for e in group]
            continue
        host = known.get(where)
        client = None
        if host is not None:
            with contextlib.suppress(Exception):
                client = _remote_client(host)
        if client is None:
            rows += [_row(e, studies, None, UNREACHABLE, "") for e in group]
            continue
        try:
            records = _ask(client, [e["job"] for e in group])
            for e in group:
                rec = records.get(e["job"])
                tail = _log_tail(client, e["job"]) if rec is not None else ""
                rows.append(_row(e, studies, rec, GONE, tail))
        finally:
            client.close()
    order = {e["job"]: i for i, e in enumerate(entries)}
    return sorted(rows, key=lambda r: order[r.job])


def resume_order(rows: list[Row]) -> list[Row]:
    """Which to pick up, in order: finished ones first (their results are waiting), then
    the rest oldest first; one per run folder (resuming one attaches its siblings)."""
    seen: set[str] = set()
    out = []
    for r in sorted(rows, key=lambda r: (not r.ended, r.submitted)):
        if r.workdir in seen:
            continue
        seen.add(r.workdir)
        out.append(r)
    return out


def find(root: Path, ref: str) -> list[dict]:
    """The pending entries `ref` names: a job id, or a study's name (all its jobs)."""
    entries = pending.entries(Path(root))
    mine = [e for e in entries if e["job"] == ref]
    if mine:
        return mine
    ids = {sid for sid, (name, _) in _studies(root).items() if name == ref}
    mine = [e for e in entries if e.get("study") in ids]
    if not mine:
        raise PuzzleError(f"no pending job or study with a pending job called '{ref}'",
                          code="not_found")  # fmt: skip
    return mine


def _entry(root: Path, job: str) -> dict:
    for e in pending.entries(Path(root)):
        if e["job"] == job:
            return e
    raise PuzzleError(f"no pending job {job}", code="not_found")


def _client_for(entry: dict, hosts=None, base: Path | None = None):
    where = entry.get("where") or HERE
    if where == HERE:
        return _local_client(base)
    host = _hosts(hosts).get(where)
    if host is None:
        raise PuzzleError(f"the job ran on '{where}', which is not one of your hosts any more",
                          code="not_found")  # fmt: skip
    return _remote_client(host)


def _request(client, where: str, op: str, **args) -> dict:
    from puzzle.jobs.service import coded
    from puzzle.runner.daemon import RunnerError

    try:
        return client.request(op, **args)
    except RunnerError as exc:
        raise coded(exc, where) from None


def cancel(root: Path, job: str, *, hosts=None, base: Path | None = None) -> str:
    """Ask the job's runner to stop it; the entry stays (resume or dismiss it after).
    Returns the job's state in words."""
    entry = _entry(root, job)
    where = entry.get("where") or HERE
    client = _client_for(entry, hosts, base)
    if client is None:
        raise PuzzleError("the job runner on this computer is not running, so the job has "
                          "already stopped", code="invalid")  # fmt: skip
    try:
        got = _request(client, where, "cancel", job=job)["job"]
    finally:
        client.close()
    return STATES.get(got.get("state") or "", got.get("state") or "")


def dismiss(root: Path, job: str, *, delete_remote: bool = False, hosts=None,
            base: Path | None = None, wait_s: float = 10.0) -> dict:  # fmt: skip
    """Forget a pending job without its results. A job still going is cancelled first;
    the runner forgets it; with `delete_remote` (which the caller confirms with the user
    first) a remote host deletes its copy of the case. The run folder here is kept.
    Returns {job, cancelled, forgotten, deleted, note}."""
    entry = _entry(root, job)
    where = entry.get("where") or HERE
    out = {"job": job, "cancelled": False, "forgotten": False, "deleted": False, "note": ""}
    try:
        client = _client_for(entry, hosts, base)
    except PuzzleError as exc:
        client, out["note"] = None, exc.message
    if client is None:
        out["note"] = out["note"] or f"the job runner on {where} is not running"
    else:
        try:
            state = _ask(client, [job]).get(job, {}).get("state")
            if state is not None and state not in FINAL:
                _request(client, where, "cancel", job=job)
                out["cancelled"] = True
                end = time.monotonic() + wait_s
                while time.monotonic() < end:
                    state = _ask(client, [job]).get(job, {}).get("state")
                    if state is None or state in FINAL:
                        break
                    time.sleep(0.1)
            if state is not None:
                delete = delete_remote and where != HERE
                _request(client, where, "forget", job=job, delete=delete)
                out["forgotten"], out["deleted"] = True, delete
            else:
                out["note"] = f"the job runner on {where} no longer knew the job"
        finally:
            client.close()
    pending.remove(Path(root), job)
    return out


def words(seconds: float | None) -> str:
    """Elapsed time for people: 45 s, 12 min 5 s, 3 h 20 min; a dash when unknown."""
    if seconds is None:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {s % 3600 // 60} min"
