"""Dialogs for derived results (ADR 0025): a solution combination, or an equation.

Cases and sources are chosen by name (studies, sweep points, result steps in words),
never by id; quantities by their listed names. The equation dialog lists every name the
expression may use and checks it as it is typed, showing the result's range. Either
dialog's `data()` is the ResultDefinition's fields, written by one command.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.errors import PuzzleError
from puzzle.model.schema import Project, ResultDefinition, Study
from puzzle.post import derived

RANGE_NOTE = (
    "Two cases: case 1 − case 2, component by component (a stress range for fatigue), "
    "and their mean. More cases: the largest minus the smallest."
)
NOTES = {
    "linear": "Each case's fields times its coefficient, added component by component; "
    "von Mises and principal stresses are then taken from the sum.",
    "srss": "The square root of the sum of the squares of the cases, component by component.",
    "envelope": "The largest (or smallest) of one result over the cases, and which case gave it.",
    "range": RANGE_NOTE,
}


def step_label(step, index: int) -> str:
    if step.mode is not None:
        return f"{index + 1}: mode {step.mode}, {step.value:.4g} Hz"
    return f"{index + 1}: t = {step.value:g}"


class _Runs:
    """The project's runs by study and point, read once per dialog."""

    def __init__(self, project: Project, store) -> None:
        from puzzle.sweep import title

        self.project, self.store = project, store
        self.points: dict[str, list[str]] = {}
        self.studies: list[Study] = []
        try:
            runs = store.runs()
        except PuzzleError:
            runs = []
        for r in runs:
            labels = r.point_labels()
            if labels:
                pts = self.points.setdefault(r.study_id, [])
                if title(labels) not in pts:
                    pts.append(title(labels))
        ran = {r.study_id for r in runs if r.status == "completed"}
        self.studies = [s for s in project.of_type(Study) if str(s.id) in ran]
        self._results: dict = {}

    def results(self, study_id: str, point: str | None):
        key = (study_id, point)
        if key not in self._results:
            from uuid import UUID

            record = derived.run_for(self.project, self.store, UUID(study_id), point)
            self._results[key] = self.store.read_results(record.run_id)
        return self._results[key]

    def fill_points(self, box: QComboBox, study_id: str | None, chosen: str | None) -> None:
        box.blockSignals(True)
        box.clear()
        pts = self.points.get(study_id or "", [])
        for p in pts:
            box.addItem(p, p)
        if not pts:
            box.addItem("(the study's run)", None)
        if chosen in pts:
            box.setCurrentIndex(pts.index(chosen))
        box.setEnabled(bool(pts))
        box.blockSignals(False)

    def fill_steps(self, box: QComboBox, study_id: str | None, point, chosen: int) -> None:
        box.blockSignals(True)
        box.clear()
        box.addItem("Last", -1)
        try:
            steps = self.results(study_id, point).steps if study_id else []
        except PuzzleError:
            steps = []
        for i, s in enumerate(steps):
            box.addItem(step_label(s, i), i)
        found = box.findData(chosen)
        box.setCurrentIndex(found if found >= 0 else 0)
        box.blockSignals(False)


