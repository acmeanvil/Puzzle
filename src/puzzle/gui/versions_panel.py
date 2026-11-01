"""The Versions panel (ADR 0059): the project's versions to roll back to.

Filters across the top (who made it, its type, when, known good only, words), the
versions newest first (number, date and time, by, what, known good, studies), the
selected one's summary beside them (what the project held then and what changed
since the version before), and what to do with it: save one, restore one, roll back
to the last known good, mark known good, save a copy from one, rename, delete.
Every change goes through the session (`save_version`, `restore_version`,
`mark_version`), so restores are history entries that undo takes back.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.errors import PuzzleError
from puzzle.gui.theme import role
from puzzle.project import versions

WHO = [("Anyone", None), ("You", "you"), ("Puzzle (automatic)", "puzzle"), ("Agents", "agent")]
WHEN = [("Any time", None), ("Today", 0), ("Last 7 days", 7), ("Last 30 days", 30),
        ("Last year", 365)]  # fmt: skip
COLUMNS = ["Version", "Date and time", "By", "What", "Known good", "Studies"]


def studies_words(v: versions.Version) -> str:
    """The studies' states in a few words: "2 up to date, 1 not run"."""
    counts: dict[str, int] = {}
    for st in v.summary.get("studies", []):
        state = (st.get("state") or "").lower() or "unknown"
        counts[state] = counts.get(state, 0) + 1
    return ", ".join(f"{n} {s}" for s, n in counts.items()) or "none"


def details_html(v: versions.Version) -> str:
    """The version's summary as the panel shows it."""
    when = v.when.astimezone().strftime("%A %d %B %Y, %H:%M")
    s = v.summary
    good = f" · <b>known good</b> ({escape(v.good_reason)})" if v.good else ""
    rows = [
        f"<h3>Version {v.number}: {escape(v.what)}</h3>",
        f"<p>{escape(when)} · by {escape(v.by)}{good}</p>",
    ]
    if v.note:
        rows.append(f"<p><i>{escape(v.note)}</i></p>")
    counts = s.get("counts", {})
    if counts:
        rows.append("<p>" + ", ".join(f"{n} {k}" for k, n in counts.items()) + "</p>")
    studies = s.get("studies", [])
    if studies:
        rows.append("<p><b>Studies</b><br>" + "<br>".join(
            f"{escape(st['name'])} ({escape(st.get('solver') or '')}): "
            f"{escape(st.get('state') or '')}" for st in studies) + "</p>")  # fmt: skip
    problems = s.get("problems", 0)
    rows.append(f"<p>{problems} problem{'s' if problems != 1 else ''}"
                + (f" · last change: {escape(s['last_command'])}" if s.get("last_command") else "")
                + "</p>")  # fmt: skip
    changes = s.get("changes", {})
    words = {"added": "Added", "changed": "Changed", "removed": "Removed"}
    if changes:
        lines = [f"{words[k]}: {escape(', '.join(items))}" for k, items in changes.items()]
        rows.append("<p><b>Since the version before</b><br>" + "<br>".join(lines) + "</p>")
    elif v.number > 1:
        rows.append("<p>Nothing changed since the version before.</p>")
    rows.append(f"<p>{len(v.files)} files</p>")
    return "".join(rows)


class VersionsPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.shown: list[versions.Version] = []
        self.every: list[versions.Version] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.who = QComboBox()
        self.kind = QComboBox()
        self.when = QComboBox()
        for box, items in ((self.who, WHO), (self.when, WHEN)):
            for text, value in items:
                box.addItem(text, value)
        self.kind.addItem("Every type", None)
        for key, text in versions.TYPES.items():
            self.kind.addItem(text, key)
        self.good = QCheckBox("Known good only")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search labels, notes, studies and changes")
        self.count = QLabel("")
        role(self.count, "muted")
        for w, tip in ((self.who, "Who made them"), (self.kind, "Why they were made"),
                       (self.when, "When they were made")):  # fmt: skip
            w.setToolTip(tip)
            w.currentIndexChanged.connect(self.fill)
            bar.addWidget(w)
        self.good.toggled.connect(self.fill)
        self.search.textChanged.connect(self.fill)
        bar.addWidget(self.good)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.count)
        layout.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLUMNS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.currentItemChanged.connect(lambda *_: self._chosen())
        self.table.itemDoubleClicked.connect(lambda *_: self.restore())
        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)

        buttons = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, tip, slot in (
            ("save", "Save version…", "Keep the project as it is now (⌘/Ctrl+S)",
             lambda: window.save_version_dialog()),
            ("restore", "Restore", "Make the project this version's again; undo takes it back",
             self.restore),
            ("good", "Roll back to last known good",
             "Restore the latest version marked known good", lambda: window.roll_back_good()),
            ("mark", "Mark known good", "Mark or unmark the selected version as known good",
             self.toggle_good),
            ("copy", "Save as new project…", "A new, independent project from this version",
             self.copy),
            ("rename", "Rename…", "Its label and note", self.rename),
            ("delete", "Delete", "Remove this version (the project is not changed)",
             self.delete),
        ):  # fmt: skip
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.buttons[key] = b
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.refresh()

    # --- reading -----------------------------------------------------------------------

    @property
    def root(self) -> Path | None:
        s = getattr(self.window, "session", None)
        return s.root if s is not None else None

    def refresh(self) -> None:
        self.every = versions.versions(self.root) if self.root is not None else []
        self.fill()

    def filters(self) -> dict:
        days = self.when.currentData()
        since = None
        if days is not None:
            start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            since = start - timedelta(days=days)
        return {
            "by": self.who.currentData(),
            "types": {self.kind.currentData()} if self.kind.currentData() else None,
            "since": since,
            "good": True if self.good.isChecked() else None,
            "text": self.search.text(),
        }

    def fill(self) -> None:
        keep = self.selected()
        self.shown = list(reversed(versions.select(self.every, **self.filters())))
        self.table.clear()
        for v in self.shown:
            item = QTreeWidgetItem([
                str(v.number), v.when.astimezone().strftime("%Y-%m-%d %H:%M"), v.by, v.what,
                "✓" if v.good else "", studies_words(v)])  # fmt: skip
            item.setData(0, Qt.ItemDataRole.UserRole, v.number)
            item.setToolTip(3, v.note or v.what)
            self.table.addTopLevelItem(item)
            if keep is not None and v.number == keep.number:
                self.table.setCurrentItem(item)
        if self.table.currentItem() is None and self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))
        for c in range(len(COLUMNS)):
            self.table.resizeColumnToContents(c)
        total = len(self.every)
        self.count.setText(f"{len(self.shown)} of {total}" if len(self.shown) != total else
                           f"{total} version{'s' if total != 1 else ''}")  # fmt: skip
        self._chosen()

    def selected(self) -> versions.Version | None:
        item = self.table.currentItem()
        if item is None:
            return None
        n = item.data(0, Qt.ItemDataRole.UserRole)
        return next((v for v in self.every if v.number == n), None)

    def _chosen(self) -> None:
        v = self.selected()
        self.detail.setHtml(details_html(v) if v else
                            "<p>No version chosen. Versions are made before runs, after a "
                            "run of changes and when you save one (⌘/Ctrl+S).</p>")  # fmt: skip
        for key in ("restore", "mark", "copy", "rename", "delete"):
            self.buttons[key].setEnabled(v is not None)
        self.buttons["good"].setEnabled(any(x.good for x in self.every))
        if v is not None:
            self.buttons["mark"].setText("Unmark known good" if v.good else "Mark known good")

    # --- acting ------------------------------------------------------------------------

    def restore(self) -> None:
        v = self.selected()
        if v is not None:
            self.window.restore_version(v.number)

    def toggle_good(self) -> None:
        v = self.selected()
        if v is None:
            return
        try:
            self.window.session.mark_version(v.number, not v.good)
        except PuzzleError as exc:
            self.window.report(exc)
        self.refresh()

    def copy(self) -> None:
        v = self.selected()
        if v is not None:
            self.window._save_as_dialog(version=v.number)

    def rename(self) -> None:
        from puzzle.gui.quick_form import Ask

        v = self.selected()
        if v is None:
            return
        got = self.window.ask_form(f"Version {v.number}", [
            Ask("label", "Label", kind="text", default=v.label or v.what),
            Ask("note", "Note", kind="multiline", default=v.note)])  # fmt: skip
        if got is not None:
            with self.window.session._locked():
                versions.edit(self.root, v.number, got["label"], got["note"])
            self.refresh()

    def delete(self) -> None:
        from puzzle.gui.quick_form import Ask

        v = self.selected()
        if v is None:
            return
        got = self.window.ask_form(f"Delete version {v.number}?", [
            Ask("sure", "Delete", choices=[("Delete this version", True), ("Keep it", False)])],
            intro=f"{v.what}, {v.when.astimezone():%Y-%m-%d %H:%M}. The project itself is not "
            "changed; only this way back is gone.")  # fmt: skip
        if got is not None and got["sure"]:
            with self.window.session._locked():
                versions.delete(self.root, v.number)
            self.refresh()
