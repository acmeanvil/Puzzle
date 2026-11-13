"""Run one solver command, streaming its output to a log file and a callback."""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from puzzle.jobs.runtime import Runtime

LOG_NAME = "solver.log"
_POLL_S = 0.1
_TERMINATE_GRACE_S = 5.0
MAX_LOG_BYTES = 256 * 1024 * 1024  # a runaway solver (e.g. a warning per node) stops filling
_QUEUE_LINES = 10_000  # bounded, so a fast solver waits for the log instead of filling memory


@dataclass(frozen=True)
class JobResult:
    returncode: int | None
    duration_s: float
    log: Path
    runtime: str
    cancelled: bool = False
    timed_out: bool = False
    outcome: str = ""  # the job runner's final state (ADR 0067); "" when run in-process
    message: str = ""  # what the runner said about how it ended

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not (self.cancelled or self.timed_out)


def run_job(
    command: list[str],
    workdir: Path,
    runtime: Runtime,
    *,
    on_output: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    timeout_s: float | None = None,
    parser=None,
    on_event: Callable | None = None,
) -> JobResult:
    """Run `command` in `workdir`; stdout and stderr go to `workdir/solver.log`.

    Returns when the process exits, is cancelled through `cancel`, or exceeds
    `timeout_s`. A stopped process is sent SIGTERM, then SIGKILL after a grace period.
    A `parser` (`puzzle.progress.Parser`, ADR 0054) reads each line, and its side files
    every poll, into events for `on_event`; it never holds the run up.
    """

    def emit(events) -> None:
        if on_event is not None:
            for e in events:
                on_event(e)

    from puzzle.jobs.service import service_for

    service = service_for(runtime)
    if service is not None:  # the job runner owns the solve (ADR 0067)
        try:
            return _through_runner(service, command, workdir, runtime, on_output=on_output,
                                   cancel=cancel, timeout_s=timeout_s, parser=parser,
                                   emit=emit)  # fmt: skip
        finally:
            service.close()

    argv = runtime.wrap(command, workdir)
    log_path = workdir / LOG_NAME
    start = time.monotonic()
    cancelled = timed_out = False

    with log_path.open("w", encoding="utf-8") as log:

        def say(line: str) -> None:  # the runtime's own notes (a remote copy's size)
            log.write(line + "\n")
            log.flush()
            if on_output:
                on_output(line)

        runtime.prepare(command, workdir, say)
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            env=runtime.environment(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        lines: queue.Queue[str | None] = queue.Queue(maxsize=_QUEUE_LINES)
        written, truncated = 0, False
        reader = threading.Thread(target=_pump, args=(proc, lines), daemon=True)
        reader.start()

        finished = False
        while not finished:
            try:
                line = lines.get(timeout=_POLL_S)
            except queue.Empty:
                line = ""
            if line is None:
                finished = True
            elif line:
                if written < MAX_LOG_BYTES:
                    log.write(line)
                    written += len(line)
                elif not truncated:
                    truncated = True
                    log.write(f"\n[puzzle: log truncated at {MAX_LOG_BYTES} bytes]\n")
                if on_output:
                    on_output(line.rstrip("\n"))
                if parser is not None:
                    emit(parser.safe_feed(line.rstrip("\n")))
            elif parser is not None:  # quiet: side files may have grown
                emit(parser.safe(parser.poll))
            stopping = cancelled or timed_out
            if not finished and cancel is not None and cancel.is_set():
                cancelled = True
            elif not finished and timeout_s is not None and time.monotonic() - start > timeout_s:
                timed_out = True
            if not finished and (cancelled or timed_out) and not stopping:
                runtime.cancel(workdir)  # the far end first (a remote run), then here
                _stop(proc)

        returncode = proc.wait()
        try:  # the case back (a remote one) even after a failure: its log says why
            runtime.finish(workdir, say)
        except Exception:
            if returncode == 0 and not (cancelled or timed_out):
                raise
        if parser is not None:
            emit(parser.safe(parser.poll))
            emit(parser.safe(lambda: parser.finish(returncode == 0)))

    return JobResult(
        returncode=returncode,
        duration_s=time.monotonic() - start,
        log=log_path,
        runtime=runtime.describe(),
        cancelled=cancelled,
        timed_out=timed_out,
    )


def _through_runner(service, command, workdir, runtime, *, on_output, cancel, timeout_s,
                    parser, emit) -> JobResult:  # fmt: skip
    """Submit (or, resuming, attach to) the job, follow its log and state until it ends,
    then bring it home. The timeout counts running time only, not time in a queue."""
    from puzzle.jobs import pending

    log_path = workdir / LOG_NAME
    ctx = pending.CONTEXT.get()
    job = pending.attached(workdir)
    label = {"folder": workdir.name, "runtime": runtime.describe()}
    log = None if service.log_is_local else log_path.open("w", encoding="utf-8")
    written, truncated = 0, False

    def say(line: str) -> None:
        if log is not None:
            log.write(line + "\n")
            log.flush()
        if on_output:
            on_output(line)

    def lines(text: str) -> None:
        nonlocal written, truncated
        for line in text.splitlines(keepends=True):
            if log is not None:
                if written < MAX_LOG_BYTES:
                    log.write(line)
                    written += len(line)
                elif not truncated:
                    truncated = True
                    log.write(f"\n[puzzle: log truncated at {MAX_LOG_BYTES} bytes]\n")
            if on_output:
                on_output(line.rstrip("\n"))
            if parser is not None:
                emit(parser.safe_feed(line.rstrip("\n")))

    try:
        if job is None:
            service.prepare(command, workdir, say)
            job = service.submit(command, workdir, label)
            if ctx is not None:
                pending.add(ctx, job, service.where, workdir, label)
        else:
            if ctx is not None:
                pending.follow(ctx.root, job)
            say(f"[puzzle] picked up job {job} on {service.where}")
        offset, partial, checked = 0, "", 0.0
        cancelled = timed_out = False
        state: dict = {}
        while True:
            text, offset = service.log(job, offset)
            text, partial = (partial + text), ""
            if text and not text.endswith("\n"):
                text, _, partial = text.rpartition("\n")
                text = text + "\n" if text else ""
            if text:
                lines(text)
            now = time.monotonic()
            if not text or now - checked >= service.poll_s:
                state, checked = service.status(job), now
                if state["state"] in ("completed", "failed", "cancelled", "walltime_exceeded",
                                      "out_of_memory", "job_lost"):  # fmt: skip
                    while True:  # the rest of the log
                        text, offset = service.log(job, offset)
                        if not text:
                            break
                        lines(partial + text)
                        partial = ""
                    if partial:
                        lines(partial + "\n")
                    break
            if cancel is not None and cancel.is_set() and not cancelled:
                cancelled = True
                service.cancel(job)
                _inside(runtime, workdir)
            running = state.get("elapsed") or 0.0
            if timeout_s is not None and running > timeout_s and not (timed_out or cancelled):
                timed_out = True
                service.cancel(job)
                _inside(runtime, workdir)
            if not text:
                if parser is not None:
                    emit(parser.safe(parser.poll))
                time.sleep(service.poll_s)
        outcome = state["state"]
        rc = state.get("exit_code")
        if outcome in ("completed", "failed"):
            returncode = rc if rc is not None else (0 if outcome == "completed" else 1)
        elif outcome == "cancelled":
            returncode = rc if rc not in (None, 0) else -15
        else:
            returncode = rc if rc not in (None, 0) else -1
        ok = returncode == 0 and not (cancelled or timed_out)
        try:
            service.finish(job, workdir, say, ok=ok)
        except Exception:
            if ok:  # the results are still there: the job stays pending, to fetch again
                raise
        if ctx is not None:
            pending.remove(ctx.root, job)
        if parser is not None:
            emit(parser.safe(parser.poll))
            emit(parser.safe(lambda: parser.finish(ok)))
        if state.get("message"):
            say(f"[puzzle] {state['message']}")
        return JobResult(
            returncode=returncode, duration_s=float(state.get("elapsed") or 0.0), log=log_path,
            runtime=f"{runtime.describe()} via the job runner", cancelled=cancelled,
            timed_out=timed_out, outcome=outcome, message=state.get("message") or "",
        )  # fmt: skip
    finally:
        if log is not None:
            log.close()


def log_tail(path: Path, lines: int = 20, max_bytes: int = 64 * 1024) -> list[str]:
    """The last lines of a log, read from its end (logs can be huge)."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        text = f.read().decode(errors="replace")
    return text.splitlines()[-lines:]


def _pump(proc: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.put(line)
    lines.put(None)


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()


def _inside(runtime, workdir) -> None:
    """A warm container's solve stopped inside it (ADR 0072): the runner stops the local
    `docker exec`, which leaves the process it started running."""
    from puzzle.jobs.warm import WarmRuntime

    if isinstance(runtime, WarmRuntime):
        import contextlib

        with contextlib.suppress(Exception):  # a cancel never fails the run's own handling
            runtime.cancel(workdir)
