"""Feedback that doesn't block (ADR 0045 decision 7).

- **Toasts:** a short note at the window's lower right when a run, mesh or export
  finishes, with an action ("Show results", "Open folder"). They stack, three at
  most, and go after a few seconds (longer for a failure); the mouse over one holds
  it. Settings > Interface > Notes when work finishes turns them off (the status bar
  still says it).
- **Progress:** the status bar's right end shows long work: what it is, a bar (busy,
  or a fraction when the work can tell, as a solve does) and Cancel when the work
  can be stopped.
- **Busy:** `busy(widget, True)` greys a panel that waits for work and says so.

No animation: the window's state changes at once, as the 3D view's does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QWidget,
)

from puzzle.gui.theme import role

MOST = 3  # toasts on screen at once
SECONDS = {"done": 6, "info": 6, "error": 12}
MARGIN = 16


class Toast(QFrame):
    """One note: a mark and the words, an optional action button, and a close."""

    def __init__(self, parent, text: str, action=None, state: str = "done") -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("state", state)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        words_for = {"done": "Finished", "error": "Failed", "info": "Note"}
        self.setAccessibleName(f"{words_for.get(state, 'Note')}: {text}")
        self.setAccessibleDescription(
            f"{text}. {action[0]} is offered." if action is not None else text
        )
        self.text = text
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        mark = QLabel({"done": "✓", "error": "✗"}.get(state, "ℹ"))
        role(mark, {"done": "done", "error": "error"}.get(state, "muted"))
        row.addWidget(mark)
        words = QLabel(text)
        words.setWordWrap(True)
        words.setMaximumWidth(320)
        words.setMinimumWidth(min(320, words.fontMetrics().horizontalAdvance(text) + 4))
        row.addWidget(words, 1)
        self.button = None
        if action is not None:
            label, slot = action
            self.button = QPushButton(label)
            self.button.clicked.connect(lambda _=False: (slot(), self.close_now()))
            row.addWidget(self.button)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Dismiss")
        close.setAccessibleName("Dismiss the note")
        close.clicked.connect(self.close_now)
        row.addWidget(close)
        self.timer = QTimer(self, singleShot=True, interval=SECONDS.get(state, 6) * 1000)
        self.timer.timeout.connect(self.close_now)
        self.closed = None  # set by Toasts

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.timer.stop()  # read at leisure
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.timer.start()
        super().leaveEvent(event)

    def close_now(self) -> None:
        self.timer.stop()
        self.hide()
        if self.closed is not None:
            self.closed(self)
        self.deleteLater()


def _announce(widget) -> None:
    """Tell screen readers a note appeared (an alert), since it takes no focus."""
    try:
        from PySide6.QtGui import QAccessible, QAccessibleEvent

        QAccessible.updateAccessibility(QAccessibleEvent(widget, QAccessible.Event.Alert))
    except Exception:  # noqa: BLE001 - no accessibility bridge: the note still shows
        pass


@dataclass
class Shown:
    """What a toast said, for tests and the Messages log."""

    text: str
    action: str | None
    state: str


class Toasts(QObject):
    """The window's toasts, stacked up from its lower right corner."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.live: list[Toast] = []
        self.shown: list[Shown] = []
        window.installEventFilter(self)

    def show(self, text: str, action=None, state: str = "done") -> Toast | None:
        """A note; `action` = (label, slot). None when notes are off (Settings)."""
        from puzzle.settings import current

        self.shown.append(Shown(text, action[0] if action else None, state))
        if current().get("interface.toasts") is False:
            return None
        toast = Toast(self.window, text, action, state)
        toast.closed = self._closed
        self.live.append(toast)
        while len(self.live) > MOST:
            self.live[0].close_now()
        toast.adjustSize()
        toast.show()
        toast.raise_()
        toast.timer.start()
        self._place()
        _announce(toast)
        return toast

    def _closed(self, toast: Toast) -> None:
        if toast in self.live:
            self.live.remove(toast)
        self._place()

    def _place(self) -> None:
        w = self.window
        bottom = w.height() - MARGIN - (w.statusBar().height() if w.statusBar() else 0)
        for toast in reversed(self.live):
            toast.adjustSize()
            size = toast.sizeHint()
            bottom -= size.height()
            toast.setGeometry(w.width() - size.width() - MARGIN, bottom, size.width(),
                              size.height())  # fmt: skip
            toast.raise_()
            bottom -= 8

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        # getattr: Qt may call this while the window is being torn down.
        if obj is getattr(self, "window", None) and event.type() == QEvent.Type.Resize:
            self._place()
        return False


