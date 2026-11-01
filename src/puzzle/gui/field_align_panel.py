"""The Import field wizard's alignment helpers and its picture of the mapping (ADR 0044
decisions 2.2 and 2.3).

`AlignHelpers` sits on the Alignment page: a target, point pairs (typed, or picked in
the view: a data point, then where it belongs on the model), and buttons that fit the
pairs, match the bounding boxes, or best-fit the points onto the target's faces
(`puzzle.field_align`). Each sets the page's move and rotation, and the data's points
are drawn over the model as they move.

`VerifyView` sits on the Verification page: the chosen column mapped and coloured on
the target with the legend, extrapolated areas in the Connections colour.

The target is meshed as a study would mesh it, once per component, in the background
(`with_target`); a component already meshed in the window is used as it is.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role


def targets(project) -> list[tuple[str, str | None]]:
    """What a field can be checked or fitted on: components and their selections."""
    from puzzle.model.schema import Component, Geometry

    out: list[tuple[str, str | None]] = [("No target (the data only)", None)]
    for c in project.of_type(Component):
        out.append((c.name, c.name))
        for part in c.parts:
            geo = project.objects.get(part.geometry)
            for s in geo.selections if isinstance(geo, Geometry) else []:
                out.append((f"{c.name}: {part.name}/{s.name}", f"{c.name}/{part.name}/{s.name}"))
    return out


def _component(project, where: str):
    from puzzle.model.schema import Component

    name = where.partition("/")[0]
    return next((c for c in project.of_type(Component) if c.name == name), None)


def with_target(wizard, where: str | None, then, failed=None) -> None:
    """`then(mesh, selection)` with the target's mesh: the window's if the component is
    meshed, else meshed once in the background and kept on the wizard. `then(None,
    None)` for no target."""
    if not where:
        then(None, None)
        return
    window = wizard.window
    comp = _component(window.session.project, where)
    if comp is None:
        then(None, None)
        return
    selection = where.partition("/")[2] or None
    cid = str(comp.id)
    cache = wizard.__dict__.setdefault("target_meshes", {})
    mesh = window.meshes.get(cid) or cache.get(cid)
    if mesh is not None:
        then(mesh, selection)
        return
    import copy

    from puzzle.project.layout import WORK_DIR

    project = copy.deepcopy(window.session.project)
    root = window.session.root

    def work(_emit):
        from puzzle.mesh import generate_mesh

        return generate_mesh(project, comp.id, root, root / WORK_DIR / "gui-field" / cid).mesh

    def done(mesh) -> None:
        cache[cid] = mesh
        then(mesh, selection)

    window.background(f"Meshing {comp.name} for the field", work, done, failed=failed,
                      lock=False)  # fmt: skip


def target_surface(mesh, selection: str | None):
    """The target's faces as a triangulated pyvista surface: a face selection's, or a
    body's (or the whole model's) outer faces."""
    from puzzle.gui.viewport import entity_surface, selection_surface

    group = mesh.groups.get(selection) if selection else None
    if group is not None and group.dim == 2:
        surface = selection_surface(mesh, selection)
    else:
        surface, _ = entity_surface(mesh)
        if surface is not None and group is not None:
            order = np.argsort(mesh.node_ids)
            mine = np.zeros(surface.n_points, bool)
            mine[order[np.searchsorted(mesh.node_ids, group.node_ids, sorter=order)]] = True
            corners = surface.faces.reshape(-1, 4)[:, 1:] if surface.is_all_triangles else None
            if corners is not None:
                surface = surface.extract_cells(np.flatnonzero(mine[corners].all(axis=1)))
    if surface is None:
        return None
    return surface.extract_surface().triangulate().clean()


class AlignHelpers(QWidget):
    """The alignment helpers (decision 2.2), under the page's own placement fields."""

    def __init__(self, wizard, page) -> None:
        super().__init__()
        self.wizard_ = wizard
        self.page = page
        from puzzle.settings import length_unit

        self.unit, self.per_metre = length_unit()
        self.target = QComboBox()
        for text, value in targets(wizard.window.session.project):
            self.target.addItem(text, value)
        self.pairs = QTableWidget(0, 6)
        self.pairs.setHorizontalHeaderLabels(
            [f"Data {a} ({self.unit})" for a in "XYZ"] + [f"Model {a} ({self.unit})" for a in "XYZ"]
        )
        self.pairs.setMinimumHeight(110)
        add = QPushButton("Add a pair")
        add.clicked.connect(lambda: self.add_pair())
        self.pick = QPushButton("Pick a pair in the view")
        self.pick.setToolTip("Double-click a data point in the view, then where it belongs")
        self.pick.clicked.connect(self.pick_pair)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_pair)
        self.fit_pairs = QPushButton("Fit the pairs")
        self.fit_pairs.clicked.connect(self.from_pairs)
        self.boxes = QPushButton("Match bounding boxes")
        self.boxes.clicked.connect(self.match_boxes)
        self.order = QCheckBox("Line up the longest sides")
        self.fit = QPushButton("Best fit onto the target")
        self.fit.setToolTip("Moves the points onto the target's faces from where they are now")
        self.fit.clicked.connect(self.best_fit)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        role(self.result, "muted")
        grid = QGridLayout()
        grid.addWidget(QLabel("Target"), 0, 0)
        grid.addWidget(self.target, 0, 1, 1, 3)
        grid.addWidget(self.boxes, 1, 0)
        grid.addWidget(self.order, 1, 1)
        grid.addWidget(self.fit, 1, 2, 1, 2)
        buttons = QHBoxLayout()
        for b in (add, self.pick, remove, self.fit_pairs):
            buttons.addWidget(b)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 6, 0, 0)
        column.addLayout(grid)
        column.addWidget(QLabel("Point pairs: a data point and where it belongs on the model"))
        column.addWidget(self.pairs)
        column.addLayout(buttons)
        column.addWidget(self.result)
        for edit in (*page.move, *page.turn, *page.about):
            edit.editingFinished.connect(self.redraw)
        page.axes.currentIndexChanged.connect(lambda _i: self.redraw())
        self.target.currentIndexChanged.connect(lambda _i: self.show_target())
        self._data: tuple | None = None  # (key, base points, values, unit)
        self._picked: np.ndarray | None = None

    # --- the data ----------------------------------------------------------------

    def data(self):
        """The field's base points (m, before its rotation and move), its first value
        column and that column's SI unit; cached while the file and reading stay."""
        from puzzle.field_align import base_points
        from puzzle.fields import cloud
        from puzzle.model.schema import FieldPlacement

        f, folder = self.wizard_.draft()
        key = (str(folder / f.file), f.length_unit, f.placement.axes,
               tuple((c.name, c.role, c.unit) for c in f.columns))  # fmt: skip
        if self._data is None or self._data[0] != key:
            project = self.wizard_.window.session.project
            base = base_points(project, folder, f)
            c = cloud(project, folder, f.model_copy(update={
                "placement": FieldPlacement(axes=f.placement.axes)}))  # fmt: skip
            column = next(iter(c.values), None)
            values = c.values.get(column) if column else None
            self._data = (key, base, values, c.units.get(column, "") if column else "")
        return self._data[1], self._data[2], self._data[3]

    def placed(self) -> np.ndarray:
        from puzzle.field_align import apply, transform_of
        from puzzle.model.schema import FieldPlacement

        base, _, _ = self.data()
        return apply(base, *transform_of(FieldPlacement(**self.page.placement())))

    # --- the view --------------------------------------------------------------------

    def show_target(self) -> None:
        """The target's component in the view (its selection highlighted), the points
        over it."""
        from puzzle.gui.field_view import _shown_mesh

        where = self.target.currentData()
        window = self.wizard_.window
        if where:
            comp = _component(window.session.project, where)
            if comp is not None:
                _shown_mesh(window, comp, where.partition("/")[2] or None)
        self.redraw()

    def redraw(self) -> None:
        """The data's points as placed now, over the model (decision 2.2's last bullet)."""
        if not self.wizard_.file.found:
            return
        try:
            points = self.placed()
            _, values, unit = self.data()
        except Exception as exc:  # noqa: BLE001 - a half-typed field: say so, draw nothing
            self.result.setText(f"Can't place the points yet: {exc}")
            return
        self.wizard_.window.viewport.show_field_points(points, values, unit)

    # --- setting the placement -------------------------------------------------------

    def set_placement(self, placement) -> None:
        for edits, values in ((self.page.move, placement.translation),
                              (self.page.turn, placement.rotation),
                              (self.page.about, placement.about)):  # fmt: skip
            for e, v in zip(edits, values, strict=True):
                e.setText(f"{v:.12g}")
        self.redraw()

    def _current(self):
        from puzzle.model.schema import FieldPlacement

        return FieldPlacement(**self.page.placement())

    def _say(self, text: str) -> None:
        self.result.setText(text)

    def _length(self, metres: float) -> str:
        return f"{metres * self.per_metre:.4g} {self.unit}"

    # --- point pairs ---------------------------------------------------------------

    def add_pair(self, data=(0.0, 0.0, 0.0), model=(0.0, 0.0, 0.0)) -> None:
        """A row: a data point and a model point, both in metres, shown in the display
        length unit."""
        row = self.pairs.rowCount()
        self.pairs.insertRow(row)
        for k, v in enumerate((*data, *model)):
            self.pairs.setItem(row, k, QTableWidgetItem(f"{v * self.per_metre:.10g}"))

    def remove_pair(self) -> None:
        rows = sorted({i.row() for i in self.pairs.selectedIndexes()}, reverse=True)
        for row in rows or [self.pairs.rowCount() - 1]:
            if row >= 0:
                self.pairs.removeRow(row)

    def pair_points(self) -> tuple[np.ndarray, np.ndarray]:
        out = []
        for row in range(self.pairs.rowCount()):
            cells = [self.pairs.item(row, k) for k in range(6)]
            out.append([float(c.text()) / self.per_metre if c and c.text() else 0.0
                        for c in cells])  # fmt: skip
        table = np.array(out, float).reshape(-1, 6)
        return table[:, :3], table[:, 3:]

    def pick_pair(self) -> None:
        """Double-click a data point (the nearest drawn point is taken), then the spot
        on the model (snapped to a node when meshed)."""
        self.redraw()
        self._say("Double-click a data point in the view…")
        self.wizard_.window.viewport.capture_point(self._data_picked, snap="none")

    def _data_picked(self, xyz) -> None:
        placed = self.placed()
        i = int(np.argmin(np.linalg.norm(placed - np.asarray(xyz), axis=1)))
        base, _, _ = self.data()
        self._picked = base[i]
        self._say("…now double-click where that point belongs on the model.")
        self.wizard_.window.viewport.capture_point(self._model_picked, snap="node")

    def _model_picked(self, xyz) -> None:
        if self._picked is not None:
            self.add_pair(tuple(self._picked), tuple(xyz))
        self._picked = None
        self._say(f"{self.pairs.rowCount()} pairs. Three or more: Fit the pairs.")

    def from_pairs(self):
        from puzzle.field_align import from_point_pairs

        data, model = self.pair_points()
        try:
            fit = from_point_pairs(data, model, about=self._current().about,
                                   axes=self._current().axes)  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - a PuzzleError in words
            self._say(str(getattr(exc, "message", exc)))
            return None
        self.set_placement(fit.placement)
        self._say(f"Fitted {fit.pairs} pairs: residual {self._length(fit.rms)} RMS, the worst "
                  f"{self._length(fit.largest)}.")  # fmt: skip
        return fit

    # --- bounding boxes and a best fit -----------------------------------------------

    def match_boxes(self, then=None) -> None:
        from puzzle.field_align import match_boxes
        from puzzle.field_display import selection_nodes

        where = self.target.currentData()
        if not where:
            self._say("Choose a target first.")
            return

        def run(mesh, selection) -> None:
            _, nodes = selection_nodes(mesh, selection)
            base, _, _ = self.data()
            current = self._current()
            fit = match_boxes(base, nodes, order=self.order.isChecked(), about=current.about,
                              axes=current.axes)  # fmt: skip
            self.set_placement(fit.placement)
            words = "boxes centred on each other"
            if fit.rms > 0:
                words += f"; their sizes differ by up to {self._length(2 * fit.largest)}"
            self._say(words[0].upper() + words[1:] + ".")
            if then is not None:
                then(fit)

        self._say("Meshing the target…")
        with_target(self.wizard_, where, run, failed=lambda exc: self._say(str(exc)))

    def best_fit(self, then=None) -> None:
        from puzzle.field_align import best_fit

        where = self.target.currentData()
        if not where:
            self._say("Choose a target first.")
            return
        window = self.wizard_.window

        def run(mesh, selection) -> None:
            surface = target_surface(mesh, selection)
            base, _, _ = self.data()
            start = self._current()

            def work(_emit):
                return best_fit(base, surface if surface is not None else mesh.coords, start)

            def done(fit) -> None:
                self.set_placement(fit.placement)
                self._say(f"Best fit: {self._length(fit.before)} RMS from the faces before, "
                          f"{self._length(fit.rms)} after ({fit.iterations} steps).")  # fmt: skip
                if then is not None:
                    then(fit)

            self._say("Fitting…")
            window.background("Fitting the field onto the target", work, done,
                              failed=lambda exc: self._say(str(exc)), lock=False)  # fmt: skip

        self._say("Meshing the target…")
        with_target(self.wizard_, where, run, failed=lambda exc: self._say(str(exc)))


