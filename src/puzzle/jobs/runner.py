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
) -> JobResult:
    """Run `command` in `workdir`; stdout and stderr go to `workdir/solver.log`.

    Returns when the process exits, is cancelled through `cancel`, or exceeds
    `timeout_s`. A stopped process is sent SIGTERM, then SIGKILL after a grace period.
    """
    argv = runtime.wrap(command, workdir)
    log_path = workdir / LOG_NAME
    start = time.monotonic()
    cancelled = timed_out = False

    with log_path.open("w") as log:
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            env=runtime.environment(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
            if not finished and cancel is not None and cancel.is_set():
                cancelled = True
                _stop(proc)
            elif not finished and timeout_s is not None and time.monotonic() - start > timeout_s:
                timed_out = True
                _stop(proc)

        returncode = proc.wait()

    return JobResult(
        returncode=returncode,
        duration_s=time.monotonic() - start,
        log=log_path,
        runtime=runtime.describe(),
        cancelled=cancelled,
        timed_out=timed_out,
    )


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
