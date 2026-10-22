"""The dialog that adds or edits one geometry history step (G4).

Its fields come from puzzle.geometry.steps; lengths are typed in mm and stored in m,
and any length, angle or number field may hold a parameter's name instead.
"""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from puzzle.geometry.steps import STEPS, format_profile, parse_profile

CHOICES = {
    "axis": ["x", "y", "z"],
    "uv": ["u", "v"],
    "mode": ["add", "cut", "intersect", "new"],
    "plane": ["xy", "yz", "xz"],
}


class StepDialog(QDialog):
    def __init__(
        self, kind: str, args: dict | None = None, name: str = "", features=(), parent=None
    ):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(f"{kind} step")
        self.fields = STEPS[kind]
        args = dict(args or {})
        form = QFormLayout()
        self.name = QLineEdit(name or kind.capitalize())
        form.addRow("Name", self.name)
        self.widgets = {}
        for f in self.fields:
            value = args.get(f.name, f.default)
            widget = self._widget(f, value, features)
            self.widgets[f.name] = widget
            unit = {"length": " (mm)", "angle": " (deg)"}.get(f.kind, "")
            form.addRow((f.label or f.name) + unit, widget)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c53030")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.result_args: dict | None = None

    def _widget(self, f, value, features):
        if f.kind in CHOICES or f.kind == "feature":
            box = QComboBox()
            box.addItems(CHOICES.get(f.kind) or list(features))
            if value:
                box.setCurrentText(str(value))
            return box
        if f.kind == "count":
            spin = QSpinBox()
            spin.setRange(1, 1000)
            spin.setValue(int(value) if isinstance(value, (int, float)) else 1)
            return spin
        if f.kind == "length":
            text = f"{value * 1000:g}" if isinstance(value, (int, float)) else str(value)
        elif f.kind == "profile":
            text = format_profile(value) if isinstance(value, dict) else str(value)
        elif f.kind == "rule":
            text = json.dumps(value)
        elif isinstance(value, float):
            text = f"{value:g}"
        else:
            text = str(value)
        line = QLineEdit(text)
        line.setMinimumWidth(360 if f.kind in ("profile", "rule", "file") else 120)
        return line

    def values(self) -> dict:
        """The step's arguments as the history stores them (SI, parameter names kept)."""
        out = {}
        for f in self.fields:
            w = self.widgets[f.name]
            if isinstance(w, QComboBox):
                out[f.name] = w.currentText()
            elif isinstance(w, QSpinBox):
                out[f.name] = w.value()
            else:
                out[f.name] = _parse(f.kind, w.text())
        return out

    def _accept(self) -> None:
        try:
            self.result_args = self.values()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


def _parse(kind: str, text: str):
    text = text.strip()
    if kind == "profile":
        return parse_profile(text)
    if kind == "rule":
        try:
            return json.loads(text)
        except ValueError:
            raise ValueError('edges or faces are a rule such as {"parallel": "z"}') from None
    if kind in ("file", "text"):
        return text
    try:
        number = float(text)
    except ValueError:
        if text.isidentifier():
            return text  # a parameter's name
        raise ValueError(f"'{text}' is neither a number nor a parameter name") from None
    return number / 1000 if kind == "length" else number
