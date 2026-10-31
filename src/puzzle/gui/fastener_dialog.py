"""Beam fasteners in the GUI (ADR 0027).

Add fastener…: two face selections of the component (a hole on each side), the
diameter in the display unit, a material and the ends (rigid or distributing).
Fasteners from holes…: the coaxial holes of different parts, one row each, to tick;
OK makes their face selections and fasteners in one batch (one undo).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from puzzle.model.schema import Geometry

FROM_TYPE = "From its bolt type"
GROUND = "ground"
ENDS = [("Rigid (stiffens the hole, like RBE2)", "rigid"),
        ("Distributing (spreads the load, like RBE3)", "distributing")]  # fmt: skip


def face_selections(project, comp) -> list[str]:
    out = []
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [f"{part.name}/{s.name}" for s in geo.selections if s.entity == "face"]
    return out


MORE = "library:?"  # "More from the library…": the material picker


def _materials(box: QComboBox, project, window=None) -> None:
    """The project's materials, the shipped fastener grades (copied in when chosen), and
    any other library material through the picker (ADR 0050)."""
    from puzzle.gui.bolt_types import _grades

    for text, value in _grades(project):
        box.addItem(text, value)
    box.addItem("More from the library…", MORE)

    def picked(_index) -> None:
        if box.currentData() != MORE:
            return
        key = window.pick_material() if window is not None else None
        box.blockSignals(True)
        if key:
            from puzzle.materials import library_entry

            value = f"library:{key}"
            found = box.findData(value)
            if found < 0:
                box.insertItem(box.count() - 1, f"{library_entry(key)['name']} (from the library)",
                               value)  # fmt: skip
                found = box.findData(value)
            box.setCurrentIndex(found)
        else:
            box.setCurrentIndex(0)
        box.blockSignals(False)

    box.currentIndexChanged.connect(picked)


def material_commands(value: str | None) -> tuple[list, str | None]:
    """A library choice copied into the project first: (commands, the material's id)."""
    from puzzle.commands.model import Create
    from puzzle.materials import library_material

    if value is None or not str(value).startswith("library:"):
        return [], value
    material = library_material(str(value).split(":", 1)[1])
    return [Create(kind="material", data=material.model_dump(mode="json"))], str(material.id)


def _ends(box: QComboBox) -> None:
    for text, value in ENDS:
        box.addItem(text, value)


class AddFastener(QDialog):
    def __init__(self, project, comp, parent=None) -> None:
        from puzzle.settings import length_unit

        super().__init__(parent)
        self.setWindowTitle(f"Add a fastener to {comp.name}")
        self.unit, self.per_metre = length_unit()
        form = QFormLayout()
        self.source, self.target = QComboBox(), QComboBox()
        for box in (self.source, self.target):
            for name in face_selections(project, comp):
                box.addItem(name, name)
        self.target.addItem("Ground (fixed: an anchor into a foundation)", GROUND)
        if self.target.count() > 2:
            self.target.setCurrentIndex(1)
        self.ground_length = QLineEdit()
        self.ground_length.setPlaceholderText("2 × the diameter")
        from puzzle.model.schema import BoltType

        self.btype = QComboBox()  # ADR 0050: a bolt type fills what is left blank
        self.btype.addItem("None: give the size here", None)
        for t in sorted(project.of_type(BoltType), key=lambda t: t.name):
            self.btype.addItem(t.name, str(t.id))
        self.diameter = QLineEdit(f"{0.01 * self.per_metre:g}")
        self.material, self.ends = QComboBox(), QComboBox()
        self.material.addItem(FROM_TYPE, None)
        self.ends.addItem(FROM_TYPE, None)
        _materials(self.material, project, parent)
        _ends(self.ends)
        self.btype.currentIndexChanged.connect(self._typed)
        self.name = QLineEdit(f"Bolt {len(comp.connectors) + 1}")
        from puzzle.model.units import pretty
        from puzzle.settings import current

        self.force_unit = current().display_units()["force"]
        self.pretension = QLineEdit("")
        self.pretension.setPlaceholderText("none")
        form.addRow("Name", self.name)
        form.addRow("Bolt type", self.btype)
        form.addRow("One end (a hole's face)", self.source)
        form.addRow("Other end", self.target)
        form.addRow(f"Anchor length below the hole ({self.unit})", self.ground_length)
        self.target.currentIndexChanged.connect(
            lambda _i: form.setRowVisible(self.ground_length, self.target.currentData() == GROUND)
        )
        form.addRow(f"Diameter ({self.unit})", self.diameter)
        form.addRow("Material", self.material)
        form.addRow("Ends", self.ends)
        form.addRow(f"Pretension ({pretty(self.force_unit)})", self.pretension)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._typed()
        self.form_rows = form
        form.setRowVisible(self.ground_length, False)

    def _typed(self) -> None:
        """With a bolt type the size, grade, ends and preload default to the type's."""
        typed = self.btype.currentData() is not None
        for box in (self.material, self.ends):
            first = box.model().item(0)
            first.setEnabled(typed)
            if typed:
                box.setCurrentIndex(0)
            elif box.currentIndex() == 0 and box.count() > 1:
                box.setCurrentIndex(1)
        if typed:
            self.diameter.clear()
        self.diameter.setPlaceholderText("from the bolt type" if typed else "")
        self.pretension.setPlaceholderText("from the bolt type" if typed else "none")

    def value(self) -> dict:
        from puzzle.model.units import plain
        from puzzle.settings import current

        out = {
            "name": self.name.text().strip() or "Bolt",
            "source": self.source.currentData(),
            "target": self.target.currentData(),
            "type": self.btype.currentData(),
            "diameter": (
                {
                    "value": float(self.diameter.text()),
                    "unit": plain(current().display_units()["length"]),
                }
                if self.diameter.text().strip()
                else None
            ),
            "material": self.material.currentData(),
            "ends": self.ends.currentData(),
            # Applied in the first static step, then locked (ADR 0027).
            "pretension": (
                {"value": float(self.pretension.text()), "unit": plain(self.force_unit)}
                if self.pretension.text().strip()
                else None
            ),
        }
        if out["target"] == GROUND:  # a bolt to ground (ADR 0050)
            out["target"] = None
            out["ground"] = True
            if self.ground_length.text().strip():
                unit = plain(current().display_units()["length"])
                out["ground_length"] = {"value": float(self.ground_length.text()), "unit": unit}
        if out["type"] is None and (out["diameter"] is None or out["material"] is None):
            raise ValueError("without a bolt type, give the diameter and the material")
        return {k: v for k, v in out.items() if v is not None}


class FastenersFromHoles(QDialog):
    def __init__(self, project, comp, root, parent=None) -> None:
        from puzzle.geometry.holes import find_hole_pairs
        from puzzle.settings import length_unit

        super().__init__(parent)
        self.setWindowTitle(f"Fasteners from holes in {comp.name}")
        unit, per_metre = length_unit()
        self.pairs = find_hole_pairs(project, comp, root)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Coaxial holes in different parts: tick the ones to join with a fastener. Each "
            "takes the smaller hole's diameter; nothing is made until OK."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(len(self.pairs), 4)
        self.table.setHorizontalHeaderLabels(["Make", "Parts", "Holes", f"Diameter ({unit})"])
        self.table.verticalHeader().hide()
        for r, p in enumerate(self.pairs):
            make = QTableWidgetItem()
            make.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, make)
            for c, text in enumerate(
                [
                    f"{p.source_part} → {p.target_part}",
                    f"{p.source_face} ↔ {p.target_face}",
                    f"{p.diameter * per_metre:.4g}",
                ],
                start=1,
            ):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        form = QFormLayout()
        self.material, self.ends = QComboBox(), QComboBox()
        _materials(self.material, project, parent)
        _ends(self.ends)
        form.addRow("Material", self.material)
        form.addRow("Ends", self.ends)
        layout.addLayout(form)
        layout.addWidget(
            QLabel(f"{len(self.pairs)} pair{'s' if len(self.pairs) != 1 else ''} of holes found.")
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(720, 360)

    def chosen(self):
        return [
            p
            for r, p in enumerate(self.pairs)
            if self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]


def add_fastener(window, comp):
    from puzzle.commands.model import Edit, EditOp

    project = window.session.project
    if len(face_selections(project, comp)) < 2:
        window.statusBar().showMessage("A fastener needs two face selections (the holes)", 8000)
        return None
    dialog = AddFastener(project, comp, window)
    if not window.run_dialog(dialog):
        return None
    try:
        value = dialog.value()
    except ValueError as exc:
        text = str(exc) if "bolt type" in str(exc) else "Type a number for the diameter"
        window.statusBar().showMessage(text[:1].upper() + text[1:], 8000)
        return None
    first, value["material"] = material_commands(value.get("material"))
    if value["material"] is None:
        del value["material"]
    op = EditOp(op="append", path="connectors", value=value)
    edit = Edit(object=str(comp.id), ops=[op])
    return window.submit_batch([*first, edit]) if first else window.submit(edit)


def fasteners_from_holes(window, comp):
    from puzzle.geometry.holes import fastener_commands

    project = window.session.project
    dialog = FastenersFromHoles(project, comp, window.session.root, window)
    if not window.run_dialog(dialog):
        return None
    chosen = dialog.chosen()
    if not chosen:
        return None
    first, material = material_commands(dialog.material.currentData())
    commands = fastener_commands(project, comp, chosen, material, dialog.ends.currentData())
    return window.submit_batch([*first, *commands])
