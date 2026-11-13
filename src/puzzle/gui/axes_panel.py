"""New coordinate system… (ADR 0049): one panel in the Properties dock, the view live.

A system is Cartesian, cylindrical or spherical, placed at an origin and turned by
- **directions:** x (or, for cylindrical and spherical, the axis) and a second
  direction in the xy plane;
- **rotation angles:** about x, then the turned y, then the turned z, from global;
- **three points:** the origin, a point on x and one in the xy plane, each picked in
  the view (snapping to nodes or corners) or typed;
- **a face or edge** of a named selection (step 3: a flat face's centroid and normal, a
  round face's axis, a ball's centre, an edge's tangent along it);
and optionally **relative to** another system's axes, or **attached to** a part (it
follows the part's placement).

The triad is drawn as it changes; Create makes it one command.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QComboBox, QLineEdit

from puzzle.gui.construction import PointRow, _Panel, _point, bounds
from puzzle.model.schema import CoordinateSystem

TYPES = [("Cartesian (x, y, z)", "cartesian"), ("Cylindrical (r, θ, z)", "cylindrical"),
         ("Spherical (r, θ, φ)", "spherical")]  # fmt: skip
HOW = [("By directions", "directions"), ("By rotation angles", "angles"),
       ("By three points", "points"), ("From a face or edge", "entity")]  # fmt: skip
NONE = "(none)"


def _vector(text: str):
    return _point(text, 1.0)


def _unique(window, base: str) -> str:
    taken = {c.name for c in window.session.project.of_type(CoordinateSystem)}
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


class AxesPanel(_Panel):
    kind = "axes"
    model = CoordinateSystem
    owned = ("name", "type", "origin", "x_axis", "xy_direction", "axis", "angles", "points",
             "relative_to", "component", "part", "geometry", "entity", "entity_name",
             "along")  # fmt: skip

    def __init__(self, window, type_: str = "cartesian",
                 editing: CoordinateSystem | None = None) -> None:  # fmt: skip
        if editing is not None:
            type_ = editing.type
        super().__init__(
            window,
            f"Edit {editing.name}" if editing is not None else "New coordinate system",
            "For loads and supports in local directions, and for results along its axes "
            "(normal stress along θ of a cylindrical system is the hoop stress). Pick, "
            "then double-click the model, to place points.",
            editing,
        )
        box = bounds(window)
        centre = tuple((box[0] + box[1]) / 2) if box is not None else (0.0, 0.0, 0.0)
        self.type = QComboBox()
        for text, value in TYPES:
            self.type.addItem(text, value)
        self.type.setCurrentIndex(self.type.findData(type_))
        self.how = QComboBox()
        for text, value in HOW:
            self.how.addItem(text, value)
        self.origin = PointRow(self, centre, "the origin")
        self.first = QLineEdit("1, 0, 0")
        self.second = QLineEdit("0, 1, 0")
        self.axis = QLineEdit("0, 0, 1")
        self.angles = QLineEdit("0, 0, 0")
        self.angles.setToolTip("Degrees about x, then the turned y, then the turned z")
        self.on_x = PointRow(self, np.add(centre, (0.01, 0, 0)), "a point on x")
        self.on_xy = PointRow(self, np.add(centre, (0, 0.01, 0)), "a point in the xy plane")
        self.name = QLineEdit(_unique(window, "Axes" if type_ == "cartesian" else type_.title()))
        # ADR 0049 step 3: from geometry, relative to another system, attached to a part.
        project = window.session.project
        from puzzle.model.schema import Component, Geometry

        self.source = QComboBox()
        for geo in project.of_type(Geometry):
            for sel in geo.selections:
                if sel.entity in ("face", "edge") and sel.members:
                    self.source.addItem(f"{geo.name}: {sel.name} ({sel.entity})",
                                        (str(geo.id), sel.entity, sel.members[0]))  # fmt: skip
        self.along = QLineEdit("0.5")
        self.along.setToolTip("Where on the edge: 0 its start, 1 its end")
        self.relative = QComboBox()
        self.relative.addItem(NONE, None)
        for cs in project.of_type(CoordinateSystem):
            if editing is None or cs.id != editing.id:  # not relative to itself
                self.relative.addItem(cs.name, str(cs.id))
        self.attached = QComboBox()
        self.attached.addItem(NONE, None)
        for comp in project.of_type(Component):
            for part in comp.parts:
                self.attached.addItem(f"{comp.name}: {part.name}", (str(comp.id), part.name))
        rows = [
            ("Type", self.type), ("Placed", self.how), (f"Origin ({self.unit})", self.origin),
            ("x direction", self.first), ("Axis (z)", self.axis),
            ("A direction in the xy plane", self.second), ("Angles (°)", self.angles),
            (f"Point on x ({self.unit})", self.on_x),
            (f"Point in the xy plane ({self.unit})", self.on_xy),
            ("Face or edge", self.source), ("Along the edge (0 to 1)", self.along),
            ("Relative to the axes of", self.relative), ("Attached to the part", self.attached),
            ("Name", self.name),
        ]  # fmt: skip
        for text, widget in rows:
            self.form.addRow(text, widget)
        for combo in (self.type, self.how, self.source, self.relative, self.attached):
            combo.currentIndexChanged.connect(lambda _=0: self.changed())
        self.along.editingFinished.connect(self.changed)
        for line in (self.first, self.second, self.axis, self.angles):
            line.editingFinished.connect(self.changed)
        if editing is not None:
            self.fill(editing)
        self.changed()

    def fill(self, cs: CoordinateSystem) -> None:
        """The panel as `cs` was made: its type, how it is placed and every value (D1)."""
        from puzzle.gui.construction import _shown

        how = ("entity" if cs.entity is not None else "points" if cs.points is not None
               else "angles" if cs.angles is not None else "directions")  # fmt: skip
        self.how.setCurrentIndex(self.how.findData(how))
        self.origin.text.setText(_shown(cs.origin, self.per_metre))
        self.first.setText(", ".join(f"{v:.6g}" for v in cs.x_axis))
        self.second.setText(", ".join(f"{v:.6g}" for v in cs.xy_direction))
        self.axis.setText(", ".join(f"{v:.6g}" for v in cs.axis))
        if cs.angles is not None:
            self.angles.setText(", ".join(f"{v:.6g}" for v in cs.angles))
        if cs.points is not None:
            for row, xyz in zip((self.origin, self.on_x, self.on_xy), cs.points, strict=True):
                row.text.setText(_shown(xyz, self.per_metre))
        if cs.entity is not None:
            for i in range(self.source.count()):
                geometry, entity, name = self.source.itemData(i)
                if (
                    entity == cs.entity
                    and name == cs.entity_name
                    and (cs.geometry is None or geometry == str(cs.geometry))
                ):
                    self.source.setCurrentIndex(i)
                    break
            self.along.setText(f"{cs.along:.6g}")
        if cs.relative_to is not None:
            self.relative.setCurrentIndex(max(0, self.relative.findData(str(cs.relative_to))))
        if cs.part is not None:  # Qt hands a stored tuple back as a list
            for i in range(1, self.attached.count()):
                if tuple(self.attached.itemData(i)) == (str(cs.component), cs.part):
                    self.attached.setCurrentIndex(i)
                    break
        self.name.setText(cs.name)

    def _rows(self) -> None:
        how, kind = self.how.currentData(), self.type.currentData()
        round_ = kind != "cartesian"
        shown = {
            self.origin: how != "points",
            self.first: how == "directions" and not round_,
            self.axis: how == "directions" and round_,
            self.second: how == "directions" and not round_,
            self.angles: how == "angles",
            self.on_x: how == "points",
            self.on_xy: how == "points",
            self.source: how == "entity",
            self.along: how == "entity" and (self.source.currentData() or ("", ""))[1] == "edge",
        }
        for widget, on in shown.items():
            self.form.setRowVisible(widget, on)

    def changed(self) -> None:
        self._rows()
        super().changed()

    def problem(self) -> str | None:
        how = self.how.currentData()
        if how == "entity":
            if self.source.currentData() is None:
                return "Name a face or edge selection on the geometry first"
            try:
                self.resolved()
            except Exception as exc:  # noqa: BLE001 - its message says what is wrong
                return str(exc)
            return None if self.name.text().strip() else "Give it a name"
        if how == "points":
            a, b, c = self.origin_points()
            if None in (a, b, c):
                return "Type each point as three numbers: x, y, z"
            if not np.linalg.norm(np.cross(np.subtract(b, a), np.subtract(c, a))):
                return "The three points are in a line"
        elif self.origin.value() is None:
            return "Type the origin as three numbers: x, y, z"
        if how == "angles" and _vector(self.angles.text()) is None:
            return "Type the angles as three numbers (degrees)"
        if how == "directions":
            try:
                CoordinateSystem(**self.data()).orientation()
            except (ValueError, TypeError, ZeroDivisionError):
                return "The directions must be three numbers each, and not parallel"
        return None if self.name.text().strip() else "Give it a name"

    def origin_points(self):
        return self.origin.value(), self.on_x.value(), self.on_xy.value()

    def data(self) -> dict:
        how, kind = self.how.currentData(), self.type.currentData()
        out = {"name": self.name.text().strip(), "type": kind}
        if self.relative.currentData():
            out["relative_to"] = self.relative.currentData()
        if self.attached.currentData():
            out["component"], out["part"] = self.attached.currentData()
        if how == "entity":
            geometry, entity, name = self.source.currentData()
            out.update(entity=entity, entity_name=name)
            if not self.attached.currentData():
                out["geometry"] = geometry
            if entity == "edge":
                out["along"] = min(max(float(self.along.text() or 0.5), 0.0), 1.0)
            return out
        if how == "points":
            out["points"] = [list(p) for p in self.origin_points()]
            return out
        out["origin"] = list(self.origin.value())
        if how == "angles":
            out["angles"] = list(_vector(self.angles.text()))
        elif kind == "cartesian":
            out["x_axis"] = list(_vector(self.first.text()) or (1, 0, 0))
            out["xy_direction"] = list(_vector(self.second.text()) or (0, 1, 0))
        else:
            out["axis"] = list(_vector(self.axis.text()) or (0, 0, 1))
        return out

    def resolved(self) -> CoordinateSystem:
        """The system as it would be made, in global terms (ADR 0049 step 3)."""
        from puzzle.axes import absolute

        system = CoordinateSystem(**self.data())
        return absolute(self.window.session.project, system, self.window.session.root)

    def preview(self) -> None:
        try:
            system = self.resolved()
        except Exception:  # noqa: BLE001 - problem() says why; nothing to draw yet
            return
        self.window.viewport.show_triad(system, _size(self.window))


def _size(window) -> float:
    box = bounds(window)
    return 0.25 * float(np.linalg.norm(box[1] - box[0])) if box is not None else 0.05


def new_axes(window, type_: str = "cartesian") -> AxesPanel:
    panel = AxesPanel(window, type_)
    window.show_tool(panel)
    return panel


def edit_axes(window, cs: CoordinateSystem) -> AxesPanel:
    """The panel again, filled from `cs`; Apply redefines it in one command (D1)."""
    panel = AxesPanel(window, editing=cs)
    window.show_tool(panel)
    return panel
