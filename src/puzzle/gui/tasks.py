"""Long work (meshing, solving) off the GUI thread, reporting back through Qt signals."""

from __future__ import annotations

import contextlib
import os
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    output = Signal(str)  # a line of solver or mesher output
    done = Signal(object)  # the function's return value
    failed = Signal(object)  # the exception


class Task(QRunnable):
    """Runs `fn(on_output)` on the thread pool; signals arrive on the GUI thread."""

    def __init__(self, fn: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            result = self.fn(self._emit)
        except Exception as exc:  # noqa: BLE001 - reported to the user, not swallowed
            exc.trace = traceback.format_exc()
            self._send(self.signals.failed, exc)
        else:
            self._send(self.signals.done, result)

    def _emit(self, line: str) -> None:
        self._send(self.signals.output, line)

    @staticmethod
    def _send(signal, value) -> None:
        """Report back, unless the window that asked has closed meanwhile (its signals
        are gone with it, and there is nobody left to tell)."""
        with contextlib.suppress(RuntimeError):
            signal.emit(value)


def start(task: Task) -> Task:
    QThreadPool.globalInstance().start(task)
    return task


def rendering_available() -> bool:
    """Whether VTK can draw here: not on Qt's offscreen platform (no OpenGL context)."""
    if os.environ.get("PUZZLE_GUI_VIEWPORT") == "0":
        return False
    return os.environ.get("QT_QPA_PLATFORM", "") not in ("offscreen", "minimal")
