"""Solves through the job runner (ADR 0067): here, or on a remote host over SSH.

`run_job` hands a solve to a `Service` when the runner is on (Settings > Solvers, on by
default; POSIX only for now). The service submits it to a runner, follows its log and
state, cancels it, and brings a finished remote case home whole before the host deletes
it. The solve belongs to the runner, not to this process: if Puzzle stops, it goes on,
and `pending` keeps what is needed to pick it up again.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from puzzle.errors import PuzzleError
from puzzle.runner.daemon import RunnerError

#: The runner's states that are a solver's own exit, not the host's doing.
SOLVER_STATES = ("completed", "failed")


def enabled() -> bool:
    """Whether solves go through the runner: the preference, on POSIX (the runner uses
    Unix sockets and process groups), unless PUZZLE_RUNNER=off (tests)."""
    if os.name != "posix" or os.environ.get("PUZZLE_RUNNER", "").lower() in ("off", "0"):
        return False
    from puzzle.settings import current

    return bool(current().get("solvers.runner"))


def local_base() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "runner"


def coded(exc: RunnerError, where: str) -> PuzzleError:
    codes = {"disconnected": "host_unreachable", "not_found": "not_found",
             "version": "unsupported", "unsupported": "unsupported"}  # fmt: skip
    code = codes.get(exc.code, "invalid")
    return PuzzleError(f"job runner on {where}: {exc}", code=code)


class Service:
    """One runner and how a case reaches it."""

    where = "this computer"
    log_is_local = True  # the job's log is the run folder's own solver.log
    poll_s = 0.25

    def __init__(self, client):
        self.client = client

    def request(self, op: str, **args) -> dict:
        try:
            return self.client.request(op, **args)
        except RunnerError as exc:
            raise coded(exc, self.where) from None

    def prepare(self, command: list[str], workdir: Path, say=None) -> None:
        """Get the case to where the job runs (nothing to do here)."""

    def spec(self, command: list[str], workdir: Path) -> dict:
        raise NotImplementedError

    def submit(self, command: list[str], workdir: Path, label: dict) -> str:
        spec = {**self.spec(command, workdir), "label": label}
        return self.request("submit", spec=spec)["job"]["id"]

    def status(self, job: str) -> dict:
        return self.request("status", jobs=[job])["jobs"][0]

    def log(self, job: str, offset: int) -> tuple[str, int]:
        got = self.request("log", job=job, offset=offset)
        return got["text"], got["offset"]

    def cancel(self, job: str) -> None:
        self.request("cancel", job=job)

    def finish(self, job: str, workdir: Path, say=None, *, ok: bool) -> None:
        """The case is the run folder itself here: only the record goes."""
        self.request("forget", job=job)

    def jobs(self) -> list[dict]:
        return self.request("status")["jobs"]

    def close(self) -> None:
        self.client.close()


class LocalService(Service):
    """The runner on this computer; the job runs in the run folder itself."""

    def __init__(self, runtime):
        from puzzle.machine import physical_cores
        from puzzle.runner import client

        self.runtime = runtime
        try:
            c = client.local(local_base(), [sys.executable, "-m", "puzzle.runner"],
                             cores=physical_cores())  # fmt: skip
        except RunnerError as exc:
            raise coded(exc, self.where) from None
        super().__init__(c)

    def spec(self, command: list[str], workdir: Path) -> dict:
        from puzzle.jobs.runtime import thread_count

        argv = self.runtime.wrap(command, workdir)
        env = self.runtime.environment(argv) or dict(os.environ)
        return {"dir": str(Path(workdir).resolve()), "argv": argv, "env": env,
                "resources": {"processes": 1, "threads": thread_count()}}  # fmt: skip


def remote_command(host, digest: str, base: str = "~/.puzzle-runner") -> str:
    """The shell line that starts the runner's bridge there: the first Python 3.10+ on
    the host (its pixi environments' first, for a native host), run isolated (-I -S) so
    only the runner's own copy of PSI/J is seen."""
    envs = host.envs_dir.rstrip("/")
    envs = "$HOME/" + envs[2:] if envs.startswith("~/") else envs
    pythons = " ".join([*([shlex.quote(host.python)] if host.python else []),
                        *(f'"{envs}/{e}/bin/python3"' for e in ("scripts", "fenicsx")),
                        *(f"python3.{v}" for v in (13, 12, 11, 10)), "python3"])  # fmt: skip
    cores = f" --cores {host.cores}" if host.cores else ""
    memory = f" --memory-gb {host.memory_gb:g}" if host.memory_gb else ""
    base_sh = "$HOME/" + base[2:] if base.startswith("~/") else base
    return (
        f'B="{base_sh}/runner-{digest}"; '
        f"for p in {pythons}; do "
        'if command -v "$p" >/dev/null 2>&1 && '
        '"$p" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; '
        f'then exec "$p" -I -S "$B/main.py" connect --dir "{base_sh}"{cores}{memory}; fi; done; '
        'echo "no Python 3.10 or later on this host" >&2; exit 97'
    )  # fmt: skip


def deploy(host, base: str = "~/.puzzle-runner") -> str:
    """The runner unpacked on the host (once per version: the folder is named for the
    bundle's hash, unpacked into a temporary one and moved into place)."""
    import subprocess

    from puzzle.runner.build import bundle

    digest, data = bundle()
    base_sh = "$HOME/" + base[2:] if base.startswith("~/") else base
    target = f"{base_sh}/runner-{digest}"
    check = host.run(f'test -f "{target}/main.py" && echo yes')
    if check.returncode == 0 and "yes" in check.stdout:
        return digest
    line = (f'mkdir -p "{base_sh}" && chmod 700 "{base_sh}" && T=$(mktemp -d "{target}.XXXXXX") && '
            f'tar xzf - -C "$T" && (mv "$T" "{target}" 2>/dev/null || rm -rf "$T")')  # fmt: skip
    try:
        done = subprocess.run(host.ssh_argv(line), input=data, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise PuzzleError(f"could not copy the job runner to {host.name}: {exc}",
                          code="host_unreachable") from None  # fmt: skip
    if done.returncode != 0:
        raise PuzzleError(f"could not copy the job runner to {host.name}: "
                          f"{done.stderr.decode(errors='replace').strip()}",
                          code="host_unreachable")  # fmt: skip
    return digest


class RemoteService(Service):
    """The runner on a remote host, reached by `ssh host … connect`. The case is copied
    there before the job and fetched back whole after it, checked file by file against
    the runner's manifest, before the host's copy is deleted."""

    log_is_local = False

    def __init__(self, runtime):
        from puzzle.jobs.remote import poll_seconds
        from puzzle.runner import client

        self.runtime = runtime
        self.host = runtime.host
        self.where = self.host.name
        self.poll_s = min(2.0, float(poll_seconds()))
        digest = deploy(self.host)
        try:
            c = client.over(self.host.ssh_argv(remote_command(self.host, digest)), self.where)
        except RunnerError as exc:
            raise coded(exc, self.where) from None
        super().__init__(c)

    def prepare(self, command: list[str], workdir: Path, say=None) -> None:
        from puzzle.jobs.remote import _unreachable, quote

        remote = self.runtime.remote_dir(workdir)
        made = self.host.run(f"mkdir -p {quote(remote)}")
        if made.returncode != 0:
            raise _unreachable(self.host, made)
        moved = self.runtime._copy(workdir, remote, up=True)
        if say is not None:
            say(moved.words())

    def spec(self, command: list[str], workdir: Path) -> dict:
        r, h = self.runtime.resources, self.host
        return {
            "dir": self.runtime.remote_dir(workdir),
            "script": self.runtime.solve_lines(command),
            "scheduler": h.scheduler,
            "owned": True,
            "resources": {"processes": r.processes, "threads": self.runtime.threads() or 1,
                          "memory_gb": r.memory_gb or h.memory_gb,
                          "walltime": r.walltime or h.walltime,
                          "partition": r.partition or h.partition,
                          "account": r.account or h.account},
        }  # fmt: skip

    def finish(self, job: str, workdir: Path, say=None, *, ok: bool) -> None:
        """Fetch into a temporary folder beside the run folder, check every file against
        the runner's manifest, move them into place, then let the host delete its copy.
        Anything short of that leaves the host's copy, to be fetched again."""
        from puzzle.runner.jobs import manifest

        expected = self.request("manifest", job=job)["files"]
        remote = self.runtime.remote_dir(workdir)
        tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=workdir.parent))
        try:
            moved = self.runtime._copy(tmp, remote, up=False)
            got = manifest(tmp)
            wrong = sorted(p for p, v in expected.items() if got.get(p) != v)
            if wrong:
                raise PuzzleError(
                    f"the case fetched from {self.where} does not match the host's copy "
                    f"({len(wrong)} files differ, {wrong[0]} first); the host keeps it",
                    code="transfer_failed", details={"files": wrong[:20]},
                )  # fmt: skip
            for rel in expected:
                dest = workdir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp / rel, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if say is not None:
            say(moved.words())
        self.request("forget", job=job, delete=not self.host.keep_case)


def service_for(runtime) -> Service | None:
    """The runner service for this runtime, or None to run in this process."""
    if not enabled():
        return None
    from puzzle.jobs.remote import RemoteRuntime

    if isinstance(runtime, RemoteRuntime):
        return RemoteService(runtime)
    return LocalService(runtime)
