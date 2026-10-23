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
        from puzzle.gui.form import FormPanel, fit_to_screen

        self.setWindowTitle("Solution combination")
        fit_to_screen(self, 760, 520)
        self.project = project
        self.runs = _Runs(project, store)
        self.definition = definition
        layout = QVBoxLayout(self)
        self.form = FormPanel()
        self.error = self.form.message  # what blocks OK
        layout.addWidget(self.form, 1)
        n = len(project.of_type(ResultDefinition)) + 1
        top = self.form.section("Combination")
        self.name = top.row(
            "name", "Name", QLineEdit(definition.name if definition else f"Combination {n}")
        )
        self.method = QComboBox()
        for key, text in derived.COMBINATIONS.items():
            self.method.addItem(text, key)
        if definition is not None:
            self.method.setCurrentIndex(self.method.findData(definition.method))
        top.row("method", "Method", self.method)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray")
        top.add_widget(self.note)

        cases = self.form.section("Cases")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Study", "Sweep point", "Result step", "Coefficient"])
        cases.add_widget(self.table)
        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        add, remove = QPushButton("Add case"), QPushButton("Remove case")
        add.clicked.connect(lambda: self.add_case())
        remove.clicked.connect(self.remove_case)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        cases.add_widget(buttons)

        self.envelope = self.form.section("Envelope")
        self.quantity, self.extreme = QComboBox(), QComboBox()
        self.extreme.addItem("Maximum", "max")
        self.extreme.addItem("Minimum", "min")
        self.envelope.row("quantity", "Result to compare", self.quantity)
        self.envelope.row("extreme", "Keep the", self.extreme)
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
            self.form.set_message("No study has been run yet: run one first.", error=True)
            ok.setEnabled(False)
            return
        try:
            ResultDefinition.model_validate(self.data())
        except PuzzleError as exc:
            self.form.set_message(str(exc), error=True)
            ok.setEnabled(False)
            return
        except ValueError as exc:
            self.form.set_message(_first_error(exc), error=True)
            ok.setEnabled(False)
            return
        self.form.set_message(None)
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
        from puzzle.gui.form import FormPanel, fit_to_screen

        self.setWindowTitle("Equation result")
        fit_to_screen(self, 680, 620)
        self.project, self.store = project, store
        self.runs = _Runs(project, store)
        layout = QVBoxLayout(self)
        self.form = FormPanel(
            "Operators + − * / ** %, comparisons, and, or, not. Functions: "
            + ", ".join(derived.FUNCTIONS)
            + "; max(x) alone is the largest over the model. Constants pi and e. "
            "Values are in SI units (Pa, m, K)."
        )
        self.status = self.form.message
        layout.addWidget(self.form, 1)
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
        top = self.form.section("Equation")
        top.row("name", "Name", self.name)
        top.row("source", "Results of", self.source)
        top.row("source_point", "Sweep point", self.point)
        top.row("source_step", "Result step", self.step)
        top.row("expression", "Equation", self.expression)
        top.row("unit", "Unit of the value", self.unit, "SI")
        names = self.form.section("Names you can use (double-click to insert)")
        self.names = QListWidget()
        self.names.setMinimumHeight(160)
        self.names.itemDoubleClicked.connect(self._insert)
        names.add_widget(self.names)
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
            self.form.set_message("No study has been run yet: run one first.", error=True)
            return
        if not self.expression.text().strip():
            self.form.set_message("Type an equation.")
            return
        try:
            d = ResultDefinition.model_validate(self.data())
            result = derived.evaluate(self.project, self.store, d)
        except PuzzleError as exc:
            self.form.set_message(str(exc), error=True)
            return
        except ValueError as exc:
            self.form.set_message(_first_error(exc), error=True)
            return
        v = result.results.steps[0].fields["equation"].values[:, 0]
        self.form.set_message(f"From {np.nanmin(v):.4g} to {np.nanmax(v):.4g} (SI).")
        ok.setEnabled(True)


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        return str(first.get("msg", exc)).removeprefix("Value error, ")
    return str(exc)


# Short names for the duty table; what each means is in the description box.
LOADINGS = [
    ("Fully reversed", "fully_reversed"),
    ("Zero-based", "zero_based"),
    ("Range", "range"),
]


