"""Legend… (the result colour bar): its range, colours and place, live.

Opened from the Results ribbon or menu into the Properties dock. Every change redraws
the result at once:

- **Range:** Auto, or a custom bottom and top in the display unit. It belongs to the
  result shown (deformation keeps its own, stress its own); "From the result" fills in
  the result's own extremes to start from.
- **Colours:** the map, the other way round, smooth or a few bands, and one colour for
  values above a custom range and one for values below it (Ansys draws both grey).
- **Legend:** where the bar sits and how big it and its text are.

The look is kept as preferences (Settings > Results); ranges last for the session.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role
from puzzle.post.legend import BANDS, COLORMAPS, OUTSIDE, POSITIONS, SIZES


def _combo(pairs, current) -> QComboBox:
    box = QComboBox()
    for text, value in pairs:
        box.addItem(text, value)
    index = box.findData(current)
    box.setCurrentIndex(index if index >= 0 else 0)
    return box


class LegendPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        view = window.viewport
        legend = view.legend
        self._loading = True
        layout = QVBoxLayout(self)
        title = QLabel("Legend")
        role(title, "title")
        layout.addWidget(title)
        self.about = QLabel("")
        role(self.about, "muted")
        self.about.setWordWrap(True)
        layout.addWidget(self.about)

        span = QGroupBox("Range")
        form = QFormLayout(span)
        self.mode = _combo([("Automatic (the result's own)", "auto"), ("Custom", "custom")],
                           "auto")  # fmt: skip
        self.top, self.bottom = QLineEdit(), QLineEdit()
        self.unit = QLabel("")
        fill = QPushButton("From the result")
        fill.setToolTip("Start from the shown result's smallest and largest values")
        fill.clicked.connect(self.fill)
        form.addRow("Range", self.mode)
        form.addRow("Top", self.top)
        form.addRow("Bottom", self.bottom)
        row = QHBoxLayout()
        row.addWidget(self.unit, 1)
        row.addWidget(fill)
        form.addRow("", row)
        layout.addWidget(span)

        look = QGroupBox("Colours")
        form = QFormLayout(look)
        self.colormap = _combo(COLORMAPS, legend.colormap)
        self.invert = QCheckBox("The other way round")
        self.invert.setChecked(legend.invert)
        self.bands = _combo(BANDS, legend.bands)
        self.above = _combo(OUTSIDE, legend.above)
        self.above.setToolTip("With a custom range: values above its top")
        self.below = _combo(OUTSIDE, legend.below)
        self.below.setToolTip("With a custom range: values below its bottom")
        form.addRow("Colours", self.colormap)
        form.addRow("", self.invert)
        form.addRow("Bands", self.bands)
        form.addRow("Above the range", self.above)
        form.addRow("Below the range", self.below)
        layout.addWidget(look)

        place = QGroupBox("Bar")
        form = QFormLayout(place)
        self.position = _combo(POSITIONS, legend.position)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(0, len(SIZES) - 1)
        self.size.setTickPosition(QSlider.TickPosition.TicksBelow)
        sizes = [v for _, v in SIZES]
        self.size.setValue(min(range(len(sizes)), key=lambda i: abs(sizes[i] - legend.size)))
        self.size_label = QLabel("")
        sized = QHBoxLayout()
        sized.addWidget(self.size, 1)
        sized.addWidget(self.size_label)
        form.addRow("Position", self.position)
        form.addRow("Size", sized)
        layout.addWidget(place)

        self.message = QLabel("")
        role(self.message, "error")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        close = QPushButton("Close")
        close.clicked.connect(lambda: window.show_tool(None))
        layout.addWidget(close)
        layout.addStretch()

        for box in (self.mode, self.colormap, self.bands, self.above, self.below, self.position):
            box.currentIndexChanged.connect(lambda _=0: self.apply())
        self.invert.toggled.connect(lambda _=False: self.apply())
        self.size.valueChanged.connect(lambda _=0: self.apply())
        for line in (self.top, self.bottom):
            line.editingFinished.connect(self.apply)
        self._loading = False
        self.load()

    def load(self) -> None:
        """Show the range of the result on screen now."""
        view = self.window.viewport
        state = view.shown if view.shown.get("kind") == "result" else {}
        key = state.get("key")
        self._loading = True
        self.unit.setText(f"in {state['unit']}" if state.get("unit") else "")
        self.about.setText(
            f"For {state.get('scalar') or 'the shown result'}; the look applies to every result"
            if key
            else "Show a result to set its range."
        )
        custom = key in view.ranges
        self.mode.setCurrentIndex(self.mode.findData("custom" if custom else "auto"))
        if custom:
            bottom, top = view.ranges[key]
            self.bottom.setText(f"{bottom:.6g}")
            self.top.setText(f"{top:.6g}")
        self.size_label.setText(SIZES[self.size.value()][0])
        self._loading = False

    def fill(self) -> None:
        state = self.window.viewport.shown
        if state.get("kind") != "result":
            return
        self.bottom.setText(f"{state['min']:.6g}")
        self.top.setText(f"{state['max']:.6g}")
        self.mode.setCurrentIndex(self.mode.findData("custom"))
        self.apply()

    def apply(self) -> None:
        if self._loading:
            return
        view = self.window.viewport
        legend = view.legend
        legend.colormap = self.colormap.currentData()
        legend.invert = self.invert.isChecked()
        legend.bands = int(self.bands.currentData())
        legend.above = self.above.currentData()
        legend.below = self.below.currentData()
        legend.position = self.position.currentData()
        legend.size = SIZES[self.size.value()][1]
        self.size_label.setText(SIZES[self.size.value()][0])
        legend.save()
        self.message.setText("")
        key = view.shown.get("key") if view.shown.get("kind") == "result" else None
        if key is not None:
            if self.mode.currentData() == "custom":
                try:
                    bottom, top = float(self.bottom.text()), float(self.top.text())
                except ValueError:
                    if self.bottom.text() and self.top.text():
                        self.message.setText("Type the bottom and the top as numbers")
                    return
                if bottom >= top:
                    self.message.setText("The top must be above the bottom")
                    return
                view.ranges[key] = (bottom, top)
            else:
                view.ranges.pop(key, None)
        self.window._redraw_result()