class VerifyView(QWidget):
    """The verification page's picture: a column mapped and coloured on the target in
    the view, with the legend; faces past the tolerance in the extrapolated colour."""

    def __init__(self, wizard) -> None:
        super().__init__()
        self.wizard_ = wizard
        self.column = QComboBox()
        self.note = QLabel("")
        self.note.setWordWrap(True)
        role(self.note, "muted")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Shown on the model"))
        row.addWidget(self.column, 1)
        row.addWidget(self.note, 2)
        self.column.currentIndexChanged.connect(lambda _i: self.draw())
        self.where: str | None = None
        self.last = None  # the Mapped drawn

    def columns(self) -> None:
        current = self.column.currentData()
        self.column.blockSignals(True)
        self.column.clear()
        for c in self.wizard_.file.columns():
            if c["role"] == "value":
                self.column.addItem(c["name"], c["name"])
        found = self.column.findData(current)
        self.column.setCurrentIndex(max(found, 0))
        self.column.blockSignals(False)

    def draw(self, where: str | None = None) -> None:
        """Map the chosen column onto `where` (a component or a selection) and draw it."""
        if where is not None:
            self.where = where
        if not self.where or not self.column.currentData():
            return
        from puzzle.field_display import mapped
        from puzzle.gui.field_view import _beyond_colour

        f, folder = self.wizard_.draft()
        window = self.wizard_.window
        column = self.column.currentData()
        project = window.session.project

        def run(mesh, selection) -> None:
            from puzzle.gui.field_view import _shown_mesh

            comp = _component(project, self.where)
            if comp is not None and window.meshes.get(str(comp.id)) is mesh:
                _shown_mesh(window, comp, selection)
            else:  # meshed for the wizard only
                window.viewport.show_mesh(mesh, selection if selection in mesh.groups else None)

            def work(_emit):
                return mapped(project, Path(folder), f, column, mesh, selection)

            def done(m) -> None:
                self.last = m
                window.viewport.show_field_values(
                    mesh, selection, m.node_ids, m.values, m.beyond, title=m.label, unit=m.unit,
                    beyond_colour=_beyond_colour(),
                )  # fmt: skip
                far = m.extrapolated
                from puzzle.field_display import _length

                self.note.setText(
                    f"{far} of {len(m.node_ids)} nodes are more than {_length(m.tolerance)} "
                    "from the data (extrapolated): marked in the view."
                    if far
                    else "Every node is within the tolerance of the data."
                )

            def failed(exc) -> None:
                self.note.setText(str(exc))

            window.background("Mapping the field onto the target", work, done, failed=failed,
                              lock=False)  # fmt: skip

        with_target(self.wizard_, self.where, run,
                    failed=lambda exc: self.note.setText(str(exc)))  # fmt: skip
