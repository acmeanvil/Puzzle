"""Beam sections in the GUI (ADR 0027 beams, ADR 0055 tables and orienting).

Add beam section…: which line part (or edge selection) it covers, the shape (typed
sizes in the display length unit, or a standard section: the AISC or EN table, a
family, a designation, its sizes and properties shown), and how the section's first
axis is set (a rectangle's width and an I's flanges lie along it): a global direction,
a coordinate system's axis, toward a point, along a face's normal or an edge, then
turned by a roll angle about the member. The face or edge may be picked in the view:
Pick in the view closes the form, the next double-click on the model chooses the
entity, and the form comes back with it (a selection of it is made with the section,
in one command, unless one already holds just that entity). An existing section is
oriented by a pick from its menu (`orient_by_pick`). The form draws the section's
outline (ADR 0055 decision 5).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
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


PICK = 2  # the form's result when it closes to pick in the view
PICK_WORDS = {"face": "a face", "edge": "an edge"}


class OutlineView(QWidget):
    """A section's outline (`post.beam_shapes.outline`) drawn to scale, its first axis
    across and its second up, in the theme's colours."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.regions: list = []
        self.setMinimumHeight(120)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(220, 140)

    def set_section(self, section) -> None:
        """A `BeamSection` (or None): its outline, or nothing when it can't be drawn."""
        from puzzle.post.beam_shapes import outline

        try:
            self.regions = outline(section) if section is not None else []
        except (KeyError, ValueError, AttributeError):
            self.regions = []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        import numpy as np

        if not self.regions:
            return
        loops = [loop for region in self.regions for loop in region if loop is not None]
        pts = np.concatenate(loops)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        span = float(max(hi - lo)) or 1.0
        margin = 16
        scale = min(self.width() - 2 * margin, self.height() - 2 * margin) / span
        centre = (lo + hi) / 2

        def at(p):
            return QPointF(self.width() / 2 + (p[0] - centre[0]) * scale,
                           self.height() / 2 - (p[1] - centre[1]) * scale)  # fmt: skip

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for loop in loops:
            path.moveTo(at(loop[0]))
            for p in loop[1:]:
                path.lineTo(at(p))
            path.closeSubpath()
        fill = palette.color(QPalette.ColorRole.Highlight)
        fill.setAlpha(90)
        painter.fillPath(path, fill)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.WindowText), 1.2))
        painter.drawPath(path)
        # The section's axes through its centroid: 1 across, 2 up.
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
        o = at((0.0, 0.0))
        painter.drawLine(QPointF(margin / 2, o.y()), QPointF(self.width() - margin / 2, o.y()))
        painter.drawLine(QPointF(o.x(), margin / 2), QPointF(o.x(), self.height() - margin / 2))
        painter.drawText(QPointF(self.width() - margin, o.y() - 4), "1")
        painter.drawText(QPointF(o.x() + 4, margin), "2")
        painter.end()


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
        self.designation.currentIndexChanged.connect(lambda _: self._draw_outline())
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
        self.pick_face = QPushButton("Pick a face in the view")
        self.pick_face.setToolTip("Close this form, double-click a face of the model, and "
                                  "the form comes back with it chosen")  # fmt: skip
        self.form.addRow("", self.pick_face)
        self.form.addRow("Edge", self.edge)
        self.pick_edge = QPushButton("Pick an edge in the view")
        self.pick_edge.setToolTip("Close this form, double-click an edge of the model, and "
                                  "the form comes back with it chosen")  # fmt: skip
        self.form.addRow("", self.pick_edge)
        self.picking: str | None = None  # "face" or "edge" when closed to pick
        self.new_selections: dict[str, dict] = {}  # "Part/Name" -> the selection to make
        self.pick_face.clicked.connect(lambda: self._pick("face"))
        self.pick_edge.clicked.connect(lambda: self._pick("edge"))
        self.form.addRow("Then turned about the member by (°)", self.roll)
        self.outline = OutlineView()
        self.form.addRow("Outline", self.outline)
        self.shape.currentIndexChanged.connect(self._show_sizes)
        for box in self.sizes.values():
            box.editingFinished.connect(self._draw_outline)
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
        self._draw_outline()

    def _draw_outline(self) -> None:
        if not hasattr(self, "outline"):
            return  # still being built
        from puzzle.model.schema import BeamSection

        try:
            value = self.value(check=False)
            value.pop("selection", None)
            section = BeamSection(selection="-", **value)
        except (ValueError, TypeError):
            section = None
        self.outline.set_section(section)

    def _pick(self, kind: str) -> None:
        """Close to pick `kind` in the view; `add_beam_section` brings the form back."""
        self.picking = kind
        self.done(PICK)

    def take_pick(self, kind: str, name: str, selection: dict | None) -> None:
        """A picked entity's selection `name` ("Part/Name") chosen for `kind`, and the
        selection to make with the section when it is new."""
        box = self.face if kind == "face" else self.edge
        if selection is not None:
            self.new_selections[name] = selection
        if box.findData(name) < 0:
            words = f"{name} (picked)" if selection is not None else name
            box.addItem(words, name)
        box.setCurrentIndex(box.findData(name))
        self.orient.setCurrentIndex(self.orient.findData("normal" if kind == "face" else "edge"))

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
        note = (
            " Its first axis runs along the shorter leg; Mirror (in Properties) puts the "
            "other leg on the other side."
            if sec.shape == "L"
            else ""
        )
        self.about.setText(sec.describe() + f", J {sec.J * 1e8:.4g} cm⁴{mass}.{note}")

    def _show_orient(self) -> None:
        mode = self.orient.currentData()
        shown = {"vector": [self.axis], "axes": [self.system, self.system_axis],
                 "toward": [self.point], "normal": [self.face], "edge": [self.edge]}  # fmt: skip
        for box in (self.axis, self.system, self.system_axis, self.point, self.face, self.edge):
            self.form.setRowVisible(box, box in shown[mode])
        self.form.setRowVisible(self.pick_face, mode == "normal")
        self.form.setRowVisible(self.pick_edge, mode == "edge")

    def value(self, check: bool = True) -> dict:
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
        if check and picked is not None and picked.currentData() is None:
            raise ValueError(f"there is nothing to orient by: add a {mode_words(mode)} first")
        if mode == "axes":
            out["axes"] = self.system.currentData()
            out["axes_axis"] = self.system_axis.currentData()
        elif mode == "toward":
            out["toward"] = self.point.currentData()
        elif mode in ("normal", "edge"):
            out["reference"] = (self.face if mode == "normal" else self.edge).currentData()
        return out


