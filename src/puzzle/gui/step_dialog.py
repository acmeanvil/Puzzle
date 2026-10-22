"""The form of one geometry history step, inline (the Geometry tab, ADR 0017) or as a
dialog (G4).

Its fields come from puzzle.geometry.steps; lengths are typed in mm and stored in m,
and any length, angle or number field may hold a parameter's name instead. A profile
is a sketch step of the part (by name), typed points or a DXF file, or a sketch of
its own. Edges (fillet, chamfer) and faces (remove) are a readable preset, the
entities picked in the view, or a typed rule.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Signal
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
TYPED, INLINE, PICKED = "typed", "inline", "picked"
EDGE_PRESETS = [
    ("Outer vertical edges", {"parallel": "z", "outer": True}),
    ("All outer edges", {"outer": True}),
    ("Edges along X", {"parallel": "x"}),
    ("Edges along Y", {"parallel": "y"}),
    ("Edges along Z", {"parallel": "z"}),
    ("Curved edges", {"curved": True}),
    ("All edges", {"all": True}),
]
FACE_PRESETS = [  # thresholds: (label, rule key, unit)
    ("Holes smaller than a radius", "holes_below", "mm"),
    ("Fillets smaller than a radius", "fillets_below", "mm"),
    ("Faces smaller than an area", "area_below", "mm²"),
]


class ProfileField(QWidget):
    """A profile: a sketch step of the part, typed points ("0, 0; 60, 0; ...", mm) or
    "dxf: file.dxf", or a sketch of its own."""

    changed = Signal()

    def __init__(self, value, open_sketch, sketches=()) -> None:
        super().__init__()
        self.sketch = None  # an inline sketch
        self.source = QComboBox()
        for name in sketches:
            self.source.addItem(f"{name} (sketch)", name)
        self.source.addItem("Typed points or a DXF file", TYPED)
        self.source.addItem("A sketch of its own", INLINE)
        self.line = QLineEdit("")
        self.line.setMinimumWidth(240)
        self.line.textChanged.connect(lambda _: self.changed.emit())
        self.button = QPushButton("Sketch…")
        self.button.setToolTip("Draw and constrain the profile")
        self.button.clicked.connect(lambda: open_sketch(self))
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.source)
        row = QHBoxLayout()
        row.addWidget(self.line, 1)
        row.addWidget(self.button)
        column.addLayout(row)
        sketch = value.get("sketch") if isinstance(value, dict) else None
        if isinstance(sketch, str) and self.source.findData(sketch) >= 0:
            self.source.setCurrentIndex(self.source.findData(sketch))
        elif isinstance(sketch, dict):
            self.sketch = sketch
            self.source.setCurrentIndex(self.source.findData(INLINE))
        else:
            if sketches and not isinstance(value, dict):
                self.source.setCurrentIndex(0)  # a new step: the part's latest sketch
            else:
                self.source.setCurrentIndex(self.source.findData(TYPED))
            self.line.setText(format_profile(value) if isinstance(value, dict) else str(value))
        self.source.currentIndexChanged.connect(lambda _: (self._show(), self.changed.emit()))
        self._show()

    def _show(self) -> None:
        from puzzle.gui.sketcher import summary

        mode = self.source.currentData()
        named = mode not in (TYPED, INLINE)
        self.line.setVisible(not named)
        self.button.setVisible(mode != TYPED and not named)
        if mode == INLINE:
            self.line.setText(f"Sketch: {summary(self.sketch or {})}")
            self.line.setReadOnly(True)
            self.button.setText("Edit sketch…" if self.sketch else "Sketch…")
        else:
            self.line.setReadOnly(False)

    def show_sketch(self) -> None:
        self.source.setCurrentIndex(self.source.findData(INLINE))
        self._show()
        self.changed.emit()

    def value(self) -> dict:
        mode = self.source.currentData()
        if mode == INLINE:
            if self.sketch is None:
                raise ValueError("draw the profile's sketch first (Sketch…)")
            return {"sketch": self.sketch}
        if mode == TYPED:
            return parse_profile(self.line.text())
        return {"sketch": mode}  # a sketch step, by name


class RuleField(QWidget):
    """Edges or faces: a preset, the ones picked in the view, or a typed rule."""

    changed = Signal()

    def __init__(self, value, entity: str, pick=None) -> None:
        super().__init__()
        self.entity = entity  # "edge" or "face"
        self.picked: list[str] = []
        self.choice = QComboBox()
        if entity == "edge":
            for text, rule in EDGE_PRESETS:
                self.choice.addItem(text, rule)
        else:
            for text, key, _unit in FACE_PRESETS:
                self.choice.addItem(text, key)
        self.choice.addItem(f"{entity.capitalize()}s picked in the view", PICKED)
        self.choice.addItem("A typed rule", TYPED)
        self.amount = QLineEdit("")  # a face preset's threshold
        self.amount.setPlaceholderText("value")
        self.amount.setMaximumWidth(90)
        self.amount.textChanged.connect(lambda _: self.changed.emit())
        self.typed = QLineEdit("")
        self.typed.textChanged.connect(lambda _: self.changed.emit())
        self.count = QLabel("")
        self.pick_button = QPushButton("Pick in view")
        self.pick_button.setEnabled(pick is not None)
        self.pick_button.clicked.connect(lambda: pick(self) if pick else None)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(self.choice, 1)
        top.addWidget(self.amount)
        column.addLayout(top)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count, 1)
        bottom.addWidget(self.pick_button)
        column.addLayout(bottom)
        column.addWidget(self.typed)
        self._load(value or {})
        self.choice.currentIndexChanged.connect(lambda _: (self._show(), self.changed.emit()))
        self._show()

    def _load(self, rule: dict) -> None:
        key = "edges" if self.entity == "edge" else "face"
        if isinstance(rule.get(key), list) or (key == "face" and isinstance(rule.get(key), str)):
            names = rule[key]
            self.picked = [names] if isinstance(names, str) else list(names)
            self.choice.setCurrentIndex(self.choice.findData(PICKED))
            return
        for i in range(self.choice.count()):
            data = self.choice.itemData(i)
            if data == rule:
                self.choice.setCurrentIndex(i)
                return
            if isinstance(data, str) and data in rule and len(rule) == 1:
                self.choice.setCurrentIndex(i)
                unit = next(u for _, k, u in FACE_PRESETS if k == data)
                v = rule[data]
                scale = 1e6 if unit == "mm²" else 1e3
                self.amount.setText(f"{v * scale:g}" if isinstance(v, (int, float)) else str(v))
                return
        self.choice.setCurrentIndex(self.choice.findData(TYPED))
        self.typed.setText(json.dumps(rule))

    def _show(self) -> None:
        data = self.choice.currentData()
        threshold = isinstance(data, str) and data not in (PICKED, TYPED)
        self.amount.setVisible(threshold)
        if threshold:
            unit = next(u for _, k, u in FACE_PRESETS if k == data)
            self.amount.setPlaceholderText(unit)
        self.typed.setVisible(data == TYPED)
        self.pick_button.setVisible(data == PICKED)
        self.count.setVisible(data == PICKED)
        n = len(self.picked)
        noun = self.entity if n == 1 else f"{self.entity}s"
        self.count.setText(f"{n} {noun} picked" if n else f"Pick {self.entity}s in the view")

    def set_picked(self, names: list[str]) -> None:
        self.picked = list(dict.fromkeys(names))
        self.choice.setCurrentIndex(self.choice.findData(PICKED))
        self._show()
        self.changed.emit()

    def value(self) -> dict:
        data = self.choice.currentData()
        if data == PICKED:
            if not self.picked:
                raise ValueError(f"pick the {self.entity}s in the view first")
            return {"edges": self.picked} if self.entity == "edge" else {"face": self.picked}
        if data == TYPED:
            try:
                return json.loads(self.typed.text())
            except ValueError:
                raise ValueError('a typed rule is JSON, such as {"parallel": "z"}') from None
        if isinstance(data, dict):
            return dict(data)
        unit = next(u for _, k, u in FACE_PRESETS if k == data)
        text = self.amount.text().strip()
        try:
            number = float(text)
        except ValueError:
            if text.isidentifier():
                return {data: text}
            raise ValueError(f"give the {unit} value for '{self.choice.currentText()}'") from None
        return {data: number / (1e6 if unit == "mm²" else 1e3)}


class StepForm(QWidget):
    """The fields of one step; `values()` gives its arguments (SI, parameters kept)."""

    changed = Signal()

    def __init__(
        self,
        kind: str,
        args: dict | None = None,
        name: str = "",
        features=(),
        params: dict[str, float] | None = None,
        sketches=(),
        pick=None,
    ) -> None:
        super().__init__()
        from puzzle.gui.choices import label

        self.kind = kind
        self.params = dict(params or {})
        self.fields = STEPS[kind]
        self.pick = pick
        args = dict(args or {})
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit(name or label(kind))
        form.addRow("Name", self.name)
        self.widgets = {}
        for f in self.fields:
            value = args.get(f.name, f.default)
            widget = self._widget(f, value, features, sketches)
            self.widgets[f.name] = widget
            unit = {"length": " (mm)", "angle": " (deg)"}.get(f.kind, "")
            form.addRow((f.label or label(f.name)) + unit, widget)

    def _widget(self, f, value, features, sketches):
        if f.kind in CHOICES or f.kind == "feature":
            box = QComboBox()
            for text, v in CHOICES.get(f.kind) or [(n, n) for n in features]:
                box.addItem(text, v)
            if value:
                box.setCurrentIndex(max(box.findData(str(value)), 0))
            box.currentIndexChanged.connect(lambda _: self.changed.emit())
            return box
        if f.kind == "profile":
            field = ProfileField(value, self.open_sketch, sketches)
            field.changed.connect(self.changed.emit)
            return field
        if f.kind == "rule":
            entity = "face" if self.kind == "remove" else "edge"
            field = RuleField(value, entity, self.pick)
            field.changed.connect(self.changed.emit)
            return field
        if f.kind == "count":
            spin = QSpinBox()
            spin.setRange(1, 1000)
            spin.setValue(int(value) if isinstance(value, (int, float)) else 1)
            spin.valueChanged.connect(lambda _: self.changed.emit())
            return spin
        if f.kind == "length":
            text = f"{value * 1000:g}" if isinstance(value, (int, float)) else str(value)
        elif isinstance(value, float):
            text = f"{value:g}"
        else:
            text = str(value)
        line = QLineEdit(text)
        line.setMinimumWidth(240 if f.kind == "file" else 100)
        line.textChanged.connect(lambda _: self.changed.emit())
        return line

    def values(self) -> dict:
        """The step's arguments as the history stores them (SI, parameter names kept)."""
        out = {}
        for f in self.fields:
            w = self.widgets[f.name]
            if isinstance(w, QComboBox):
                out[f.name] = w.currentData()
            elif isinstance(w, (ProfileField, RuleField, QSpinBox)):
                out[f.name] = w.value()
            else:
                out[f.name] = _parse(f.kind, w.text())
        return out

    def open_sketch(self, field: ProfileField) -> None:
        """The sketch editor on an inline profile: its sketch, or typed points as lines."""
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


class StepDialog(QDialog):
    """A step's form as a dialog (the model tree's menus)."""

    def __init__(
        self,
        kind: str,
        args: dict | None = None,
        name: str = "",
        features=(),
        parent=None,
        params: dict[str, float] | None = None,
        sketches=(),
    ):
        super().__init__(parent)
        from puzzle.gui.choices import label

        self.kind = kind
        self.setWindowTitle(f"{label(kind)} step")
        self.form = StepForm(kind, args, name, features, params, sketches)
        self.name, self.widgets, self.params = self.form.name, self.form.widgets, self.form.params
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c53030")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.result_args: dict | None = None

    def values(self) -> dict:
        return self.form.values()

    def open_sketch(self, field) -> None:
        self.form.edit_sketch = self.edit_sketch
        self.form.open_sketch(field)

    def edit_sketch(self, spec: dict | None) -> dict | None:
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
