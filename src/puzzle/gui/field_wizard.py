"""The Import field wizard (ADR 0044): File, Alignment, Verification, Name.

Nothing changes in the project until Finish, which copies the file in and makes the
imported field with one command (one undo). The verification page meshes the chosen
target in the background, as a study would, and shows the check's report.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from puzzle.gui.theme import role

ROLES = [
    ("X coordinate", "x"),
    ("Y coordinate", "y"),
    ("Z coordinate", "z"),
    ("A value", "value"),
    ("Ignored", "ignore"),
]
LENGTHS = [("Metres", "m"), ("Millimetres", "mm"), ("Centimetres", "cm"), ("Inches", "in"),
           ("Feet", "ft")]  # fmt: skip


def _combo(pairs, value) -> QComboBox:
    box = QComboBox()
    for text, v in pairs:
        box.addItem(text, v)
    found = box.findData(value)
    box.setCurrentIndex(max(found, 0))
    return box


def _triple(values=(0.0, 0.0, 0.0)) -> tuple[QHBoxLayout, list[QLineEdit]]:
    row = QHBoxLayout()
    edits = []
    for v in values:
        e = QLineEdit(f"{v:g}")
        e.setMaximumWidth(90)
        row.addWidget(e)
        edits.append(e)
    return row, edits


class FilePage(QWizardPage):
    """The file and how it is read: each column's role and unit."""

    def __init__(self, wizard) -> None:
        super().__init__()
        self.setTitle("File")
        self.setSubTitle(
            "Points and values: CSV, TXT or DAT (Puzzle's point clouds, Ansys External Data, "
            "test data), or VTU. Give each column its role and unit."
        )
        self.wizard_ = wizard
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self.choose)
        top = QHBoxLayout()
        top.addWidget(self.path, 1)
        top.addWidget(browse)
        self.length = _combo(LENGTHS, "m")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Column", "Role", "Unit"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.note = QLabel("")
        role(self.note, "muted")
        form = QFormLayout()
        form.addRow("Points in", self.length)
        column = QVBoxLayout(self)
        column.addLayout(top)
        column.addLayout(form)
        column.addWidget(self.table, 1)
        column.addWidget(self.note)
        self.found: dict | None = None

    def choose(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Import field", "", "Fields (*.csv *.txt *.dat *.vtu *.vtk);;All files (*)"
        )
        if chosen:
            self.load(Path(chosen))

    def load(self, path: Path) -> None:
        """Read a file's likely layout into the page (tests call this directly)."""
        from puzzle.fields import sniff

        try:
            self.found = sniff(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user as is
            self.found = None
            self.note.setText(f"Can't read it: {exc}")
            self.completeChanged.emit()
            return
        self.path.setText(str(path))
        self.length.setCurrentIndex(max(self.length.findData(self.found["length_unit"]), 0))
        self.table.setRowCount(len(self.found["columns"]))
        for i, c in enumerate(self.found["columns"]):
            name = QTableWidgetItem(c["name"])
            self.table.setItem(i, 0, name)
            self.table.setCellWidget(i, 1, _combo(ROLES, c["role"]))
            self.table.setCellWidget(i, 2, QLineEdit(c["unit"]))
        self.note.setText(f"{len(self.found['columns'])} columns read from {path.name}")
        self.completeChanged.emit()

    def columns(self) -> list[dict]:
        out = []
        for i in range(self.table.rowCount()):
            out.append({
                "name": self.table.item(i, 0).text(),
                "role": self.table.cellWidget(i, 1).currentData(),
                "unit": self.table.cellWidget(i, 2).text().strip(),
            })  # fmt: skip
        return out

    def isComplete(self) -> bool:  # noqa: N802 - Qt's name
        roles = [c["role"] for c in self.columns()] if self.found else []
        return bool(self.found) and {"x", "y", "z"} <= set(roles) and "value" in roles


class AlignPage(QWizardPage):
    """Where the file's points go on the model."""

    def __init__(self, wizard) -> None:
        super().__init__()
        self.setTitle("Alignment")
        self.setSubTitle(
            "Rotated about a point (degrees about x, then y, then z), then moved, both in "
            "metres; or given in one of the project's coordinate systems."
        )
        from puzzle.model.schema import CoordinateSystem

        systems = [("None (global axes)", None)] + [
            (cs.name, str(cs.id)) for cs in wizard.window.session.project.of_type(CoordinateSystem)
        ]
        self.axes = _combo(systems, None)
        form = QFormLayout(self)
        row, self.move = _triple()
        form.addRow("Move by (m)", row)
        row, self.turn = _triple()
        form.addRow("Rotate (degrees)", row)
        row, self.about = _triple()
        form.addRow("About the point (m)", row)
        form.addRow("In the axes of", self.axes)
        from puzzle.gui.field_align_panel import AlignHelpers

        self.helpers = AlignHelpers(wizard, self)  # point pairs, boxes, a best fit
        form.addRow(self.helpers)

    def initializePage(self) -> None:  # noqa: N802 - Qt's name
        self.helpers.show_target()  # the target, and the points drawn over it

    def placement(self) -> dict:
        def read(edits):
            return tuple(float(e.text() or 0) for e in edits)

        out = {"translation": read(self.move), "rotation": read(self.turn),
               "about": read(self.about)}  # fmt: skip
        if self.axes.currentData():
            out["axes"] = self.axes.currentData()
        return out


class CheckPage(QWizardPage):
    """The check's report on a target the field will be used on."""

    def __init__(self, wizard) -> None:
        super().__init__()
        self.setTitle("Verification")
        self.setSubTitle(
            "How the data lands on the model: point count and ranges, and with a target, "
            "the share of it near the data. Check before using it."
        )
        self.wizard_ = wizard
        from puzzle.model.schema import Component, Geometry

        project = wizard.window.session.project
        targets = [("No target (the data only)", None)]
        for c in project.of_type(Component):
            targets.append((c.name, c.name))
            for part in c.parts:
                geo = project.objects.get(part.geometry)
                for s in geo.selections if isinstance(geo, Geometry) else []:
                    targets.append((f"{c.name}: {part.name}/{s.name}",
                                    f"{c.name}/{part.name}/{s.name}"))  # fmt: skip
        self.target = _combo(targets, None)
        from puzzle.gui.field_align_panel import VerifyView

        self.view = VerifyView(wizard)  # the field mapped and coloured on the target
        self.run = QPushButton("Check")
        self.run.clicked.connect(self.check)
        self.report = QLabel("Not checked yet.")
        self.report.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.target, 1)
        row.addWidget(self.run)
        column = QVBoxLayout(self)
        column.addLayout(row)
        column.addWidget(self.view)
        column.addWidget(self.report, 1)
        self.last: dict | None = None

    def initializePage(self) -> None:  # noqa: N802 - Qt's name
        self.view.columns()
        align = self.wizard_.align.helpers.target.currentData()
        if align and self.target.currentData() is None:  # the one aligned on
            self.target.setCurrentIndex(max(self.target.findData(align), 0))

    def check(self) -> None:
        from puzzle.fields import check
        from puzzle.gui.field_align_panel import with_target

        f, folder = self.wizard_.draft()
        window = self.wizard_.window
        where = self.target.currentData()
        self.report.setText("Checking…")

        def run(mesh, selection) -> None:
            from puzzle.field_display import selection_nodes

            targets = selection_nodes(mesh, selection)[1] if mesh is not None else None

            def work(emit):
                return check(window.session.project, folder, f, targets)

            def failed(exc) -> None:
                self.report.setText(str(exc))

            window.background("Checking the field", work, lambda r: self.show_report(r, where),
                              failed=failed, lock=False)  # fmt: skip

        # The target meshed as a study would, once per component (the window's mesh
        # when it has one), and kept for the alignment helpers and the picture.
        with_target(self.wizard_, where, run, failed=lambda exc: self.report.setText(str(exc)))

    def show_report(self, report: dict, where: str | None = None) -> None:
        self.last = report
        if where:
            self.view.draw(where)  # the field mapped and coloured on the target
        lines = [f"{report['points']} points" + (" (sparse)" if report["sparse"] else "")]
        for name, c in report["columns"].items():
            lines.append(
                f"{name}: {c['min']:.6g} to {c['max']:.6g}, mean {c['mean']:.6g} {c['unit']}"
            )
        if "coverage" in report:
            lines.append(f"{report['coverage']:.1%} of the target within "
                         f"{report['tolerance'] * 1e3:.3g} mm of a data point")  # fmt: skip
        lines += [f"Warning: {w}" for w in report["warnings"]]
        self.report.setText("\n".join(lines))


