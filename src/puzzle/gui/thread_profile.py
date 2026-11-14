"""A thread's basic profile drawn in its contact's Properties (ADR 0076 step 6).

`outline(spec, pitches)` is the screw's tooth outline over a few pitches, pure numbers:
x along the axis, y the radius, from the minor diameter to the major, each flank at its
own angle from the radial plane (the load flank first, as `Thread.bearing` reads it).
`ThreadProfile` paints it with the pitch, angles and diameters written beside it, in the
theme's colours, so a buttress or a square thread can be checked at a glance.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


def outline(spec, pitches: int = 2) -> list[tuple[float, float]]:
    """The tooth outline (x along the axis, y the radius, metres) over `pitches`: root,
    load flank up, crest, other flank down, per pitch. Crest and root share what the
    flanks leave of the pitch equally (the basic profile's flats)."""
    h = max((spec.major - spec.minor) / 2, 1e-12)
    load, other = (math.tan(math.radians(a)) for a in spec.angles)
    flats = max(spec.pitch - h * (load + other), 0.0)
    crest = root = flats / 2
    r0, r1 = spec.minor / 2, spec.major / 2
    pts = []
    x = 0.0
    for _ in range(pitches):
        pts += [(x, r0), (x + root, r0)]
        x += root
        pts.append((x + h * load, r1))
        x += h * load
        pts.append((x + crest, r1))
        x += crest
        pts.append((x + h * other, r0))
        x += h * other
    return pts


class ThreadProfile(QWidget):
    """The outline of `outline`, scaled to the widget, with its numbers beside it."""

    def __init__(self, spec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.points = outline(spec)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        load, other = spec.angles
        mm = 1e3
        self.setToolTip(
            f"Pitch {spec.pitch * mm:.4g} mm, flanks {load:g}° (load) and {other:g}°, "
            f"major {spec.major * mm:.4g} mm, pitch diameter {spec.pitch_diameter * mm:.4g} "
            f"mm, minor {spec.minor * mm:.4g} mm"
        )

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        box = QRectF(self.rect()).adjusted(8, 8, -8, -28)
        xs = [x for x, _ in self.points]
        ys = [y for _, y in self.points]
        span_x = max(max(xs) - min(xs), 1e-12)
        span_y = max(max(ys) - min(ys), 1e-12)
        scale = min(box.width() / span_x, box.height() / span_y)
        left = box.left() + (box.width() - span_x * scale) / 2

        def at(x: float, y: float) -> QPointF:
            return QPointF(left + (x - min(xs)) * scale, box.bottom() - (y - min(ys)) * scale)

        path = QPainterPath(at(*self.points[0]))
        for x, y in self.points[1:]:
            path.lineTo(at(x, y))
        p.setPen(QPen(pal.highlight().color(), 2))
        p.drawPath(path)
        p.setPen(QPen(pal.mid().color(), 1, Qt.PenStyle.DashLine))
        pd = self.spec.pitch_diameter / 2
        p.drawLine(at(min(xs), pd), at(max(xs), pd))  # the pitch line
        p.setPen(pal.text().color())
        load, other = self.spec.angles
        p.drawText(QRectF(self.rect()).adjusted(8, 0, -8, -4),
                   int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft),
                   f"P {self.spec.pitch * 1e3:.4g} mm   flanks {load:g}° / {other:g}°   "
                   f"d {self.spec.major * 1e3:.4g}, d2 {self.spec.pitch_diameter * 1e3:.4g}, "
                   f"d1 {self.spec.minor * 1e3:.4g} mm")  # fmt: skip
        p.end()


def add_to_properties(window, contact) -> ThreadProfile | None:
    """Under a threaded contact's Properties: its profile, or nothing when the thread
    can't be resolved yet (no size and no diameters)."""
    from PySide6.QtWidgets import QTreeWidgetItem

    from puzzle.threads import spec

    if contact.thread is None:
        return None
    try:
        s = spec(contact.thread)
    except ValueError:
        return None
    panel = window.properties
    item = QTreeWidgetItem(panel.invisibleRootItem(), ["Thread profile", ""])
    child = QTreeWidgetItem(item, [""])
    child.setFirstColumnSpanned(True)
    drawing = ThreadProfile(s)
    panel.setItemWidget(child, 0, drawing)
    panel.thread_profile = drawing
    item.setExpanded(True)
    return drawing
