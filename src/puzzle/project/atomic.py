"""Files that survive a crash and a second writer (ADR 0090 package 2).

Two primitives, used by the project store, its history, versions, the jobs file and
the trust list:

- `write_text`/`write_bytes`: a file written whole or not at all. The new content goes
  to a temporary file in the same folder, which then replaces the old one (`os.replace`
  is atomic on Windows and POSIX). A reader holding the old file open on Windows makes
  the replace fail for a moment, so it is retried briefly.
- `Lock`: a lock file naming its holder (pid, host, a random token). It is stale only
  when the holder is gone: a dead process on this host, or for another host a file its
  holder stopped refreshing (a heartbeat thread touches it while held). Taking and
  breaking it is serialised by an OS lock on a guard file beside it, so two waiters
  never both break a stale lock. Releasing removes the file only while the token is
  still ours, and `check` lets a holder find out it lost the lock before it writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from puzzle.errors import PuzzleError

REPLACE_TRIES = 40  # x 25 ms: a reader on Windows holds a file only briefly
STALE_S = 120.0  # another host's lock, not refreshed for this long, is stale
HEARTBEAT_S = 15.0
SUFFIX = ".tmp"


def temp_name(path: Path, token: str | None = None) -> Path:
    """A temporary file beside `path` (same folder, so the replace is a rename)."""
    path = Path(path)
    return path.with_name(f".{path.name}.{token or secrets.token_hex(4)}{SUFFIX}")


def write_temp(path: Path, data: bytes, *, durable: bool = True, token: str | None = None) -> Path:
    """`data` in a temporary file beside `path`, flushed to disk when `durable`."""
    tmp = temp_name(path, token)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
        if durable:
            f.flush()
            os.fsync(f.fileno())
    return tmp


def replace(src: Path, dst: Path) -> None:
    """`os.replace`, retried while Windows refuses it because a reader has `dst` open."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.025)


def _unlink(path: Path) -> None:
    """Remove a file, retried while Windows refuses because a reader has it open. A
    client waiting for the lock reads it to see who holds it: a release that met that
    read failed and left the lock naming a live process, so the waiter timed out."""
    for attempt in range(REPLACE_TRIES):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.025)


def write_bytes(path: Path, data: bytes, *, durable: bool = True) -> None:
    """Write `path` whole or not at all."""
    tmp = write_temp(path, data, durable=durable)
    try:
        replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str, *, encoding: str = "utf-8", durable: bool = True) -> None:
    write_bytes(path, text.encode(encoding), durable=durable)


def append_line(path: Path, line: str, *, side: Path | None = None, durable: bool = True) -> None:
    """Append one line to an append-only log (history.jsonl), first mending a torn end:
    a last line without its newline that still parses gets the newline; one that does
    not (a crash part-way through writing it) is moved to `side`, never dropped."""
    path = Path(path)
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                start = _last_line_start(f, size)
                f.seek(start)
                tail = f.read(size - start)
                try:
                    json.loads(tail)
                    f.seek(0, os.SEEK_END)
                    f.write(b"\n")
                except ValueError:
                    if side is not None:
                        side.parent.mkdir(parents=True, exist_ok=True)
                        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
                        with open(side, "ab") as s:
                            s.write(f"# set aside {stamp} from {path.name}\n".encode())
                            s.write(tail + b"\n")
                            s.flush()
                            os.fsync(s.fileno())
                    f.truncate(start)
        f.seek(0, os.SEEK_END)
        f.write(data)
        f.flush()
        if durable:
            os.fsync(f.fileno())


def _last_line_start(f, size: int) -> int:
    """Where the last line of an open file starts (after the last newline)."""
    pos, chunk = size, 65536
    while pos > 0:
        step = min(chunk, pos)
        pos -= step
        f.seek(pos)
        block = f.read(step)
        k = block.rfind(b"\n")
        if k >= 0:
            return pos + k + 1
    return 0


def tail_lines(path: Path, count: int = 1) -> list[bytes]:
    """The last `count` complete or torn lines of a file, read from its end."""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        pos, data, chunk = size, b"", 65536
        while pos > 0 and data.count(b"\n") <= count:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        return lines[-count:]


# --- processes ---------------------------------------------------------------------


