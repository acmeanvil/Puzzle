"""The Mesh tab (ADR 0019): one component's mesh recipe and every control on it.

The component is chosen at the top; its recipe's controls are listed below in order
(double-click edits, the menu moves or deletes). The tools come in ribbon groups above
the view: Sizing, Method, Inflation, Quality and Mesher. A control's options open in
the Properties dock (`ControlPanel`); Apply makes it one command. After meshing, the
view colours the elements by shape quality, and the quality box lists the measures and
the worst elements (double-click one to see where it is).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.model.schema import Component, MeshRecipe, Quantity

# Each control's fields: (key, readable name, kind). Kinds: selection[:entity], length,
# int, float, point, text.
FIELDS = {
    "size": [("selection", "Selection", "selection"), ("size", "Element size", "length")],
    "refine_near": [
        ("selection", "Near", "selection"),
        ("size", "Element size near it", "length"),
        ("near", "Out to", "length"),
        ("far", "Back to the global size by", "length"),
    ],
    "transfinite": [
        ("selection", "Edges", "selection:edge"),
        ("count", "Elements along each edge", "int"),
        ("progression", "Grading (each over the one before)", "float"),
    ],
    "box": [
        ("name", "Name", "text"),
        ("low", "Low corner", "point"),
        ("high", "High corner", "point"),
        ("size", "Element size inside", "length"),
    ],
    "ball": [
        ("name", "Name", "text"),
        ("centre", "Centre", "point"),
        ("radius", "Radius", "length"),
        ("size", "Element size inside", "length"),
    ],
    "boundary_layer": [
        ("selection", "Faces", "selection:face"),
        ("first_height", "First layer height", "length"),
        ("growth", "Growth ratio", "float"),
        ("layers", "Layers", "int"),
    ],
}
TOOLS = [
    (
        "Sizing",
        [("size", "Local size"), ("refine_near", "Refine near"), ("box", "Box"), ("ball", "Ball")],
    ),
    ("Method", [("transfinite", "Edge count")]),
    ("Inflation", [("boundary_layer", "Boundary layer")]),
]
TITLES = {kind: name for _, tools in TOOLS for kind, name in tools}


class ControlPanel(QWidget):
    """A control's options in the Properties dock; Apply makes it one Edit command."""

    def __init__(self, bench, recipe: MeshRecipe, kind: str, index: int | None, values: dict):
        from puzzle.gui.form import FormPanel
        from puzzle.settings import length_unit

        super().__init__()
        self.bench, self.recipe_id, self.kind, self.index = bench, recipe.id, kind, index
        self.unit, self.per_metre = length_unit()
        self.form = FormPanel(f"{TITLES[kind]}: {'edit' if index is not None else 'new'}")
        section = self.form.section(TITLES[kind])
        self.widgets: dict[str, object] = {}
        for key, name, what in FIELDS[kind]:
            widget, unit = self._widget(what, values.get(key))
            self.widgets[key] = (what, widget)
            section.row(key, name, widget, unit=unit)
        apply, cancel = QPushButton("Apply"), QPushButton("Cancel")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        cancel.clicked.connect(self.bench.close_tool)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(apply)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form, 1)
        layout.addLayout(row)

    def _widget(self, what: str, value):
        if what.startswith("selection"):
            box = QComboBox()
            entity = what.split(":", 1)[1] if ":" in what else None
            for name in self.bench.selections(entity):
                box.addItem(name, name)
            if value is not None:
                box.setCurrentIndex(max(0, box.findData(value)))
            return box, ""
        if what == "length":
            line = QLineEdit()
            if value is not None:
                q = Quantity.model_validate(value)
                line.setText(f"{q.to_si() * self.per_metre:g}")
            return line, self.unit
        if what == "int":
            spin = QSpinBox()
            spin.setRange(1, 100000)
            spin.setValue(int(value) if value is not None else 1)
            return spin, ""
        if what == "float":
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0.001, 100.0)
            spin.setValue(float(value) if value is not None else 1.0)
            return spin, ""
        if what == "point":
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            spins = []
            for c in value or (0.0, 0.0, 0.0):
                spin = QDoubleSpinBox()
                spin.setDecimals(4)
                spin.setRange(-1e9, 1e9)
                spin.setValue(float(c) * self.per_metre)
                row.addWidget(spin)
                spins.append(spin)
            holder.spins = spins
            return holder, self.unit
        line = QLineEdit(str(value or ""))
        return line, ""

    def values(self) -> dict:
        """The control as typed: lengths in the display unit, stored as typed."""
        from puzzle.model.units import plain
        from puzzle.settings import current

        unit = plain(current().display_units()["length"])
        out = {"kind": self.kind}
        for key, (what, widget) in self.widgets.items():
            if what.startswith("selection"):
                out[key] = widget.currentData()
            elif what == "length":
                out[key] = {"value": float(widget.text()), "unit": unit}
            elif what in ("int", "float"):
                out[key] = widget.value()
            elif what == "point":
                out[key] = [s.value() / self.per_metre for s in widget.spins]
            else:
                out[key] = widget.text()
        return out

    def apply(self) -> None:
        from puzzle.commands.model import Edit, EditOp

        try:
            value = self.values()
        except ValueError:
            self.form.set_message("Type a number for each length", error=True)
            return
        if any(v is None for v in value.values()):
            self.form.set_message(
                "Pick a selection (the component has none of this kind)", error=True
            )
            return
        op = (
            EditOp(op="set", path=f"controls.{self.index}", value=value)
            if self.index is not None
            else EditOp(op="append", path="controls", value=value)
        )
        if self.bench.window.submit(Edit(object=str(self.recipe_id), ops=[op])) is not None:
            self.bench.close_tool()
            self.bench.changed()


