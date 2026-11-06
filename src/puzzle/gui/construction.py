"""Construction geometry in the GUI (ADR 0046): paths and planes for result tools.

New path… and New plane… open a panel in the Properties dock, so the view stays live
while it is filled in: each point has a Pick button, and the next double-click on the
model gives that point, anywhere on a face (snapped to a corner when close). The
panel draws the path or plane as it changes. It starts from the corners picked in the
view (two make a path; one places a plane, three tilt it), else from the model's
extent: a path through its thinnest side at the centre, a plane across its longest
side at the middle. Selecting one in the tree draws it over the model.

They can also be placed on the model's geometry: a plane on a face (picked in the view,
or a face selection), through its centroid facing its normal; a path along a straight
edge (picked, or an edge selection), between its two ends. The numbers are stored, with
`made_from` saying in words what they came from. Edit… (the tree's right-click menu, a
double-click, Model → Construction geometry) reopens the panel filled from the object,
and Apply changes every field it owns in one command.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role
from puzzle.model.schema import ConstructionGeometry, CoordinateSystem

NORMALS = [
    ("Along X", "x"),
    ("Along Y", "y"),
    ("Along Z", "z"),
    ("Typed below", "typed"),
]
AXIS = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
SNAPS = [
    ("Mesh nodes", "node"),
    ("CAD corners", "corner"),
    ("Anywhere on the surface", "none"),
]
PLANES = [("YZ plane (faces its x)", "yz"), ("ZX plane (faces its y)", "zx"),
          ("XY plane (faces its z)", "xy")]  # fmt: skip


def _point(text: str, per_metre: float):
    from puzzle.gui.result_tools import _point as parse

    return parse(text, per_metre)


def _shown(values, per_metre: float) -> str:
    return ", ".join(f"{v * per_metre:.6g}" for v in values)


def bounds(window) -> tuple[np.ndarray, np.ndarray] | None:
    """The shown or last meshed model's extent (min, max), if any."""
    mesh = getattr(window.viewport, "mesh", None)
    if mesh is None and window.meshes:
        mesh = next(iter(window.meshes.values()))
    coords = getattr(mesh, "coords", None)
    if coords is None or not len(coords):
        return None
    return coords.min(axis=0), coords.max(axis=0)


def picked_points(window) -> list[np.ndarray]:
    """Points picked in the view (the Points filter), in pick order."""
    view = window.viewport
    return [np.asarray(view.vertices[e], float) for e in view.picked
            if e[0] == 0 and e in view.vertices]  # fmt: skip


PICKED = "picked"  # a source's data: the face or edge picked in the view now


def model_mesh(window):
    """The mesh faces and edges are read from: the one shown, else the last meshed."""
    mesh = window.viewport.node_mesh()
    if mesh is None and window.meshes:
        mesh = next(iter(window.meshes.values()))
    return mesh


def geometry_sources(mesh, dim: int) -> list[tuple[str, str]]:
    """(name, group) for the mesh's face (dim 2) or edge (dim 1) selections, named as
    "Tip (Bar)"."""
    out = []
    for name, group in sorted((mesh.groups if mesh is not None else {}).items()):
        if group.dim == dim and "/" in name:
            part, selection = name.split("/", 1)
            out.append((f"{selection} ({part})", name))
    return out


def picked_entities(window, dim: int) -> list[tuple[int, int]]:
    return [tuple(e) for e in window.viewport.picked if e[0] == dim]


