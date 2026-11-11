"""The solvers on this computer, on the Settings dialog's Solvers page (ADR 0068
decision 6): each solver and how a solve runs it here (native, in a container, or not
available, with why), and CalculiX with PARDISO built here (ADR 0051), with a button
that builds it now.

Looking the solvers up asks each container engine about its images, so it runs on a
thread; so does a build, which goes on if the dialog closes (`puzzle.solver_builds`
keeps its outcome). The panel polls what the threads leave in `_STATE` rather than
being signalled, so a closed dialog is never called back.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from puzzle.gui.theme import role

HOW = {
    "native": "Native",
    "container": "In a container",
    "missing": "Not available",
    "machine": "In Puzzle's Linux machine",
}
STATE = {
    "built": "Built: solves use it",
    "building": "Being built",
    "failed": "The build failed",
    "ready": "Not built yet: the plain CalculiX (SPOOLES) is used",
    "unavailable": "Not available here",
}
# What the threads leave for the panel: the rows, PARDISO's status, the build's last line.
_STATE: dict = {"rows": None, "pardiso": None, "line": "", "building": False, "looking": False}
_LOCK = threading.Lock()


def start_build() -> bool:
    """Build CalculiX with PARDISO in a process of its own (`solver_builds.start`: it
    goes on when Puzzle closes), unless one is going. -> started"""
    from puzzle import solver_builds

    with _LOCK:
        started = solver_builds.start()
    if started:
        _STATE["building"] = True
    return started


def building() -> bool:
    """Whether the build is going: its state file says so and its process lives."""
    from puzzle import solver_builds

    going = solver_builds.status().state == "building"
    if _STATE["building"] and not going:
        _STATE["rows"] = None  # look again: CalculiX's program changed
    _STATE["building"] = going
    if going:
        _STATE["line"] = solver_builds.last_line() or _STATE["line"]
    return going


class SolversPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        heading = QLabel("Solvers on this computer")
        role(heading, "strong")
        self.list = QFormLayout()
        self.checking = QLabel("Checking…")
        role(self.checking, "muted")
        self.pardiso = QLabel("")
        self.pardiso.setWordWrap(True)
        self.line = QLabel("")
        role(self.line, "muted")
        self.line.setWordWrap(True)
        self.build = QPushButton("Build CalculiX with PARDISO now")
        self.build.setToolTip("Compile CalculiX with Intel MKL's PARDISO on this computer "
                              "(a few minutes, in the background)")  # fmt: skip
        self.build.clicked.connect(self._build)
        self.repair = QPushButton("Repair a solver environment…")
        self.repair.setToolTip("Reinstall one of the installed app's solver environments "
                               "from its package list (downloads its packages again)")  # fmt: skip
        self.repair.clicked.connect(self._repair)
        row = QHBoxLayout()
        row.addWidget(self.build)
        row.addWidget(self.repair)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(heading)
        layout.addWidget(self.checking)
        layout.addLayout(self.list)
        layout.addWidget(QLabel("CalculiX with PARDISO:"))
        layout.addWidget(self.pardiso)
        layout.addWidget(self.line)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._show)
        self.shown_rows = None

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        if _STATE["rows"] is None:
            self._look()
        self.timer.start()
        self._show()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self.timer.stop()
        super().hideEvent(event)

    def _look(self) -> None:
        from puzzle import solver_builds

        with _LOCK:
            if _STATE["looking"]:
                return
            _STATE["looking"] = True

        def work() -> None:
            try:
                _STATE["pardiso"] = solver_builds.status()
                _STATE["rows"] = solver_builds.solvers()
            except Exception as exc:  # shown, never raised into Qt
                _STATE["rows"] = []
                _STATE["line"] = f"couldn't list the solvers: {exc}"
            finally:
                _STATE["looking"] = False

        threading.Thread(target=work, name="solvers-look", daemon=True).start()

    def _repair(self, name: str | None = None) -> bool:
        """Pick an environment and reinstall it on a thread (ADR 0068 decision 6)."""
        from puzzle import solver_repair

        why = solver_repair.why_not()
        if why:
            _STATE["line"] = f"Repair: {why}"
            self._show()
            return False
        if name is None:
            from PySide6.QtWidgets import QInputDialog

            names = [e.name for e in solver_repair.environments()]
            name, ok = QInputDialog.getItem(self, "Repair a solver environment",
                                            "Environment", names, 0, False)  # fmt: skip
            if not ok:
                return False

        def work() -> None:
            def say(line: str) -> None:
                _STATE["line"] = line

            try:
                solver_repair.repair(name, say=say)
            except solver_repair.RepairError as exc:
                _STATE["line"] = str(exc)
            _STATE["rows"] = None

        threading.Thread(target=work, name="solver-repair", daemon=True).start()
        _STATE["line"] = f"repairing {name}…"
        self._show()
        return True

    def _build(self) -> None:
        if start_build():
            _STATE["line"] = "starting the build"
        self._show()

    def _show(self) -> None:
        from puzzle import solver_builds

        rows = _STATE["rows"]
        if rows is None and not building():
            self._look()
        if rows is not None and rows is not self.shown_rows:
            self.shown_rows = rows
            while self.list.rowCount():
                self.list.removeRow(0)
            for r in rows:
                text = f"{HOW[r.how]}: {r.where}" + (f" ({r.note})" if r.note else "")
                value = QLabel(text)
                value.setWordWrap(True)
                self.list.addRow(r.name, value)
            self.checking.setVisible(False)
        status = solver_builds.status() if not building() else None
        if status is not None:
            _STATE["pardiso"] = status
        state = "building" if building() else (status.state if status else "ready")
        detail = "" if building() or status is None else status.detail
        self.pardiso.setText(STATE[state] + (f": {detail}" if detail and state != "built" else ""))
        line = _STATE["line"]
        self.line.setText(line if building() or state == "failed" or "repair" in line.lower()
                          or "reinstall" in line else "")  # fmt: skip
        self.build.setEnabled(state in ("ready", "failed", "built"))
        self.build.setText("Build it again" if state == "built" else
                           "Build CalculiX with PARDISO now")  # fmt: skip
