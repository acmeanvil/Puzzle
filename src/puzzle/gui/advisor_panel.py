"""The Advisor dock (ADR 0040): the workflow for the selected component and study,
each step done (✓), needed (●) or optional (○) with its reason and one button.

It follows the tree's selection and every project change; the steps come from
`puzzle.advisor.advise`, so the agent's `next_steps` says the same. It opens for new
projects until the user closes it once (Settings > General keeps that), and an empty
project offers the cantilever example to try.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from puzzle.model.schema import Component, Study

MARKS = {"done": ("✓", "#2e9d57"), "needed": ("●", "#d4a72c"), "optional": ("○", "#8a96a3")}
EXAMPLE = Path(__file__).parents[3] / "examples" / "cantilever"


class AdvisorPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.advice = []
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.heading = QLabel("")
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet("font-weight: bold; font-size: 14px")
        self.scope = QLabel("")
        self.scope.setStyleSheet("color: gray")
        self.scope.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addWidget(self.scope)
        self.rows = QWidget()
        self.grid = QGridLayout(self.rows)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setVerticalSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.rows)
        layout.addWidget(scroll, 1)
        self.example = QPushButton("Try the cantilever example")
        self.example.setToolTip("A copy of a solved beam to explore: mesh it, run it, read it")
        self.example.clicked.connect(self.open_example)
        layout.addWidget(self.example)

    # --- what it shows -----------------------------------------------------------------

    def scope_objects(self):
        """The component and study the Advisor speaks about: the tree's selection, else
        the first of each."""
        w = self.window
        if w.session is None:
            return None, None
        node = w.tree.current()
        obj = w._object(node)
        comp = w._component_of(node)
        study = obj if isinstance(obj, Study) else None
        if not isinstance(comp, Component) or comp.id not in w.session.project.objects:
            comp = None
        return comp, study

    def refresh(self) -> None:
        from puzzle.advisor import advise, next_step

        w = self.window
        self._clear()
        if w.session is None:
            self.heading.setText("Open or make a project to begin.")
            self.scope.setText("")
            self.example.setVisible(EXAMPLE.is_dir())
            return
        comp, study = self.scope_objects()
        try:
            runs = w._store().runs()
        except Exception:  # noqa: BLE001 - an unreadable store: no runs, said elsewhere
            runs = []
        meshed = comp is not None and str(comp.id) in w.meshes
        self.advice = advise(w.session.project, runs, comp, study, meshed=meshed)
        first = next_step(self.advice)
        self.heading.setText(f"Next: {first.label or first.title}" if first else "All set")
        about = [x.name for x in (comp, study) if x is not None]
        self.scope.setText(f"For {' and '.join(about)}" if about else "For the project")
        for row, a in enumerate(self.advice):
            mark, colour = MARKS[a.state]
            sign = QLabel(mark)
            sign.setStyleSheet(f"color: {colour}; font-size: 14px")
            text = QLabel(f"<b>{a.title}</b><br><span style='color: gray'>{a.detail}</span>")
            text.setWordWrap(True)
            self.grid.addWidget(sign, row, 0)
            self.grid.addWidget(text, row, 1)
            if a.action and a.state != "done":
                b = QPushButton(a.label or "Go")
                b.clicked.connect(lambda _=False, act=a.action: self.do(act))
                self.buttons[a.action] = b
                self.grid.addWidget(b, row, 2)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(len(self.advice), 1)
        empty = not w.session.project.objects
        self.example.setVisible(empty and EXAMPLE.is_dir())

    def _clear(self) -> None:
        self.buttons = {}
        for row in range(self.grid.rowCount()):  # a stretch left from the last list
            self.grid.setRowStretch(row, 0)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    # --- what its buttons do -----------------------------------------------------------

    def do(self, action: str):
        """Go where a step's button points; each is the same function as its menu item."""
        from puzzle.gui import menus

        w = self.window
        comp, study = self.scope_objects()
        project = w.session.project
        if comp is None:
            comps = project.of_type(Component)
            comp = comps[0] if comps else None
        if study is None and comp is not None:
            study = next((s for s in project.of_type(Study) if s.component == comp.id), None)
        if action == "import_geometry":
            return menus.import_step_file(w)
        if action == "pick":
            w.toggle_picking(True)
            w.statusBar().showMessage(
                "Double-click faces in the view, then Selections → New from picks…", 12000
            )
            return None
        if action == "add_material":
            return w._add_material_dialog()
        if action == "material_builder":
            return w.open_material_builder()
        if action == "new_mesh":
            return menus.new_mesh(w)
        if action == "new_component":
            return menus.new_component(w)
        if action == "new_study":
            return menus.new_study(w)
        if action == "show_problems":
            w.output_tabs.setCurrentWidget(w.problems)
            w.fold_messages(False)
            return None
        if comp is None:
            return None
        if action == "assign_material":
            return menus.assign_material(w, comp)
        if action == "add_physics":
            return self._add_physics(comp)
        if action.startswith("add_feature:"):
            from puzzle.model.catalog import spec

            _, index, kind = action.split(":")
            physics = comp.physics[int(index)]
            return menus.add_feature(w, comp, int(index), spec(physics.type, kind))
        if action == "mesh":
            self._select(str(comp.id))
            return w.mesh_selected()
        if study is None:
            return None
        if action == "solver_settings":
            from puzzle.gui.solver_settings import edit_solver_settings

            return edit_solver_settings(w, study)
        if action == "run":
            self._select(str(study.id))
            return w.run_selected()
        if action == "results":
            runs = [r for r in w._store().runs() if r.study_id == str(study.id)]
            if runs:
                w._select_run(runs[-1].run_id)
            return None
        return None

    def _select(self, object_id: str) -> None:
        from puzzle.gui.tree import Node

        self.window.tree.select(Node("object", object_id))

    def _add_physics(self, comp):
        from puzzle.gui import menus
        from puzzle.gui.choices import label
        from puzzle.gui.quick_form import Ask
        from puzzle.model.catalog import CATALOG

        present = {p.type for p in comp.physics}
        choices = [(label(k), k) for k in CATALOG if k not in present]
        got = self.window.ask_form("Add physics", [Ask("physics", "Physics", choices=choices)])
        return None if got is None else menus.add_physics(self.window, comp, got["physics"])

    def open_example(self) -> None:
        """A fresh copy of the cantilever example in the user's data folder, opened."""
        from puzzle.materials import user_data_dir

        base = Path(user_data_dir()) / "examples"
        n = 1
        while (base / f"cantilever-{n}").exists():
            n += 1
        target = base / f"cantilever-{n}"
        shutil.copytree(
            EXAMPLE, target, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
        )
        self.window.open(target)