def add_beam_section(window, comp, dialog: AddBeamSection | None = None):
    """The form, then one command: the section, with the selection a pick made."""
    from puzzle.commands.model import Edit, EditOp

    if dialog is None:
        dialog = AddBeamSection(window.session.project, comp, window)
    dialog.picking = None
    accepted = window.run_dialog(dialog)
    if dialog.picking is not None:  # back with the picked entity (or as it was)
        kind = dialog.picking

        def got(name, selection):
            if name is not None:
                dialog.take_pick(kind, name, selection)
            add_beam_section(window, comp, dialog)

        pick_entity(window, comp, kind, got)
        return None
    if not accepted:
        return None
    try:
        value = dialog.value()
    except ValueError as exc:
        words = str(exc) if "orient" in str(exc) else "Type a number for each size"
        window.statusBar().showMessage(words[:1].upper() + words[1:], 8000)
        return None
    op = EditOp(op="append", path="beam_sections", value=value)
    edit = Edit(object=str(comp.id), ops=[op])
    made = selection_commands(window, comp, dialog.new_selections, value.get("reference"))
    return window.submit_batch([*made, edit]) if made else window.submit(edit)


# --- orienting by a pick in the view (ADR 0055) -------------------------------------------


def pick_entity(window, comp, kind: str, done) -> None:
    """The next double-click on the model picks a face or an edge (`kind`) of `comp`;
    `done(name, selection)` gets its selection's name ("Part/Name"): one that holds
    just that entity, else a new one to make (`selection`: its value, None when it
    exists). `done(None, None)` when nothing was hit."""
    from puzzle.gui.tree import Node

    cid = str(comp.id)
    if window.mesh_component != cid:  # its model in the view, to pick on
        window.tree.select(Node("object", cid, "", comp.name))
        window._selected()
    view = window.viewport

    def captured(xyz) -> None:
        if xyz is None:
            done(None, None)
            return
        entity = view.entity_at(xyz, kind)
        named = window.mesh_names.get(cid, {}).get(entity) if entity is not None else None
        if named is None:
            window.statusBar().showMessage(f"No {kind} there; pick again from the form", 8000)
            done(None, None)
            return
        done(*picked_selection(window, comp, kind, *named))

    view.capture_point(captured, snap="none")
    window.statusBar().showMessage(
        f"Double-click {PICK_WORDS[kind]} of the model to orient the beams by", 20000
    )


