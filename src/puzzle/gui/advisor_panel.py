"""The Advisor dock (ADR 0040): the workflow for the selected component and study,
each step done (✓), needed (●) or optional (○) with its reason and one button.

It follows the tree's selection and every project change; the steps come from
`puzzle.advisor.advise`, so the agent's `next_steps` says the same. It opens for new
projects until the user closes it once (Settings > General keeps that), and an empty
project offers the cantilever example to try.
"""

from __future__ import annotations

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

from puzzle.gui.theme import current as tokens
from puzzle.gui.theme import role
from puzzle.model.schema import Component, Study

# A shape and a role each, so a step's state reads without colour too.
MARKS = {"done": ("✓", "done"), "needed": ("●", "needed"), "optional": ("○", "muted")}
EXAMPLE = Path(__file__).parents[3] / "examples" / "cantilever"
# What a study's state reads as in the Advisor's list of the others (UX-6).
STATES = {
    "up_to_date": "results ready",
    "out_of_date": "out of date",
    "not_run": "not run",
    "running": "running",
    "failed": "failed",
    "missing": "missing input",
}


def _study(project, study_id):
    from uuid import UUID

    try:
        obj = project.objects.get(UUID(str(study_id)))
    except ValueError:
        return None
    return obj if isinstance(obj, Study) else None


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
        role(self.heading, "title")
        self.scope = QLabel("")
        role(self.scope, "muted")
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

    def sizeHint(self):  # noqa: N802 - Qt's name
        """Narrow by default: the view keeps the room (its rows wrap)."""
        from PySide6.QtCore import QSize

        return QSize(320, 600)

    # --- what it shows -----------------------------------------------------------------

    def scope_objects(self):
        """The component and study the Advisor speaks about (UX-6): the selected study;
        else the study of the selected run or result, or of the results in the view;
        else the study run last; else the selected item's component's first. It once
        spoke of the first study while another's results were on screen."""
        comp, study, _run = self._scope()
        return comp, study

    def _scope(self):
        """(component, study, whether a run or its results are what is selected)."""
        w = self.window
        if w.session is None:
            return None, None, False
        project = w.session.project
        node = w.tree.current()
        obj = w._object(node)
        comp = w._component_of(node)
        if not isinstance(comp, Component) or comp.id not in project.objects:
            comp = None
        study = obj if isinstance(obj, Study) else None
        run = False
        if study is None and node is not None and node.kind in ("sweep", "compare"):
            study = _study(project, node.object)
            run = study is not None
        if study is None and node is not None and node.kind in ("run", "result", "result_group"):
            study = self._study_of_run(node.object)
            run = study is not None
        if study is None and comp is None:
            shown = getattr(w, "_result_run", None)
            if shown and w.context == "results":
                study = self._study_of_run(shown)
                run = study is not None
            if study is None:
                study = self._last_run()
        if study is not None and comp is None:
            comp = project.objects.get(study.component)
            comp = comp if isinstance(comp, Component) else None
        return comp, study, run

    def _runs(self):
        try:
            return self.window._store().runs()
        except Exception:  # noqa: BLE001 - an unreadable store: no runs, said elsewhere
            return []

    def _study_of_run(self, run_id: str):
        project = self.window.session.project
        if str(run_id).startswith("derived:"):
            return None
        record = next((r for r in self._runs() if r.run_id == run_id), None)
        return _study(project, record.study_id) if record is not None else None

    def _last_run(self):
        project = self.window.session.project
        for record in sorted(self._runs(), key=lambda r: r.started_at, reverse=True):
            study = _study(project, record.study_id)
            if study is not None:
                return study
        return None

    def refresh(self) -> None:
        from puzzle.advisor import advise, next_step

        w = self.window
        self._clear()
        if w.session is None:
            self.heading.setText("Open or make a project to begin.")
            self.scope.setText("")
            self.example.setVisible(EXAMPLE.is_dir())
            return
        comp, study, on_run = self._scope()
        runs = self._runs()
        meshed = comp is not None and w.current_mesh(comp) is not None
        self.advice = advise(
            w.session.project, runs, comp, study, meshed=meshed, root=w.session.root
        )
        first = next_step(self.advice)
        if on_run and study is not None:  # a run's results: what to do with them
            self.heading.setText(f"Results of {study.name}")
        else:
            self.heading.setText(f"Next: {first.label or first.title}" if first else "All set")
        about = [x.name for x in (comp, study) if x is not None]
        self.scope.setText(f"For {' and '.join(about)}" if about else "For the project")
        for row, a in enumerate(self.advice):
            mark, state = MARKS[a.state]
            sign = role(QLabel(mark), state)
            sign.setAccessibleName({"done": "Done", "needed": "Needed"}.get(a.state, "Optional"))
            muted = tokens().muted
            text = QLabel(f"<b>{a.title}</b><br><span style='color: {muted}'>{a.detail}</span>")
            text.setWordWrap(True)
            self.grid.addWidget(sign, row, 0)
            self.grid.addWidget(text, row, 1)
            if a.action and a.state != "done":
                b = QPushButton(a.label or "Go")
                b.clicked.connect(lambda _=False, act=a.action: self.do(act))
                self.buttons[a.action] = b
                self.grid.addWidget(b, row, 2)
        rows = len(self.advice)
        if on_run and study is not None:
            rows = self._with_results(rows)
        rows = self._others(study, runs, rows)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(rows, 1)
        empty = not w.session.project.objects
        self.example.setVisible(empty and EXAMPLE.is_dir())

    def _with_results(self, row: int) -> int:
        """A run is selected: what to do with its results, a button each (UX-6)."""
        from PySide6.QtWidgets import QHBoxLayout

        self.grid.addWidget(QLabel("<b>With these results</b>"), row, 1)
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        for key, words, tip in (
            ("probe", "Probe", "Read a value where you double-click the part"),
            ("reaction", "Reaction…", "The force and moment a support takes"),
            ("export", "Export…", "The results for other software"),
        ):
            b = QPushButton(words)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.do(f"results:{k}"))
            self.buttons[f"results:{key}"] = b
            line.addWidget(b)
        line.addStretch()
        self.grid.addWidget(holder, row + 1, 1, 1, 2)
        return row + 2

    def _others(self, study, runs, row: int) -> int:
        """The project's other studies, one line each with its state and a button that
        selects it, so the Advisor never reads as if the one shown were the only one."""
        from puzzle.project.status import status

        project = self.window.session.project
        others = [s for s in project.of_type(Study) if study is None or s.id != study.id]
        if not others:
            return row
        states = getattr(self.window, "states", None) or status(project, runs)  # the tree's
        title = QLabel("<b>Other studies</b>")
        self.grid.addWidget(title, row, 1)
        row += 1
        for s in others:
            state = states.get(str(s.id))
            word = STATES.get(state.state if state else "not_run", "not run")
            text = QLabel(f"{s.name}: {word}")
            text.setWordWrap(True)
            role(text, "muted")
            self.grid.addWidget(text, row, 1)
            b = QPushButton("Show")
            b.setToolTip(f"Select {s.name}; the Advisor then follows it")
            b.clicked.connect(lambda _=False, i=str(s.id): self._select(i))
            self.buttons[f"study:{s.id}"] = b
            self.grid.addWidget(b, row, 2)
            row += 1
        return row

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
        if action == "trust":  # ADR 0062
            from puzzle.gui.main_menu import trust_project

            return trust_project(w)
        if action.startswith("skin_depth:"):  # ADR 0047
            return add_skin_refinement(w, action.split(":", 1)[1])
        if action.startswith("convergence:"):  # ADR 0066
            from puzzle.gui.convergence_panel import check

            return check(w, action.split(":", 1)[1])
        if action.startswith("apply_best:"):  # ADR 0063: only when asked
            from puzzle.gui.optimisation_panel import apply_best

            return apply_best(w, action.split(":", 1)[1])
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
        if action == "contact_tool":
            return w.open_contacts("initial")
        if action == "find_contacts":
            from puzzle.gui.contact_search import find_contacts_dialog

            return find_contacts_dialog(w, comp)
        if action == "material_builder":
            return w.open_material_builder()
        if action == "new_mesh":
            return menus.new_mesh(w)
        if action == "new_component":
            return menus.new_component(w)
        if action == "new_study":
            return menus.new_study(w)
        if action == "new_analysis":
            return menus.new_analysis(w)
        if action.startswith("results:"):  # a selected run's next steps (UX-6)
            ribbon = w.results_ribbon
            what = action.split(":", 1)[1]
            if w.result is None or w.context != "results":
                self.do("results")
            if what == "export":
                from puzzle.gui.main_menu import _export

                return _export(w)
            return ribbon.buttons[what].click()
        if action == "show_problems":
            w.output_tabs.setCurrentWidget(w.problems)
            w.fold_messages(False)
            return None
        if comp is None:
            return None
        if action.startswith("thread:"):  # ADR 0076: a bonded tapped hole as a thread
            from puzzle.gui.thread_dialog import thread_contact

            return thread_contact(w, comp, int(action.split(":", 1)[1]))
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
        from puzzle.gui.start_page import open_example

        open_example(self.window, "cantilever")


def add_skin_refinement(window, study_id: str):
    """Refine each conductor of the study near its surface for its skin depth (ADR
    0047): one Edit of the mesh recipe's controls."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.magnetics import skin_refinement
    from puzzle.model.schema import Component, MeshRecipe, Study

    project = window.session.project
    study = project.get(study_id, Study)
    recipe = project.get(project.get(study.component, Component).mesh, MeshRecipe)
    controls = skin_refinement(project, study)
    if not controls:
        return None
    ops = [EditOp(op="append", path="controls", value=c.model_dump(mode="json")) for c in controls]
    return window.submit(Edit(object=str(recipe.id), ops=ops))