def edge_ends(mesh, entities) -> tuple[np.ndarray, np.ndarray]:
    """The two ends of a straight edge (Gmsh (1, tag) entities, joined end to end); a
    curved or closed edge is refused, since a path is a straight line."""
    keys = {tuple(e) for e in entities}
    blocks = [b for b in mesh.blocks if b.dim == 1 and tuple(b.entity) in keys]
    if not blocks:
        raise ValueError("No edge to place a path on: mesh the component first")
    corners = np.concatenate([b.connectivity[:, :2] for b in blocks]).ravel()
    ids, counts = np.unique(corners, return_counts=True)
    ends = ids[counts == 1]
    if len(ends) != 2:
        raise ValueError("A closed edge has no ends: pick a straight edge, or two points")
    a, b = mesh.node_coords(ends)
    used = np.unique(np.concatenate([x.connectivity.ravel() for x in blocks]))
    length = float(np.linalg.norm(b - a))
    off = np.linalg.norm(np.cross(mesh.node_coords(used) - a, (b - a) / length), axis=1)
    if off.max() > 1e-4 * length:
        raise ValueError("A curved edge: a path is straight, so pick a straight edge or two points")
    return a, b


def _unique(window, base: str) -> str:
    taken = {c.name for c in window.session.project.of_type(ConstructionGeometry)}
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _create(window, data: dict):
    from puzzle.commands.model import Create

    return window.submit(Create(kind="construction", data=data))


