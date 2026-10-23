"""One editor component for long forms (ADR 0036): a property grid in folding sections,
a description box for the focused row, and the message that blocks OK at the top.

    form = FormPanel()
    duty = form.section("Duty")
    duty.add_widget(table)                       # full width
    how = form.section("Stress and life")
    how.row("design_life", "Design life", line, unit="duties")
    form.set_visible("design_life", False)       # label, field and unit together
    form.set_message("Needs a tensile strength", error=True)

Rows' descriptions come from `DESCRIPTIONS` (shared with tooltips, and later the
Advisor, ADR 0040), or from the row's own `help`. The panel scrolls inside itself, so
a dialog using it is never taller than the screen.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Field key -> what it means, with its source where it has one. One table for the
# description box, tooltips and the Advisor.
DESCRIPTIONS = {
    "name": "The name the tree and the results list show.",
    "loading": "Each kind of cycle. Fully reversed: the case and its reverse (mean zero). "
    "Zero-based: the case, then nothing (amplitude and mean half of it). Range: between a "
    "range combination's case 1 and case 2. Scale multiplies the stresses; cycles count "
    "per repetition of the duty (Miner's rule sums them).",
    "stress": "How each node's stress tensor becomes one number. Signed von Mises keeps the "
    "sign of the largest principal stress, so compression doesn't count as tension.",
    "mean_stress": "The completely reversed stress as damaging as the cycle's amplitude and "
    "mean (Shigley Sec. 6-12). A compressive mean gets no benefit.",
    "life_model": "Where cycles to failure come from: Shigley's S-N line from the tensile "
    "strength, the material's measured S-N curve, or its strain-life model.",
    "sn_curve": "Which of the material's S-N curves to read (Material builder, Fatigue tab).",
    "design_life": "How many repetitions of the duty the part must last. Damage and the "
    "safety factor are taken at it.",
    "surface": "Surface factor ka = a·Sut^b (Shigley Eq. 6-19, Table 6-2).",
    "size": "Size factor kb (Shigley Eqs. 6-20 to 6-25): rotating rounds, non-rotating "
    "rounds and rectangles by their effective diameter; axial loading has none.",
    "diameter": "The round's diameter, for its size factor.",
    "height": "The rectangle's height h, for de = 0.808√(h·b).",
    "width": "The rectangle's width b, for de = 0.808√(h·b).",
    "reliability": "Reliability factor ke = 1 − 0.08·za (Shigley Eq. 6-29, Table 6-5).",
    "temperature": "Temperature factor kd (Shigley Eq. 6-27, 21 to 538 °C).",
    "misc_factor": "Other effects kf: plating, corrosion, fretting (Shigley Sec. 6-9).",
    "endurance_limit": "A measured rotating-beam endurance limit S'e. Blank: 0.5 Sut, "
    "steels only (Shigley Eq. 6-8).",
    "notch_from": "The fatigue stress-concentration factor, given, or Kf = 1 + q(Kt − 1) with "
    "Neuber's notch sensitivity q (Shigley Eqs. 6-32 to 6-35, steels). Leave it at 1 where the "
    "mesh resolves the notch: its stresses already include Kt.",
    "notch_factor": "Kf, multiplying the alternating stress.",
    "notch_kt": "The theoretical stress-concentration factor Kt, from charts or a fine model.",
    "notch_radius": "The notch root radius r, for Neuber's √a/√r.",
    "notch_shear": "Neuber's constant for torsion rather than bending and axial loads.",
    "notch_on_mean": "Kf on the mean stress too. Shigley applies it to the alternating "
    "stress only for ductile materials.",
    "method": "How the cases combine.",
    "quantity": "The result compared at each node across the cases.",
    "extreme": "Keep the largest or the smallest over the cases.",
    "source": "The results the equation reads: a study's latest run, or another derived result.",
    "expression": "An expression over the names listed below, evaluated at each node.",
    "unit": "The kind of unit the value has, so it shows in the display units.",
}


class Section(QWidget):
    """A folding group of rows: a header button, then label | field | unit rows."""

    def __init__(self, panel: FormPanel, title: str, open: bool = True) -> None:
        super().__init__()
        self.panel = panel
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(2)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(open)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.header.setArrowType(Qt.ArrowType.DownArrow if open else Qt.ArrowType.RightArrow)
        self.header.toggled.connect(self.set_open)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(16, 0, 0, 0)
        self.grid.setColumnStretch(1, 1)
        self.grid.setHorizontalSpacing(10)
        self.body.setVisible(open)
        column.addWidget(self.header)
        column.addWidget(self.body)

    def set_open(self, open: bool) -> None:
        self.header.setChecked(open)
        self.header.setArrowType(Qt.ArrowType.DownArrow if open else Qt.ArrowType.RightArrow)
        self.body.setVisible(open)

    def row(self, key: str, label: str, widget: QWidget, unit: str = "", help: str = ""):
        """A field: its label, the widget, and its unit beside it."""
        r = self.grid.rowCount()
        name = QLabel(label)
        name.setBuddy(widget)
        units = QLabel(unit)
        units.setStyleSheet("color: gray")
        self.grid.addWidget(name, r, 0, Qt.AlignmentFlag.AlignRight)
        self.grid.addWidget(widget, r, 1)
        self.grid.addWidget(units, r, 2)
        if isinstance(widget, QComboBox):  # never narrower than its longest name
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.panel._register(key, (name, widget, units), help or DESCRIPTIONS.get(key, ""))
        return widget

    def add_widget(self, widget: QWidget, key: str = "", help: str = "") -> QWidget:
        """A full-width widget: a table, a check box, a note."""
        self.grid.addWidget(widget, self.grid.rowCount(), 0, 1, 3)
        if isinstance(widget, QTableWidget):
            fit_table(widget)
        if key:
            self.panel._register(key, (widget,), help or DESCRIPTIONS.get(key, ""))
        return widget


def fit_table(table: QTableWidget) -> None:
    """Columns as wide as their contents, headers that wrap rather than clip."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setMinimumHeight(110)


