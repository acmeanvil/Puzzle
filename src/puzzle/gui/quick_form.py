"""One dialog for everything a new object needs (no chains of popups).

Creating a study used to ask five questions in five popups: component, solver,
analysis, physics, name. `ask_form` asks them together:

    values = window.ask_form("New study", [
        Ask("component", "Component", choices=[("Bracket", "<id>")]),
        Ask("physics", "Physics this step solves", choices=lambda v: ...,
            shown=lambda v: ...),
        Ask("name", "Name", follow=lambda v: f"{v['component']} static"),
    ])

- choices are (shown name, value) pairs, or a function of the values so far (they are
  rebuilt when an earlier field changes);
- `shown` hides a field that doesn't apply (it is then left out of the values);
- `follow` gives a text field a default that tracks the other fields until it is typed
  in (a study's name follows its component and analysis);
- `check` returns a message that blocks OK, shown at the top of the dialog.

Tests fill the dialog through `window.run_dialog` and `QuickForm.set`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role


@dataclass
class Ask:
    key: str
    label: str
    kind: str = "choice"  # "choice", "text", "multiline" or "folder"
    choices: list | Callable[[dict], list] = field(default_factory=list)
    default: Any = None
    help: str = ""
    shown: Callable[[dict], bool] | None = None
    follow: Callable[[dict], str] | None = None  # text: a default that tracks the others

    def __post_init__(self) -> None:
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"'{self.key}' needs choices")


class QuickForm(QDialog):
    def __init__(
        self,
        title: str,
        asks: list[Ask],
        intro: str = "",
        check: Callable[[dict], str | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.asks = {a.key: a for a in asks}
        self.check = check
        self.widgets: dict[str, QWidget] = {}
        self.typed: set[str] = set()  # text fields the user has typed in
        self._building = False
        layout = QVBoxLayout(self)
        if intro:
            text = QLabel(intro)
            text.setWordWrap(True)
            role(text, "muted")
            layout.addWidget(text)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        role(self.message, "error")
        self.message.hide()
        layout.addWidget(self.message)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        for a in asks:
            self._add(a)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(420)
        self._refresh()

    # --- building ----------------------------------------------------------------------

    def _add(self, a: Ask) -> None:
        if a.kind == "choice":
            w = QComboBox()
            w.currentIndexChanged.connect(lambda _=0: self._changed())
            self.widgets[a.key] = w
            self.form.addRow(a.label, w)
        elif a.kind == "text":
            w = QLineEdit("" if a.default is None else str(a.default))
            w.textEdited.connect(lambda _=None, k=a.key: self._typed(k))
            w.textChanged.connect(lambda _=None: self._changed())
            self.widgets[a.key] = w
            self.form.addRow(a.label, w)
        elif a.kind == "multiline":
            w = QPlainTextEdit("" if a.default is None else str(a.default))
            w.setMinimumHeight(90)
            w.textChanged.connect(lambda: self._changed())
            self.widgets[a.key] = w
            self.form.addRow(a.label, w)
        elif a.kind == "folder":
            w = QLineEdit("" if a.default is None else str(a.default))
            w.textChanged.connect(lambda _=None: self._changed())
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, line=w: self._browse(line))
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(w, 1)
            h.addWidget(browse)
            self.widgets[a.key] = w
            self.form.addRow(a.label, row)
        else:
            raise ValueError(f"unknown field kind '{a.kind}'")
        if a.help:
            w.setToolTip(a.help)

    def _browse(self, line: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder", line.text())
        if path:
            line.setText(path)

    def _typed(self, key: str) -> None:
        self.typed.add(key)

    def _changed(self) -> None:
        if not self._building:
            self._refresh()

    def _refresh(self) -> None:
        """Rebuild dependent choices, hide fields that don't apply, update followers."""
        self._building = True
        try:
            for a in self.asks.values():
                values = self.values(all_fields=True)
                w = self.widgets[a.key]
                if a.kind == "choice":
                    pairs = a.choices(values) if callable(a.choices) else a.choices
                    pairs = [p if isinstance(p, tuple) else (str(p), p) for p in pairs]
                    current = w.currentData() if w.count() else a.default
                    if [w.itemData(i) for i in range(w.count())] != [p[1] for p in pairs]:
                        w.clear()
                        for text, value in pairs:
                            w.addItem(text, value)
                    index = w.findData(current)
                    w.setCurrentIndex(index if index >= 0 else (0 if w.count() else -1))
                if a.shown is not None:
                    visible = bool(a.shown(self.values(all_fields=True)))
                    self.form.setRowVisible(w if a.kind != "folder" else w.parentWidget(), visible)
                if a.follow is not None and a.key not in self.typed:
                    w.setText(a.follow(self.values(all_fields=True)))
        finally:
            self._building = False
        problem = self.check(self.values()) if self.check else None
        self.message.setText(problem or "")
        self.message.setVisible(bool(problem))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)

    # --- values ------------------------------------------------------------------------

    def values(self, all_fields: bool = False) -> dict:
        """Each field's value (a choice's value, a text's stripped text); fields that
        don't apply are left out unless `all_fields`."""
        out: dict = {}
        for a in self.asks.values():
            w = self.widgets[a.key]
            if a.kind == "choice":
                value = w.currentData()
            elif a.kind == "multiline":
                value = w.toPlainText().strip()
            else:
                value = w.text().strip()
            if all_fields or a.shown is None or a.shown(out | {a.key: value}):
                out[a.key] = value
        return out

    def set(self, key: str, value) -> None:
        """Fill a field as a user would: a choice by its value, a text by typing."""
        w = self.widgets[key]
        if self.asks[key].kind == "choice":
            index = w.findData(value)
            if index < 0:
                shown = [w.itemData(i) for i in range(w.count())]
                raise ValueError(f"'{key}' has no choice {value!r}; it offers {shown}")
            w.setCurrentIndex(index)
        elif self.asks[key].kind == "multiline":
            w.setPlainText(str(value))
        else:
            self.typed.add(key)
            w.setText(str(value))
        self._refresh()

    def problem(self) -> str | None:
        return self.message.text() or None

    def _accept(self) -> None:
        if self.check is None or not self.check(self.values()):
            self.accept()


def ask_form(window, title: str, asks: list[Ask], intro: str = "", check=None) -> dict | None:
    """The values of a one-dialog form, or None if cancelled (or blocked by `check`)."""
    dialog = QuickForm(title, asks, intro, check, window)
    if not window.run_dialog(dialog):
        return None
    if check is not None and check(dialog.values()):
        return None
    return dialog.values()


def folder_name(path: str) -> str:
    return Path(path).name if path else ""
