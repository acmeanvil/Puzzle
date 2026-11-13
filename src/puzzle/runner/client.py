"""Talking to a runner: over its socket here, or over `ssh host … connect` there."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from puzzle.runner import PROTOCOL
from puzzle.runner.daemon import RunnerError

START_S = 15.0  # how long a daemon may take to come up


def runner_dir(base: Path) -> Path:
    """One runner per protocol version: an older one keeps serving its own jobs."""
    return Path(base).expanduser() / f"p{PROTOCOL}"


def self_command() -> list[str]:
    """How to start this runner again: the zipapp it runs from, or the package."""
    main = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if main is not None and main.name == "main.py" and (main.parent / "puzzle").is_dir():
        # an unpacked bundle (build.py), with the flags it was started with: isolated,
        # so the host's own site-packages never shadow the bundle's PSI/J
        flags = [f for f, on in (("-I", sys.flags.isolated), ("-S", sys.flags.no_site)) if on]
        return [sys.executable, *flags, str(main.resolve())]
    return [sys.executable, "-m", "puzzle.runner"]


class Client:
    """Requests to one runner, one at a time (a lock keeps answers in order)."""

    def __init__(self, reader, writer, close=None, where: str = "this computer"):
        self.reader, self.writer, self._close = reader, writer, close
        self.where = where
        self.lock = threading.Lock()
        self.n = 0
        self.info = self.request("hello", protocol=PROTOCOL)

    def request(self, op: str, **args) -> dict:
        with self.lock:
            self.n += 1
            try:
                self.writer.write(json.dumps({"id": self.n, "op": op, **args}).encode() + b"\n")
                self.writer.flush()
                line = self.reader.readline()
            except (OSError, ValueError) as exc:
                lost = f"lost the runner on {self.where}: {exc}"
                raise RunnerError("disconnected", lost) from None
            if not line:
                raise RunnerError("disconnected", f"lost the runner on {self.where}")
            answer = json.loads(line)
        if not answer.get("ok"):
            raise RunnerError(answer.get("code") or "invalid", answer.get("error") or "")
        return answer

    def close(self) -> None:
        if self._close is not None:
            with contextlib.suppress(OSError):
                self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _connect_socket(path: str) -> socket.socket | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError:
        s.close()
        return None
    return s


def ensure_daemon(base: Path, command: list[str] | None = None, *, cores: int = 0,
                  memory_gb: float = 0.0) -> socket.socket:  # fmt: skip
    """A connected socket to the runner for `base`, starting it (detached, in its own
    session, so it outlives this process) when none is running."""
    root = runner_dir(base)
    info = root / "runner.json"

    def try_connect() -> socket.socket | None:
        try:
            path = json.loads(info.read_text(encoding="utf-8"))["socket"]
        except (OSError, ValueError, KeyError):
            return None
        return _connect_socket(path)

    s = try_connect()
    if s is not None:
        return s
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    argv = [*(command or self_command()), "daemon", "--dir", str(base)]
    if cores:
        argv += ["--cores", str(cores)]
    if memory_gb:
        argv += ["--memory-gb", str(memory_gb)]
    with (root / "daemon.log").open("ab") as log:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)  # fmt: skip
    end = time.monotonic() + START_S
    while time.monotonic() < end:
        s = try_connect()
        if s is not None:
            return s
        time.sleep(0.05)
    tail = (root / "daemon.log").read_text(errors="replace", encoding="utf-8")[-2000:]
    raise RunnerError("unsupported", f"the job runner did not start:\n{tail}")


def local(base: Path, command: list[str] | None = None, **kw) -> Client:
    s = ensure_daemon(base, command, **kw)
    stream = s.makefile("rwb")

    def close() -> None:
        stream.close()
        s.close()

    return Client(stream, stream, close)


def over(argv: list[str], where: str) -> Client:
    """A client over a process's stdin and stdout: `ssh host python3 runner.pyz connect`."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)  # fmt: skip

    def close() -> None:
        try:
            proc.stdin.close()
        finally:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    try:
        return Client(proc.stdout, proc.stdin, close, where)
    except RunnerError as exc:
        close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RunnerError(exc.code, f"{exc}\n{err}".strip()) from None


def bridge(base: Path, **kw) -> None:
    """`connect`: stdin to the runner and its answers to stdout, until either closes."""
    s = ensure_daemon(base, **kw)
    out = sys.stdout.buffer

    def down() -> None:
        try:
            while data := s.recv(65536):
                out.write(data)
                out.flush()
        except OSError:
            pass
        finally:
            os._exit(0)

    threading.Thread(target=down, daemon=True).start()
    stdin = sys.stdin.buffer
    try:
        while data := stdin.read1(65536):
            s.sendall(data)
    except OSError:
        pass
    s.shutdown(socket.SHUT_WR)
    time.sleep(0.5)  # let the last answer through