def default_path(window) -> tuple[tuple, tuple]:
    points = picked_points(window)
    if len(points) >= 2:
        return tuple(points[0]), tuple(points[1])
    box = bounds(window)
    if box is None:
        return (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    lo, hi = box
    axis = int(np.argmin(hi - lo))
    start, end = (lo + hi) / 2, (lo + hi) / 2
    start[axis], end[axis] = lo[axis], hi[axis]
    return tuple(start), tuple(end)


def default_plane(window) -> tuple[tuple, str, tuple]:
    """(origin, normal choice, typed normal) from the picks, else the model's middle
    across its longest side."""
    points = picked_points(window)
    if len(points) >= 3:
        n = np.cross(points[1] - points[0], points[2] - points[0])
        if np.linalg.norm(n):
            return tuple(points[0]), "typed", tuple(n / np.linalg.norm(n))
    box = bounds(window)
    if box is None:
        origin, axis = (np.zeros(3) if not points else points[0]), 0
    else:
        lo, hi = box
        axis = int(np.argmax(hi - lo))
        origin = points[0] if points else (lo + hi) / 2
    key = "xyz"[axis]
    return tuple(origin), key, AXIS[key]


class PointRow(QWidget):
    """x, y, z typed in the display length unit, or picked: Pick, then double-click the
    model."""

    def __init__(self, panel, xyz, tip: str) -> None:
        super().__init__()
        self.panel = panel
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.text = QLineEdit(_shown(xyz, panel.per_metre))
        self.text.editingFinished.connect(panel.changed)
        self.pick = QToolButton()
        self.pick.setText("Pick")
        self.pick.setCheckable(True)
        self.pick.setToolTip(f"Then double-click the model at {tip}")
        self.pick.toggled.connect(self._toggled)
        row.addWidget(self.text, 1)
        row.addWidget(self.pick)
        self.tip = tip

    def _toggled(self, on: bool) -> None:
        view = self.panel.window.viewport
        if not on:
            if view.capture is not None and self.panel.picking is self:
                view.capture_point(None)
            return
        self.panel.start_picking(self)
        view.capture_point(self.got, self.panel.snap())
        self.panel.window.statusBar().showMessage(
            f"Double-click the model at {self.tip}{self.panel.snap_note()}", 15000
        )

    def got(self, xyz) -> None:
        self.text.setText(_shown(xyz, self.panel.per_metre))
        self.exact = (tuple(xyz), self.text.text())  # a picked node, to the last digit
        self.pick.blockSignals(True)
        self.pick.setChecked(False)
        self.pick.blockSignals(False)
        self.panel.picking = None
        node = self.panel.window.viewport.shown.get("snapped")
        bar = self.panel.window.statusBar()
        bar.showMessage(f"Node {node}", 5000) if node is not None else bar.clearMessage()
        self.panel.changed()

    def value(self):
        """The point in metres: exactly as picked, unless the text was edited since."""
        exact = getattr(self, "exact", None)
        if exact is not None and exact[1] == self.text.text():
            return exact[0]
        return _point(self.text.text(), self.panel.per_metre)


class _Panel(QWidget):
    """What the panels share: a title, an intro, a form, a message and Create/Cancel, or
    Apply/Cancel when `editing` an object (one Edit command for every field it owns)."""

    kind = "construction"  # the project object Create makes
    model = ConstructionGeometry
    # The fields the panel sets. An edit sets each, back to its default when the panel's
    # choice leaves it out, so an object made another way is left consistent.
    owned = ("name", "type", "start", "end", "origin", "normal", "axes", "plane", "offset",
             "made_from")  # fmt: skip

    def __init__(self, window, title: str, intro: str, editing=None) -> None:
        from puzzle.settings import length_unit

        super().__init__()
        self.window = window
        self.editing = editing
        self.unit, self.per_metre = length_unit()
        self.picking: PointRow | None = None
        # It scrolls rather than squeezing its rows when the dock is short.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        layout = QVBoxLayout(inner)
        heading = QLabel(title)
        role(heading, "title")
        about = QLabel(intro)
        about.setWordWrap(True)
        role(about, "muted")
        layout.addWidget(heading)
        layout.addWidget(about)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.snapping = QComboBox()
        for text, value in SNAPS:
            self.snapping.addItem(text, value)
        meshed = window.viewport.node_mesh() is not None
        self.snapping.setCurrentIndex(self.snapping.findData("node" if meshed else "corner"))
        self.snapping.setToolTip(
            "Where a picked point goes: the nearest node of the shown mesh or result, a "
            "corner of the CAD, or where you double-clicked"
        )
        self.form.addRow("Picks snap to", self.snapping)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        role(self.message, "error")
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.create_button = QPushButton("Apply" if editing is not None else "Create")
        self.create_button.setDefault(True)
        self.create_button.clicked.connect(self.create)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close_panel)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.create_button)
        layout.addLayout(buttons)
        layout.addStretch()

    def start_picking(self, row: PointRow) -> None:
        if self.picking is not None and self.picking is not row:
            self.picking.pick.blockSignals(True)
            self.picking.pick.setChecked(False)
            self.picking.pick.blockSignals(False)
        self.picking = row

    def snap(self) -> str:
        return self.snapping.currentData()

    def snap_note(self) -> str:
        """Why node snapping falls back, said while picking."""
        if self.snap() == "node" and self.window.viewport.node_mesh() is None:
            return " (no mesh shown: mesh the component or show a result to snap to nodes)"
        return ""

    def close_panel(self) -> None:
        self.window.viewport.capture_point(None)
        self.window.show_tool(None)

    def changed(self) -> None:
        problem = self.problem()
        self.message.setText(problem or "")
        if problem is None:
            self.preview()

    def create(self):
        problem = self.problem()
        if problem is not None:
            self.message.setText(problem)
            return None
        from puzzle.commands.model import Create, Edit

        if self.editing is None:
            out = self.window.submit(Create(kind=self.kind, data=self.data()))
        else:
            out = self.window.submit(Edit(object=str(self.editing.id), ops=self.edit_ops()))
        self.close_panel()
        return out

    def edit_ops(self) -> list[dict]:
        """Set every owned field: as the panel has it, else back to its default."""
        data = self.data()
        ops = []
        for name in self.owned:
            value = data[name] if name in data else self.model.model_fields[name].default
            if isinstance(value, tuple):
                value = list(value)
            ops.append({"op": "set", "path": name, "value": value})
        return ops


