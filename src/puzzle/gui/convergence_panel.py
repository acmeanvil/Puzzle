"""Mesh convergence in the window (ADR 0066): Check mesh convergence… on a study asks
the meshes, ratio and target in one form, keeps them on the study (one Edit, so the
Advisor reads the verdict later), solves the meshes not run yet in the background, and
shows the Convergence panel: each mesh's elements and values, each quantity plotted
against the element count with its extrapolated value, and the verdicts.
"""

from __future__ import annotations

import threading
from uuid import UUID

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

LEVELS = [(f"{n} meshes", n) for n in range(2, 7)]
RATIOS = [("1.3 (sizes ÷ 1.3)", 1.3), ("1.5 (sizes ÷ 1.5)", 1.5), ("2 (sizes halved)", 2.0)]


def check(window, study_id: str):
    """Ask the check's settings, keep them on the study, then run it."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Convergence

    study = window.session.project.objects.get(UUID(study_id))
    if study is None:
        return None
    c = study.convergence or Convergence()

    def valid(v) -> str | None:
        try:
            t = float(str(v.get("target", "")).strip().rstrip("%"))
        except ValueError:
            return "the target is a percentage, e.g. 2"
        return None if 0 < t < 100 else "the target is between 0 and 100 %"

    got = window.ask_form("Check mesh convergence", [
        Ask("levels", "Meshes", choices=LEVELS, default=c.levels,
            help="Three or more judge the convergence (Roache's grid convergence index)"),
        Ask("ratio", "Each finer by", choices=RATIOS, default=c.ratio,
            help="Every size of the mesh recipe divided by this from one mesh to the next"),
        Ask("target", "Converged within (%)", kind="text", default=f"{c.target * 100:g}",
            help="The grid convergence index that counts as converged"),
    ], check=valid)  # fmt: skip
    if not got:
        return None
    value = {"levels": int(got["levels"]), "ratio": float(got["ratio"]),
             "target": float(str(got["target"]).strip().rstrip("%")) / 100,
             "quantities": [q.model_dump(mode="json") for q in c.quantities]}  # fmt: skip
    window.submit(Edit(object=study_id, ops=[EditOp(op="set", path="convergence",
                                                    value=value)]))  # fmt: skip
    return run(window, study_id)


def run(window, study_id: str) -> None:
    """Solve the meshes not run yet in the background; the panel shows them after."""
    from puzzle import convergence as conv
    from puzzle.project.layout import WORK_DIR

    s = window.session
    study = s.project.objects.get(UUID(study_id))
    if study is None:
        return
    _, c = conv.settings(s.project, study.name)
    stop = threading.Event()
    window.convergence_stop = stop
    project, root, store = s.project, s.root, window._store()

    def work(emit):
        return conv.run(project, study, root, root / WORK_DIR / "convergence" / study_id,
                        store=store, count=c.levels, ratio=c.ratio, outputs=c.quantities,
                        on_output=emit, cancel=stop)  # fmt: skip

    def done(levels) -> None:
        window.convergence_stop = None
        window.refresh()
        window.show_convergence(study_id)
        vs = conv.verdicts(levels, c.target)
        said = "; ".join(f"{v.quantity}: {v.words}" for v in vs) or "too few meshes ran"
        window.statusBar().showMessage(f"{study.name}: {said}", 15000)

    window.background(f"Checking {study.name}'s mesh convergence…", work, done)


FIELDS = [("Displacement (structural) or temperature (heat)", None)]


def error_estimate(window, study_id=None, definition=None):
    """An error estimate (ADR 0066 phase 2) as a derived result, in one form: the
    study, the field (the physics' own, or any nodal field's gradient jumps), the
    target the refinement ratio is for. Made, or changed, by one command."""
    from puzzle.commands.model import Create, Edit, EditOp
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Study

    project = window.session.project
    studies = [(s.name, str(s.id)) for s in project.of_type(Study)]
    if not studies:
        window.statusBar().showMessage("An error estimate reads a study's run: make one first",
                                       8000)  # fmt: skip
        return None

    def fields(v) -> list:
        """The physics' own, then the chosen study's other nodal fields."""
        from uuid import UUID as _U

        from puzzle.post.derived import run_for
        from puzzle.post.quantities import words

        try:
            record = run_for(project, window._store(), _U(v.get("source") or studies[0][1]))
            step = window._store().read_results(record.run_id).steps[-1]
        except Exception:  # noqa: BLE001 - no run yet: the physics' own only
            return FIELDS
        own = ("displacement", "temperature")
        return FIELDS + [(f"{words(n)}: a relative indicator", n)
                         for n, f in step.fields.items()
                         if f.location == "node" and n not in own]  # fmt: skip

    d = definition
    got = window.ask_form("Error estimate", [
        Ask("source", "Study", choices=studies,
            default=str(d.source) if d else study_id),
        Ask("quantity", "From", choices=fields, default=d.quantity if d else None,
            help="Displacement and temperature give the energy-norm error "
            "(Zienkiewicz–Zhu); other fields their gradient's jumps between elements"),
        Ask("target", "Target error (%)", kind="text",
            default=f"{(d.error_target if d else 0.02) * 100:g}",
            help="The refinement ratio is above 1 where an element is too big for it"),
        Ask("name", "Name", kind="text", default=d.name if d else "Error estimate"),
    ], check=lambda v: None if _percent(v.get("target")) else "the target is a "
       "percentage between 0 and 100")  # fmt: skip
    if not got:
        return None
    data = {"name": got["name"], "method": "error_estimate", "source": got["source"],
            "quantity": got.get("quantity"), "error_target": _percent(got["target"])}  # fmt: skip
    if d is None:
        outcome = window.submit(Create(kind="result", data=data))
    else:
        current = d.model_dump(mode="json")
        ops = [EditOp(op="set", path=k, value=v) for k, v in data.items() if current.get(k) != v]
        outcome = window.submit(Edit(object=str(d.id), ops=ops)) if ops else None
    if outcome is not None and getattr(outcome, "created", None):
        return outcome.created[0]["id"]
    return None


def _percent(text) -> float | None:
    try:
        t = float(str(text).strip().rstrip("%")) / 100
    except ValueError:
        return None
    return t if 0 < t < 1 else None


class ConvergencePanel(QWidget):
    """One study's convergence check: the meshes, a plot per quantity, the verdicts."""

    def __init__(self, window) -> None:
        from puzzle.gui import theme
        from puzzle.gui.material_builder import Curve

        super().__init__()
        self.window = window
        self.study_id: str | None = None
        self.levels: list = []
        self.verdicts: list = []
        column = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = theme.role(QLabel("No convergence check shown"), "title")
        top.addWidget(self.title, 1)
        self.run_button = QPushButton("Run the rest")
        self.run_button.setToolTip("Solve the meshes not run yet")
        self.run_button.clicked.connect(lambda: self.study_id and run(window, self.study_id))
        top.addWidget(self.run_button)
        column.addLayout(top)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().hide()
        column.addWidget(self.table, 1)
        self.plot = Curve("Elements", "")
        column.addWidget(self.plot, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

    def show(self, study_id: str) -> None:  # noqa: A003 - shows a study's check
        self.study_id = study_id
        self.refresh()

    def refresh(self) -> None:
        from puzzle import convergence as conv
        from puzzle.errors import PuzzleError

        s = self.window.session
        study = s.project.objects.get(UUID(self.study_id)) if self.study_id and s else None
        if study is None:
            self.title.setText("No convergence check shown")
            self.table.setRowCount(0)
            return
        _, c = conv.settings(s.project, study.name)
        self.title.setText(f"{study.name}: mesh convergence")
        try:
            self.levels = conv.run(s.project, study, s.root, s.root, store=self.window._store(),
                                   count=c.levels, ratio=c.ratio, outputs=c.quantities,
                                   read_only=True)  # fmt: skip
        except PuzzleError as exc:
            self.summary.setText(exc.message)
            self.levels = []
        self.verdicts = conv.verdicts(self.levels, c.target)
        self._render()

    def _render(self) -> None:
        names = list(dict.fromkeys(k for lv in self.levels for k in lv.values))
        heads = ["Mesh", "Elements", "Nodes", *names]
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.setRowCount(len(self.levels))
        for r, lv in enumerate(self.levels):
            cells = [lv.label.split(": ", 1)[-1], str(lv.elements) if lv.run else "",
                     str(lv.nodes) if lv.run else ""]  # fmt: skip
            cells += [_number(lv.values.get(n), lv.kinds.get(n)) if lv.run else lv.reason
                      for n in names]  # fmt: skip
            for col, text in enumerate(cells):
                self.table.setItem(r, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        series = []
        for n in names:
            pts = [(float(lv.elements), lv.values[n]) for lv in self.levels
                   if lv.run and lv.values.get(n) is not None]  # fmt: skip
            series.append((n, pts))
            v = next((v for v in self.verdicts if v.quantity == n), None)
            if v is not None and v.extrapolated is not None and pts:
                series.append((f"{n}: extrapolated", [(pts[0][0], v.extrapolated),
                                                      (pts[-1][0], v.extrapolated)]))  # fmt: skip
        self.plot.y_label = names[0] if len(names) == 1 else "Value"
        self.plot.set_series(series)
        self.run_button.setEnabled(any(not lv.run for lv in self.levels))
        lines = [f"{v.quantity}: {v.words}" for v in self.verdicts]
        ran = sum(1 for lv in self.levels if lv.run)
        if ran < len(self.levels):
            lines.append(f"{ran} of {len(self.levels)} meshes run.")
        self.summary.setText("\n".join(lines))


def _number(value, kind) -> str:
    """Six digits in the display units: a convergence table's changes are small."""
    from puzzle.model.units import DIMENSIONS, pretty, shown
    from puzzle.settings import current

    if value is None:
        return ""
    if kind is None or kind not in DIMENSIONS:
        return f"{value:.6g}"
    v, unit = shown(value, DIMENSIONS[kind][1], current().display_units())
    return f"{v:.6g} {pretty(unit)}"
