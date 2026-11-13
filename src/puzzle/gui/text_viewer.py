"""A log in a window of its own: search with every match marked, errors and warnings
coloured by the solvers' own markers (`puzzle.solver_logs.classify`), Previous/Next
problem, line wrap, Copy all, Save as… and Open folder.

It shows a solve's whole logs from its run folder (a run's Solver output…, Results →
View → Solver output…), follows the Output tab while a solve writes to it (Output's
Open in window), and is Help → Log's window too (`gui.log_viewer`).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QKeySequence,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from puzzle import solver_logs
from puzzle.gui import theme

MARKED = 5000  # matches marked at most; the count goes on past it
POLL_MS = 700  # how often a followed log is checked for new text


class LogHighlighter(QSyntaxHighlighter):
    """Errors, warnings and good news in the theme's colours, a line at a time."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        t = theme.current()
        self.formats: dict[str, QTextCharFormat] = {}
        for kind, colour, bold in (("error", t.error, True), ("warning", t.warning, False),
                                   ("good", t.done, False)):  # fmt: skip
            f = QTextCharFormat()
            f.setForeground(QColor(colour))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            self.formats[kind] = f

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt's name)
        kind = solver_logs.classify(text)
        if kind is not None:
            self.setFormat(0, len(text), self.formats[kind])


