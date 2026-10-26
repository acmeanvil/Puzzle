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
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role
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
        role(self.recipe_label, "muted")
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

        from puzzle.gui.ribbon import Ribbon

        self.ribbon = Ribbon()
        self.buttons: dict[str, QToolButton] = {}
        button, group = Ribbon.button, self.ribbon.group

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
        seeds = button("Show seeds", "Where the edges' nodes will fall, before meshing")
        seeds.clicked.connect(self.show_seeds)
        self.buttons["seeds"] = seeds
        group("Quality", [mesh_now, seeds])
        self.mesher = {}
        for name, value in (("Gmsh", "gmsh"), ("Netgen", "netgen")):
            b = button(name, f"Mesh with {name}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, v=value: self.set_mesher(v))
            self.mesher[value] = b
        options = button("Gmsh options…", "Any of Gmsh's own mesh options, searchable")
        options.clicked.connect(self.edit_gmsh_options)
        self.buttons["gmsh_options"] = options
        group("Mesher", [*self.mesher.values(), options])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.component)
        layout.addWidget(self.recipe_label)
        hint = QLabel("Controls, in order: double-click edits; the tools are above the view.")
        hint.setWordWrap(True)
        role(hint, "muted")
        layout.addWidget(hint)
        layout.addWidget(self.controls, 1)
        layout.addWidget(QLabel("Methods"))
        self.methods = QLabel("")
        self.methods.setWordWrap(True)
        self.methods.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.methods)
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

    def edit_gmsh_options(self):
        """Gmsh options…: search Gmsh's mesh options, set some; OK is one command."""
        from puzzle.commands.model import Edit, EditOp

        recipe = self.recipe()
        if recipe is None:
            return None
        dialog = GmshOptionsDialog(recipe.gmsh_options, self)
        if not self.window.run_dialog(dialog):
            return None
        try:
            chosen = dialog.chosen()
        except ValueError as exc:
            self.window.statusBar().showMessage(str(exc), 8000)
            return None
        if chosen == recipe.gmsh_options:
            return None
        op = EditOp(op="set", path="gmsh_options", value=chosen)
        return self._submit(Edit(object=str(recipe.id), ops=[op]))

    # --- meshing and quality ------------------------------------------------------------

    def mesh(self):
        comp = self.component_obj()
        if comp is None:
            return None
        return self.window.start_meshing(comp, then=self._meshed)

    def show_seeds(self):
        """The edge seeds with the recipe's sizes and controls, over the preview."""
        import copy

        from puzzle.mesh import generate_mesh
        from puzzle.project.layout import WORK_DIR

        comp = self.component_obj()
        if comp is None:
            return None
        s = self.window.session
        project = copy.deepcopy(s.project)
        out = s.root / WORK_DIR / "gui-seeds" / str(comp.id)

        def work(_emit):
            return generate_mesh(project, comp.id, s.root, out, seeds=True)

        def done(result) -> None:
            self.window.show_mesh_bench()
            self.window.viewport.show_seeds(result.seeds)
            self.window.statusBar().showMessage(
                f"{len(result.seeds):,} nodes along the edges", 8000
            )

        return self.window.background("Placing edge seeds…", work, done)

    def _meshed(self, meshed) -> None:
        self._show_quality()
        self.window.show_mesh_bench()

    def body_methods(self) -> list[tuple[str, str, str, list]]:
        """(body, method, why, its faces) for the component's bodies, from its preview."""
        comp = self.component_obj()
        cached = self.window.previews.get(str(comp.id)) if comp is not None else None
        if not cached:
            return []
        result = cached[1]
        out = []
        for v, (method, why) in sorted(result.methods.items()):
            part, body = result.names.get(v, ("?", f"body {v[1]}"))
            out.append((f"{part}/{body}", method, why, result.boundaries.get(v, [])))
        return out

    def show_methods(self) -> None:
        rows = self.body_methods()
        if not rows:
            self.methods.setText("")
            return
        lines = [
            f"{body}: {'hexahedra can be used' if method == 'hexahedra' else 'tetrahedra'} ({why})"
            for body, method, why, _faces in rows
        ]
        self.methods.setText(
            "\n".join(lines)
            + "\nBlock-like bodies are highlighted; Element shape → Hexahedra meshes them so. "
            "Sweeping isn't offered: Gmsh can't sweep imported (OpenCASCADE) bodies."
        )

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


class GmshOptionsDialog(QDialog):
    """Gmsh's mesh options with their defaults and descriptions, a search box above; a
    value typed in "Your value" sets the option for this recipe, an empty one clears it."""

    def __init__(self, current: dict, parent=None) -> None:
        from PySide6.QtWidgets import QAbstractItemView, QDialogButtonBox, QTableWidget

        from puzzle.mesh.gmsh_options import mesh_options

        super().__init__(parent)
        self.setWindowTitle("Gmsh options")
        self.options = list(mesh_options())
        layout = QVBoxLayout(self)
        note = QLabel(
            "Gmsh's own mesh options, for what the Mesh tab's controls don't cover. Type a "
            "value to set one for this recipe; clear it to go back to Gmsh's default."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: a word of the name or the description")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QTableWidget(len(self.options), 4)
        self.table.setHorizontalHeaderLabels(["Option", "Default", "Your value", "What it does"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for r, o in enumerate(self.options):
            for c, text in enumerate([o.name, f"{o.default:g}" if not o.text else o.default]):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, item)
            mine = current.get(o.name)
            self.table.setItem(r, 2, QTableWidgetItem("" if mine is None else f"{mine}"))
            what = QTableWidgetItem(o.description)
            what.setFlags(what.flags() & ~Qt.ItemFlag.ItemIsEditable)
            what.setToolTip(o.description)
            self.table.setItem(r, 3, what)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(3, 420)
        layout.addWidget(self.table)
        if not self.options:
            layout.addWidget(QLabel("Gmsh's option list could not be read (no gmsh program)."))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(900, 520)

    def _filter(self, words: str) -> None:
        terms = words.lower().split()
        for r, o in enumerate(self.options):
            hit = all(t in o.name.lower() or t in o.description.lower() for t in terms)
            self.table.setRowHidden(r, not hit)

    def set_value(self, name: str, text: str) -> None:
        row = next(r for r, o in enumerate(self.options) if o.name == name)
        self.table.item(row, 2).setText(text)

    def chosen(self) -> dict:
        """The options given a value: numbers for number options, text for text ones."""
        out = {}
        for r, o in enumerate(self.options):
            text = self.table.item(r, 2).text().strip()
            if not text:
                continue
            if o.text:
                out[o.name] = text
            else:
                try:
                    out[o.name] = float(text)
                except ValueError as exc:
                    raise ValueError(f"{o.name} takes a number, not '{text}'") from exc
        return out
