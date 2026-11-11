"""Help → Log: the GUI's log (`gui.errors`) in the log window (`gui.text_viewer`).

The log holds every uncaught error with its traceback, Python's warnings and Puzzle's
own log messages, and a crash's native stack. Errors only narrows it to the errors.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox

from puzzle.gui import errors
from puzzle.gui.text_viewer import TextViewer

SHOWN_BYTES = 400_000  # the end of the log; older lines stay in the file


def read(errors_only: bool = False, path=None) -> str:
    """The end of the log; with `errors_only`, just the errors (each `---` block)."""
    path = path or errors.log_path()
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - SHOWN_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    if not errors_only:
        return text
    blocks, keep = [], False
    for line in text.splitlines():
        if line.startswith("--- ") or line.startswith("Fatal Python error"):
            keep = True
        elif line.startswith("=== Puzzle started"):
            keep = False
        if keep:
            blocks.append(line)
    return "\n".join(blocks)


class LogViewer(TextViewer):
    """The log in the shared log window (search, colours, problems); Errors only
    narrows it to the errors."""

    def __init__(self, parent=None) -> None:
        self.only_errors = QCheckBox("Errors only")
        super().__init__(parent, title="Log", load=lambda: read(self.only_errors.isChecked()),
                         folder=errors.log_path().parent, note=str(errors.log_path()))  # fmt: skip
        self.only_errors.toggled.connect(lambda _on: self.refresh())
        self.layout().itemAt(0).layout().addWidget(self.only_errors)


def show(window) -> LogViewer:
    viewer = LogViewer(window)
    viewer.show()
    return viewer
