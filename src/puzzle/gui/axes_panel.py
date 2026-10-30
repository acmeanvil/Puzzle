"""New coordinate system… (ADR 0049): one panel in the Properties dock, the view live.

A system is Cartesian, cylindrical or spherical, placed at an origin and turned by
- **directions:** x (or, for cylindrical and spherical, the axis) and a second
  direction in the xy plane;
- **rotation angles:** about x, then the turned y, then the turned z, from global;
- **three points:** the origin, a point on x and one in the xy plane, each picked in
  the view (snapping to nodes or corners) or typed.

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
       ("By three points", "points")]  # fmt: skip


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

    def __init__(self, window, type_: str = "cartesian") -> None:
        super().__init__(
            window,
            "New coordinate system",
            "For loads and supports in local directions, and for results along its axes "
            "(normal stress along θ of a cylindrical system is the hoop stress). Pick, "
            "then double-click the model, to place points.",
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
        rows = [
            ("Type", self.type), ("Placed", self.how), (f"Origin ({self.unit})", self.origin),
            ("x direction", self.first), ("Axis (z)", self.axis),
            ("A direction in the xy plane", self.second), ("Angles (°)", self.angles),
            (f"Point on x ({self.unit})", self.on_x),
            (f"Point in the xy plane ({self.unit})", self.on_xy), ("Name", self.name),
        ]  # fmt: skip
        for text, widget in rows:
            self.form.addRow(text, widget)
        for combo in (self.type, self.how):
            combo.currentIndexChanged.connect(lambda _=0: self.changed())
        for line in (self.first, self.second, self.axis, self.angles):
            line.editingFinished.connect(self.changed)
        self.changed()

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
        }
        for widget, on in shown.items():
            self.form.setRowVisible(widget, on)

    def changed(self) -> None:
        self._rows()
        super().changed()

    def problem(self) -> str | None:
        how = self.how.currentData()
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

    def preview(self) -> None:
        system = CoordinateSystem(**self.data())
        self.window.viewport.show_triad(system, _size(self.window))


def _size(window) -> float:
    box = bounds(window)
    return 0.25 * float(np.linalg.norm(box[1] - box[0])) if box is not None else 0.05


def new_axes(window, type_: str = "cartesian") -> AxesPanel:
    panel = AxesPanel(window, type_)
    window.show_tool(panel)
    return panel