class PathPanel(_Panel):
    HOW = [("Between two points", "points"), ("Along an edge", "edge")]

    def __init__(self, window, editing: ConstructionGeometry | None = None) -> None:
        picked = len(picked_points(window)) >= 2
        start_note = (
            ""
            if editing is not None
            else "It starts from the two corners picked in the view."
            if picked
            else "It starts across the model's thinnest side."
        )
        super().__init__(
            window,
            "Edit path" if editing is not None else "New path",
            "A straight line for result tools: a result along it, or the stress "
            "linearized through a wall. Pick, then double-click the model, or place it "
            "along a straight edge. " + start_note,
            editing,
        )
        start, end = (editing.start, editing.end) if editing else default_path(window)
        self.how = QComboBox()
        for text, value in self.HOW:
            self.how.addItem(text, value)
        self.start = PointRow(self, start, "the path's start")
        self.end = PointRow(self, end, "the path's end")
        self.edge = QComboBox()
        self.edge.addItem("The edge picked in the view", PICKED)
        for text, value in geometry_sources(model_mesh(window), 1):
            self.edge.addItem(f"Edge selection: {text}", value)
        self.edge.setToolTip(
            "Pick an edge (Pick, then Edges in the pick bar), or choose an edge selection"
        )
        self.name = QLineEdit(editing.name if editing else _unique(window, "Path"))
        self.form.addRow("Placed", self.how)
        self.form.addRow(f"From ({self.unit})", self.start)
        self.form.addRow(f"To ({self.unit})", self.end)
        self.form.addRow("Edge", self.edge)
        self.form.addRow("Name", self.name)
        for box in (self.how, self.edge):
            box.currentIndexChanged.connect(lambda _=0: self.changed())
        self.changed()

    def along_edge(self) -> bool:
        return self.how.currentData() == "edge"

    def changed(self) -> None:
        edge = self.along_edge()
        for widget, on in ((self.start, not edge), (self.end, not edge), (self.edge, edge)):
            self.form.setRowVisible(widget, on)
        super().changed()

    def _edge(self) -> tuple[tuple, tuple, str]:
        """The chosen edge's ends and its words; ValueError says what is wrong."""
        mesh = model_mesh(self.window)
        if mesh is None:
            raise ValueError("Mesh the component to place a path on its edges")
        source = self.edge.currentData()
        if source == PICKED:
            entities = picked_entities(self.window, 1)
            if not entities:
                raise ValueError(
                    "Pick an edge in the view (Pick, then Edges), or choose an edge selection"
                )
            words = "the picked edge"
        else:
            entities = mesh.groups[source].entities
            words = f"edge {self.edge.currentText().split(': ', 1)[-1]}"
        a, b = edge_ends(mesh, entities)
        return tuple(map(float, a)), tuple(map(float, b)), words

    def ends(self):
        if self.along_edge():
            a, b, _ = self._edge()
            return a, b
        return self.start.value(), self.end.value()

    def problem(self) -> str | None:
        try:
            a, b = self.ends()
        except ValueError as exc:
            return str(exc)
        if a is None or b is None:
            return "Type each end as three numbers: x, y, z"
        if np.allclose(a, b):
            return "The two ends are the same point"
        return None if self.name.text().strip() else "Give it a name"

    def preview(self) -> None:
        a, b = self.ends()
        self.window.viewport.show_construction("path", a, b, 0)

    def data(self) -> dict:
        a, b = self.ends()
        out = {"name": self.name.text().strip(), "type": "path",
               "start": list(a), "end": list(b)}  # fmt: skip
        if self.along_edge():
            out["made_from"] = self._edge()[2]
        return out


