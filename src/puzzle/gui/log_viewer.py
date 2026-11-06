"""Help → Log: the GUI's log (`gui.errors`) in a window, to read, copy or send.

The log holds every uncaught error with its traceback, Python's warnings and Puzzle's
own log messages, and a crash's native stack. Errors only narrows it to the errors.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from puzzle.gui import errors, theme

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


class LogViewer(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log")
        self.resize(900, 560)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.only_errors = QCheckBox("Errors only")
        self.only_errors.toggled.connect(self.fill)
        where = theme.role(QLabel(str(errors.log_path())), "muted")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top = QHBoxLayout()
        top.addWidget(where, 1)
        top.addWidget(self.only_errors)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        for label, slot in (("Refresh", self.fill), ("Copy all", self.copy),
                            ("Open folder", self.open_folder)):  # fmt: skip
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.text, 1)
        layout.addWidget(buttons)
        self.fill()

    def fill(self) -> None:
        text = read(self.only_errors.isChecked())
        self.text.setPlainText(text or "Nothing logged yet.")
        self.text.moveCursor(QTextCursor.MoveOperation.End)

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(errors.log_path().parent)))


def show(window) -> LogViewer:
    viewer = LogViewer(window)
    viewer.show()
    return viewer
