"""Errors in the GUI, written down and shown instead of lost.

PySide prints an exception raised in a slot and carries on, so an action that fails just
stops. Started from the Start menu (`pythonw`, no console) the print went nowhere and the
user saw nothing at all (ADR 0068's first Windows tests: projects that wouldn't open).
`install` sends every uncaught exception, and Python's own crash dump, to the log in the
user folder, and tells the user what failed and where the details are.
"""

from __future__ import annotations

import faulthandler
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

MAX_LOG_BYTES = 2_000_000  # started afresh past this: a log, not an archive

_shown = threading.Event()  # one message box at a time
_state: dict = {}  # the open log file and the path, once installed


def log_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "logs" / "gui.log"


def install(path: Path | None = None) -> Path:
    """Write uncaught exceptions (and, without a console, everything printed) to `path`;
    returns it."""
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_suffix(".old.log"))
    log = path.open("a", encoding="utf-8", buffering=1)
    log.write(f"\n=== Puzzle started {datetime.now().isoformat(timespec='seconds')} ===\n")
    _state.update(log=log, path=path)
    if sys.stderr is None:  # pythonw: nowhere for prints and warnings to go
        sys.stderr = log
    if sys.stdout is None:
        sys.stdout = log
    faulthandler.enable(log)  # a crash in native code leaves its stack here
    import logging

    handler = logging.StreamHandler(log)  # Puzzle's warnings and Python's, here too
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.captureWarnings(True)
    _state["handler"] = handler
    sys.excepthook = _hook
    threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)
    return path


def _hook(kind, value, tb) -> None:
    text = "".join(traceback.format_exception(kind, value, tb))
    log = _state.get("log")
    if log is not None:
        log.write(f"--- {datetime.now().isoformat(timespec='seconds')}\n{text}")
        log.flush()
    if sys.__stderr__ is not None:
        sys.__stderr__.write(text)
    if issubclass(kind, KeyboardInterrupt):
        return
    _tell(f"{kind.__name__}: {value}")


def _tell(message: str) -> None:
    """A message box on the GUI thread; nothing from other threads or without an app."""
    try:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        return
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread() or _shown.is_set():
        return
    _shown.set()
    try:
        box = QMessageBox(QMessageBox.Icon.Warning, "Something went wrong",
                          "Puzzle hit an error and stopped what it was doing.",
                          parent=app.activeWindow())  # fmt: skip
        box.setInformativeText(message[:500])
        box.setDetailedText(f"The details are in {_state.get('path', log_path())}")
        box.exec()
    finally:
        _shown.clear()
