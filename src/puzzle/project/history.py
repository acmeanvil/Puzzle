"""Reading and appending history.jsonl (ADR 0015), the project's append-only record.

A crash part-way through an append leaves a torn last line. Reading skips it, and the
next append sets it aside in `.puzzle/history.torn` (`atomic.append_line`), so the
history goes on. A line that does not parse in the middle (a torn line that a later
append was glued onto, by an older Puzzle, or a hand edit) is skipped and reported
as the problem `damaged_history`; an entry glued after it is still read.

Reads are cached per file and, since the file only grows, a later read parses only
the lines added since (the bytes up to the last complete line are checked unchanged).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puzzle.project import atomic
from puzzle.project.layout import HISTORY, TORN


@dataclass
class _Read:
    offset: int = 0  # bytes up to the end of the last complete line
    edge: bytes = b""  # the bytes just before `offset`, to see the file wasn't rewritten
    lines: int = 0  # complete lines read
    entries: list[dict] = field(default_factory=list)
    bad: list[int] = field(default_factory=list)
    stamp: tuple[int, int] = (-1, -1)


_cache: dict[str, _Read] = {}
_lock = threading.Lock()
EDGE = 64


def _parse(line: str) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        k = line.find('{"seq":', 1)  # an entry glued onto a torn one
        if k < 0:
            return None
        try:
            entry = json.loads(line[k:])
        except ValueError:
            return None
    return entry if isinstance(entry, dict) else None


def _scan(path: Path) -> _Read:
    st = path.stat()
    stamp = (st.st_size, st.st_mtime_ns)
    key = str(path)
    with _lock:
        known = _cache.get(key)
        if known is not None and known.stamp == stamp:
            return known
    with open(path, "rb") as f:
        done = known if known is not None and known.offset <= st.st_size else None
        if done is not None and done.offset:
            f.seek(max(0, done.offset - EDGE))
            if f.read(min(EDGE, done.offset)) != done.edge:
                done = None  # rewritten, not appended to: read it all again
        if done is None:
            done = _Read()
        f.seek(done.offset)
        data = f.read()
    end = data.rfind(b"\n") + 1  # complete lines only; a torn end waits for its newline
    got = _Read(offset=done.offset + end, lines=done.lines, entries=list(done.entries),
                bad=list(done.bad), stamp=stamp)  # fmt: skip
    for raw in data[:end].split(b"\n")[:-1]:
        got.lines += 1
        if not raw.strip():
            continue
        entry = _parse(raw.decode("utf-8", errors="replace"))
        if entry is None:
            got.bad.append(got.lines)
        else:
            got.entries.append(entry)
    with open(path, "rb") as f:
        f.seek(max(0, got.offset - EDGE))
        got.edge = f.read(min(EDGE, got.offset))
    torn = data[end:]
    if torn.strip():  # a last line without its newline: read when whole (it may be)
        entry = _parse(torn.decode("utf-8", errors="replace"))
        if entry is not None:
            view = _Read(**{**got.__dict__, "entries": [*got.entries, entry]})
            with _lock:
                _cache[key] = got
            return view
    with _lock:
        _cache[key] = got
    return got


def read(root: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """Every entry that parses, and the line numbers (from 1) of complete lines that
    don't. A torn last line is not counted (the next append mends it)."""
    path = Path(root) / HISTORY
    try:
        got = _scan(path)
    except FileNotFoundError:
        return [], []
    return [dict(e) for e in got.entries], list(got.bad)


def entries(root: Path) -> list[dict[str, Any]]:
    return read(root)[0]


def newest_first(root: Path):
    """The entries from the newest back, each a copy, read lazily from the cached
    read: a caller that wants only the latest few stops early (BE-11)."""
    path = Path(root) / HISTORY
    try:
        got = _scan(path)
    except FileNotFoundError:
        return
    for e in reversed(got.entries):
        yield dict(e)


def last_seq(root: Path) -> int:
    """The latest entry's seq, read from the file's end (the history only grows)."""
    path = Path(root) / HISTORY
    lines = atomic.tail_lines(path, 4)
    for raw in reversed(lines):
        entry = _parse(raw.decode("utf-8", errors="replace"))
        if entry is not None and "seq" in entry:
            return int(entry["seq"])
    if lines:  # the end is unreadable: count the whole file
        return max((e.get("seq", 0) for e in entries(root)), default=0)
    return 0


def append(root: Path, entry: dict[str, Any], *, durable: bool = True) -> None:
    """Append one entry (with its seq already set), mending a torn end first."""
    root = Path(root)
    atomic.append_line(root / HISTORY, json.dumps(entry, separators=(",", ":")),
                       side=root / TORN, durable=durable)  # fmt: skip


def damage(root: Path) -> list[int]:
    """Line numbers of entries in the middle that do not parse."""
    path = Path(root) / HISTORY
    if not os.path.exists(path):
        return []
    return read(root)[1]