class CombinationDialog(QDialog):
    def __init__(self, project: Project, store, parent=None, definition=None, study=None):
        super().__init__(parent)
        self.setWindowTitle("Solution combination")
        self.resize(720, 420)
        self.project = project
        self.runs = _Runs(project, store)
        self.definition = definition
        layout = QVBoxLayout(self)
        form = QFormLayout()
        n = len(project.of_type(ResultDefinition)) + 1
        self.name = QLineEdit(definition.name if definition else f"Combination {n}")
        self.method = QComboBox()
        for key, text in derived.COMBINATIONS.items():
            self.method.addItem(text, key)
        if definition is not None:
            self.method.setCurrentIndex(self.method.findData(definition.method))
        form.addRow("Name", self.name)
        form.addRow("Method", self.method)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray")
        layout.addWidget(self.note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Study", "Sweep point", "Result step", "Coefficient"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add, remove = QPushButton("Add case"), QPushButton("Remove case")
        add.clicked.connect(lambda: self.add_case())
        remove.clicked.connect(self.remove_case)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        self.envelope = QWidget()
        env = QFormLayout(self.envelope)
        env.setContentsMargins(0, 0, 0, 0)
        self.quantity, self.extreme = QComboBox(), QComboBox()
        self.extreme.addItem("Maximum", "max")
        self.extreme.addItem("Minimum", "min")
        env.addRow("Result to compare", self.quantity)
        env.addRow("Keep the", self.extreme)
        layout.addWidget(self.envelope)

        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if definition is not None:
            for c in definition.cases:
                self.add_case(str(c.study), c.point, c.step, c.coefficient)
            if definition.extreme:
                self.extreme.setCurrentIndex(self.extreme.findData(definition.extreme))
        else:
            first = str(study) if study else None
            self.add_case(first)
            self.add_case(first)
        self.method.currentIndexChanged.connect(self._update)
        self._update()
        if definition is not None and definition.quantity:
            self.quantity.setCurrentIndex(max(0, self.quantity.findData(definition.quantity)))

    # cases -----------------------------------------------------------------------------

    def add_case(self, study=None, point=None, step: int = -1, coefficient: float = 1.0) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        studies, points, steps = QComboBox(), QComboBox(), QComboBox()
        for s in self.runs.studies:
            studies.addItem(s.name, str(s.id))
        if study is not None and studies.findData(study) >= 0:
            studies.setCurrentIndex(studies.findData(study))
        coef = QLineEdit(f"{coefficient:g}")
        self.table.setCellWidget(r, 0, studies)
        self.table.setCellWidget(r, 1, points)
        self.table.setCellWidget(r, 2, steps)
        self.table.setCellWidget(r, 3, coef)
        self.runs.fill_points(points, studies.currentData(), point)
        self.runs.fill_steps(steps, studies.currentData(), points.currentData(), step)

        def study_changed(_=None):
            self.runs.fill_points(points, studies.currentData(), None)
            self.runs.fill_steps(steps, studies.currentData(), points.currentData(), -1)
            self._update()

        def point_changed(_=None):
            self.runs.fill_steps(steps, studies.currentData(), points.currentData(), -1)
            self._update()

        studies.currentIndexChanged.connect(study_changed)
        points.currentIndexChanged.connect(point_changed)
        steps.currentIndexChanged.connect(self._update)
        coef.textChanged.connect(self._update)
        self._update()

    def remove_case(self) -> None:
        r = self.table.currentRow()
        self.table.removeRow(r if r >= 0 else self.table.rowCount() - 1)
        self._update()

    def cases(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            study, point, step, coef = (self.table.cellWidget(r, c) for c in range(4))
            if study.currentData() is None:
                continue
            try:
                value = float(coef.text())
            except ValueError:
                raise PuzzleError(
                    f"case {r + 1}: the coefficient '{coef.text()}' is not a number",
                    code="invalid",
                ) from None
            out.append(
                {
                    "study": study.currentData(),
                    "point": point.currentData(),
                    "step": step.currentData(),
                    "coefficient": value,
                }
            )
        return out

    # the definition ------------------------------------------------------------------

    def data(self) -> dict:
        method = self.method.currentData()
        out = {"name": self.name.text().strip(), "method": method, "cases": self.cases()}
        if method == "envelope":
            out["quantity"] = self.quantity.currentData()
            out["extreme"] = self.extreme.currentData()
        return out

    def _update(self, *_) -> None:
        method = self.method.currentData()
        self.note.setText(NOTES.get(method, ""))
        self.envelope.setVisible(method == "envelope")
        if method == "envelope":
            self._fill_quantities()
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if not self.runs.studies:
            self.error.setText("No study has been run yet: run one first.")
            ok.setEnabled(False)
            return
        try:
            ResultDefinition.model_validate(self.data())
        except PuzzleError as exc:
            self.error.setText(str(exc))
            ok.setEnabled(False)
            return
        except ValueError as exc:
            self.error.setText(_first_error(exc))
            ok.setEnabled(False)
            return
        self.error.setText("")
        ok.setEnabled(True)

    def _fill_quantities(self) -> None:
        chosen = self.quantity.currentData()
        keys: list | None = None
        labels = {}
        for r in range(self.table.rowCount()):
            study, point = self.table.cellWidget(r, 0), self.table.cellWidget(r, 1)
            if study is None or study.currentData() is None:
                continue
            try:
                items = derived._items_of(
                    self.runs.results(study.currentData(), point.currentData())
                )
            except PuzzleError:
                continue
            labels.update({i.key: i.label for i in items})
            mine = [i.key for i in items]
            keys = mine if keys is None else [k for k in keys if k in mine]
        self.quantity.blockSignals(True)
        self.quantity.clear()
        for k in keys or []:
            self.quantity.addItem(labels[k], k)
        found = self.quantity.findData(chosen)
        self.quantity.setCurrentIndex(found if found >= 0 else 0)
        self.quantity.blockSignals(False)


class EquationDialog(QDialog):
    def __init__(self, project: Project, store, parent=None, definition=None, source=None):
        super().__init__(parent)
        self.setWindowTitle("Equation result")
        self.resize(640, 520)
        self.project, self.store = project, store
        self.runs = _Runs(project, store)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        n = len(project.of_type(ResultDefinition)) + 1
        self.name = QLineEdit(definition.name if definition else f"Equation {n}")
        self.source, self.point, self.step = QComboBox(), QComboBox(), QComboBox()
        for s in self.runs.studies:
            self.source.addItem(s.name, str(s.id))
        for d in project.of_type(ResultDefinition):
            if definition is None or d.id != definition.id:
                self.source.addItem(f"{d.name} (derived)", str(d.id))
        wanted = str(definition.source) if definition else (str(source) if source else None)
        if wanted and self.source.findData(wanted) >= 0:
            self.source.setCurrentIndex(self.source.findData(wanted))
        self.expression = QLineEdit(definition.expression if definition else "")
        self.expression.setPlaceholderText("e.g. von_mises / 250e6")
        self.unit = QComboBox()
        from puzzle.model.units import DIMENSIONS

        self.unit.addItem("(no unit)", None)
        for key, (text, _) in DIMENSIONS.items():
            self.unit.addItem(text, key)
        if definition is not None and definition.unit:
            self.unit.setCurrentIndex(self.unit.findData(definition.unit))
        form.addRow("Name", self.name)
        form.addRow("Results of", self.source)
        form.addRow("Sweep point", self.point)
        form.addRow("Result step", self.step)
        form.addRow("Equation", self.expression)
        form.addRow("Unit of the value (SI)", self.unit)
        layout.addLayout(form)
        layout.addWidget(QLabel("Names you can use (double-click to insert):"))
        self.names = QListWidget()
        self.names.itemDoubleClicked.connect(self._insert)
        layout.addWidget(self.names, 1)
        help_text = QLabel(
            "Operators + − * / ** %, comparisons, and, or, not. Functions: "
            + ", ".join(derived.FUNCTIONS)
            + "; max(x) alone is the largest over the model. Constants pi and e. "
            "Values are in SI units (Pa, m, K)."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: gray")
        layout.addWidget(help_text)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.point.currentIndexChanged.connect(self._point_changed)
        self.step.currentIndexChanged.connect(self._update)
        self.expression.textChanged.connect(self._update)
        self.unit.currentIndexChanged.connect(self._update)
        self._source_changed(
            point=definition.source_point if definition else None,
            step=definition.source_step if definition else -1,
        )

    def _is_study(self) -> bool:
        return any(str(s.id) == self.source.currentData() for s in self.runs.studies)

    def _source_changed(self, _=None, point=None, step: int = -1) -> None:
        study = self.source.currentData() if self._is_study() else None
        self.runs.fill_points(self.point, study, point)
        if study:
            self.runs.fill_steps(self.step, study, self.point.currentData(), step)
        else:
            self.step.clear()
            self.step.addItem("(the combination)", -1)
        self._fill_names()
        self._update()

    def _point_changed(self, _=None) -> None:
        if self._is_study():
            self.runs.fill_steps(self.step, self.source.currentData(), self.point.currentData(), -1)
        self._fill_names()
        self._update()

    def _items(self):
        from uuid import UUID

        sid = self.source.currentData()
        if sid is None:
            return []
        try:
            if self._is_study():
                return derived._items_of(self.runs.results(sid, self.point.currentData()))
            d = self.project.objects.get(UUID(sid))
            return derived.evaluate(self.project, self.store, d).items
        except PuzzleError:
            return []

    def _fill_names(self) -> None:
        self.names.clear()
        for item in self._items():
            self.names.addItem(f"{derived.name_of(item)}  —  {item.label}")
        for name, value in derived.parameters(self.project).items():
            self.names.addItem(f"{name}  —  parameter, {value:g} (SI)")

    def _insert(self, item) -> None:
        self.expression.insert(item.text().split("  —  ")[0])
        self.expression.setFocus()

    def data(self) -> dict:
        out = {
            "name": self.name.text().strip(),
            "method": "equation",
            "source": self.source.currentData(),
            "expression": self.expression.text().strip(),
            "unit": self.unit.currentData(),
            "source_step": self.step.currentData() if self.step.currentData() is not None else -1,
        }
        if self._is_study() and self.point.currentData() is not None:
            out["source_point"] = self.point.currentData()
        return out

    def _update(self, *_) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(False)
        if self.source.currentData() is None:
            self.status.setText("No study has been run yet: run one first.")
            return
        if not self.expression.text().strip():
            self.status.setText("Type an equation.")
            return
        try:
            d = ResultDefinition.model_validate(self.data())
            result = derived.evaluate(self.project, self.store, d)
        except PuzzleError as exc:
            self.status.setStyleSheet("color: #b00020")
            self.status.setText(str(exc))
            return
        except ValueError as exc:
            self.status.setStyleSheet("color: #b00020")
            self.status.setText(_first_error(exc))
            return
        v = result.results.steps[0].fields["equation"].values[:, 0]
        self.status.setStyleSheet("")
        self.status.setText(f"From {np.nanmin(v):.4g} to {np.nanmax(v):.4g} (SI).")
        ok.setEnabled(True)


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        return str(first.get("msg", exc)).removeprefix("Value error, ")
    return str(exc)