class FatigueDialog(QDialog):
    """The fatigue tool (ADR 0026): a duty of cycle blocks, the stress measure, the
    mean-stress correction and the life model; Shigley's Marin factors for his line.
    Lengths and stresses are typed in the display units."""

    def __init__(self, project: Project, store, parent=None, definition=None, study=None):
        from puzzle.materials.fatigue import CORRECTIONS, SIZE_BASES, SURFACE_FINISHES
        from puzzle.post.fatigue_tool import MODEL_LABELS, STRESS_LABELS
        from puzzle.settings import current, length_unit

        super().__init__(parent)
        self.setWindowTitle("Fatigue tool")
        self.project, self.store = project, store
        self.runs = _Runs(project, store)
        self.length, self.per_metre = length_unit()
        self.stress_unit = current().display_units()["pressure"]
        s = definition.fatigue if definition is not None else None
        from PySide6.QtWidgets import QCheckBox

        from puzzle.gui.form import FormPanel, fit_to_screen
        from puzzle.model.units import pretty

        fit_to_screen(self, 820, 760)
        layout = QVBoxLayout(self)
        self.form = form = FormPanel(
            "High-cycle, proportional loading. Life is in duties (cycles, for one cycle per "
            "duty); damage is at the design life; the safety factor scales every stress."
        )
        self.status = form.message  # what blocks OK, or the result's summary
        layout.addWidget(form, 1)
        n = len(project.of_type(ResultDefinition)) + 1

        top = form.section("Fatigue tool")
        self.name = top.row(
            "name", "Name", QLineEdit(definition.name if definition else f"Fatigue {n}")
        )

        duty = form.section("Duty: the kinds of cycle, and how many of each per duty")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Loading", "Load case or combination", "Result step", "Scale", "Cycles per duty"]
        )
        duty.add_widget(self.table, "loading")
        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        add, remove = QPushButton("Add cycle"), QPushButton("Remove cycle")
        add.clicked.connect(lambda: self.add_block())
        remove.clicked.connect(self.remove_block)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        duty.add_widget(buttons)

        how = form.section("Stress and life")
        self.stress, self.correction, self.model, self.curve = (QComboBox() for _ in range(4))
        for key, text in STRESS_LABELS.items():
            self.stress.addItem(text, key)
        for key, (text, expr) in CORRECTIONS.items():
            self.correction.addItem(f"{text}: {expr}", key)
        for key, text in MODEL_LABELS.items():
            self.model.addItem(text, key)
        self.design_life = QLineEdit(f"{s.design_life:g}" if s else "1e6")
        how.row("stress", "Stress measure", self.stress)
        how.row("mean_stress", "Mean-stress correction", self.correction)
        how.row("life_model", "Life from", self.model)
        how.row("sn_curve", "S-N curve", self.curve)
        how.row("design_life", "Design life", self.design_life, "duties")

        self.marin = form.section("Shigley's Marin factors")
        mf = self.marin
        self.surface, self.size = QComboBox(), QComboBox()
        for key, (text, *_rest) in SURFACE_FINISHES.items():
            self.surface.addItem(text, key)
        for key, text in SIZE_BASES.items():
            self.size.addItem(text, key)
        self.diameter, self.rect_height, self.rect_width = QLineEdit(), QLineEdit(), QLineEdit()
        self.reliability = QLineEdit("50")
        self.temperature = QLineEdit("20")
        self.misc = QLineEdit("1")
        self.endurance = QLineEdit()
        self.endurance.setPlaceholderText("blank: 0.5 Sut (steels)")
        mf.row("surface", "Surface finish", self.surface)
        mf.row("size", "Size", self.size)
        mf.row("diameter", "Diameter", self.diameter, self.length)
        mf.row("height", "Height h", self.rect_height, self.length)
        mf.row("width", "Width b", self.rect_width, self.length)
        mf.row("reliability", "Reliability", self.reliability, "%")
        mf.row("temperature", "Temperature", self.temperature, "°C")
        mf.row("misc_factor", "Other effects factor kf", self.misc)
        mf.row("endurance_limit", "Measured endurance limit S'e", self.endurance,
               pretty(self.stress_unit))  # fmt: skip

        notched = s is not None and (s.notch_from == "neuber" or s.notch_factor != 1)
        notch = form.section("Notch: fatigue stress-concentration factor Kf", open=notched)
        self.notch_from = QComboBox()
        self.notch_from.addItem("Given directly", "direct")
        self.notch_from.addItem("From Kt and the notch radius (Neuber)", "neuber")
        self.kf = QLineEdit("1")
        self.kt = QLineEdit()
        self.radius = QLineEdit()
        self.shear = QCheckBox("Shear (torsion) notch sensitivity")
        self.on_mean = QCheckBox("Apply Kf to the mean stress too")
        self.notch_info = QLabel()
        self.notch_info.setStyleSheet("color: gray")
        self.notch_info.setWordWrap(True)
        notch.row("notch_from", "Kf from", self.notch_from)
        notch.row("notch_factor", "Kf", self.kf)
        notch.row("notch_kt", "Kt (theoretical)", self.kt)
        notch.row("notch_radius", "Notch radius", self.radius, self.length)
        notch.add_widget(self.shear, "notch_shear")
        notch.add_widget(self.on_mean, "notch_on_mean")
        notch.add_widget(self.notch_info)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if s is None:  # a new tool starts from the schema's defaults
            from puzzle.model.schema import FatigueSettings

            for box, value in (
                (self.stress, FatigueSettings.model_fields["stress"].default),
                (self.correction, FatigueSettings.model_fields["mean_stress"].default),
                (self.model, FatigueSettings.model_fields["life_model"].default),
                (self.surface, FatigueSettings.model_fields["surface"].default),
                (self.size, FatigueSettings.model_fields["size"].default),
            ):
                box.setCurrentIndex(box.findData(value))
        if s is not None:
            for box, value in (
                (self.stress, s.stress),
                (self.correction, s.mean_stress),
                (self.model, s.life_model),
                (self.surface, s.surface),
                (self.size, s.size),
            ):
                box.setCurrentIndex(box.findData(value))
            for line, value in (
                (self.diameter, s.diameter),
                (self.rect_height, s.height),
                (self.rect_width, s.width),
            ):
                line.setText(f"{value * self.per_metre:g}" if value is not None else "")
            self.reliability.setText(f"{s.reliability:g}")
            self.temperature.setText(f"{s.temperature:g}")
            self.misc.setText(f"{s.misc_factor:g}")
            if s.endurance_limit is not None:
                from puzzle.model.units import convert

                self.endurance.setText(f"{convert(s.endurance_limit, 'Pa', self.stress_unit):g}")
            self.notch_from.setCurrentIndex(self.notch_from.findData(s.notch_from))
            self.kf.setText(f"{s.notch_factor:g}")
            self.kt.setText(f"{s.notch_kt:g}" if s.notch_kt is not None else "")
            self.radius.setText(
                f"{s.notch_radius * self.per_metre:g}" if s.notch_radius is not None else ""
            )
            self.shear.setChecked(s.notch_shear)
            self.on_mean.setChecked(s.notch_on_mean)
            for b in s.blocks:
                self.add_block(b.model_dump(mode="json"))
        else:
            self.add_block(
                {"loading": "fully_reversed", "case": {"study": study}} if study else None
            )
        self._fill_curves(s.sn_curve if s else None)
        for w in (self.stress, self.correction, self.model, self.surface, self.size, self.curve,
                  self.notch_from):  # fmt: skip
            w.currentIndexChanged.connect(self._update)
        for w in (self.kf, self.kt, self.radius):
            w.textChanged.connect(self._update)
        for w in (self.shear, self.on_mean):
            w.toggled.connect(self._update)
        for w in (
            *(self.design_life, self.diameter, self.rect_height, self.rect_width),
            *(self.reliability, self.temperature, self.misc, self.endurance, self.name),
        ):
            w.textChanged.connect(self._update)
        self._update()

    # the duty ----------------------------------------------------------------------------

    def add_block(self, block: dict | None = None) -> None:
        block = block or {}
        r = self.table.rowCount()
        self.table.insertRow(r)
        loading, source, step = QComboBox(), QComboBox(), QComboBox()
        for text, key in LOADINGS:
            loading.addItem(text, key)
        loading.setCurrentIndex(max(0, loading.findData(block.get("loading", "fully_reversed"))))
        scale = QLineEdit(f"{block.get('scale', 1.0):g}")
        cycles = QLineEdit(f"{block.get('cycles', 1.0):g}")
        for c, w in enumerate((loading, source, step, scale, cycles)):
            self.table.setCellWidget(r, c, w)
        case = block.get("case") or {}

        def fill_sources(chosen=None):
            source.blockSignals(True)
            source.clear()
            if loading.currentData() == "range":
                for d in self.project.of_type(ResultDefinition):
                    if d.method == "range":
                        source.addItem(d.name, str(d.id))
                if source.count() == 0:
                    source.addItem("(make a range combination first)", None)
            else:
                for s in self.runs.studies:
                    source.addItem(s.name, str(s.id))
            found = source.findData(chosen)
            source.setCurrentIndex(found if found >= 0 else 0)
            source.blockSignals(False)
            fill_steps()

        def fill_steps(chosen=-1):
            if loading.currentData() == "range" or source.currentData() is None:
                step.clear()
                step.addItem("(the combination)", -1)
                step.setEnabled(False)
            else:
                step.setEnabled(True)
                self.runs.fill_steps(step, source.currentData(), None, chosen)

        fill_sources(block.get("combination") or case.get("study"))
        if case.get("step") is not None:
            fill_steps(case["step"])
        loading.currentIndexChanged.connect(lambda _: (fill_sources(), self._update()))
        source.currentIndexChanged.connect(
            lambda _: (fill_steps(), self._fill_curves(), self._update())
        )
        step.currentIndexChanged.connect(self._update)
        scale.textChanged.connect(self._update)
        cycles.textChanged.connect(self._update)
        self._update()

    def remove_block(self) -> None:
        r = self.table.currentRow()
        self.table.removeRow(r if r >= 0 else self.table.rowCount() - 1)
        self._update()

    def blocks(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            loading, source, step, scale, cycles = (self.table.cellWidget(r, c) for c in range(5))
            try:
                sc, cy = float(scale.text()), float(cycles.text())
            except ValueError:
                raise PuzzleError(
                    f"cycle {r + 1}: scale and cycles are numbers", code="invalid"
                ) from None
            b = {"loading": loading.currentData(), "scale": sc, "cycles": cy}
            if loading.currentData() == "range":
                b["combination"] = source.currentData()
            else:
                b["case"] = {"study": source.currentData(), "step": step.currentData()}
            out.append(b)
        return out

    # the definition ------------------------------------------------------------------

    def _number(self, line: QLineEdit, what: str, optional: bool = False):
        text = line.text().strip()
        if not text and optional:
            return None
        try:
            return float(text)
        except ValueError:
            raise PuzzleError(f"{what}: '{text}' is not a number", code="invalid") from None

    def data(self) -> dict:
        from puzzle.model.units import convert

        def length(line, what):
            v = self._number(line, what, optional=True)
            return v / self.per_metre if v is not None else None

        se = self._number(self.endurance, "Endurance limit", optional=True)
        settings = {
            "blocks": self.blocks(),
            "stress": self.stress.currentData(),
            "mean_stress": self.correction.currentData(),
            "life_model": self.model.currentData(),
            "sn_curve": self.curve.currentData(),
            "design_life": self._number(self.design_life, "Design life"),
            "surface": self.surface.currentData(),
            "size": self.size.currentData(),
            "diameter": length(self.diameter, "Diameter"),
            "height": length(self.rect_height, "Height"),
            "width": length(self.rect_width, "Width"),
            "reliability": self._number(self.reliability, "Reliability"),
            "temperature": self._number(self.temperature, "Temperature"),
            "misc_factor": self._number(self.misc, "Other effects factor"),
            "endurance_limit": convert(se, self.stress_unit, "Pa") if se is not None else None,
            "notch_from": self.notch_from.currentData(),
            "notch_shear": self.shear.isChecked(),
            "notch_on_mean": self.on_mean.isChecked(),
        }
        if settings["notch_from"] == "direct":
            settings["notch_factor"] = self._number(self.kf, "Kf")
        else:
            settings["notch_kt"] = self._number(self.kt, "Kt", optional=True)
            settings["notch_radius"] = length(self.radius, "Notch radius")
        return {"name": self.name.text().strip(), "method": "fatigue", "fatigue": settings}

    def _materials(self):
        """The materials of the first cycle's component (for their S-N curves)."""
        from uuid import UUID

        from puzzle.model.schema import Component, Material

        if self.table.rowCount() == 0:
            return []
        source = self.table.cellWidget(0, 1)
        sid = source.currentData()
        if sid is None:
            return []
        obj = self.project.objects.get(UUID(sid))
        if isinstance(obj, ResultDefinition) and obj.cases:
            obj = self.project.objects.get(obj.cases[0].study)
        if not isinstance(obj, Study):
            return []
        comp = self.project.objects.get(obj.component)
        if not isinstance(comp, Component):
            return []
        mats = [self.project.objects.get(a.material) for a in comp.materials]
        return [m for m in mats if isinstance(m, Material)]

    def _notch_text(self) -> str:
        """Neuber's q and Kf for each material, as the tool will take them."""
        from puzzle.materials.fatigue import notch_factor, notch_sensitivity

        try:
            kt = float(self.kt.text())
            r = float(self.radius.text()) / self.per_metre
        except ValueError:
            return "Give Kt and the notch radius."
        parts = []
        for m in self._materials():
            q = m.properties.get("tensile_strength")
            if q is None:
                parts.append(f"{m.name}: needs its tensile strength")
                continue
            qn = notch_sensitivity(q.to_si(), r, torsion=self.shear.isChecked())
            parts.append(f"{m.name}: q = {qn:.3g}, Kf = {notch_factor(kt, qn):.3g}")
        return "; ".join(parts) + " (Neuber, Shigley Eqs. 6-32 to 6-35; steels)"

    def _fill_curves(self, chosen=None) -> None:
        chosen = chosen or self.curve.currentData()
        self.curve.blockSignals(True)
        self.curve.clear()
        self.curve.addItem("(each material's first)", None)
        names = []
        for m in self._materials():
            names += [c.name for c in m.fatigue.sn_curves if c.name not in names]
        for name in names:
            self.curve.addItem(name, name)
        found = self.curve.findData(chosen)
        self.curve.setCurrentIndex(found if found >= 0 else 0)
        self.curve.blockSignals(False)

    def _update(self, *_) -> None:
        model = self.model.currentData()
        self.marin.setVisible(model == "shigley")
        self.curve.setEnabled(model == "sn_curve")
        size = self.size.currentData()
        self.form.set_visible("diameter", size in ("rotating_round", "non_rotating_round"))
        self.form.set_visible("height", size == "rectangle")
        self.form.set_visible("width", size == "rectangle")
        neuber = self.notch_from.currentData() == "neuber"
        self.form.set_visible("notch_factor", not neuber)
        for key in ("notch_kt", "notch_radius", "notch_shear"):
            self.form.set_visible(key, neuber)
        self.notch_info.setText(self._notch_text() if neuber else "")
        self.notch_info.setVisible(neuber)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(False)
        if not self.runs.studies:
            self.form.set_message("No study has been run yet: run one first.", error=True)
            return
        try:
            d = ResultDefinition.model_validate(self.data())
            result = derived.evaluate(self.project, self.store, d)
        except PuzzleError as exc:
            self.form.set_message(str(exc), error=True)
            return
        except ValueError as exc:
            self.form.set_message(_first_error(exc), error=True)
            return
        f = result.results.steps[0].fields
        life = f["fatigue_life"].values[:, 0]
        sf = f["fatigue_safety_factor"].values[:, 0]
        shortest = "infinite" if life.min() >= 1e12 else f"{life.min():.3g}"
        text = f"Shortest life {shortest}; smallest safety factor {sf.min():.3g}."
        if result.warnings:
            text += " " + " ".join(result.warnings)
        self.form.set_message(text)
        ok.setEnabled(True)