class _Focus(QObject):
    """Shows a row's description when its field gets focus or the mouse enters it."""

    def __init__(self, panel: FormPanel, key: str) -> None:
        super().__init__(panel)
        self.panel, self.key = panel, key

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.Enter):
            self.panel.describe(self.key)
        return False


class FormPanel(QWidget):
    """Sections of rows in a scroll area, a message banner on top, a description below."""

    def __init__(self, default_description: str = "", parent=None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.hide()
        column.addWidget(self.message)
        self.inner = QWidget()
        self.sections_layout = QVBoxLayout(self.inner)
        self.sections_layout.setContentsMargins(4, 0, 8, 0)
        self.sections_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.inner)
        column.addWidget(scroll, 1)
        self.description = QLabel(default_description)
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(48)
        self.description.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.description.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.description.setStyleSheet(
            "QLabel { color: gray; border-top: 1px solid rgba(128,128,128,90); padding: 6px; }"
        )
        column.addWidget(self.description)
        self.default_description = default_description
        self.rows: dict[str, tuple] = {}
        self.descriptions: dict[str, str] = {}
        self.sections: list[Section] = []

    def section(self, title: str, open: bool = True) -> Section:
        s = Section(self, title, open)
        self.sections_layout.insertWidget(self.sections_layout.count() - 1, s)
        self.sections.append(s)
        return s

    def _register(self, key: str, widgets: tuple, description: str) -> None:
        self.rows[key] = widgets
        self.descriptions[key] = description
        for w in widgets:
            if description:
                w.setToolTip(description)
            w.installEventFilter(_Focus(self, key))

    def describe(self, key: str) -> None:
        self.description.setText(self.descriptions.get(key) or self.default_description)

    def set_visible(self, key: str, visible: bool) -> None:
        """A row's label, field and unit shown or hidden together."""
        for w in self.rows.get(key, ()):
            w.setVisible(visible)

    def set_message(self, text: str | None, error: bool = False) -> None:
        """The banner: an error that blocks OK (red), or a result line; None hides it."""
        self.message.setVisible(bool(text))
        self.message.setText(text or "")
        style = (
            "background: rgba(229, 83, 75, 40); color: #ff8a80;"
            if error
            else "background: rgba(79, 143, 230, 30);"
        )
        self.message.setStyleSheet(f"QLabel {{ {style} padding: 6px; border-radius: 4px; }}")


def fit_to_screen(dialog, width: int, height: int) -> None:
    """A dialog's size, never taller or wider than most of the screen."""
    screen = dialog.screen().availableGeometry() if dialog.screen() else None
    if screen is not None:
        width = min(width, int(screen.width() * 0.9))
        height = min(height, int(screen.height() * 0.85))
    dialog.resize(width, height)