class PlanePanel(_Panel):
    def __init__(self, window, editing: ConstructionGeometry | None = None) -> None:
        super().__init__(
            window,
            "Edit plane" if editing is not None else "New plane",
            "A plane for result tools: a result over its cut, or the force and moment "
            "across it. Pick, then double-click the model; tilt it through three points; "
            "or place it on a face.",
            editing,
        )
        origin, which, typed = default_plane(window)
        if editing is not None and editing.axes is None:
            origin, typed = editing.origin, editing.normal
            n = np.asarray(typed, float) / np.linalg.norm(typed)
            which = next((k for k, v in AXIS.items() if np.allclose(n, v)), "typed")
        project = window.session.project
        self.systems = [(a.name, str(a.id)) for a in project.of_type(CoordinateSystem)
                        if a.type == "cartesian"]  # fmt: skip
        self.how = QComboBox()
        self.how.addItem("Through a point, facing a direction", "point")
        self.how.addItem("On a face", "face")
        if self.systems:
            self.how.addItem("One of a coordinate system's planes", "axes")
        self.face = QComboBox()
        self.face.addItem("The face picked in the view", PICKED)
        for text, value in geometry_sources(model_mesh(window), 2):
            self.face.addItem(f"Face selection: {text}", value)
        self.face.setToolTip(
            "Pick a face (Pick, then Faces in the pick bar), or choose a face selection"
        )
        self.origin = PointRow(self, origin, "a point on the plane")
        self.facing = QComboBox()
        for text, value in NORMALS:
            self.facing.addItem(text, value)
        self.facing.setCurrentIndex(self.facing.findData(which))
        self.direction = QLineEdit(", ".join(f"{v:.6g}" for v in typed))
        self.three = QPushButton("Through three points…")
        self.three.setToolTip("Double-click three points on the model, one after another")
        self.three.clicked.connect(self.pick_three)
        self.axes, self.plane = QComboBox(), QComboBox()
        for text, value in self.systems:
            self.axes.addItem(text, value)
        for text, value in PLANES:
            self.plane.addItem(text, value)
        self.offset = QLineEdit("0")
        self.name = QLineEdit(editing.name if editing else _unique(window, "Plane"))
        if editing is not None and editing.axes is not None and self.systems:
            self.how.setCurrentIndex(self.how.findData("axes"))
            self.axes.setCurrentIndex(max(0, self.axes.findData(str(editing.axes))))
            self.plane.setCurrentIndex(max(0, self.plane.findData(editing.plane)))
            self.offset.setText(f"{editing.offset * self.per_metre:.6g}")
        rows = [
            ("Placed", self.how), (f"Through ({self.unit})", self.origin),
            ("Facing", self.facing), ("Direction (x, y, z)", self.direction),
            ("", self.three), ("Face", self.face), ("Coordinate system", self.axes),
            ("Its plane", self.plane),
            (f"Moved along its normal ({self.unit})", self.offset), ("Name", self.name),
        ]  # fmt: skip
        for text, widget in rows:
            self.form.addRow(text, widget)
        for box in (self.how, self.facing, self.face, self.axes, self.plane):
            box.currentIndexChanged.connect(lambda _=0: self.changed())
        for line in (self.direction, self.offset):
            line.editingFinished.connect(self.changed)
        self._three: list = []
        self.changed()

    def by_point(self) -> bool:
        return self.how.currentData() == "point"

    def on_face(self) -> bool:
        return self.how.currentData() == "face"

    def _show_rows(self) -> None:
        point, face, axes = self.by_point(), self.on_face(), self.how.currentData() == "axes"
        shown = {
            self.origin: point, self.facing: point, self.three: point,
            self.direction: point and self.facing.currentData() == "typed",
            self.face: face, self.axes: axes, self.plane: axes, self.offset: face or axes,
        }  # fmt: skip
        for widget, on in shown.items():
            self.form.setRowVisible(widget, on)

    def changed(self) -> None:
        self._show_rows()
        super().changed()

    def pick_three(self) -> None:
        """Three double-clicks: the plane through them, placed at the first."""
        self._three = []
        view = self.window.viewport

        def got(xyz) -> None:
            self._three.append(np.asarray(xyz, float))
            if len(self._three) < 3:
                self.window.statusBar().showMessage(
                    f"Double-click point {len(self._three) + 1} of 3 on the model", 15000
                )
                view.capture_point(got, self.snap())
                return
            a, b, c = self._three
            n = np.cross(b - a, c - a)
            if not np.linalg.norm(n):
                self.message.setText("The three points are in a line: pick them again")
                return
            n /= np.linalg.norm(n)
            self.origin.text.setText(_shown(a, self.per_metre))
            self.direction.setText(", ".join(f"{v:.6g}" for v in n))
            self.facing.setCurrentIndex(self.facing.findData("typed"))
            self.window.statusBar().clearMessage()
            self.changed()

        self.window.statusBar().showMessage(
            f"Double-click point 1 of 3 on the model{self.snap_note()}", 15000
        )
        view.capture_point(got, self.snap())

    def normal(self):
        if self.facing.currentData() == "typed":
            return _point(self.direction.text(), 1.0)
        return AXIS[self.facing.currentData()]

    def _face(self) -> tuple[np.ndarray, np.ndarray, str]:
        """The chosen face's centroid, normal and words; ValueError says what is wrong."""
        from puzzle.post.cuts import face_plane

        mesh = model_mesh(self.window)
        if mesh is None:
            raise ValueError("Mesh the component to place a plane on its faces")
        source = self.face.currentData()
        if source == PICKED:
            entities = picked_entities(self.window, 2)
            if not entities:
                raise ValueError(
                    "Pick a face in the view (Pick, then Faces), or choose a face selection"
                )
            words = "the picked face"
        else:
            entities = mesh.groups[source].entities
            words = f"face {self.face.currentText().split(': ', 1)[-1]}"
        centre, normal = face_plane(mesh, entities)
        return centre, normal, words

    def _offset(self) -> float | None:
        try:
            return float(self.offset.text() or 0) / self.per_metre
        except ValueError:
            return None

    def problem(self) -> str | None:
        if self.on_face():
            try:
                self._face()
            except ValueError as exc:
                return str(exc)
            if self._offset() is None:
                return "The offset is a number"
        elif self.by_point():
            if self.origin.value() is None:
                return "Type the point as three numbers: x, y, z"
            n = self.normal()
            if n is None or not np.linalg.norm(n):
                return "Type the direction as three numbers, not all zero"
        else:
            try:
                float(self.offset.text() or 0)
            except ValueError:
                return "The offset is a number"
        return None if self.name.text().strip() else "Give it a name"

    def data(self) -> dict:
        data = {"name": self.name.text().strip(), "type": "plane"}
        if self.on_face():
            centre, normal, words = self._face()
            origin = centre + self._offset() * normal
            return data | {"origin": [float(v) for v in origin],
                           "normal": [float(v) for v in normal], "made_from": words}  # fmt: skip
        if self.by_point():
            return data | {"origin": list(self.origin.value()), "normal": list(self.normal())}
        return data | {"axes": self.axes.currentData(), "plane": self.plane.currentData(),
                       "offset": float(self.offset.text() or 0) / self.per_metre}  # fmt: skip

    def preview(self) -> None:
        made = ConstructionGeometry(**self.data())
        a, n = made.frame(self.window.session.project)
        box = bounds(self.window)
        size = 1.0
        if box is not None:
            lo, hi = box
            size = 0.6 * float(np.linalg.norm(hi - lo))
        self.window.viewport.show_construction("plane", a, n, size)


