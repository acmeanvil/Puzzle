"""The Sections panel (ADR 0038): section cuts saved in the project, to recall later.

"Save current cut" keeps the view's cut (across X, Y or Z, a face selection, a
construction plane, or a picked face's plane, with its offset, side and ghost) as a
`SectionCut`; clicking one sets the cut bar to it and cuts the shown result. Saving,
renaming (double-click or F2) and deleting are commands, so they undo.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.commands.model import Create, Delete, Edit, EditOp
from puzzle.errors import PuzzleError
from puzzle.gui.theme import role, same_size
from puzzle.model.schema import ConstructionGeometry, SectionCut

ROLE = Qt.ItemDataRole.UserRole


def describe(cut: SectionCut, project, unit: tuple[str, float]) -> str:
    """One line saying where a saved cut is, for its tooltip and the list."""
    name, per_m = unit
    if cut.plane in ("x", "y", "z"):
        where = f"across {cut.plane.upper()} at {cut.fraction:.0%}"
    elif cut.plane == "selection":
        part, _, sel = (cut.selection or "").partition("/")
        where = f"along {sel} ({part})"
    elif cut.plane == "construction":
        c = project.objects.get(cut.construction) if project is not None else None  # a UUID
        where = f"along {c.name}" if isinstance(c, ConstructionGeometry) else "plane gone"
    else:
        where = "along a picked face"
    if cut.plane not in ("x", "y", "z") and cut.offset:
        where += f", offset {cut.offset * per_m:g} {name}"
    return where + (", flipped" if cut.flip else "")


class SectionsPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.hint = role(QLabel("Cut the result (Section cut in the view's toolbar), then "
                                "save the cut here to recall it."), "muted")  # fmt: skip
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.list = QListWidget()
        self.list.itemClicked.connect(self.recall)
        self.list.itemChanged.connect(self._renamed)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.save_button = QPushButton("Save current cut")
        self.save_button.clicked.connect(lambda: self.save())
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete)
        same_size([self.save_button, self.delete_button], width=False)
        row.addWidget(self.save_button)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        layout.addLayout(row)
        self._filling = False

    def _bar(self, show: bool = False):
        """The view's cut bar; `show` opens it (as the toolbar's Section cut does)."""
        bar = self.window.viewport.view_bar
        if show and (getattr(bar, "cut_bar", None) is None or bar.cut_bar.isHidden()):
            bar.actions_by_name["section"].setChecked(True)
            bar.toggle_cut_bar(True)
        return getattr(bar, "cut_bar", None)

    def refresh(self) -> None:
        from puzzle.settings import length_unit

        session = self.window.session
        project = session.project if session is not None else None
        cuts = sorted(project.of_type(SectionCut), key=lambda c: c.name) if project else []
        self._filling = True
        self.list.clear()
        for c in cuts:
            item = QListWidgetItem(c.name)
            item.setData(ROLE, str(c.id))
            item.setToolTip(describe(c, project, length_unit()))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.list.addItem(item)
        self._filling = False
        self.delete_button.setEnabled(bool(cuts))

    def save(self, name: str | None = None) -> SectionCut | None:
        """Keep the view's cut as a saved section, named "Section n" unless given."""
        bar = self._bar()
        data = bar.saved() if bar is not None and not bar.isHidden() else None
        if data is None:
            self.window.report(PuzzleError("there is no section cut to save: cut the result "
                                           "first (Section cut in the view's toolbar)",
                                           code="invalid"))  # fmt: skip
            return None
        project = self.window.session.project
        taken = {c.name for c in project.of_type(SectionCut)}
        n = len(taken) + 1
        while name is None or name in taken:
            name, n = f"Section {n}", n + 1
        out = self.window.submit(Create(kind="cut", data=data | {"name": name}))
        self.refresh()
        if out is None:
            return None
        return next(c for c in self.window.session.project.of_type(SectionCut) if c.name == name)

    def _saved(self, item) -> SectionCut | None:
        from uuid import UUID

        obj = self.window.session.project.objects.get(UUID(item.data(ROLE))) if item else None
        return obj if isinstance(obj, SectionCut) else None

    def recall(self, item) -> None:
        """Cut the view along a saved section."""
        saved = self._saved(item)
        if saved is None:
            return
        bar = self._bar(show=True)
        try:
            bar.recall(saved)
        except ValueError as exc:
            self.window.report(PuzzleError(str(exc), code="not_found"))

    def delete(self) -> None:
        saved = self._saved(self.list.currentItem())
        if saved is not None:
            self.window.submit(Delete(object=str(saved.id)))
            self.refresh()

    def _renamed(self, item) -> None:
        saved = None if self._filling else self._saved(item)
        name = item.text().strip()
        if saved is None or not name or name == saved.name:
            return
        self.window.submit(Edit(object=str(saved.id), ops=[EditOp(op="set", path="name",
                                                                  value=name)]))  # fmt: skip
        self.refresh()
