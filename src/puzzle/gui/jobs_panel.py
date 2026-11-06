"""The Jobs panel (ADR 0067 phase 4): the project's solves that are with a job runner.

One row per job: study, solver, where it runs, its state in words, how long it has run
and the log's last line. Job ids stay out of sight (they are the CLI's). Resume picks a
job up in the background like a run (the Solution panel and Output follow it, its
results show like any run's), Cancel stops it, Open log shows its log, Dismiss forgets
it without its results after asking.

While the panel is shown it refreshes every few seconds from what is cheap: the
project's pending file and the local runner. Remote hosts are asked on Refresh only
(each is an SSH connection); their answers are kept between refreshes. Reading always
happens off the GUI thread.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.tasks import Task, start
from puzzle.gui.theme import role
from puzzle.jobs import overview

COLUMNS = ["Study", "Solver", "Where", "State", "Elapsed", "Last line"]
EVERY_MS = 3000
EMPTY = ("No solves are with a job runner for this project. A solve started here goes "
         "on if Puzzle closes; it is listed here until its results are home.")  # fmt: skip


def where_words(where: str) -> str:
    return "This computer" if where == overview.HERE else where


class JobsPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.rows: list[overview.Row] = []
        self.remote: dict[str, overview.Row] = {}  # job → its row from the last live ask
        self._reading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.note = QLabel(EMPTY)
        self.note.setWordWrap(True)
        role(self.note, "muted")
        layout.addWidget(self.note)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLUMNS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setAccessibleName("Jobs")
        self.table.currentItemChanged.connect(lambda *_: self._chosen())
        self.table.itemDoubleClicked.connect(lambda *_: self.open_log())
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, tip, slot in (
            ("resume", "Resume", "Pick the job up: follow it and bring its results home",
             self.resume),
            ("resume_all", "Resume all", "Pick up every job, finished ones first",
             self.resume_all),
            ("cancel", "Cancel", "Stop the job; it stays listed until resumed or dismissed",
             self.cancel),
            ("log", "Open log", "The solver's log as far as it has got", self.open_log),
            ("dismiss", "Dismiss…", "Forget the job without its results", self.dismiss),
            ("refresh", "Refresh", "Ask every runner again, remote hosts too (over SSH)",
             lambda: self.refresh(live=True)),
        ):  # fmt: skip
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=slot: f())
            self.buttons[key] = b
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.status = QLabel("")
        role(self.status, "muted")
        buttons.addWidget(self.status)
        layout.addLayout(buttons)
        self.timer = QTimer(self, interval=EVERY_MS)
        self.timer.timeout.connect(lambda: self.refresh())
        self.fill([])

    # --- reading -----------------------------------------------------------------------

    @property
    def root(self) -> Path | None:
        s = getattr(self.window, "session", None)
        return s.root if s is not None else None

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().hideEvent(event)
        self.timer.stop()

    def refresh(self, live: bool = False, *, wait: bool = False) -> None:
        """Read the rows off the GUI thread (`wait`: here and now, for tests)."""
        root = self.root
        if root is None:
            self.fill([])
            return
        if wait:
            self.fill(overview.jobs(root, live=live), live)
            return
        if self._reading:
            return
        self._reading = True
        if live:
            self.status.setText("Asking the hosts…")
        task = Task(lambda _emit: overview.jobs(root, live=live))

        def got(rows) -> None:
            self._reading = False
            with contextlib.suppress(RuntimeError):  # the window closed meanwhile
                if self.root == root:
                    self.fill(rows, live)

        def failed(exc) -> None:
            self._reading = False
            self.status.setText("Could not read the jobs")
            self.window.report(exc)

        task.signals.done.connect(got)
        task.signals.failed.connect(failed)
        self._task = task  # kept alive until it reports
        start(task)

    def fill(self, rows: list[overview.Row], live: bool = False) -> None:
        if live:
            self.remote = {r.job: r for r in rows if r.where != overview.HERE}
        else:  # keep what the hosts said when last asked
            rows = [self.remote.get(r.job, r) if r.state == overview.NOT_ASKED else r
                    for r in rows]  # fmt: skip
        keep = self.selected()
        self.rows = rows
        from puzzle.gui.choices import solver_label

        self.table.clear()
        for r in rows:
            item = QTreeWidgetItem([
                r.study or "A removed study", solver_label(r.solver) if r.solver else "—",
                where_words(r.where), r.state, overview.words(r.elapsed), r.last_line])  # fmt: skip
            item.setData(0, Qt.ItemDataRole.UserRole, r.job)
            item.setToolTip(3, r.message or r.state)
            item.setToolTip(5, r.last_line)
            self.table.addTopLevelItem(item)
            if keep is not None and r.job == keep.job:
                self.table.setCurrentItem(item)
        if self.table.currentItem() is None and self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))
        for c in range(len(COLUMNS) - 1):
            self.table.resizeColumnToContents(c)
        self.note.setVisible(not rows)
        self.table.setVisible(bool(rows))
        n = len(rows)
        self.status.setText(f"{n} job{'s' if n != 1 else ''}" if n else "")
        self._chosen()

    def selected(self) -> overview.Row | None:
        item = self.table.currentItem()
        if item is None:
            return None
        job = item.data(0, Qt.ItemDataRole.UserRole)
        return next((r for r in self.rows if r.job == job), None)

    def _chosen(self) -> None:
        r = self.selected()
        busy = r is not None and r.study_id in getattr(self.window, "running", set())
        self.buttons["resume"].setEnabled(r is not None and not busy)
        self.buttons["resume_all"].setEnabled(len(self.rows) > 1)
        self.buttons["cancel"].setEnabled(r is not None and not r.ended)
        self.buttons["log"].setEnabled(r is not None)
        self.buttons["dismiss"].setEnabled(r is not None)

    # --- acting ------------------------------------------------------------------------

    def resume(self) -> None:
        r = self.selected()
        if r is not None:
            self.window.resume_jobs([r.job])

    def resume_all(self) -> None:
        self.window.resume_jobs([r.job for r in overview.resume_order(self.rows)])

    def cancel(self) -> None:
        r = self.selected()
        if r is None:
            return
        root = self.root

        def done(state) -> None:
            self.window.statusBar().showMessage(f"{r.study or 'The job'}: {state}", 6000)
            self.refresh()

        self.window.background(f"Cancelling {r.study or 'the job'}…",
                               lambda _emit: overview.cancel(root, r.job), done,
                               lock=False)  # fmt: skip

    def open_log(self) -> Path | None:
        """The job's log: the run folder's own here; a remote one as fetched so far."""
        r = self.selected()
        if r is None:
            return None
        log = Path(r.folder) / "solver.log"
        if not log.exists():
            self.window.statusBar().showMessage(
                "No log here yet: a remote job's log comes home with its results", 6000
            )
            return None
        self.window.open_url(QUrl.fromLocalFile(str(log)))
        return log

    def dismiss(self) -> dict | None:
        from puzzle.gui.quick_form import Ask

        r = self.selected()
        if r is None:
            return None
        asks = [Ask("sure", "Dismiss", choices=[("Forget this job", True), ("Keep it", False)])]
        remote = r.where != overview.HERE
        if remote:
            asks.append(Ask("delete", f"The case on {r.where}", choices=[
                ("Keep it there", False), ("Delete it there", True)]))  # fmt: skip
        still = "" if r.ended else " It is still going, so it is cancelled first."
        got = self.window.ask_form(f"Dismiss {r.study or 'the job'}?", asks,
                                   intro=f"Its results are not brought home.{still} The run "
                                   "folder on this computer is kept.")  # fmt: skip
        if not got or not got["sure"]:
            return None
        root, delete = self.root, bool(got.get("delete"))

        def done(out) -> None:
            note = f" ({out['note']})" if out.get("note") else ""
            self.window.statusBar().showMessage(f"Dismissed {r.study or 'the job'}{note}", 8000)
            self.window.refresh()
            self.refresh()

        self.window.background(f"Dismissing {r.study or 'the job'}…",
                               lambda _emit: overview.dismiss(root, r.job, delete_remote=delete),
                               done, lock=False)  # fmt: skip
        return got