def picked_selection(window, comp, kind: str, part_name: str, member: str):
    """("Part/Name", the new selection's value or None): an existing selection of the
    part's geometry that holds just this entity, else a new one named for its use."""
    project = window.session.project
    part = next(p for p in comp.parts if p.name == part_name)
    geo = project.objects.get(part.geometry)
    for sel in geo.selections:
        if sel.entity == kind and list(sel.members) == [member]:
            return f"{part_name}/{sel.name}", None
    taken = {s.name for s in geo.selections}
    base = "Beam orientation face" if kind == "face" else "Beam orientation edge"
    names = (f"{base} {k}" if k > 1 else base for k in range(1, 1000))
    name = next(n for n in names if n not in taken)
    anchors = window.mesh_anchors.get(str(comp.id), {})
    anchor = anchors.get((part_name, kind, member))
    value = {"name": name, "entity": kind, "members": [member],
             "anchors": {member: anchor} if anchor is not None else {}}  # fmt: skip
    return f"{part_name}/{name}", value


def selection_commands(window, comp, new: dict, used: str | None) -> list:
    """The command making the picked selection the section uses, if it is new."""
    from puzzle.commands.model import Edit, EditOp

    if not used or used not in new:
        return []
    part_name = used.partition("/")[0]
    part = next(p for p in comp.parts if p.name == part_name)
    return [Edit(object=str(part.geometry),
                 ops=[EditOp(op="append", path="selections", value=new[used])])]  # fmt: skip


def orient_by_pick(window, comp, index: int, kind: str) -> None:
    """An existing beam section oriented by a face's normal or along an edge picked in
    the view: one command (with the selection, when it is new)."""
    from puzzle.commands.model import Edit, EditOp

    def got(name, selection):
        if name is None:
            return
        current = window.session.project.objects.get(comp.id, comp)
        ops = [EditOp(op="set", path=f"beam_sections.{index}.orient",
                      value="normal" if kind == "face" else "edge"),
               EditOp(op="set", path=f"beam_sections.{index}.reference", value=name)]  # fmt: skip
        edit = Edit(object=str(current.id), ops=ops)
        made = selection_commands(window, current, {name: selection} if selection else {}, name)
        out = window.submit_batch([*made, edit]) if made else window.submit(edit)
        if out is not None:
            what = "normal of" if kind == "face" else "direction of"
            window.statusBar().showMessage(f"Its first axis now follows the {what} {name}", 8000)

    pick_entity(window, comp, kind, got)


def orient_from_menu(window, comp) -> None:
    """Model → Orient a beam section by a pick…: which section and by what, in one
    form (the section chosen in the tree first), then the pick."""
    from puzzle.gui.quick_form import Ask

    if not comp.beam_sections:
        window.statusBar().showMessage(f"{comp.name} has no beam section yet", 8000)
        return
    node = window.tree.current()
    here = None
    if node is not None and node.kind == "beam_section" and node.object == str(comp.id):
        here = int(node.path.split(".")[1])
    sections = [(f"Beam section on {b.selection}: {b.describe()}", i)
                for i, b in enumerate(comp.beam_sections)]  # fmt: skip
    if here is not None:
        sections.insert(0, sections.pop(here))
    got = window.ask_form(
        "Orient a beam section by a pick",
        [
            Ask("section", "Beam section", choices=sections),
            Ask(
                "kind",
                "Its first axis",
                choices=[("Along a picked face's normal", "face"), ("Along a picked edge", "edge")],
            ),
        ],  # fmt: skip
        intro="Then double-click the face or edge in the view.",
    )
    if got is not None:
        orient_by_pick(window, comp, got["section"], got["kind"])


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
