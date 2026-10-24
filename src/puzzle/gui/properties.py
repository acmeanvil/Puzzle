"""The properties panel: an object's fields as an editable tree.

Generated from the object's own data, so every schema field is editable without a
hand-written form. Each edit becomes one Edit command (`set` at a dotted path), so the
GUI changes the project the way every other client does (ADR 0015). Quantities show
as "5 mm" and are typed the same way. Anything with a fixed set of values (another
object, a selection, a solver, a schema choice, yes or no) is a dropdown of readable
names (`gui.choices`); ids and internal keys are never shown, only stored.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHeaderView, QTreeWidget, QTreeWidgetItem

from puzzle.gui.choices import label

PATH = Qt.ItemDataRole.UserRole
READ_ONLY = {"kind"}
HIDDEN = {"id"}  # internal: never shown
# Plain numbers stored in a fixed unit: the unit goes with the name.
UNITS = {"translation": " (m)", "rotation": " (degrees)", "facet_angle": " (degrees)"}
# What an unset optional value says (ADR 0029: never "null").
UNSET = {"source_unit": "From the file", "temperature": "Any temperature"}
NONE_TEXT = "(none)"


def _is_geometry(d: dict) -> bool:
    return "facet_angle" in d and "history" in d


def _faceted(d: dict) -> bool:
    return str(d.get("source") or "").lower().endswith((".stl", ".obj", ".3mf"))


# Fields that only mean something in one mode of their object: (is this the object,
# does the field apply). A field that doesn't apply isn't shown (ADR 0029).
APPLIES = {
    "source": (_is_geometry, lambda d: d.get("source") is not None),
    "source_unit": (_is_geometry, lambda d: d.get("source") is not None),
    "facet_angle": (_is_geometry, _faceted),
    "history": (_is_geometry, lambda d: d.get("source") is None),
}


def applies(owner: Any, key: Any) -> bool:
    rule = APPLIES.get(key) if isinstance(owner, dict) else None
    return rule is None or not rule[0](owner) or rule[1](owner)


def is_quantity(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"value", "unit"}


def format_value(value: Any) -> str:
    if is_quantity(value):
        from puzzle.model.units import pretty, shown
        from puzzle.settings import current

        unit = value["unit"]
        if unit == "dimensionless":
            return f"{value['value']:g}"
        v, u = shown(value["value"], unit, current().display_units())  # ADR 0024
        return f"{v:.6g} {pretty(u)}"
    if value is None:
        return NONE_TEXT
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return f"{value:.6g}"
    return json.dumps(value)


STEPS = "steps"  # marks a feature's steps row, edited as text
SHOWN = Qt.ItemDataRole.UserRole + 7
ALL_STEPS = "All steps"


def steps_text(steps: list[int]) -> str:
    return ", ".join(str(s) for s in steps) if steps else ALL_STEPS


def parse_steps(text: str) -> list[int]:
    """ "2, 3" or "2 3" -> [2, 3]; empty or "All steps" -> [] (every step)."""
    text = text.strip()
    if not text or text.lower() in (ALL_STEPS.lower(), "all"):
        return []
    steps = sorted({int(t) for t in text.replace(",", " ").split()})
    if steps[0] < 1:
        raise ValueError("steps are numbered from 1")
    return steps


def parse_value(text: str, old: Any) -> Any:
    """The typed text, as the type the field held: "5 mm" for a quantity, JSON otherwise."""
    if old == STEPS:
        return parse_steps(text)
    text = text.strip()
    if is_quantity(old):
        from puzzle.model.units import plain, shown
        from puzzle.settings import current

        number, _, unit = text.partition(" ")
        if not unit.strip():  # a bare number is in the unit shown
            unit = shown(old["value"], old["unit"], current().display_units())[1]
        return {"value": float(number), "unit": plain(unit)}
    if old is None and text in (NONE_TEXT, ""):
        return None
    if isinstance(old, str) or old is None:
        try:
            parsed = json.loads(text)
        except ValueError:
            return text
        return parsed if old is None else text
    if isinstance(old, bool):
        return text.lower() in ("true", "1", "yes", "on")
    return json.loads(text)


class PropertiesPanel(QTreeWidget):
    """Shows one object (or part of one, from `path` down)."""

    edited = Signal(str, str, object)  # object id, dotted path, new value

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Property", "Value"])
        # Names as wide as they need, values the rest: the panel shares a narrow column.
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setStretchLastSection(True)
        self.object_id = ""
        self._filling = False
        self.itemChanged.connect(self._changed)

    def show_object(
        self, object_id: str, data: dict[str, Any], path: str, names: dict, choices=None
    ) -> None:
        """Show `data` (the object's JSON form) from `path` down; `names` maps ids to
        names; `choices(path)` gives a field's [(label, value)] or None (free text)."""
        self._filling = True
        self.clear()
        self.object_id = object_id
        self.choices = choices or (lambda _path: None)
        node: Any = data
        for part in [p for p in path.split(".") if p]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        self._fill(self.invisibleRootItem(), node, path, names)
        self.expandToDepth(1)
        self._filling = False

    def show_text(self, rows: dict[str, Any]) -> None:
        """Read-only key/value rows (a run, a section)."""
        self._filling = True
        self.clear()
        self.object_id = ""
        for key, value in rows.items():
            QTreeWidgetItem(self.invisibleRootItem(), [key, format_value(value)])
        self._filling = False

    def _fill(self, parent, node: Any, path: str, names: dict) -> None:
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if key in HIDDEN or not applies(node, key):
                continue
            sub = f"{path}.{key}" if path else str(key)
            if isinstance(node, list):
                xyz = len(node) == 3 and all(isinstance(v, (int, float)) for v in node)
                title = "XYZ"[int(key)] if xyz else item_title(key, value, names)
            else:
                title = label(str(key)) + UNITS.get(str(key), "")
            if key == "steps" and isinstance(node, dict) and "selection" in node:
                # A feature's solve steps (ADR 0037): typed as "2, 3"; empty is every step.
                item = QTreeWidgetItem(parent, ["Applies in steps", steps_text(value)])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setData(1, PATH, (sub, STEPS))
                item.setData(1, SHOWN, steps_text(value))  # put back if the text won't parse
                continue
            if isinstance(value, (dict, list)) and not is_quantity(value):
                if not value:  # nothing to show: adding is in the item's context menu
                    continue
                item = QTreeWidgetItem(parent, [title, ""])
                self._fill(item, value, sub, names)
                continue
            options = None if key in READ_ONLY else self.choices(sub)
            if options is None and isinstance(value, bool):
                options = [("Yes", True), ("No", False)]
            if options is None and isinstance(value, str) and value in names:
                options = [(names[value], value)]  # a reference with nothing to pick from
            shown = names.get(value, format_value(value)) if isinstance(value, str) else None
            if value is None and str(key) in UNSET:
                shown = UNSET[str(key)]
            item = QTreeWidgetItem(parent, [title, shown or format_value(value)])
            if key in READ_ONLY:
                item.setText(1, label(value) if isinstance(value, str) else format_value(value))
                continue
            if options is not None:
                self._dropdown(item, sub, value, options)
                continue
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setData(1, PATH, (sub, value))

    def _dropdown(self, item, path: str, value, options) -> None:
        """A choice field: readable names shown, the value itself stored."""
        box = QComboBox()
        box.setProperty("path", path)
        for text, v in options:
            box.addItem(text, v)
        index = next((i for i, (_, v) in enumerate(options) if v == value), -1)
        if index < 0:  # a value outside the choices: show it rather than hide it
            box.addItem(format_value(value), value)
            index = box.count() - 1
        box.setCurrentIndex(index)
        item.setText(1, "")
        box.currentIndexChanged.connect(
            lambda _i, b=box: (
                None
                if self._filling
                else self.edited.emit(self.object_id, b.property("path"), b.currentData())
            )
        )
        self.setItemWidget(item, 1, box)

    def dropdown(self, path: str) -> QComboBox | None:
        """The dropdown of a field, by its dotted path (tests, the agent's highlights)."""
        stack = [self.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                child = item.child(i)
                box = self.itemWidget(child, 1)
                if isinstance(box, QComboBox) and box.property("path") == path:
                    return box
                stack.append(child)
        return None

    choices = staticmethod(lambda _path: None)

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 1 or item.data(1, PATH) is None:
            return
        path, old = item.data(1, PATH)
        try:
            value = parse_value(item.text(1), old)
        except ValueError:
            self._filling = True
            item.setText(1, item.data(1, SHOWN) if old == STEPS else format_value(old))
            self._filling = False
            return
        self.edited.emit(self.object_id, path, value)


def item_title(index, value, names: dict) -> str:
    """A list item by what it is, not its index: its name, or its type and selection
    ("Force on Bar/Tip"), or a history step's number, kind and name."""
    if isinstance(value, dict):
        if "kind" in value and "name" in value and "args" in value:  # a geometry step
            return f"{int(index) + 1}. {label(value['kind'])} {value['name']}"
        if "name" in value:
            return str(value["name"])
        if "type" in value:
            on = value.get("selection")
            return f"{label(value['type'])} on {on}" if on else label(value["type"])
        if "material" in value:
            return f"{names.get(value['material'], 'Material')} on {value.get('selection', '')}"
    if isinstance(value, str):
        return f"{int(index) + 1}"
    return f"{int(index) + 1}"
