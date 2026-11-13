"""Find contacts… (ADR 0039): the parts that touch, for review before anything is made.

One row per pair of parts that touch: the faces on each side, the gap and overlap in
the display units, a type to make (bonded by default; a type no solver of the
component's studies can write is greyed, its reason in the tooltip), the friction for a
frictional one, and for two coaxial cylinders of near-equal radius a Thread tick with the
likely size (ADR 0076). Choosing a row shows both sides' faces in the view. OK makes the
ticked rows' face selections and contacts in one batch, so one undo removes them all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from puzzle.geometry.contacts import Proposal, body_labels, find_contacts, proposals

# (readable name, type, the solver capability key it needs)
TYPES = [
    ("Bonded", "bonded", "bonded_conformal"),
    ("Frictionless", "frictionless", "frictionless"),
    ("Frictional", "frictional", "frictional"),
]
COLUMNS = ["Make", "Parts", "Faces", "Gap", "Overlap", "Type", "Friction", "Thread"]
THREAD = COLUMNS.index("Thread")  # ADR 0076: coaxial cylinders offered as a thread


def writers(project, comp) -> dict[str, list[str]]:
    """Capability key -> readable names of the component's study solvers that write it;
    every key is open when no study uses the component yet."""
    from puzzle.adapters import available_adapters
    from puzzle.gui.choices import solver_label
    from puzzle.model.schema import Study

    adapters = available_adapters()
    solvers = sorted({s.solver for s in project.of_type(Study) if s.component == comp.id})
    out: dict[str, list[str]] = {}
    for _name, _type, key in TYPES:
        out[key] = [
            solver_label(s)
            for s in solvers
            if s in adapters and key in adapters[s].capabilities.contacts
        ]
    return out if solvers else {key: ["any"] for _n, _t, key in TYPES}


class ContactSearch(QDialog):
    def __init__(self, project, comp, root, parent=None, show_faces=None) -> None:
        from puzzle.settings import area_unit, length_unit

        super().__init__(parent)
        self.setWindowTitle(f"Find contacts in {comp.name}")
        self.project, self.comp, self.root = project, comp, root
        self.show_faces = show_faces  # (list of (part, face name)) -> None, or None
        self.length, self.per_metre = length_unit()
        self.area, self.per_square_metre = area_unit()
        self.can_write = writers(project, comp)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Faces of different parts, or of different bodies of a part, that touch or "
            "nearly. Tick the contacts to make; "
            "nothing is made until OK, and one undo removes them all."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel(f"Gap up to ({self.length})"))
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setDecimals(4)
        self.tolerance.setRange(0.0, 1e9)
        self.tolerance.setSpecialValueText("0.5 % of the model")
        row.addWidget(self.tolerance)
        row.addWidget(QLabel("Angle (degrees)"))
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0.0, 90.0)
        self.angle.setValue(10.0)
        row.addWidget(self.angle)
        again = QPushButton("Search again")
        again.clicked.connect(self.search)
        row.addWidget(again)
        row.addStretch(1)
        layout.addLayout(row)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(
            [f"Gap ({self.length})" if c == "Gap" else c for c in COLUMNS]
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        layout.addWidget(self.table)
        self.note = QLabel("")
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(900, 420)
        self.found: list[Proposal] = []
        self.search()

    def search(self) -> None:
        tolerance = self.tolerance.value() / self.per_metre or None
        pairs = find_contacts(
            self.project, self.comp, self.root, tolerance=tolerance, angle=self.angle.value()
        )
        self.found = proposals(pairs, body_labels(self.project, self.comp))
        self.table.setRowCount(len(self.found))
        for r, prop in enumerate(self.found):
            make = QTableWidgetItem()
            make.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, make)
            self._text(r, 1, prop.name)
            faces = f"{', '.join(prop.source_faces)}  ↔  {', '.join(prop.target_faces)}"
            self._text(r, 2, faces)
            self._text(r, 3, f"{prop.distance * self.per_metre:.4g}")
            self._text(r, 4, f"{prop.area * self.per_square_metre:.4g} {self.area}")
            self.table.setCellWidget(r, 5, self._types())
            friction = QDoubleSpinBox()
            friction.setRange(0.0, 5.0)
            friction.setSingleStep(0.05)
            friction.setValue(0.2)
            self.table.setCellWidget(r, 6, friction)
            self._thread(r, prop)
        self.table.resizeColumnsToContents()
        if not self.found:
            self.note.setText("No parts or bodies touch within the gap and angle given.")
            return
        text = f"{len(self.found)} pair{'s' if len(self.found) != 1 else ''} touch."
        if any(p.within_part and self._shared(p) for p in self.found):
            text += (" Bodies of one part are already joined where they touch (its "
                     "geometry's Share faces): Bonded records that; Frictionless or "
                     "Frictional meshes the two bodies apart there.")  # fmt: skip
        if any(p.thread for p in self.found):
            text += (" Coaxial cylinders of near-equal radius may be a thread (a tapped "
                     "hole, a screw in a nut): tick Thread to make the contact act along "
                     "its flanks, giving each thread's load (bonded, no separation or "
                     "frictionless).")  # fmt: skip
        self.note.setText(text)

    def _thread(self, row: int, prop: Proposal) -> None:
        """The Thread cell: a tick box with the likely size, for coaxial cylinders."""
        item = QTableWidgetItem("")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if prop.thread and prop.thread.get("size"):
            item.setText(prop.thread["size"])
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(f"Coaxial cylinders: a {prop.thread['size']} thread? (likely "
                            "size from the inner cylinder; change it in the contact's "
                            "Properties)")  # fmt: skip
        elif prop.thread:
            item.setText("Coaxial")
            item.setToolTip("Coaxial cylinders of no standard size: make the contact, then "
                            "Make it a thread… on it")  # fmt: skip
        self.table.setItem(row, THREAD, item)

    def _shared(self, prop: Proposal) -> bool:
        from puzzle.model.schema import Geometry

        part = next(p for p in self.comp.parts if p.name == prop.source_part)
        return self.project.get(part.geometry, Geometry).share_faces

    def _text(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, column, item)

    def _types(self) -> QComboBox:
        box = QComboBox()
        for name, value, key in TYPES:
            box.addItem(name, value)
            item = (
                box.model().item(box.count() - 1)
                if isinstance(box.model(), QStandardItemModel)
                else None
            )
            who = self.can_write.get(key, [])
            if item is not None:
                item.setToolTip(
                    f"Written by {', '.join(who)}"
                    if who
                    else "None of this component's solvers write it yet"
                )
                item.setEnabled(bool(who))
        return box

    def _show(self, row: int) -> None:
        if self.show_faces is None or not 0 <= row < len(self.found):
            return
        prop = self.found[row]
        self.show_faces(
            [(prop.source_part, f) for f in prop.source_faces]
            + [(prop.target_part, f) for f in prop.target_faces]
        )

    def accepted_proposals(self) -> list[Proposal]:
        """The ticked rows, with the type and friction chosen."""
        out = []
        for r, prop in enumerate(self.found):
            if self.table.item(r, 0).checkState() != Qt.CheckState.Checked:
                continue
            prop.type = self.table.cellWidget(r, 5).currentData()
            prop.friction = (
                self.table.cellWidget(r, 6).value() if prop.type == "frictional" else None
            )
            thread = self.table.item(r, THREAD)
            prop.use_thread = bool(prop.thread and prop.thread.get("size")) and (
                thread is not None and thread.checkState() == Qt.CheckState.Checked
            )
            out.append(prop)
        return out


def find_contacts_dialog(window, comp):
    """Component → Find contacts…: search, review, then one batch for the ticked rows."""
    from puzzle.geometry.contacts import contact_commands

    if not comp.parts:
        window.statusBar().showMessage("Contacts are between parts or bodies: add a part", 8000)
        return None

    def show_faces(faces):
        window.show_faces(comp, faces)

    dialog = ContactSearch(
        window.session.project, comp, window.session.root, window, show_faces=show_faces
    )
    if not window.run_dialog(dialog):
        return None
    chosen = dialog.accepted_proposals()
    if not chosen:
        return None
    return window.submit_batch(contact_commands(window.session.project, comp, chosen))


def add_contact(window, comp):
    """Model → Add contact…: one contact between two face selections, asked in one form
    (the search above proposes them; this is for one the search can't see, or a gap)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.fastener_dialog import face_selections
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Contact

    faces = face_selections(window.session.project, comp)
    if len(faces) < 2:
        window.statusBar().showMessage(
            "A contact is between two face selections: make them first (Pick, Faces), "
            "or use Find contacts…", 10000)  # fmt: skip
        return None
    can = writers(window.session.project, comp)
    kinds = [(name if can.get(key) else f"{name} (no solver here writes it)", value)
             for name, value, key in TYPES]  # fmt: skip
    pairs = [(f, f) for f in faces]
    taken = {c.name for c in comp.contacts}

    def check(v):
        if v.get("source") == v.get("target"):
            return "Choose two different selections"
        if not v.get("name", "").strip():
            return "Give it a name"
        if v["name"].strip() in taken:
            return f"Another contact is already called '{v['name'].strip()}'"
        if v.get("type") == "frictional":
            try:
                if float(v.get("friction", "")) < 0:
                    raise ValueError
            except ValueError:
                return "Type the friction coefficient (0.2, say)"
        return None

    got = window.ask_form("Add contact", [
        Ask("source", "Source faces", choices=pairs,
            help="The contact (slave) side: usually the smaller or softer face"),
        Ask("target", "Target faces", choices=pairs, default=faces[1],
            help="The target (master) side"),
        Ask("type", "Type", choices=kinds),
        Ask("friction", "Friction coefficient", kind="text", default="0.2",
            shown=lambda v: v.get("type") == "frictional"),
        Ask("name", "Name", kind="text",
            follow=lambda v: f"{v.get('source', '').split('/')[-1]} to "
                             f"{v.get('target', '').split('/')[-1]}"),
    ], intro="Two face selections that touch, or nearly, of different parts or of "
             "different bodies of one part.", check=check)  # fmt: skip
    if got is None:
        return None
    contact = Contact(
        name=got["name"].strip(),
        type=got["type"],
        source=got["source"],
        target=got["target"],
        friction=float(got["friction"]) if got["type"] == "frictional" else None,
    )
    op = EditOp(op="append", path="contacts", value=contact.model_dump(mode="json"))
    return window.submit(Edit(object=str(comp.id), ops=[op]))