def new_path(window) -> PathPanel:
    """A straight line between two points (Ansys's Path), for the Path and Linearized
    stress tools: a panel in the Properties dock, the view live beside it."""
    panel = PathPanel(window)
    window.show_tool(panel)
    return panel


def new_plane(window) -> PlanePanel:
    """A plane through the model (Ansys's Surface): a result over its cut, or the force
    and moment carried across it."""
    panel = PlanePanel(window)
    window.show_tool(panel)
    return panel


def edit_construction(window, obj: ConstructionGeometry) -> _Panel:
    """Its panel again, filled from it; Apply changes it in one command (D3)."""
    panel = (PathPanel if obj.type == "path" else PlanePanel)(window, editing=obj)
    window.show_tool(panel)
    return panel


def construction_choices(window, kind: str) -> list[tuple[str, str]]:
    """The project's construction paths or planes, as (name, id)."""
    return [(c.name, str(c.id)) for c in window.session.project.of_type(ConstructionGeometry)
            if c.type == kind]  # fmt: skip


def describe(c: ConstructionGeometry, project) -> str:
    """The tree's text after the name: what it is."""
    if c.type == "path":
        return "path"
    if c.axes is not None:
        axes = project.objects.get(c.axes)
        return f"plane: {c.plane.upper()} of {axes.name if axes else '?'}"
    return "plane"
