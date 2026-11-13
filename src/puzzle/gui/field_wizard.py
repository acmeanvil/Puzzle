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
    ("Time (rows grouped by it)", "time"),
    ("Frequency (rows grouped by it, for harmonic steps)", "frequency"),
    ("Ignored", "ignore"),
]
TIMING = [
    ("Holds still", "still"),
    ("Varies in time: every step of the source", "every_step"),
    ("Varies in time: one file per time", "files"),
    ("Varies with frequency: every step of the source", "every_step_frequency"),
    ("Varies with frequency: one file per frequency", "files_frequency"),
]
FILTER = ("Fields (*.csv *.txt *.dat *.vtu *.vtk *.prof *.exo *.e *.case);;"
          "All files (*)")  # fmt: skip
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
            "test data), VTU, a Fluent profile, Exodus II, EnSight Gold, or an OpenFOAM case "
            "folder. Give each column its role and unit."
        )
        self.wizard_ = wizard
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self.choose)
        folder = QPushButton("OpenFOAM case…")
        folder.clicked.connect(self.choose_case)
        top = QHBoxLayout()
        top.addWidget(self.path, 1)
        top.addWidget(browse)
        top.addWidget(folder)
        self.length = _combo(LENGTHS, "m")
        self.region = _combo([("The whole source", "")], "")
        self.step = _combo([("The last", "")], "")
        self.timing = _combo(TIMING, "still")
        self.timing.currentIndexChanged.connect(lambda _i: self._timing_changed())
        self.more = QLineEdit()
        self.more.setReadOnly(True)
        self.more.setPlaceholderText("more files, one per time")
        add = QPushButton("Add files…")
        add.clicked.connect(self.choose_more)
        more_row = QHBoxLayout()
        more_row.addWidget(self.more, 1)
        more_row.addWidget(add)
        self.times = QLineEdit()
        self.times.setPlaceholderText("seconds (hertz with frequency), one per file (or per "
                                      "step), e.g. 0, 5, 10")  # fmt: skip
        self.extra: list[Path] = []
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Column", "Role", "Unit"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.note = QLabel("")
        role(self.note, "muted")
        form = QFormLayout()
        form.addRow("Points in", self.length)
        form.addRow("Region", self.region)
        form.addRow("Step", self.step)
        form.addRow("In time", self.timing)
        form.addRow("Files", more_row)
        form.addRow("Times (s) or frequencies (Hz)", self.times)
        column = QVBoxLayout(self)
        column.addLayout(top)
        column.addLayout(form)
        column.addWidget(self.table, 1)
        column.addWidget(self.note)
        self.found: dict | None = None

    def choose(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Import field", "", FILTER)
        if chosen:
            self.load(Path(chosen))

    def choose_case(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "OpenFOAM case folder")
        if chosen:
            self.load(Path(chosen))

    def choose_more(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "More files, one per time", "", FILTER)
        if chosen:
            self.set_more([Path(c) for c in chosen])

    def set_more(self, paths: list[Path]) -> None:
        """The files after the first, one per time (tests call this directly)."""
        self.extra = list(paths)
        self.more.setText(", ".join(p.name for p in self.extra))
        if self.timing.currentData() != "files_frequency":  # one per frequency stays so
            self.timing.setCurrentIndex(self.timing.findData("files"))

    def _timing_changed(self) -> None:
        how = self.timing.currentData()
        self.step.setEnabled(how == "still")
        self.more.setEnabled(how in ("files", "files_frequency"))
        self.times.setEnabled(how != "still")

    def timing_data(self) -> dict:
        """What the field's times come from: still, every step, or files with times;
        `axis` time, or frequency (the times are then hertz)."""
        how = self.timing.currentData()
        text = self.times.text().replace(";", ",").replace(" ", ",")
        times = [float(t) for t in text.split(",") if t.strip()] or None
        axis = "frequency" if how.endswith("_frequency") else "time"
        how = how.removesuffix("_frequency")
        return {"how": how, "times": times, "files": self.extra, "axis": axis}

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
        whole = {"exodus": "Every node or element", "ensight": "The volume parts",
                 "openfoam": "The cells", "fluent_profile": "Every profile"}  # fmt: skip
        regions = [(whole.get(self.found["format"], "The whole file"), "")]
        regions += [(r, r) for r in self.found.get("regions", [])]
        steps = [("The last", "")] + [(f"{s['index']}: {s['time']:g}", s["index"])
                                      for s in self.found.get("steps", [])]  # fmt: skip
        for box, pairs in ((self.region, regions), (self.step, steps)):
            box.clear()
            for text, value in pairs:
                box.addItem(text, value)
        multi = len(self.found.get("steps", [])) > 1
        self.timing.setCurrentIndex(self.timing.findData("still"))
        self._timing_changed()
        notes = "; ".join(self.found.get("notes", []))
        self.note.setText(f"{len(self.found['columns'])} columns read from {path.name}"
                          + (f", {len(self.found['steps'])} steps" if multi else "")
                          + (f" ({notes})" if notes else ""))  # fmt: skip
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
        if not self.found:
            return False
        # A table names its coordinate columns; the other formats carry their points.
        placed = self.found["format"] != "csv" or {"x", "y", "z"} <= set(roles)
        return placed and "value" in roles


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
        # Windows' default Aero style paints its own white pages over the theme's
        # palette (I6: light in dark mode); Modern follows the palette everywhere.
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
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
        from puzzle.model.schema import FieldColumn, FieldPlacement, FieldTime, ImportedField

        path = Path(self.file.path.text())
        found = self.file.found or {}
        timing = self.file.timing_data()
        times = []
        if timing["how"] == "files":
            files = [path, *timing["files"]]
            given = timing["times"] or []
            if len(given) != len(files):
                raise ValueError(f"{len(files)} files need {len(files)} times or frequencies")
            times = [FieldTime(time=t, file=str(f)) for t, f in zip(given, files, strict=True)]
        elif timing["how"] == "every_step":
            steps = found.get("steps", [])
            given = timing["times"] or [s["time"] for s in steps]
            if len(given) != len(steps):
                raise ValueError(f"the source has {len(steps)} steps; give as many times")
            times = [FieldTime(time=t, index=s["index"]) for t, s in zip(given, steps,
                     strict=True)]  # fmt: skip
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
            region=self.file.region.currentData() or "",
            index=self.file.step.currentData() or "" if timing["how"] == "still" else "",
            times=times,
            axis=timing["axis"] if times else "time",
        )
        return f, path.parent

    def finish(self):
        """The Create command's outcome (the file copied into the project first)."""
        from puzzle.commands.model import Create
        from puzzle.fields import import_file

        f, _ = self.draft()
        root = self.window.session.root
        rel, digest = import_file(root, Path(self.file.path.text()))
        data = f.model_dump(mode="json", exclude={"id", "kind"})
        data.update(file=rel, file_hash=digest)
        for t in data["times"]:  # one file per time: each copied in too
            if t["file"]:
                t["file"], t["file_hash"] = import_file(root, Path(t["file"]))
        if not data["times"]:
            data.pop("times")
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
