"""Long work (meshing, solving) off the GUI thread, reporting back through Qt signals."""

from __future__ import annotations

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
            result = self.fn(self.signals.output.emit)
        except Exception as exc:  # noqa: BLE001 - reported to the user, not swallowed
            exc.trace = traceback.format_exc()
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


def start(task: Task) -> Task:
    QThreadPool.globalInstance().start(task)
    return task


def rendering_available() -> bool:
    """Whether VTK can draw here: not on Qt's offscreen platform (no OpenGL context)."""
    if os.environ.get("PUZZLE_GUI_VIEWPORT") == "0":
        return False
    return os.environ.get("QT_QPA_PLATFORM", "") not in ("offscreen", "minimal")