class MeshBench(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.comp_id = None
        self.tool: ControlPanel | None = None

        self.component = QComboBox()
        self.component.currentIndexChanged.connect(lambda _: self._component_chosen())
        self.recipe_label = QLabel("")
        self.recipe_label.setStyleSheet("color: gray")
        self.controls = QListWidget()
        self.controls.itemDoubleClicked.connect(lambda item: self.edit(self.controls.row(item)))
        self.controls.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.controls.customContextMenuRequested.connect(self._control_menu)
        self.quality = QLabel("Not meshed yet.")
        self.quality.setWordWrap(True)
        # Grows with its text: a word-wrapped label otherwise gets clipped in a column.
        self.quality.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.worst = QListWidget()
        self.worst.itemDoubleClicked.connect(self._show_worst)

        self.ribbon = QWidget()
        bar = QHBoxLayout(self.ribbon)
        bar.setContentsMargins(4, 2, 4, 2)
        self.ribbon.setStyleSheet(
            "QGroupBox { font-weight: bold; margin-top: 2px; padding-top: 14px; }"
            "QToolButton { padding: 2px 8px; }"
            "QToolButton:checked { background: #3d7bd9; color: white; border-radius: 3px; }"
        )
        self.buttons: dict[str, QToolButton] = {}

        def button(text: str, tip: str = "") -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            return b

        def group(title: str, buttons: list[QToolButton]) -> None:
            box = QGroupBox(title)
            g = QGridLayout(box)
            g.setContentsMargins(4, 2, 4, 2)
            g.setSpacing(2)
            per_row = max(1, (len(buttons) + 1) // 2)
            for n, b in enumerate(buttons):
                g.addWidget(b, n // per_row, n % per_row)
            bar.addWidget(box)

        for title, tools in TOOLS:
            buttons = []
            for kind, name in tools:
                b = button(name)
                b.clicked.connect(lambda _=False, k=kind: self.start_tool(k))
                self.buttons[kind] = b
                buttons.append(b)
            group(title, buttons)
        mesh_now = button("Mesh", "Mesh the component with this recipe")
        mesh_now.clicked.connect(self.mesh)
        self.buttons["mesh"] = mesh_now
        group("Quality", [mesh_now])
        self.mesher = {}
        for name, value in (("Gmsh", "gmsh"), ("Netgen", "netgen")):
            b = button(name, f"Mesh with {name}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, v=value: self.set_mesher(v))
            self.mesher[value] = b
        group("Mesher", list(self.mesher.values()))
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.component)
        layout.addWidget(self.recipe_label)
        hint = QLabel("Controls, in order: double-click edits; the tools are above the view.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        layout.addWidget(hint)
        layout.addWidget(self.controls, 1)
        layout.addWidget(QLabel("Quality"))
        layout.addWidget(self.quality)
        layout.addWidget(self.worst, 1)

    # --- state ------------------------------------------------------------------------

    def component_obj(self) -> Component | None:
        s = self.window.session
        if s is None or self.comp_id is None:
            return None
        obj = s.project.objects.get(self.comp_id)
        return obj if isinstance(obj, Component) else None

    def recipe(self) -> MeshRecipe | None:
        comp = self.component_obj()
        if comp is None:
            return None
        obj = self.window.session.project.objects.get(comp.mesh)
        return obj if isinstance(obj, MeshRecipe) else None

    def selections(self, entity: str | None = None) -> list[str]:
        """The component's selections as Part/Name, of one entity kind when given."""
        from puzzle.model.schema import Geometry

        comp = self.component_obj()
        if comp is None:
            return []
        project = self.window.session.project
        out = []
        for part in comp.parts:
            geo = project.objects.get(part.geometry)
            if isinstance(geo, Geometry):
                out += [
                    f"{part.name}/{s.name}"
                    for s in geo.selections
                    if entity is None or s.entity == entity
                ]
        return out

    def refresh(self) -> None:
        s = self.window.session
        if s is None:
            return
        comps = sorted(s.project.of_type(Component), key=lambda c: c.name)
        self.component.blockSignals(True)
        self.component.clear()
        for c in comps:
            self.component.addItem(c.name, str(c.id))
        if self.comp_id is None and comps:
            self.comp_id = comps[0].id
        index = self.component.findData(str(self.comp_id))
        self.component.setCurrentIndex(index)
        if index < 0:
            self.comp_id = None
        self.component.blockSignals(False)
        recipe = self.recipe()
        self.recipe_label.setText(
            f"Recipe: {recipe.name}" if recipe else "This component has no mesh recipe"
        )
        self.controls.clear()
        for i, c in enumerate(recipe.controls if recipe else []):
            self.controls.addItem(QListWidgetItem(f"{i + 1}. {c.describe()}"))
        for value, b in self.mesher.items():
            b.setChecked(recipe is not None and recipe.mesher == value)
        for b in self.buttons.values():
            b.setEnabled(recipe is not None)
        self._show_quality()

    def _component_chosen(self) -> None:
        from uuid import UUID

        data = self.component.currentData()
        self.comp_id = UUID(data) if data else None
        self.close_tool(refresh=False)
        self.refresh()
        self.window.show_mesh_bench()

    def changed(self) -> None:
        """After an edit: list it, and mesh again if the user previews on every change."""
        from puzzle.settings import current

        self.refresh()
        if current().get("meshing.preview") == "live":
            self.mesh()

    # --- tools ------------------------------------------------------------------------

    def start_tool(self, kind: str, index: int | None = None) -> ControlPanel | None:
        recipe = self.recipe()
        if recipe is None:
            return None
        if index is not None:
            values = recipe.controls[index].model_dump(mode="json")
        else:
            size = recipe.global_size.model_dump(mode="json") if recipe.global_size else None
            values = {"size": size, "count": 10, "progression": 1.0, "growth": 1.2, "layers": 5}
            values["name"] = "Box" if kind == "box" else "Ball"
        self.close_tool(refresh=False)
        self.tool = ControlPanel(self, recipe, kind, index, values)
        self.window.show_tool_panel(self.tool)
        return self.tool

    def edit(self, index: int) -> None:
        recipe = self.recipe()
        if recipe is not None and 0 <= index < len(recipe.controls):
            self.start_tool(recipe.controls[index].kind, index)

    def close_tool(self, refresh: bool = True) -> None:
        if self.tool is not None:
            self.tool = None
            self.window.close_tool_panel()
        if refresh:
            self.refresh()

    def _control_menu(self, pos) -> None:
        from puzzle.commands.model import Edit, EditOp

        row = self.controls.row(self.controls.itemAt(pos)) if self.controls.itemAt(pos) else -1
        recipe = self.recipe()
        if row < 0 or recipe is None:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.edit(row))
        controls = [c.model_dump(mode="json") for c in recipe.controls]

        def move(by: int) -> None:
            controls.insert(row + by, controls.pop(row))
            self._submit(
                Edit(object=str(recipe.id), ops=[EditOp(op="set", path="controls", value=controls)])
            )

        if row > 0:
            menu.addAction("Move up", lambda: move(-1))
        if row < len(controls) - 1:
            menu.addAction("Move down", lambda: move(1))
        menu.addSeparator()
        menu.addAction(
            "Delete",
            lambda: self._submit(
                Edit(object=str(recipe.id), ops=[EditOp(op="remove", path=f"controls.{row}")])
            ),
        )
        menu.exec(self.controls.mapToGlobal(pos))

    def _submit(self, command) -> None:
        if self.window.submit(command) is not None:
            self.changed()

    def set_mesher(self, value: str) -> None:
        from puzzle.commands.model import Edit, EditOp

        recipe = self.recipe()
        if recipe is None or recipe.mesher == value:
            self.refresh()
            return
        self._submit(
            Edit(object=str(recipe.id), ops=[EditOp(op="set", path="mesher", value=value)])
        )

    # --- meshing and quality ------------------------------------------------------------

    def mesh(self):
        comp = self.component_obj()
        if comp is None:
            return None
        return self.window.start_meshing(comp, then=self._meshed)

    def _meshed(self, meshed) -> None:
        self._show_quality()
        self.window.show_mesh_bench()

    def _show_quality(self) -> None:
        comp = self.component_obj()
        q = self.window.mesh_quality.get(str(comp.id)) if comp is not None else None
        self.worst.clear()
        if not q:
            self.quality.setText("Not meshed yet: Mesh (in the ribbon) shows the quality here.")
            return
        m = q["measures"]
        self.quality.setText(
            f"{q['elements']:,} elements. Shape: worst {m['shape']['min']:.3f}, mean "
            f"{m['shape']['mean']:.3f} (1 is a regular element). Edge aspect ratio: worst "
            f"{m['aspect_ratio']['max']:.1f}. The view colours elements by shape; the worst "
            "are listed below (double-click one to see it)."
        )
        for w in q.get("worst", []):
            x, y, z = w["centre"]
            item = QListWidgetItem(f"Shape {w['shape']:.3f} at ({x:.4g}, {y:.4g}, {z:.4g}) m")
            item.setData(Qt.ItemDataRole.UserRole, w["centre"])
            self.worst.addItem(item)

    def _show_worst(self, item) -> None:
        centre = item.data(Qt.ItemDataRole.UserRole)
        if centre is not None:
            self.window.viewport.mark_point(tuple(centre), "worst element")