class NamePage(QWizardPage):
    def __init__(self, wizard) -> None:
        super().__init__()
        self.setTitle("Name")
        self.setSubTitle("Finish copies the file into the project and adds the field.")
        self.name = QLineEdit("")
        form = QFormLayout(self)
        form.addRow("Name", self.name)

    def initializePage(self) -> None:  # noqa: N802 - Qt's name
        if not self.name.text():
            self.name.setText(Path(self.wizard().file.path.text()).stem or "Imported field")


class FieldWizard(QWizard):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Import field")
        self.file = FilePage(self)
        self.align = AlignPage(self)
        self.verify = CheckPage(self)
        self.naming = NamePage(self)
        for page in (self.file, self.align, self.verify, self.naming):
            self.addPage(page)

    def draft(self):
        """The field as it stands, reading the chosen file where it is (nothing copied)."""
        from puzzle.model.schema import FieldColumn, FieldPlacement, ImportedField

        path = Path(self.file.path.text())
        found = self.file.found or {}
        f = ImportedField(
            name=self.naming.name.text() or path.stem,
            file=path.name,
            format=found.get("format", "csv"),
            delimiter=found.get("delimiter"),
            skip_rows=found.get("skip_rows", 0),
            header=found.get("header", True),
            columns=[FieldColumn(**c) for c in self.file.columns()],
            length_unit=self.file.length.currentData(),
            placement=FieldPlacement(**self.align.placement()),
        )
        return f, path.parent

    def finish(self):
        """The Create command's outcome (the file copied into the project first)."""
        from puzzle.commands.model import Create
        from puzzle.fields import import_file

        f, _ = self.draft()
        rel, digest = import_file(self.window.session.root, Path(self.file.path.text()))
        data = f.model_dump(mode="json", exclude={"id", "kind"})
        data.update(file=rel, file_hash=digest)
        return self.window.submit(Create(kind="field", data=data))


def import_field(window):
    """File → Import field…: the wizard, then one command on Finish. Not modal: its
    alignment page picks points in the main view and draws the data over the model."""
    wizard = FieldWizard(window)
    wizard.setModal(False)
    wizard.accepted.connect(wizard.finish)
    wizard.finished.connect(lambda _result: window._selected())  # the view as it was
    window._field_wizard = wizard  # kept while open
    wizard.show()
    return wizard