class TextViewer(QDialog):
    """`load()` gives the text to show and a note (where it came from); `files` are the
    logs to choose between, else `follow` is a text box to mirror as it grows."""

    def __init__(self, parent=None, *, title: str, files: list[Path] | None = None,
                 follow: QPlainTextEdit | None = None, load: Callable[[], str] | None = None,
                 folder: Path | None = None, note: str = "") -> None:  # fmt: skip
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 700)
        self.files = files or []
        self.follow = follow
        self.loader = load
        self.folder = folder
        self._seen: object = None
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = LogHighlighter(self.text.document())

        top = QHBoxLayout()
        self.choice = QComboBox()
        for path in self.files:
            self.choice.addItem(str(path.relative_to(folder)) if folder else path.name, path)
        self.choice.currentIndexChanged.connect(lambda _i: self.fill(force=True))
        self.choice.setVisible(len(self.files) > 1)
        top.addWidget(self.choice)
        self.where = theme.role(QLabel(note), "muted")
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.where, 1)
        self.wrap = QCheckBox("Wrap lines")
        self.wrap.toggled.connect(self._wrap)
        top.addWidget(self.wrap)

        find = QHBoxLayout()
        self.query = QLineEdit(placeholderText="Find (Enter: next, Shift+Enter: previous)")
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(lambda _t: self.mark())
        self.query.returnPressed.connect(lambda: self.find(True))
        QShortcut(QKeySequence("Shift+Return"), self.query, lambda: self.find(False))
        self.case = QCheckBox("Match case")
        self.case.toggled.connect(lambda _on: self.mark())
        self.found = theme.role(QLabel(""), "muted")
        find.addWidget(self.query, 1)
        find.addWidget(self.case)
        for label, slot in (("Previous", lambda: self.find(False)),
                            ("Next", lambda: self.find(True))):  # fmt: skip
            button = QPushButton(label)
            button.clicked.connect(slot)
            find.addWidget(button)
        find.addWidget(self.found)
        QShortcut(QKeySequence.StandardKey.Find, self, self._focus_find)
        QShortcut(QKeySequence.StandardKey.FindNext, self, lambda: self.find(True))
        QShortcut(QKeySequence.StandardKey.FindPrevious, self, lambda: self.find(False))

        bottom = QHBoxLayout()
        self.problems = theme.role(QLabel(""), "muted")
        bottom.addWidget(self.problems, 1)
        self.buttons: dict[str, QPushButton] = {}
        actions = [("Previous problem", lambda: self.problem(False)),
                   ("Next problem", lambda: self.problem(True)), ("Refresh", self.refresh),
                   ("Copy all", self.copy), ("Save as…", self.save_as)]  # fmt: skip
        if folder is not None:
            actions.append(("Open folder", self.open_folder))
        actions.append(("Close", self.reject))
        for label, slot in actions:
            button = QPushButton(label)
            button.clicked.connect(slot)
            bottom.addWidget(button)
            self.buttons[label] = button

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(find)
        layout.addWidget(self.text, 1)
        layout.addLayout(bottom)
        for button in self.findChildren(QPushButton):  # Enter in Find is Next, not a button
            button.setAutoDefault(False)
            button.setDefault(False)
        self.fill(force=True)
        self.timer = QTimer(self, interval=POLL_MS)
        self.timer.timeout.connect(self.fill)
        if self.follow is not None or self.files:  # a solve may still be writing
            self.timer.start()

    # --- what is shown --------------------------------------------------------------

    def current_file(self) -> Path | None:
        return self.choice.currentData() if self.files else None

    def _signature(self) -> object:
        if self.follow is not None:
            doc = self.follow.document()
            return doc.blockCount(), doc.lastBlock().text(), doc.characterCount()
        path = self.current_file()
        if path is not None:
            try:
                st = path.stat()
            except OSError:
                return None
            return str(path), st.st_size, st.st_mtime_ns
        return None

    def _text(self) -> str:
        if self.follow is not None:
            return self.follow.toPlainText()
        path = self.current_file()
        if path is not None:
            try:
                text, cut = solver_logs.read_end(path)
            except OSError as exc:
                return f"Couldn't read {path}: {exc}"
            if cut:
                self.where.setText(f"{path} (the last {solver_logs.SHOWN_BYTES // 1_000_000} MB; "
                                   "Save as… keeps the whole file)")  # fmt: skip
            else:
                self.where.setText(str(path))
            return text
        return self.loader() if self.loader is not None else ""

    def fill(self, force: bool = False) -> None:
        """Show the text again when it changed, staying at the end if the reader was."""
        sig = self._signature()
        if not force and sig == self._seen:
            return
        self._seen = sig
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        keep = bar.value()
        self.text.setPlainText(self._text() or "Nothing written yet.")
        if force or at_end:
            self.text.moveCursor(QTextCursor.MoveOperation.End)
            self.text.ensureCursorVisible()
        else:
            bar.setValue(keep)
        self.mark()
        self._count_problems()

    def refresh(self) -> None:
        self.fill(force=True)

    def _wrap(self, on: bool) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if on else QPlainTextEdit.LineWrapMode.NoWrap
        self.text.setLineWrapMode(mode)

    def _count_problems(self) -> None:
        seen = {"error": 0, "warning": 0}
        for row in self.text.toPlainText().splitlines():
            kind = solver_logs.classify(row)
            if kind in seen:
                seen[kind] += 1
        e, w = seen["error"], seen["warning"]
        self.problems.setText(f"{e} error line{'s' * (e != 1)}, {w} warning line{'s' * (w != 1)}")
        for label in ("Previous problem", "Next problem"):
            self.buttons[label].setEnabled(bool(e or w))

    # --- search -----------------------------------------------------------------------

    def _focus_find(self) -> None:
        self.query.setFocus()
        self.query.selectAll()

    def _flags(self, forward: bool = True) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if not forward:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def mark(self) -> int:
        """Mark every match (up to MARKED) and say how many there are."""
        word = self.query.text()
        doc = self.text.document()
        marks: list[QTextEdit.ExtraSelection] = []
        count = 0
        if word:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(theme.current().highlight))
            fmt.setForeground(QColor(theme.current().accent_text))
            cursor = doc.find(word, 0, self._flags())
            while not cursor.isNull():
                count += 1
                if count <= MARKED:
                    sel = QTextEdit.ExtraSelection()
                    sel.cursor, sel.format = cursor, fmt
                    marks.append(sel)
                cursor = doc.find(word, cursor, self._flags())
        self.text.setExtraSelections(marks)
        self.found.setText("" if not word else (f"{count} match{'es' * (count != 1)}"
                                                if count else "not found"))  # fmt: skip
        return count

    def find(self, forward: bool) -> bool:
        """Move to the next (or previous) match, wrapping round at the end."""
        word = self.query.text()
        if not word:
            return False
        if self.text.find(word, self._flags(forward)):
            return True
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start if forward
                            else QTextCursor.MoveOperation.End)  # fmt: skip
        self.text.setTextCursor(cursor)
        return self.text.find(word, self._flags(forward))

    def problem(self, forward: bool) -> int | None:
        """Go to the next (or previous) error or warning line; its line number."""
        doc = self.text.document()
        here = self.text.textCursor().blockNumber()
        n = doc.blockCount()
        for step in range(1, n + 1):
            i = (here + step) % n if forward else (here - step) % n
            if solver_logs.classify(doc.findBlockByNumber(i).text()) in ("error", "warning"):
                cursor = QTextCursor(doc.findBlockByNumber(i))
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                self.text.setTextCursor(cursor)
                self.text.centerCursor()
                return i + 1
        return None

    # --- out --------------------------------------------------------------------------

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())

    def save_as(self, path: str | None = None) -> Path | None:
        """The whole file (not only the end shown), or the text shown."""
        source = self.current_file()
        if path is None:
            start = source.name if source is not None else "output.txt"
            path, _ = QFileDialog.getSaveFileName(self, "Save the log as", start,
                                                  "Text (*.txt *.log);;All files (*)")  # fmt: skip
            if not path:
                return None
        out = Path(path)
        if source is not None and source.exists():
            shutil.copyfile(source, out)
        else:
            out.write_text(self.text.toPlainText(), encoding="utf-8")
        return out

    def open_folder(self) -> None:
        if self.folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))


