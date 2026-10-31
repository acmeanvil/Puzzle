"""Beam sections in the GUI (ADR 0027 beams, ADR 0055 tables and orienting).

Add beam section…: which line part (or edge selection) it covers, the shape (typed
sizes in the display length unit, or a standard section: the AISC or EN table, a
family, a designation, its sizes and properties shown), and how the section's first
axis is set (a rectangle's width and an I's flanges lie along it): a global direction,
a coordinate system's axis, toward a point, along a face's normal or an edge, then
turned by a roll angle about the member.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from puzzle.gui.theme import role
from puzzle.model.schema import Geometry

SHAPES = [
    ("Rectangle (solid)", "rectangle"),
    ("Circle (solid)", "circle"),
    ("Tube (round, hollow)", "tube"),
    ("Box (rectangular, hollow)", "box"),
    ("From the section tables (AISC, EN)", "library"),
]
SIZES = {
    "rectangle": ("width", "height"),
    "circle": ("diameter",),
    "tube": ("diameter", "thickness"),
    "box": ("width", "height", "thickness"),
    "library": (),
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
ORIENTS = [
    ("A global direction", "vector"),
    ("A coordinate system's axis", "axes"),
    ("Toward a point", "toward"),
    ("Along a face's normal", "normal"),
    ("Along an edge", "edge"),
]


def _selections_of(project, comp, entity: str) -> list[str]:
    out = []
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [f"{part.name}/{s.name}" for s in geo.selections if s.entity == entity]
    return out


def mode_words(mode: str) -> str:
    return {"axes": "coordinate system", "toward": "point selection",
            "normal": "face selection", "edge": "edge selection"}[mode]  # fmt: skip


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
        # A standard section (ADR 0055).
        from puzzle import sections

        self.standard, self.family, self.designation = QComboBox(), QComboBox(), QComboBox()
        for key, text in sections.STANDARDS.items():
            self.standard.addItem(text, key)
        self.about = role(QLabel(""), "muted")
        self.about.setWordWrap(True)
        self.form.addRow("Table", self.standard)
        self.form.addRow("Family", self.family)
        self.form.addRow("Section", self.designation)
        self.form.addRow("", self.about)
        self.standard.currentIndexChanged.connect(self._families)
        self.family.currentIndexChanged.connect(self._designations)
        self.designation.currentIndexChanged.connect(self._about)
        self._families()
        # How the first axis is set.
        from puzzle.model.schema import CoordinateSystem

        self.orient, self.system, self.system_axis = QComboBox(), QComboBox(), QComboBox()
        for text, value in ORIENTS:
            self.orient.addItem(text, value)
        for cs in sorted(project.of_type(CoordinateSystem), key=lambda c: c.name):
            self.system.addItem(cs.name, str(cs.id))
        for axis in "xyz":
            self.system_axis.addItem(f"Its {axis} axis", axis)
        self.system_axis.setCurrentIndex(1)
        self.point, self.face, self.edge = QComboBox(), QComboBox(), QComboBox()
        for box, entity in ((self.point, "vertex"), (self.face, "face"), (self.edge, "edge")):
            for name in _selections_of(project, comp, entity):
                box.addItem(name, name)
        self.roll = QLineEdit("0")
        self.form.addRow("Section's first axis", self.orient)
        self.form.addRow("Direction", self.axis)
        self.form.addRow("Coordinate system", self.system)
        self.form.addRow("Axis", self.system_axis)
        self.form.addRow("Point", self.point)
        self.form.addRow("Face", self.face)
        self.form.addRow("Edge", self.edge)
        self.form.addRow("Then turned about the member by (°)", self.roll)
        self.shape.currentIndexChanged.connect(self._show_sizes)
        self.orient.currentIndexChanged.connect(self._show_orient)
        self._show_sizes()
        self._show_orient()
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
        table = self.shape.currentData() == "library"
        for box in (self.standard, self.family, self.designation, self.about):
            self.form.setRowVisible(box, table)

    def _families(self) -> None:
        from puzzle import sections

        self.family.blockSignals(True)
        self.family.clear()
        for f in sections.families(self.standard.currentData()):
            self.family.addItem(f, f)
        self.family.blockSignals(False)
        self._designations()

    def _designations(self) -> None:
        from puzzle import sections

        self.designation.blockSignals(True)
        self.designation.clear()
        for d in sections.designations(self.standard.currentData(), self.family.currentData()):
            self.designation.addItem(d, d)
        self.designation.blockSignals(False)
        self._about()

    def _about(self) -> None:
        from puzzle import sections

        name = self.designation.currentData()
        if not name:
            self.about.setText("")
            return
        sec = sections.get(self.standard.currentData(), name)
        mass = f", {sec.mass:.4g} kg/m" if sec.mass else ""
        note = "" if sec.shape in sections.SUPPORTED else " Single angles aren't taken yet."
        self.about.setText(sec.describe() + f", J {sec.J * 1e8:.4g} cm⁴{mass}.{note}")

    def _show_orient(self) -> None:
        mode = self.orient.currentData()
        shown = {"vector": [self.axis], "axes": [self.system, self.system_axis],
                 "toward": [self.point], "normal": [self.face], "edge": [self.edge]}  # fmt: skip
        for box in (self.axis, self.system, self.system_axis, self.point, self.face, self.edge):
            self.form.setRowVisible(box, box in shown[mode])

    def value(self) -> dict:
        from puzzle.model.units import plain
        from puzzle.settings import current

        unit = plain(current().display_units()["length"])
        shape = self.shape.currentData()
        out = {
            "selection": self.selection.currentData(),
            "shape": shape,
            "orientation": list(self.axis.currentData()),
            "orient": self.orient.currentData(),
            "roll": float(self.roll.text() or 0),
        }
        for key in SIZES[shape]:
            out[key] = {"value": float(self.sizes[key].text()), "unit": unit}
        if shape == "library":
            out["standard"] = self.standard.currentData()
            out["designation"] = self.designation.currentData()
        mode = out["orient"]
        picked = {"axes": self.system, "toward": self.point, "normal": self.face,
                  "edge": self.edge}.get(mode)  # fmt: skip
        if picked is not None and picked.currentData() is None:
            raise ValueError(f"there is nothing to orient by: add a {mode_words(mode)} first")
        if mode == "axes":
            out["axes"] = self.system.currentData()
            out["axes_axis"] = self.system_axis.currentData()
        elif mode == "toward":
            out["toward"] = self.point.currentData()
        elif mode in ("normal", "edge"):
            out["reference"] = (self.face if mode == "normal" else self.edge).currentData()
        return out


def add_beam_section(window, comp):
    from puzzle.commands.model import Edit, EditOp

    dialog = AddBeamSection(window.session.project, comp, window)
    if not window.run_dialog(dialog):
        return None
    try:
        value = dialog.value()
    except ValueError as exc:
        words = str(exc) if "orient" in str(exc) else "Type a number for each size"
        window.statusBar().showMessage(words[:1].upper() + words[1:], 8000)
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
