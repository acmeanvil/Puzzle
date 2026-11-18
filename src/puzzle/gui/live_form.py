"""A one-dialog form (`gui.quick_form`) that shows what its values give while they are
typed: a line, a table or a plot below the fields, refreshed on every change (after a
short pause once the dialog is on screen, so a slow preview doesn't lag typing). Used by
the forms for what MCP tools do (ADR 0072 section 10), so each shows the same function's
answer the tool gives. `kind="file"` is a path with a Browse… button.

    form = LiveForm(window, "VDI 2230 check", asks, changed=show, below=table,
                    help_key="form:vdi2230")
    window.run_dialog(form)
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

from puzzle.gui.quick_form import Ask, QuickForm


class LiveForm(QuickForm):
    def __init__(self, window, title: str, asks: list[Ask], intro: str = "", check=None, *,
                 changed: Callable[[dict], None] | None = None, below: QWidget | None = None,
                 help_key: str = "", file_filter: str = "All files (*)") -> None:  # fmt: skip
        self._changed_hook = None
        self._file_filter = file_filter
        super().__init__(title, asks, intro, check, window)
        self.window_ = window
        if help_key:
            self.setProperty("help", help_key)  # F1 lands on the guide's heading (ADR 0083)
        if below is not None:
            layout = self.layout()
            layout.insertWidget(layout.indexOf(self.buttons), below, 1)
        self._timer = QTimer(self, singleShot=True, interval=300)
        self._timer.timeout.connect(self.update_preview)
        self._changed_hook = changed
        self.update_preview()

    def _add(self, a: Ask) -> None:
        if a.kind != "file":
            return super()._add(a)
        w = QLineEdit("" if a.default is None else str(a.default))
        w.textChanged.connect(lambda _=None: self._changed())
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda _=False, line=w: self._browse_file(line))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(w, 1)
        h.addWidget(browse)
        self.widgets[a.key] = w
        self.form.addRow(a.label, row)
        if a.help:
            w.setToolTip(a.help)
        return None

    def _browse_file(self, line: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", line.text(),
                                              self._file_filter)  # fmt: skip
        if path:
            line.setText(path)

    def _refresh(self) -> None:
        super()._refresh()
        if self._changed_hook is None:
            return
        if self.isVisible():
            self._timer.start()
        else:  # not on screen (building it, tests): at once
            self.update_preview()

    def update_preview(self) -> None:
        if self._changed_hook is not None:
            self._changed_hook(self.values())


def quantity(text: str, unit: str, kind: str | None = None) -> dict:
    """ "6 mm" -> {"value": 6, "unit": "mm"}; a bare number is in `unit` (the unit
    shown, ADR 0024); the unit must measure what `unit` does (or `kind`). Raises
    ValueError in words (`units.parse_quantity`, PANEL-11, PANEL-17)."""
    from puzzle.model.units import parse_quantity

    value, given = parse_quantity(text, unit, kind)
    return {"value": value, "unit": given}


def shown_length(metres: float) -> str:
    """A length in the display unit, as a form's default text."""
    from puzzle.settings import length_unit

    unit, per_metre = length_unit()
    return f"{metres * per_metre:g} {unit}"
