"""Where the GUI's time goes: slow steps and stalls written to the log (Help → Log).

Two pieces, both cheap:

- `step("Properties")` around the parts of an action (`with timing.action("Selecting
  Bracket"): ... with timing.step("view"): ...`): when the action takes longer than
  `SLOW_MS`, one line says how long and how it split up.
- `StallWatch`: a timer that should fire every 50 ms; when it fires late, the GUI thread
  was held, and the line says for how long and inside which action (if one was named).

Started by the window; `PUZZLE_SLOW_MS` changes the threshold (0 logs every action).
"""

from __future__ import annotations

import contextlib
import logging
import os
import time

log = logging.getLogger("puzzle.gui.timing")
SLOW_MS = float(os.environ.get("PUZZLE_SLOW_MS", 100))
TICK_MS = 50

_current: list[dict] = []  # the actions in progress, innermost last
_ended: list = [None, 0.0]  # the last action to end, and when


@contextlib.contextmanager
def action(name: str, timed: bool = True):
    """A user action (a tree click, a dialog opening): logged when it was slow. Untimed,
    it only names what a stall happened in (a menu item whose dialog waits for you)."""
    entry = {"name": name, "start": time.perf_counter(), "steps": [], "timed": timed}
    _current.append(entry)
    try:
        yield entry
    finally:
        _current.pop()
        _ended[:] = [name, time.perf_counter()]
        ms = (time.perf_counter() - entry["start"]) * 1000
        if timed and ms >= SLOW_MS:
            parts = ", ".join(f"{n} {t:.0f}" for n, t in entry["steps"] if t >= 1)
            log.warning("slow: %s took %.0f ms%s", name, ms, f" ({parts} ms)" if parts else "")


@contextlib.contextmanager
def step(name: str):
    """A part of the action in progress (nothing outside one)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        if _current:
            _current[-1]["steps"].append((name, (time.perf_counter() - start) * 1000))


def named(name: str, slot):
    """`slot` run as an untimed action: a menu item, which may wait in a dialog."""

    def run(*args, **kwargs):
        with action(name, timed=False):
            return slot(*args, **kwargs)

    return run


def doing(since: float | None = None) -> str | None:
    """The action in progress, else the last one if it ended after `since`."""
    if _current:
        return _current[-1]["name"]
    name, at = _ended
    return name if since is not None and at >= since else None


class StallWatch:
    """Notices the GUI thread being held: a timer that comes back late."""

    def __init__(self, parent) -> None:
        from PySide6.QtCore import QTimer

        self.timer = QTimer(parent, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self._last = time.perf_counter()
        self.stalls: list[float] = []  # their lengths in ms (tests)

    def start(self) -> None:
        self._last = time.perf_counter()
        self.timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        late = (now - self._last) * 1000 - TICK_MS
        self._last = now
        if late >= max(SLOW_MS, 150):
            self.stalls.append(late)
            what = doing(since=now - (late + TICK_MS) / 1000)
            log.warning("the window was held %.0f ms%s", late, f" ({what})" if what else "")
