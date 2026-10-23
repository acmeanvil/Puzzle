"""The material builder (ADR 0020): browse the library, make or edit your materials.

Left: every library material by family, the variants under it, with a search; yours
are marked. Right: the chosen material, in tabs:

- Properties, grouped (General, Structural, Thermal, Fluid, Electromagnetic) from the
  property registry, in the display units (Settings); each may have a temperature
  table, edited in a two-column grid with its curve drawn beside it.
- Models: plasticity (hardening curves, per temperature if given), orthotropic
  elasticity, hyperelasticity, anisotropic conductivity.
- Solver keywords: a solver's own material keywords, passed through (experts).
- Sources: where the values come from.
- Completeness: for every solver and analysis it runs, what the material still lacks
  and which of its models that solver would refuse; updated as you type.

Save keeps it in your materials (never in the shipped or imported library: a library
material you change is saved as your copy). New variant starts a material that
inherits everything from the chosen one. Add to project copies it into the project
(one command, as Add material does).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.materials.registry import BY_KEY, GROUPS, MODELS, PROPERTIES, completeness

KEY = Qt.ItemDataRole.UserRole


def _units():
    from puzzle.settings import current

    return current().display_units()


def _shown(value: float, unit: str) -> tuple[float, str]:
    from puzzle.model.units import shown

    return shown(value, unit, _units())


def _display_unit(kind: str | None, si: str) -> str:
    return _units().get(kind, si) if kind else si


def _number(text: str) -> float:
    return float(text.strip().replace(",", ""))


class Curve(QWidget):
    """A small plot of a table's points (x, y), for checking a curve at a glance."""

    def __init__(self) -> None:
        super().__init__()
        self.points: list[tuple[float, float]] = []
        self.setMinimumSize(220, 160)

    def set_points(self, points) -> None:
        self.points = [(x, y) for x, y in points if math.isfinite(x) and math.isfinite(y)]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#fbfcfd"))
        if len(self.points) < 2:
            p.setPen(QColor("#8a94a3"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Two points or more to plot")
            return
        xs, ys = [x for x, _ in self.points], [y for _, y in self.points]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if x1 == x0:
            x1 = x0 + 1
        if y1 == y0:
            y1 = y0 + 1
        m, w, h = 12, self.width() - 24, self.height() - 24

        def at(x, y):
            return QPointF(m + (x - x0) / (x1 - x0) * w, m + h - (y - y0) / (y1 - y0) * h)

        p.setPen(QPen(QColor("#b8c2cc"), 1))
        p.drawRect(m, m, w, h)
        p.setPen(QPen(QColor("#2b6cb0"), 2))
        pts = [at(x, y) for x, y in sorted(self.points)]
        for a, b in zip(pts, pts[1:], strict=False):
            p.drawLine(a, b)
        for q in pts:
            p.drawEllipse(q, 2.5, 2.5)


class TableEditor(QDialog):
    """Two columns of numbers (and the curve they make)."""

    def __init__(self, title: str, headers: tuple[str, str], rows, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.grid = QTableWidget(0, 2)
        self.grid.setHorizontalHeaderLabels(list(headers))
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for a, b in rows:
            self._row(a, b)
        self._row("", "")
        self.curve = Curve()
        self.grid.itemChanged.connect(self._changed)
        add = QPushButton("Add row")
        add.clicked.connect(lambda: self._row("", ""))
        remove = QPushButton("Remove row")
        remove.clicked.connect(lambda: self.grid.removeRow(max(self.grid.currentRow(), 0)))
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c53030")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addWidget(self.grid, 1)
        body.addWidget(self.curve, 1)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addLayout(row)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.result_rows: list[tuple[float, float]] | None = None
        self._changed()

    def _row(self, a, b) -> None:
        r = self.grid.rowCount()
        self.grid.insertRow(r)
        for c, v in enumerate((a, b)):
            self.grid.setItem(
                r, c, QTableWidgetItem(f"{v:.6g}" if isinstance(v, float) else str(v))
            )

    def rows(self) -> list[tuple[float, float]]:
        out = []
        for r in range(self.grid.rowCount()):
            cells = [self.grid.item(r, c) for c in (0, 1)]
            texts = [c.text().strip() if c else "" for c in cells]
            if not any(texts):
                continue
            out.append((_number(texts[0]), _number(texts[1])))
        return out

    def _changed(self, *_):
        try:
            self.curve.set_points(self.rows())
            self.error.setText("")
        except ValueError:
            self.error.setText("Every cell is a number")

    def _accept(self) -> None:
        try:
            self.result_rows = self.rows()
        except ValueError:
            self.error.setText("Every cell is a number")
            return
        self.accept()


class MaterialEditor(QWidget):
    """One material's fields; `material()` builds it back (raises ValueError when a
    field is wrong, with words saying which)."""

    def __init__(self, on_change) -> None:
        super().__init__()
        self.on_change = on_change
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.family = QLabel("")
        self.family.setStyleSheet("color: gray")
        head = QFormLayout()
        head.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        head.addRow("Name", self.name)
        head.addRow("Description", self.description)
        head.addRow("Family", self.family)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._properties_tab(), "Properties")
        self.tabs.addTab(self._models_tab(), "Models")
        self.keywords = QPlainTextEdit()
        self.tabs.addTab(self._keywords_tab(), "Solver keywords")
        self.sources = QPlainTextEdit()
        self.sources.setPlaceholderText("One source per line: a datasheet, a standard, a page")
        self.tabs.addTab(self.sources, "Sources")
        self.report = QTableWidget(0, 3)
        self.report.setHorizontalHeaderLabels(["Solver", "Analysis", "Status"])
        header = self.report.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.report.verticalHeader().hide()
        self.report.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabs.addTab(self.report, "Completeness")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(head)
        layout.addWidget(self.tabs, 1)
        for w in (self.name, self.description):
            w.textChanged.connect(lambda _: self.on_change())
        self.base = None  # the Material being edited

    # --- tabs -------------------------------------------------------------------------

    def _properties_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        self.values: dict[str, QLineEdit] = {}
        self.units: dict[str, QLabel] = {}
        self.table_buttons: dict[str, QPushButton] = {}
        self.tables: dict[str, dict] = {}
        for group in GROUPS:
            box = QGroupBox(group)
            form = QFormLayout(box)
            for p in [p for p in PROPERTIES if p.group == group]:
                line = QLineEdit()
                line.setPlaceholderText("not set")
                line.textChanged.connect(lambda _: self.on_change())
                unit = QLabel("")
                table = QPushButton("Table…")
                table.setToolTip("Values at several temperatures")
                table.clicked.connect(lambda _=False, k=p.key: self.edit_table(k))
                row = QHBoxLayout()
                row.addWidget(line, 1)
                row.addWidget(unit)
                row.addWidget(table)
                form.addRow(p.label, row)
                self.values[p.key], self.units[p.key], self.table_buttons[p.key] = line, unit, table
            column.addWidget(box)
        column.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _models_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        self.model_on: dict[str, QCheckBox] = {}
        # Plasticity: hardening curves (one per temperature, or one for all).
        self.curves: list[dict] = []
        plastic = QGroupBox()
        pl = QVBoxLayout(plastic)
        self.model_on["plasticity"] = QCheckBox(MODELS["plasticity"])
        pl.addWidget(self.model_on["plasticity"])
        self.curve_list = QLabel("No hardening curve yet")
        edit_curve = QPushButton("Hardening curve…")
        edit_curve.setToolTip(
            "True stress against equivalent plastic strain; the first row is the yield stress at 0"
        )
        edit_curve.clicked.connect(self.edit_curve)
        row = QHBoxLayout()
        row.addWidget(self.curve_list, 1)
        row.addWidget(edit_curve)
        pl.addLayout(row)
        column.addWidget(plastic)
        self.fields: dict[str, dict[str, QLineEdit]] = {}
        for model, names in (
            (
                "orthotropic_elasticity",
                ["e1", "e2", "e3", "nu12", "nu13", "nu23", "g12", "g13", "g23"],
            ),
            ("hyperelasticity", ["c10", "c01", "d1"]),
            ("anisotropic_conductivity", ["k1", "k2", "k3"]),
        ):
            box = QGroupBox()
            form = QFormLayout(box)
            self.model_on[model] = QCheckBox(MODELS[model])
            form.addRow(self.model_on[model])
            if model == "hyperelasticity":
                self.hyper_kind = QComboBox()
                self.hyper_kind.addItem("Neo-Hookean", "neo_hookean")
                self.hyper_kind.addItem("Mooney-Rivlin", "mooney_rivlin")
                self.hyper_kind.currentIndexChanged.connect(lambda _: self.on_change())
                form.addRow("Model", self.hyper_kind)
            self.fields[model] = {}
            for n in names:
                line = QLineEdit()
                line.textChanged.connect(lambda _: self.on_change())
                form.addRow(_field_label(n), line)
                self.fields[model][n] = line
            column.addWidget(box)
        for box in self.model_on.values():
            box.toggled.connect(lambda _: self.on_change())
        note = QLabel(
            "A solver that can't use a model refuses the study rather than ignoring it; the "
            "Completeness tab says which."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        column.addWidget(note)
        column.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _keywords_tab(self) -> QWidget:
        from puzzle.gui.choices import SOLVERS

        page = QWidget()
        column = QVBoxLayout(page)
        warn = QLabel(
            "For experts: the solver's own material keywords, written as given into that "
            "solver's input. A solver that can't place them refuses the study."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b7791f")
        self.keyword_solver = QComboBox()
        for key, name in SOLVERS.items():
            self.keyword_solver.addItem(name, key)
        self.keyword_texts: dict[str, str] = {}
        self.keyword_solver.currentIndexChanged.connect(lambda _: self._show_keywords())
        self.keywords.textChanged.connect(self._keep_keywords)
        column.addWidget(warn)
        column.addWidget(self.keyword_solver)
        column.addWidget(self.keywords, 1)
        return page

    def _show_keywords(self) -> None:
        self.keywords.blockSignals(True)
        self.keywords.setPlainText(self.keyword_texts.get(self.keyword_solver.currentData(), ""))
        self.keywords.blockSignals(False)

    def _keep_keywords(self) -> None:
        self.keyword_texts[self.keyword_solver.currentData()] = self.keywords.toPlainText()
        self.on_change()

    # --- loading and building -----------------------------------------------------------

    def load(self, material, family: str = "") -> None:
        """Show a Material (SI inside, display units shown)."""
        self.base = material
        widgets = [self.name, self.description, *self.values.values()]
        for w in widgets:
            w.blockSignals(True)
        self.name.setText(material.name)
        self.description.setText(material.description)
        self.family.setText(family or "(a family of its own)")
        for key, line in self.values.items():
            p = BY_KEY[key]
            unit = _display_unit(p.kind, p.unit)
            self.units[key].setText(_pretty(unit))
            q = material.properties.get(key)
            if q is None:
                line.setText("")
            else:
                v, _ = _shown(q.value, q.unit) if p.kind else (q.value, q.unit)
                line.setText(f"{v:.6g}")
        self.tables = {
            k: {
                "unit": t.unit,
                "points": list(t.curve()),
                "reference_temperature": t.reference_temperature,
            }
            for k, t in material.tables.items()
        }
        self._table_labels()
        m = material.models
        self.curves = (
            [{"temperature": c.temperature, "points": list(c.points)} for c in m.plasticity.curves]
            if m.plasticity
            else []
        )
        self.model_on["plasticity"].setChecked(m.plasticity is not None)
        for model, fields in self.fields.items():
            data = getattr(m, model)
            self.model_on[model].setChecked(data is not None)
            for n, line in fields.items():
                line.setText(f"{_to_display(n, getattr(data, n)):.6g}" if data is not None else "")
        if m.hyperelasticity is not None:
            self.hyper_kind.setCurrentIndex(self.hyper_kind.findData(m.hyperelasticity.model))
        self._curve_label()
        self.keyword_texts = dict(material.solver_keywords)
        self._show_keywords()
        self.sources.setPlainText("\n".join(material.sources))
        for w in widgets:
            w.blockSignals(False)
        self.on_change()

    def material(self):
        """The edited Material; ValueError (in words) when a field is wrong."""
        from puzzle.model.schema import (
            Material,
            MaterialModels,
            PropertyTable,
            Quantity,
            TablePoint,
        )
        from puzzle.model.units import convert

        properties = {}
        for key, line in self.values.items():
            text = line.text().strip()
            if not text:
                continue
            p = BY_KEY[key]
            try:
                value = _number(text)
            except ValueError:
                raise ValueError(f"{p.label}: '{text}' is not a number") from None
            unit = _display_unit(p.kind, p.unit)
            properties[key] = Quantity(value=convert(value, unit, p.unit), unit=p.unit)
        tables = {
            k: PropertyTable(
                unit=t["unit"],
                reference_temperature=t.get("reference_temperature"),
                points=[TablePoint(temperature=a, value=b) for a, b in t["points"]],
            )
            for k, t in self.tables.items()
            if t["points"]
        }
        models = {}
        if self.model_on["plasticity"].isChecked():
            if not self.curves:
                raise ValueError("Plasticity: give its hardening curve (Hardening curve…)")
            models["plasticity"] = {"curves": self.curves}
        for model, fields in self.fields.items():
            if not self.model_on[model].isChecked():
                continue
            data = {}
            for n, line in fields.items():
                text = line.text().strip()
                if not text and model == "hyperelasticity" and n in ("c01", "d1"):
                    continue
                try:
                    data[n] = _to_si(n, _number(text))
                except ValueError:
                    raise ValueError(
                        f"{MODELS[model]}: {_field_label(n)} is not a number"
                    ) from None
            if model == "hyperelasticity":
                data["model"] = self.hyper_kind.currentData()
            models[model] = data
        base = self.base
        try:
            return Material(
                name=self.name.text().strip() or "Material",
                description=self.description.text().strip(),
                info=dict(base.info) if base else {},
                properties=properties,
                tables=tables,
                sources=[s for s in self.sources.toPlainText().splitlines() if s.strip()],
                models=MaterialModels(**models),
                solver_keywords={k: v for k, v in self.keyword_texts.items() if v.strip()},
            )
        except Exception as exc:  # noqa: BLE001 - schema checks, in their own words
            raise ValueError(_first_error(exc)) from None

    # --- tables and curves ---------------------------------------------------------------

    def edit_table(self, key: str) -> None:
        p = BY_KEY[key]
        t = self.tables.get(key, {"unit": p.unit, "points": []})
        rows = [(a - 273.15, b) for a, b in t["points"]]
        rows = self.ask_table(
            f"{p.label} against temperature",
            ("Temperature (°C)", f"{p.label} ({_pretty(t['unit'])})"),
            rows,
        )
        if rows is None:
            return
        self.tables[key] = {**t, "points": [(a + 273.15, b) for a, b in rows]}
        self._table_labels()
        self.on_change()

    def edit_curve(self) -> None:
        from puzzle.model.units import convert

        unit = _display_unit("pressure", "Pa")
        current = self.curves[0]["points"] if self.curves else []
        rows = [(e, convert(s, "Pa", unit)) for e, s in current]
        rows = self.ask_table(
            "Hardening curve", ("Equivalent plastic strain", f"True stress ({_pretty(unit)})"), rows
        )
        if rows is None:
            return
        points = [(e, convert(s, unit, "Pa")) for e, s in rows]
        self.curves = [{"temperature": None, "points": points}] if points else []
        self.model_on["plasticity"].setChecked(bool(points))
        self._curve_label()
        self.on_change()

    def ask_table(self, title, headers, rows):
        """The table editor's rows, or None if cancelled (tests replace this)."""
        dialog = TableEditor(title, headers, rows, self)
        return dialog.result_rows if dialog.exec() else None

    def _table_labels(self) -> None:
        for key, button in self.table_buttons.items():
            n = len(self.tables.get(key, {}).get("points", []))
            button.setText(f"Table ({n})…" if n else "Table…")

    def _curve_label(self) -> None:
        from puzzle.model.units import convert

        if not self.curves:
            self.curve_list.setText("No hardening curve yet")
            return
        pts = self.curves[0]["points"]
        unit = _display_unit("pressure", "Pa")
        y = convert(pts[0][1], "Pa", unit)
        extra = f", {len(self.curves)} temperatures" if len(self.curves) > 1 else ""
        self.curve_list.setText(f"Yield {y:.4g} {_pretty(unit)}, {len(pts)} points{extra}")

    def show_report(self) -> str | None:
        """Fill the Completeness tab; returns the error that stops building, if any."""
        from puzzle.gui.choices import solver_label

        try:
            material = self.material()
        except ValueError as exc:
            self.report.setRowCount(0)
            return str(exc)
        rows = completeness(material)
        self.report.setRowCount(len(rows))
        for i, r in enumerate(rows):
            if r["refused"]:
                status = "Refuses " + ", ".join(MODELS[m].lower() for m in r["refused"])
            elif r["missing"]:
                status = "Needs " + ", ".join(BY_KEY[k].label.lower() for k in r["missing"])
            else:
                status = "Complete"
            for c, text in enumerate((solver_label(r["solver"]), r["analysis"], status)):
                item = QTableWidgetItem(text)
                if c == 2:
                    item.setForeground(QColor("#2f855a" if status == "Complete" else "#b7791f"))
                self.report.setItem(i, c, item)
        return None


class MaterialBuilder(QDialog):
    def __init__(self, window=None, parent=None) -> None:
        super().__init__(parent)
        from puzzle.materials.store import LibraryStore

        self.window = window
        self.store = LibraryStore()
        self.setWindowTitle("Material builder")
        self.resize(1100, 720)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the library")
        self.search.textChanged.connect(lambda _: self.list())
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(lambda *_: self._chosen())
        new, variant, delete, undo = (
            QPushButton("New"),
            QPushButton("New variant"),
            QPushButton("Remove mine"),
            QPushButton("Undo"),
        )
        variant.setToolTip("A material that inherits everything from the chosen one")
        new.clicked.connect(self.new)
        variant.clicked.connect(self.new_variant)
        delete.clicked.connect(self.remove)
        undo.clicked.connect(self.undo)
        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.tree, 1)
        buttons = QGridLayout()  # two by two: the labels stay whole
        for i, b in enumerate((new, variant, delete, undo)):
            buttons.addWidget(b, i // 2, i % 2)
        left.addLayout(buttons)
        self.editor = MaterialEditor(self._edited)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        save, add, close = (
            QPushButton("Save to my materials"),
            QPushButton("Add to project"),
            QPushButton("Close"),
        )
        save.clicked.connect(self.save)
        add.clicked.connect(self.add_to_project)
        add.setEnabled(window is not None and getattr(window, "session", None) is not None)
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        for b in (save, add, close):
            bottom.addWidget(b)
        right = QVBoxLayout()
        right.addWidget(self.editor, 1)
        body = QHBoxLayout()
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(320)
        body.addWidget(lw)
        body.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(bottom)
        self.key: str | None = None  # the library key being edited (None: new)
        self.parent_key: str | None = None
        self.list()

    # --- the library ---------------------------------------------------------------------

    def list(self, select: str | None = None) -> None:
        from puzzle.materials import _library, families, search_library

        lib, mine = _library(), set(self.store.entries())
        wanted = set(search_library(self.search.text())) if self.search.text() else set(lib)
        self.tree.blockSignals(True)
        self.tree.clear()
        chosen = None
        for family, variants in families().items():
            keys = [k for k in [family, *variants] if k in wanted]
            if not keys:
                continue
            top = QTreeWidgetItem(self.tree, [self._title(family, lib, mine)])
            top.setData(0, KEY, family)
            for v in variants:
                if v in wanted or family in wanted:
                    item = QTreeWidgetItem(top, [self._title(v, lib, mine)])
                    item.setData(0, KEY, v)
                    if v == select:
                        chosen = item
            if family == select:
                chosen = top
            top.setExpanded(bool(variants) and bool(self.search.text()) or family == select)
        self.tree.blockSignals(False)
        if chosen is not None:
            self.tree.setCurrentItem(chosen)

    @staticmethod
    def _title(key, lib, mine) -> str:
        name = lib[key]["name"]
        return f"{name}  (mine)" if key in mine else name

    def _chosen(self) -> None:
        from puzzle.materials import _library, library_material

        item = self.tree.currentItem()
        if item is None:
            return
        self.key = item.data(0, KEY)
        self.parent_key = _library()[self.key].get("parent")
        family = _library()[self.parent_key]["name"] if self.parent_key else ""
        self.editor.load(library_material(self.key), f"a variant of {family}" if family else "")

    def _edited(self) -> None:
        problem = self.editor.show_report()
        self.status.setText(problem or "")
        self.status.setStyleSheet("color: #c53030" if problem else "")

    # --- actions -------------------------------------------------------------------------

    def new(self) -> None:
        from puzzle.model.schema import Material

        self.key, self.parent_key = None, None
        self.tree.clearSelection()
        self.editor.load(Material(name="New material"))

    def new_variant(self) -> None:
        from puzzle.materials import library_material

        if self.key is None:
            self.status.setText("Choose the material to make a variant of")
            return
        base = library_material(self.key)
        base.name = f"{base.name} (variant)"
        family = self.parent_key or self.key
        self.key, self.parent_key = None, family
        self.editor.load(base, f"a variant of {library_material(family).name}")

    def save(self) -> str | None:
        """Into your materials: a new key from the name, or the key being edited (a
        library material edited becomes your copy). Returns the key saved."""
        from puzzle.errors import PuzzleError
        from puzzle.materials.store import entry_of, key_for

        try:
            material = self.editor.material()
        except ValueError as exc:
            self.status.setText(str(exc))
            return None
        key = self.key or self._free_key(key_for(material.name))
        entry = entry_of(material, self.parent_key)
        if self.parent_key:  # a variant keeps only what differs from its family
            entry = _differences(entry, self.parent_key)
        try:
            self.store.save(key, entry)
        except PuzzleError as exc:
            self.status.setText(exc.message)
            return None
        self.key = key
        self.status.setText(f"Saved '{material.name}' to your materials")
        self.list(select=key)
        return key

    def _free_key(self, base: str) -> str:
        from puzzle.materials import _library

        key, n = base, 2
        while key in _library():
            key, n = f"{base}_{n}", n + 1
        return key

    def remove(self) -> None:
        if self.key is None or self.key not in self.store.entries():
            self.status.setText("Only your own materials can be removed")
            return
        self.store.remove(self.key)
        self.status.setText("Removed (Undo brings it back)")
        self.list(select=self.key)

    def undo(self) -> None:
        key = self.store.undo()
        self.status.setText("Undone" if key else "Nothing to undo")
        self.list(select=key)

    def add_to_project(self) -> None:
        """A copy in the project, as one command."""
        from puzzle.commands.model import Create

        if self.window is None or self.window.session is None:
            return
        try:
            material = self.editor.material()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        data = material.model_dump(mode="json", exclude={"id"})
        if self.window.submit(Create(kind="material", data=data)) is not None:
            self.status.setText(f"Added '{material.name}' to the project")


def _differences(entry: dict, parent: str) -> dict:
    """A variant's entry: only what differs from its family (it inherits the rest)."""
    from puzzle.materials import library_entry

    family = library_entry(parent)
    out = {"name": entry["name"], "parent": parent}
    for part in ("properties", "tables", "info", "models", "solver_keywords"):
        changed = {k: v for k, v in entry.get(part, {}).items() if family.get(part, {}).get(k) != v}
        if changed:
            out[part] = changed
    if entry.get("description") and entry["description"] != family.get("description"):
        out["description"] = entry["description"]
    if entry.get("sources") and entry["sources"] != family.get("sources"):
        out["sources"] = entry["sources"]
    return out


FIELD_LABELS = {
    "e1": "E1",
    "e2": "E2",
    "e3": "E3",
    "nu12": "ν12",
    "nu13": "ν13",
    "nu23": "ν23",
    "g12": "G12",
    "g13": "G13",
    "g23": "G23",
    "c10": "C10",
    "c01": "C01",
    "d1": "D1 (1/Pa; 0 incompressible)",
    "k1": "k1",
    "k2": "k2",
    "k3": "k3",
}
# Model fields in a kind of unit (shown in the display units; stored in SI).
FIELD_KINDS = {
    **dict.fromkeys(["e1", "e2", "e3", "g12", "g13", "g23", "c10", "c01"], ("pressure", "Pa")),
    **dict.fromkeys(["k1", "k2", "k3"], ("conductivity", "W/(m*K)")),
}


def _field_label(name: str) -> str:
    label = FIELD_LABELS.get(name, name)
    if name in FIELD_KINDS:
        kind, si = FIELD_KINDS[name]
        label += f" ({_pretty(_display_unit(kind, si))})"
    return label


def _to_display(name: str, value: float) -> float:
    from puzzle.model.units import convert

    if name not in FIELD_KINDS:
        return value
    kind, si = FIELD_KINDS[name]
    return convert(value, si, _display_unit(kind, si))


def _to_si(name: str, value: float) -> float:
    from puzzle.model.units import convert

    if name not in FIELD_KINDS:
        return value
    kind, si = FIELD_KINDS[name]
    return convert(value, _display_unit(kind, si), si)


def _pretty(unit: str) -> str:
    from puzzle.model.units import pretty

    return "" if unit == "dimensionless" else pretty(unit)


def _first_error(exc) -> str:
    """A schema error's first message, without Pydantic's framing."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        e = errors()[0]
        return str(e.get("msg", exc)).removeprefix("Value error, ")
    return str(exc)
