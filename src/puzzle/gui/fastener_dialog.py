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

from puzzle.model.schema import Geometry, Material

ENDS = [("Rigid (stiffens the hole, like RBE2)", "rigid"),
        ("Distributing (spreads the load, like RBE3)", "distributing")]  # fmt: skip


def face_selections(project, comp) -> list[str]:
    out = []
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [f"{part.name}/{s.name}" for s in geo.selections if s.entity == "face"]
    return out


def _materials(box: QComboBox, project) -> None:
    for m in sorted(project.of_type(Material), key=lambda m: m.name):
        box.addItem(m.name, str(m.id))


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
        if self.target.count() > 1:
            self.target.setCurrentIndex(1)
        self.diameter = QLineEdit(f"{0.01 * self.per_metre:g}")
        self.material, self.ends = QComboBox(), QComboBox()
        _materials(self.material, project)
        _ends(self.ends)
        self.name = QLineEdit(f"Bolt {len(comp.connectors) + 1}")
        from puzzle.model.units import pretty
        from puzzle.settings import current

        self.force_unit = current().display_units()["force"]
        self.pretension = QLineEdit("")
        self.pretension.setPlaceholderText("none")
        form.addRow("Name", self.name)
        form.addRow("One end (a hole's face)", self.source)
        form.addRow("Other end", self.target)
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

    def value(self) -> dict:
        from puzzle.model.units import plain
        from puzzle.settings import current

        return {
            "name": self.name.text().strip() or "Bolt",
            "source": self.source.currentData(),
            "target": self.target.currentData(),
            "diameter": {
                "value": float(self.diameter.text()),
                "unit": plain(current().display_units()["length"]),
            },
            "material": self.material.currentData(),
            "ends": self.ends.currentData(),
            # Applied in the first static step, then locked (ADR 0027).
            "pretension": (
                {"value": float(self.pretension.text()), "unit": plain(self.force_unit)}
                if self.pretension.text().strip()
                else None
            ),
        }


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
        _materials(self.material, project)
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
    if len(face_selections(project, comp)) < 2 or not project.of_type(Material):
        window.statusBar().showMessage(
            "A fastener needs two face selections (the holes) and a material", 8000
        )
        return None
    dialog = AddFastener(project, comp, window)
    if not window.run_dialog(dialog):
        return None
    try:
        value = dialog.value()
    except ValueError:
        window.statusBar().showMessage("Type a number for the diameter", 8000)
        return None
    op = EditOp(op="append", path="connectors", value=value)
    return window.submit(Edit(object=str(comp.id), ops=[op]))


def fasteners_from_holes(window, comp):
    from puzzle.geometry.holes import fastener_commands

    project = window.session.project
    if not project.of_type(Material):
        window.statusBar().showMessage("Add a material for the fasteners first", 8000)
        return None
    dialog = FastenersFromHoles(project, comp, window.session.root, window)
    if not window.run_dialog(dialog):
        return None
    chosen = dialog.chosen()
    if not chosen:
        return None
    commands = fastener_commands(
        project, comp, chosen, dialog.material.currentData(), dialog.ends.currentData()
    )
    return window.submit_batch(commands)
