"""The agent panel's Pick a local model… (ADR 0022): `agent.pick` in the background,
each task's outcome as it lands, then the ranking and the recommendation, and Use it
to save the choice in agent.toml; Cancel stops after the task under way (what was
measured in full is still ranked). Nothing is downloaded."""

from __future__ import annotations

import threading

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from puzzle.gui.theme import role


class PickDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        from puzzle.agent.settings import load_settings

        self.window = window
        self.setWindowTitle("Pick a local model")
        self.found = None
        self.stop = threading.Event()  # Cancel: measuring stops after the current task
        layout = QVBoxLayout(self)
        intro = role(QLabel("Runs Puzzle's evaluation tasks on each model the local endpoint "
                            "already has that fits this computer's memory, and recommends "
                            "the smallest that does them best. It takes a few minutes per "
                            "model; nothing is downloaded."), "muted")  # fmt: skip
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Endpoint"))
        self.base_url = QLineEdit(load_settings().base_url)
        row.addWidget(self.base_url, 1)
        self.start = QPushButton("Measure")
        self.start.setDefault(True)
        self.start.clicked.connect(self.measure)
        row.addWidget(self.start)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.use = QPushButton("Use it")
        self.use.setEnabled(False)
        self.use.clicked.connect(self.use_it)
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        self.cancel.setToolTip("Stop after the task under way")
        self.cancel.clicked.connect(self.cancel_pick)
        buttons.addWidget(self.cancel)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.use)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.resize(620, 460)

    def measure(self) -> None:
        from puzzle.agent import pick

        url = self.base_url.text().strip()
        self.start.setEnabled(False)
        self.stop.clear()
        self.cancel.setEnabled(True)
        self.log.clear()
        self.log.appendPlainText(f"Asking {url} for its models…")

        def work(emit):
            return pick.pick(
                url,
                on_task=lambda m, o: emit(
                    f"{m} / {o.task}: "
                    f"{'endpoint failed' if o.connection else 'pass' if o.passed else 'fail'}"
                    f" ({o.seconds:.0f} s)"
                ),
                cancel=self.stop,
            )

        self.window.background("Measuring local models", work, self.measured, lock=False,
                               output=self.log.appendPlainText, cancel=self.stop,
                               failed=lambda exc: self.failed(exc))  # fmt: skip

    def cancel_pick(self) -> None:
        self.stop.set()
        self.cancel.setEnabled(False)
        self.log.appendPlainText("Stopping after the task under way…")

    def reject(self) -> None:
        self.stop.set()  # closing stops a pick under way too
        super().reject()

    def measured(self, found) -> None:
        self.found = found
        self.start.setEnabled(True)
        self.cancel.setEnabled(False)
        for c in found.candidates:
            if not c.fits:
                self.log.appendPlainText(f"{c.name}: left out, {c.why}")
        for m in found.ranked:
            core = f"core {m.core_passed}/{m.core_total}"
            self.log.appendPlainText(f"{m.model}: {m.passed}/{m.total} tasks, {core}, "
                                     f"{m.median_s:.0f} s a task")  # fmt: skip
        for m in found.incomplete:
            self.log.appendPlainText(f"{m.model}: not measured in full, so not ranked")
        self.result.setText(found.words)
        self.use.setEnabled(found.recommended is not None)

    def failed(self, exc) -> None:
        self.start.setEnabled(True)
        self.cancel.setEnabled(False)
        self.result.setText(str(getattr(exc, "message", exc)))
        role(self.result, "error")

    def use_it(self) -> None:
        from puzzle.agent import pick

        if self.found is None or self.found.recommended is None:
            return
        path = pick.choose(self.found.recommended, self.base_url.text().strip())
        self.window.agent.agent = None  # the next message builds the agent on the new model
        self.window.statusBar().showMessage(f"The agent now uses {self.found.recommended} "
                                            f"({path.name})", 10000)  # fmt: skip
        self.accept()