# --- opened from the window ---------------------------------------------------------


def _keep(window, viewer: TextViewer) -> TextViewer:
    """Held by the window, or it is collected at once; forgotten when closed."""
    held = window.__dict__.setdefault("_text_viewers", [])
    held.append(viewer)
    viewer.finished.connect(lambda _r: held.remove(viewer) if viewer in held else None)
    viewer.show()
    return viewer


def output_window(window) -> TextViewer:
    """The Output tab in a window of its own, following it as a solve writes."""
    return _keep(window, TextViewer(window, title="Output", follow=window.log,
                                    note="The Output tab, as it grows"))  # fmt: skip


def solver_output(window, run_id: str | None = None) -> TextViewer | None:
    """A solve's whole logs: the run given, else the one selected or shown, else the
    selected study's newest attempt (a failed or stopped one too)."""
    from puzzle.model.schema import Study

    root = window.session.root
    folder, title = None, "Solver output"
    if run_id is None:
        node = window.tree.current()
        if node is not None and node.kind == "run":
            run_id = node.object
    if run_id is None and window._object(window.tree.current()) is None:
        run_id = getattr(window, "_result_run", None)
    if run_id is not None:
        record = window._store().get_run(run_id)
        folder = solver_logs.folder_of(root, record)
        title = f"Solver output: {record.study_name}"
    else:
        study = window._object(window.tree.current())
        if not isinstance(study, Study):
            from puzzle.gui.main_menu import current_study

            study = current_study(window, "Solver output")
        if study is None:
            return None
        folder = solver_logs.latest(root, study.name)
        title = f"Solver output: {study.name}"
    files = solver_logs.logs(folder) if folder is not None else []
    if not files:
        window.statusBar().showMessage(
            "Solver output: no logs kept for that run (its run folder is gone, or it was "
            "reused from an identical run)", 8000)  # fmt: skip
        return None
    return _keep(window, TextViewer(window, title=title, files=files, folder=folder))
