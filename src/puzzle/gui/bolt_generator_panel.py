"""Generate bolts… (ADR 0050): a panel beside the view that proposes bolts for a
component's holes, previewed as tubes in the view and listed with a tick each; Make
creates the ticked ones, with their face selections, in one batch (one undo).

Anchors come from a rule (hole diameters, parts, an axis, lengths; kept on the
component so it can be run again, when it also lists the bolts it made whose holes are
gone), from two face selections (each hole of one paired with the coaxial hole of the
other), or from the faces picked in the view. Each bolt gets a type (chosen, or the
largest that fits its hole), a name by a pattern and a group.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role

MODES = [("A rule over the model's holes", "rule"), ("Two face selections", "sets"),
         ("The faces picked in the view", "picks")]  # fmt: skip
AXES = [("Any direction", None), ("Along X", (1.0, 0.0, 0.0)), ("Along Y", (0.0, 1.0, 0.0)),
        ("Along Z", (0.0, 0.0, 1.0))]  # fmt: skip


class GenerateBolts(QDialog):
    def __init__(self, window, comp) -> None:
        from puzzle.gui.fastener_dialog import ENDS, _materials, face_selections
        from puzzle.model.schema import BoltType
        from puzzle.settings import length_unit

        super().__init__(window)
        self.window, self.comp = window, comp
        self.setWindowTitle(f"Generate bolts in {comp.name}")
        self.setModal(False)
        self.unit, self.per_metre = length_unit()
        self.planned = None
        project = window.session.project
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = QComboBox()
        for text, value in MODES:
            self.mode.addItem(text, value)
        form.addRow("Bolts from", self.mode)
        layout.addLayout(form)

        self.pages = QStackedWidget()
        # A rule.
        rule = QWidget()
        rf = QFormLayout(rule)
        self.rule_name = QLineEdit(self._next_rule_name())
        self.min_d, self.max_d = QLineEdit(), QLineEdit()
        for box in (self.min_d, self.max_d):
            box.setPlaceholderText("any")
        self.parts = QLineEdit()
        self.parts.setPlaceholderText("every part; or names, comma separated")
        self.axis = QComboBox()
        for text, value in AXES:
            self.axis.addItem(text, value)
        self.min_l, self.max_l = QLineEdit(), QLineEdit()
        for box in (self.min_l, self.max_l):
            box.setPlaceholderText("any")
        rf.addRow("Rule name", self.rule_name)
        rf.addRow(f"Hole diameter from ({self.unit})", self.min_d)
        rf.addRow(f"to ({self.unit})", self.max_d)
        rf.addRow("Parts", self.parts)
        rf.addRow("Axis", self.axis)
        rf.addRow(f"Length from ({self.unit})", self.min_l)
        rf.addRow(f"to ({self.unit})", self.max_l)
        self.same_part = QCheckBox("Also join two holes of one part (a split clamp)")
        self.to_ground = QCheckBox("A hole with no partner: a bolt to ground (an anchor)")
        self.ground_length = QLineEdit()
        self.ground_length.setPlaceholderText("2 × the diameter")
        self.rings = QCheckBox("Imprint the bearing rings under heads and nuts in the parts")
        self.rings.setToolTip(
            "Exact faces for the bolts to bear on, and a mesh that follows "
            "them (bolt types with a bolt-and-nut or cap-screw joint)"
        )
        rf.addRow("", self.rings)
        rf.addRow("", self.same_part)
        rf.addRow("", self.to_ground)
        rf.addRow(f"Anchor length below the hole ({self.unit})", self.ground_length)
        self.pages.addWidget(rule)
        # Two sets.
        sets = QWidget()
        sf = QFormLayout(sets)
        self.first, self.second = QComboBox(), QComboBox()
        for box in (self.first, self.second):
            for name in face_selections(project, comp):
                box.addItem(name, name)
        if self.second.count() > 1:
            self.second.setCurrentIndex(1)
        sf.addRow("Holes of", self.first)
        sf.addRow("paired with holes of", self.second)
        self.pages.addWidget(sets)
        # Picks.
        picks = QWidget()
        pf = QVBoxLayout(picks)
        self.pick_note = role(QLabel(""), "muted")
        self.pick_note.setWordWrap(True)
        pf.addWidget(self.pick_note)
        self.pages.addWidget(picks)
        layout.addWidget(self.pages)
        self.mode.currentIndexChanged.connect(self._mode)

        common = QFormLayout()
        self.btype = QComboBox()
        self.btype.addItem("Matched to each hole (the largest type that fits)", None)
        for t in sorted(project.of_type(BoltType), key=lambda t: t.name):
            self.btype.addItem(t.name, str(t.id))
        self.material = QComboBox()
        _materials(self.material, project, window)
        self.ends = QComboBox()
        self.ends.addItem("From the bolt type", None)
        for text, value in ENDS:
            self.ends.addItem(text, value)
        self.names = QLineEdit("Bolt {n}")
        self.group = QLineEdit()
        self.group.setPlaceholderText("the rule's name")
        common.addRow("Bolt type", self.btype)
        common.addRow("Material, where no type fits", self.material)
        common.addRow("Ends", self.ends)
        common.addRow("Names ({n} counts up)", self.names)
        common.addRow("Group", self.group)
        layout.addLayout(common)

        row = QHBoxLayout()
        self.find_button = QPushButton("Find holes")
        self.find_button.clicked.connect(lambda: self.find())
        self.summary = role(QLabel(""), "muted")
        row.addWidget(self.find_button)
        row.addWidget(self.summary, 1)
        layout.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Make", "Name", "Holes", f"Hole ({self.unit})",
                                              "Type", f"Length ({self.unit})"])  # fmt: skip
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.remove_stale = QCheckBox("Remove the rule's bolts whose holes are gone")
        self.remove_stale.hide()
        layout.addWidget(self.remove_stale)
        buttons = QHBoxLayout()
        self.make_button = QPushButton("Make bolts")
        self.make_button.setEnabled(False)
        self.make_button.clicked.connect(lambda: self.make())
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(self.make_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._mode()
        self.resize(760, 640)

    def _next_rule_name(self) -> str:
        taken = {r.name for r in self.comp.bolt_rules}
        n = 1
        while f"Bolt rule {n}" in taken:
            n += 1
        return f"Bolt rule {n}"

    def _mode(self) -> None:
        self.pages.setCurrentIndex(self.mode.currentIndex())
        picked = self.window.picked_names() if hasattr(self.window, "picked_names") else []
        self.pick_note.setText(
            f"{len(picked)} face{'s' if len(picked) != 1 else ''} picked: Pick in the view's "
            "toolbar, Faces, then double-click the hole walls; they are paired by axis."
        )

    # --- finding -----------------------------------------------------------------------

    def _length(self, box: QLineEdit):
        from puzzle.model.schema import Quantity

        text = box.text().strip()
        if not text:
            return None
        return Quantity(value=float(text), unit=self.unit)

    def rule(self):
        from puzzle.model.schema import BoltRule

        parts = [p.strip() for p in self.parts.text().split(",") if p.strip()]
        return BoltRule(
            name=self.rule_name.text().strip() or self._next_rule_name(),
            min_diameter=self._length(self.min_d),
            max_diameter=self._length(self.max_d),
            parts=parts,
            axis=self.axis.currentData(),
            min_length=self._length(self.min_l),
            max_length=self._length(self.max_l),
            type=self.btype.currentData(),
            names=self.names.text().strip() or "Bolt {n}",
            group=self.group.text().strip() or None,
            same_part=self.same_part.isChecked(),
            bearing_faces=self.rings.isChecked(),
            ground_unpaired=self.to_ground.isChecked(),
            ground_length=self._length(self.ground_length),
        )

    def find(self) -> None:
        from puzzle import bolt_generator as gen
        from puzzle.model.schema import BoltType

        project, root = self.window.session.project, self.window.session.root
        comp = project.objects[self.comp.id]
        mode = self.mode.currentData()
        try:
            rule = self.rule() if mode == "rule" else None
            if mode == "rule":
                pairs = gen.by_rule(project, comp, root, rule)
            elif mode == "sets":
                pairs = gen.by_sets(project, comp, root, self.first.currentData(),
                                    self.second.currentData())  # fmt: skip
            else:
                pairs = gen.by_picks(project, comp, root, self.window.picked_names())
        except ValueError as exc:
            self.summary.setText(str(exc))
            return
        chosen = project.objects.get(self.btype.currentData()) if self.btype.currentData() else None
        if rule is None:  # names and type still come from the form
            from puzzle.model.schema import BoltRule

            rule_like = BoltRule(name="", names=self.names.text().strip() or "Bolt {n}")
            self.planned = gen.plan(project, comp, pairs, None,
                                    chosen if isinstance(chosen, BoltType) else None)  # fmt: skip
            for p, n in zip(self.planned.add, _names(rule_like, comp, len(self.planned.add)),
                            strict=True):  # fmt: skip
                p.name = n
        else:
            self.planned = gen.plan(project, comp, pairs, rule)
        self._rule = rule
        self._fill()

    def _fill(self) -> None:
        planned = self.planned
        self.table.setRowCount(len(planned.add))
        for r, p in enumerate(planned.add):
            make = QTableWidgetItem()
            make.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, make)
            other = (f"{p.pair.target_part}/{p.pair.target_face}" if p.pair.target_part
                     else "ground")  # fmt: skip
            cells = [p.name, f"{p.pair.source_part}/{p.pair.source_face} ↔ {other}",
                     f"{p.pair.diameter * self.per_metre:.4g}", p.type_name or "none: " + p.note,
                     f"{p.length * self.per_metre:.4g}" if p.pair.target_part else "—"]  # fmt: skip
            for c, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        words = [f"{len(planned.add)} new bolt{'s' if len(planned.add) != 1 else ''}"]
        if planned.kept:
            words.append(f"{len(planned.kept)} already there")
        if planned.stale:
            words.append(f"{len(planned.stale)} of the rule's bolts have lost their holes: "
                         + ", ".join(planned.stale))  # fmt: skip
        self.summary.setText("; ".join(words))
        self.remove_stale.setVisible(bool(planned.stale))
        self.make_button.setEnabled(bool(planned.add) or bool(planned.stale))
        self._preview()

    def _preview(self) -> None:
        """The proposed bolts as tubes in the view, between the holes' centres."""
        import numpy as np

        from puzzle.post.symbols import ORANGE, Glyphs

        viewport = getattr(self.window, "viewport", None)
        if viewport is None or self.planned is None:
            return
        from puzzle import bolt_generator as gen

        found, _ = gen.holes(self.window.session.project, self.comp, self.window.session.root)
        by = {(h.part, h.face): h for h in found}
        glyphs = []
        for p in self.planned.add:
            a = by[p.pair.source_part, p.pair.source_face]
            if p.pair.target_part is None:  # toward the ground, 2 d along the hole's axis
                down = a.axis if a.axis @ np.array([0.0, 0.0, -1.0]) >= 0 else -a.axis
                b_centre = a.centre + 2 * p.pair.diameter * down
            else:
                b_centre = by[p.pair.target_part, p.pair.target_face].centre
            points = np.array([a.centre, b_centre])
            glyphs.append(Glyphs(f"Proposed {p.name}", "tube", ORANGE, points,
                                 radius=p.pair.diameter / 2))  # fmt: skip
        viewport.show_symbols(glyphs)

    # --- making ------------------------------------------------------------------------

    def chosen(self):
        from puzzle.bolt_generator import Plan

        ticked = [p for r, p in enumerate(self.planned.add)
                  if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]  # fmt: skip
        return Plan(ticked, self.planned.stale, self.planned.kept)

    def make(self):
        from puzzle import bolt_generator as gen
        from puzzle.gui.fastener_dialog import material_commands

        if self.planned is None:
            return None
        project = self.window.session.project
        comp = project.objects[self.comp.id]
        first, material = material_commands(self.material.currentData())
        try:
            batch = gen.commands(project, comp, self.chosen(), material=material,
                                 ends=self.ends.currentData(), rule=self._rule,
                                 remove_stale=self.remove_stale.isChecked())  # fmt: skip
        except ValueError as exc:
            self.summary.setText(str(exc))
            return None
        if not batch:
            return None
        out = self.window.submit_batch([*first, *batch])
        self.comp = self.window.session.project.objects[self.comp.id]
        self.find()
        return out


def _names(rule, comp, count: int) -> list[str]:
    taken = {c.name for c in comp.connectors}
    out, n = [], 1
    while len(out) < count:
        name = rule.names.format(n=n, rule=rule.name)
        if name not in taken:
            out.append(name)
            taken.add(name)
        n += 1
    return out


def generate_bolts(window, comp) -> GenerateBolts:
    dialog = GenerateBolts(window, comp)
    window.bolt_generator = dialog  # kept, so a test (or the agent panel) can reach it
    dialog.show()
    return dialog
