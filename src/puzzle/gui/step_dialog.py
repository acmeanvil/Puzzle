"""The dialog that adds or edits one geometry history step (G4).

Its fields come from puzzle.geometry.steps; lengths are typed in mm and stored in m,
and any length, angle or number field may hold a parameter's name instead. A profile
is typed as points or a DXF file, or drawn in the sketch editor ("Sketch…", G5).
"""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from puzzle.geometry.steps import STEPS, format_profile, parse_profile

# (shown name, stored value)
CHOICES = {
    "axis": [("X axis", "x"), ("Y axis", "y"), ("Z axis", "z")],
    "uv": [("The plane's first axis (u)", "u"), ("The plane's second axis (v)", "v")],
    "mode": [
        ("Add to the part", "add"),
        ("Cut from the part", "cut"),
        ("Keep only the overlap", "intersect"),
        ("A new body", "new"),
    ],
    "plane": [("XY plane", "xy"), ("YZ plane", "yz"), ("XZ plane", "xz")],
}


class ProfileField(QWidget):
    """A profile: typed points ("0, 0; 60, 0; ...", mm), "dxf: file.dxf", or a sketch."""

    def __init__(self, value, open_sketch) -> None:
        super().__init__()
        self.sketch = value.get("sketch") if isinstance(value, dict) else None
        self.line = QLineEdit(format_profile(value) if isinstance(value, dict) else str(value))
        self.line.setMinimumWidth(300)
        self.button = QPushButton("Sketch…")
        self.button.setToolTip("Draw and constrain the profile")
        self.button.clicked.connect(lambda: open_sketch(self))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.line, 1)
        row.addWidget(self.button)
        self.show_sketch()

    def show_sketch(self) -> None:
        from puzzle.gui.sketcher import summary

        if self.sketch is not None:
            self.line.setText(f"Sketch: {summary(self.sketch)}")
            self.line.setReadOnly(True)
            self.button.setText("Edit sketch…")

    def value(self) -> dict:
        if self.sketch is not None:
            return {"sketch": self.sketch}
        return parse_profile(self.line.text())


class StepDialog(QDialog):
    def __init__(
        self,
        kind: str,
        args: dict | None = None,
        name: str = "",
        features=(),
        parent=None,
        params: dict[str, float] | None = None,
    ):
        super().__init__(parent)
        from puzzle.gui.choices import label

        self.kind = kind
        self.params = dict(params or {})
        self.setWindowTitle(f"{label(kind)} step")
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
            for text, v in CHOICES.get(f.kind) or [(n, n) for n in features]:
                box.addItem(text, v)
            if value:
                box.setCurrentIndex(max(box.findData(str(value)), 0))
            return box
        if f.kind == "profile":
            return ProfileField(value, self.open_sketch)
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
                out[f.name] = w.currentData()
            elif isinstance(w, (ProfileField, QSpinBox)):
                out[f.name] = w.value()
            else:
                out[f.name] = _parse(f.kind, w.text())
        return out

    def open_sketch(self, field: ProfileField) -> None:
        """The sketch editor on the profile: its sketch, or its points as lines."""
        spec = field.sketch
        if spec is None:
            try:
                spec = _polygon_sketch(parse_profile(field.line.text()), self.params)
            except ValueError:
                spec = None
        done = self.edit_sketch(spec)
        if done is not None:
            field.sketch = done
            field.show_sketch()

    def edit_sketch(self, spec: dict | None) -> dict | None:
        """The editor's result, or None if cancelled (tests replace this)."""
        from puzzle.gui.sketcher import SketchEditor

        editor = SketchEditor(spec, self.params, self)
        return editor.sketch() if editor.exec() else None

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


def _polygon_sketch(profile: dict, params: dict) -> dict | None:
    """A typed polygon as an unconstrained sketch: its corners joined by lines."""
    points = profile.get("points") or []
    if len(points) < 3:
        return None

    def number(v):
        return float(params[v]) if isinstance(v, str) else float(v)

    names = [f"P{i}" for i in range(1, len(points) + 1)]
    return {
        "points": {n: [number(u), number(v)] for n, (u, v) in zip(names, points, strict=True)},
        "lines": {
            f"line-{i}": [names[i - 1], names[i % len(names)]] for i in range(1, len(names) + 1)
        },
        "constraints": [],
    }
