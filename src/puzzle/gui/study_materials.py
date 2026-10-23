"""A study's material table (ADR 0018): each location keeps its material, or tries several.

One list per material assignment of the study's component, every project material
ticked or not; the assigned one is ticked to start with. Ticking more than one makes
the study run once per material (with several locations: every combination, or in
pairs). OK writes the study's steps in one Edit command, so it undoes in one step.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from puzzle import sweep
from puzzle.model.schema import Component, Material, Project, Study

ROLE = Qt.ItemDataRole.UserRole
COMBINATIONS = [("Every combination", "grid"), ("In pairs (first with first, …)", "paired")]


class StudyMaterials(QDialog):
    def __init__(self, project: Project, study: Study, parent=None, add_material=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Materials for {study.name}")
        self.project, self.study = project, study
        self.add_material = add_material  # () -> (project, new material id), or None
        comp = project.get(study.component, Component)
        self.assigned = {a.selection: str(a.material) for a in comp.materials}
        current = sweep.material_table(study)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Tick one material to use it here, or several to run the study once per "
            "material and compare the results."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.lists: dict[str, QListWidget] = {}
        for location, material in self.assigned.items():
            box = QGroupBox(location)
            inner = QVBoxLayout(box)
            lst = QListWidget()
            ticked = set(current.get(location) or [material])
            for m in project.of_type(Material):
                self._item(lst, m, str(m.id) in ticked)
            lst.itemChanged.connect(self._update)
            inner.addWidget(lst)
            self.lists[location] = lst
            layout.addWidget(box)
        row = QHBoxLayout()
        self.combination_label = QLabel("Combine the locations:")
        self.combination = QComboBox()
        for text, value in COMBINATIONS:
            self.combination.addItem(text, value)
        first = next(iter(study.sweeps()), None)
        if first is not None:
            self.combination.setCurrentIndex(self.combination.findData(first.combination))
        self.combination.currentIndexChanged.connect(self._update)
        row.addWidget(self.combination_label)
        row.addWidget(self.combination, 1)
        layout.addLayout(row)
        more = QPushButton("Add material from the library…")
        more.clicked.connect(self._add)
        layout.addWidget(more)
        self.count = QLabel()
        layout.addWidget(self.count)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update()

    def _item(self, lst: QListWidget, m: Material, ticked: bool) -> None:
        mark = "  (assigned)" if any(str(m.id) == a for a in self.assigned.values()) else ""
        item = QListWidgetItem(m.name + mark)
        item.setData(ROLE, str(m.id))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
        lst.addItem(item)

    def _add(self) -> None:
        if self.add_material is None:
            return
        added = self.add_material()
        if added is None:
            return
        self.project, mid = added  # the window's project, now with the new material
        m = self.project.objects.get(mid)
        if isinstance(m, Material):
            for lst in self.lists.values():
                self._item(lst, m, False)
        self._update()

    def table(self) -> dict[str, list[str]]:
        out = {}
        for location, lst in self.lists.items():
            items = [lst.item(i) for i in range(lst.count())]
            out[location] = [i.data(ROLE) for i in items if i.checkState() == Qt.CheckState.Checked]
        return out

    def steps(self) -> list[dict]:
        return sweep.with_material_table(
            self.project, self.study, self.table(), self.combination.currentData()
        )

    def runs(self) -> int:
        study = Study.model_validate({**self.study.model_dump(mode="json"), "steps": self.steps()})
        return sweep.run_count(study)

    def _update(self, *_) -> None:
        table = self.table()
        several = sum(len(v) > 1 for v in table.values())
        self.combination.setVisible(several > 1)
        self.combination_label.setVisible(several > 1)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        empty = [loc for loc, ids in table.items() if not ids]
        if empty:
            self.count.setText(f"Tick at least one material for {', '.join(empty)}.")
            ok.setEnabled(False)
            return
        try:
            n = self.runs()
        except ValueError:  # pairs of unequal lengths
            self.count.setText("In pairs, every location needs the same number of materials.")
            ok.setEnabled(False)
            return
        ok.setEnabled(True)
        self.count.setText("The study makes 1 run." if n == 1 else f"The study makes {n} runs.")