@dataclass
class Job:
    message: str
    cancel: object = None  # a threading.Event the work watches, or None
    fraction: float | None = None
    stopping: bool = field(default=False)


class StatusProgress(QWidget):
    """Long work in the status bar: its words, a bar and Cancel (when it can stop)."""

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(6)
        self.label = role(QLabel(""), "muted")
        self.bar = QProgressBar()
        self.bar.setFixedWidth(140)
        self.bar.setMaximumHeight(14)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName("Progress")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop this work (a solve stops at its next check)")
        self.cancel_button.clicked.connect(self.cancel)
        for w in (self.label, self.bar, self.cancel_button):
            row.addWidget(w)
        self.jobs: dict[int, Job] = {}
        self.hide()

    def start(self, key, message: str, cancel=None) -> None:
        self.jobs[id(key)] = Job(message, cancel)
        self._show()

    def update(self, key, fraction: float | None) -> None:
        job = self.jobs.get(id(key))
        if job is not None:
            job.fraction = fraction
            self._show()

    def stop(self, key) -> None:
        self.jobs.pop(id(key), None)
        self._show()

    def cancel(self) -> None:
        """Stop the job the bar shows: the newest one that can still be stopped."""
        job = self._stoppable()
        if job is not None:
            job.cancel.set()
            job.stopping = True
        self._show()

    def _stoppable(self) -> Job | None:
        return next((j for j in reversed(self.jobs.values())
                     if j.cancel is not None and not j.stopping), None)  # fmt: skip

    def current(self) -> Job | None:
        """The job shown: the newest one Cancel can stop, else the newest. A run that
        can be stopped keeps its Cancel while a mesh (which can't) starts beside it."""
        if not self.jobs:
            return None
        return self._stoppable() or next(reversed(self.jobs.values()))

    def _show(self) -> None:
        job = self.current()
        if job is None:
            self.hide()
            return
        more = len(self.jobs) - 1
        words = job.message + (f" (and {more} more)" if more else "")
        self.label.setText("Stopping…" if job.stopping else words)
        if job.fraction is None:
            self.bar.setRange(0, 0)  # busy
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, job.fraction)) * 1000))
        self.bar.setAccessibleDescription(job.message)
        stoppable = job.cancel is not None
        self.cancel_button.setVisible(stoppable)
        self.cancel_button.setEnabled(stoppable and not job.stopping)
        if stoppable:  # which job Cancel stops, when several run
            self.cancel_button.setToolTip(f"Stop {job.message} (a solve stops at its next check)")
            self.cancel_button.setAccessibleName(f"Cancel {job.message}")
        self.show()


def busy(widget: QWidget, on: bool, words: str = "Working…") -> None:
    """A panel that waits for work: greyed, with a line saying so on top. Controls
    that were disabled before stay disabled after."""
    note = widget.findChild(QLabel, "busy_note", Qt.FindChildOption.FindDirectChildrenOnly)
    if on:
        if note is None:
            note = role(QLabel(words, widget), "muted")
            note.setObjectName("busy_note")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setText(words)
        note.setAccessibleName(words)
        note.setGeometry(0, 0, widget.width(), 28)
        note.show()
        note.raise_()
    elif note is not None:
        note.hide()
    was_busy = bool(widget.property("busy"))
    widget.setProperty("busy", on)
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if child is note:
            continue
        if on and not was_busy:  # remember how it was, once
            child.setProperty("enabled_before_busy", child.isEnabled())
        if on:
            child.setEnabled(False)
        elif was_busy:
            before = child.property("enabled_before_busy")
            child.setEnabled(True if before is None else bool(before))
            child.setProperty("enabled_before_busy", None)