def alive(pid: int) -> bool:
    """Whether process `pid` is running. Not `os.kill(pid, 0)` on Windows: there it
    terminates the process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


HOST = socket.gethostname()


# --- the OS lock on a guard file ---------------------------------------------------


def _os_try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    with contextlib.suppress(OSError):
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _guard(path: Path, deadline: float) -> Iterator[bool]:
    """The guard's OS lock held for a moment (True), or False past `deadline`. It dies
    with its process, so it needs no staleness rule of its own."""
    guard = path.with_name(path.name + ".guard")
    fd = os.open(guard, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while not _os_try_lock(fd):
            if time.monotonic() > deadline:
                yield False
                return
            time.sleep(0.005)
        try:
            yield True
        finally:
            _os_unlock(fd)
    finally:
        os.close(fd)


# --- the lock file -----------------------------------------------------------------


class Lock:
    """A lock file naming its holder; see the module's docstring."""

    def __init__(self, path: Path, *, what: str = "the project", stale_s: float = STALE_S,
                 heartbeat_s: float = HEARTBEAT_S) -> None:  # fmt: skip
        self.path = Path(path)
        self.what = what
        self.stale_s = stale_s
        self.heartbeat_s = heartbeat_s
        self.token: str | None = None
        self._stop: threading.Event | None = None

    def holder(self) -> dict | None:
        """Who holds it, as the file says: {pid, host, token, time}; None when free.
        A lock written by an older Puzzle holds only a pid."""
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return {}
        if raw.isdigit():
            return {"pid": int(raw), "host": HOST}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def stale(self, holder: dict) -> bool:
        """Whether a holder is gone: a dead process here; elsewhere (or unreadable), a
        file not refreshed for `stale_s`."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        if holder.get("host") == HOST and isinstance(holder.get("pid"), int):
            return not alive(holder["pid"])
        if not holder:  # empty or unreadable: its writer died between create and write
            return age > 2.0
        return age > self.stale_s

    def acquire(self, timeout: float = 10.0) -> None:
        """Take the lock, waiting up to `timeout` s; `busy` when another holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        token = secrets.token_hex(8)
        while True:
            with _guard(self.path, deadline) as got:
                if got:
                    holder = self.holder()
                    if holder is None or self.stale(holder):
                        self._write(token)
                        self.token = token
                        self._start_heartbeat()
                        return
            if time.monotonic() > deadline:
                holder = self.holder() or {}
                raise PuzzleError(
                    f"another client is changing {self.what}"
                    + (f" (process {holder['pid']} on {holder.get('host', '?')})"
                       if holder.get("pid") else ""),
                    code="busy",
                    details={"holder": holder},
                )  # fmt: skip
            time.sleep(0.02)

    def _write(self, token: str) -> None:
        data = {"pid": os.getpid(), "host": HOST, "token": token, "time": time.time()}
        write_text(self.path, json.dumps(data), encoding="utf-8", durable=False)

    def held(self) -> bool:
        """Whether the file still names our token."""
        return self.token is not None and (self.holder() or {}).get("token") == self.token

    def check(self) -> None:
        """Raise `lock_lost` when another client took the lock from us: before writing,
        so a holder that lost it never writes over the other's change."""
        if not self.held():
            raise PuzzleError(
                f"another client took the lock on {self.what} while this one was working "
                "(it was held too long, or the lock file was removed); nothing was written. "
                "Try again: the change is made on the project as it is now",
                code="lock_lost",
            )

    def release(self) -> None:
        """Remove the lock if it is still ours (never another client's). No guard is
        needed: nobody breaks a lock whose holder is alive and refreshing it."""
        self._stop_heartbeat()
        if self.token is None:
            return
        try:
            if self.held():
                _unlink(self.path)
        finally:
            self.token = None

    def _start_heartbeat(self) -> None:
        stop = threading.Event()
        self._stop = stop
        path, token, every = self.path, self.token, self.heartbeat_s

        def beat() -> None:
            while not stop.wait(every):
                if (self.holder() or {}).get("token") != token:
                    return
                with contextlib.suppress(OSError):
                    os.utime(path)

        threading.Thread(target=beat, name="puzzle-lock-heartbeat", daemon=True).start()

    def _stop_heartbeat(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None

    @contextlib.contextmanager
    def hold(self, timeout: float = 10.0) -> Iterator[Lock]:
        self.acquire(timeout)
        try:
            yield self
        finally:
            self.release()


@contextlib.contextmanager
def locked(path: Path, *, what: str = "the file", timeout: float = 10.0) -> Iterator[Lock]:
    """Hold `<path>.lock` around a read-modify-write of a small shared file."""
    path = Path(path)
    with Lock(path.with_name(path.name + ".lock"), what=what).hold(timeout) as lock:
        yield lock
