"""Beam sections in the GUI (ADR 0027 beams).

Add beam section…: which line part (or edge selection) it covers, the shape, its sizes
in the display length unit (only the ones the shape takes), and the direction of the
section's first axis (a rectangle's width lies along it).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from puzzle.model.schema import Geometry

SHAPES = [
    ("Rectangle (solid)", "rectangle"),
    ("Circle (solid)", "circle"),
    ("Tube (round, hollow)", "tube"),
    ("Box (rectangular, hollow)", "box"),
]
SIZES = {
    "rectangle": ("width", "height"),
    "circle": ("diameter",),
    "tube": ("diameter", "thickness"),
    "box": ("width", "height", "thickness"),
}
SIZE_LABELS = {
    "width": "Width (along the first axis)",
    "height": "Height (across it)",
    "diameter": "Outside diameter",
    "thickness": "Wall thickness",
}
AXES = [
    ("Global Y", (0.0, 1.0, 0.0)),
    ("Global Z", (0.0, 0.0, 1.0)),
    ("Global X", (1.0, 0.0, 0.0)),
]


def section_targets(project, comp) -> list[str]:
    """The parts, then their edge selections: what a section can cover."""
    out = [p.name for p in comp.parts]
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [f"{part.name}/{s.name}" for s in geo.selections if s.entity == "edge"]
    return out


class AddBeamSection(QDialog):
    def __init__(self, project, comp, parent=None) -> None:
        from puzzle.settings import length_unit

        super().__init__(parent)
        self.setWindowTitle(f"Add a beam section to {comp.name}")
        self.unit, self.per_metre = length_unit()
        self.form = QFormLayout()
        self.selection, self.shape, self.axis = QComboBox(), QComboBox(), QComboBox()
        for name in section_targets(project, comp):
            self.selection.addItem(name, name)
        for text, value in SHAPES:
            self.shape.addItem(text, value)
        for text, value in AXES:
            self.axis.addItem(text, value)
        defaults = {"width": 0.02, "height": 0.04, "diameter": 0.03, "thickness": 0.003}
        self.sizes = {k: QLineEdit(f"{v * self.per_metre:g}") for k, v in defaults.items()}
        self.form.addRow("Beams", self.selection)
        self.form.addRow("Shape", self.shape)
        for key, box in self.sizes.items():
            self.form.addRow(f"{SIZE_LABELS[key]} ({self.unit})", box)
        self.form.addRow("Section's first axis", self.axis)
        self.shape.currentIndexChanged.connect(self._show_sizes)
        self._show_sizes()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(buttons)

    def _show_sizes(self) -> None:
        wanted = SIZES[self.shape.currentData()]
        for key, box in self.sizes.items():
            self.form.setRowVisible(box, key in wanted)

    def value(self) -> dict:
        from puzzle.model.units import plain
        from puzzle.settings import current

        unit = plain(current().display_units()["length"])
        shape = self.shape.currentData()
        out = {
            "selection": self.selection.currentData(),
            "shape": shape,
            "orientation": list(self.axis.currentData()),
        }
        for key in SIZES[shape]:
            out[key] = {"value": float(self.sizes[key].text()), "unit": unit}
        return out


def add_beam_section(window, comp):
    from puzzle.commands.model import Edit, EditOp

    dialog = AddBeamSection(window.session.project, comp, window)
    if not window.run_dialog(dialog):
        return None
    try:
        value = dialog.value()
    except ValueError:
        window.statusBar().showMessage("Type a number for each size", 8000)
        return None
    op = EditOp(op="append", path="beam_sections", value=value)
    return window.submit(Edit(object=str(comp.id), ops=[op]))


# --- shells (ADR 0027) -------------------------------------------------------------------

OFFSETS = [
    ("Centred on the sheet", 0.0),
    ("The sheet is the shell's bottom face", 0.5),
    ("The sheet is the shell's top face", -0.5),
]


def shell_targets(project, comp) -> list[str]:
    """The parts, then their face selections: what a thickness can cover."""
    out = [p.name for p in comp.parts]
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [f"{part.name}/{s.name}" for s in geo.selections if s.entity == "face"]
    return out


def add_shell_thickness(window, comp):
    """A sheet body's thickness and where the sheet sits in it, in one form."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.quick_form import Ask
    from puzzle.model.units import plain
    from puzzle.settings import current, length_unit

    unit, per_metre = length_unit()

    def thickness(text: str) -> float | None:
        try:
            value = float(text)
        except ValueError:
            return None
        return value if value > 0 else None

    got = window.ask_form(
        f"Add a shell thickness to {comp.name}",
        [
            Ask(
                "selection",
                "Sheets",
                choices=[(t, t) for t in shell_targets(window.session.project, comp)],
            ),
            Ask("thickness", f"Thickness ({unit})", kind="text", default=f"{0.005 * per_metre:g}"),
            Ask(
                "offset",
                "Where the sheet is",
                choices=OFFSETS,
                help="Normally centred; a sheet drawn on a part's face sits on one side",
            ),
        ],  # fmt: skip
        intro="A sheet body (a surface) solved as a shell of this thickness.",
        check=lambda v: (
            None if thickness(v["thickness"]) is not None else "Type a thickness greater than zero"
        ),
    )
    if got is None:
        return None
    value = {
        "selection": got["selection"],
        "thickness": {
            "value": thickness(got["thickness"]),
            "unit": plain(current().display_units()["length"]),
        },
        "offset": got["offset"],
    }
    op = EditOp(op="append", path="shell_sections", value=value)
    return window.submit(Edit(object=str(comp.id), ops=[op]))
