"""The properties panel: an object's fields as an editable tree.

Generated from the object's own data, so every schema field is editable without a
hand-written form. Each edit becomes one Edit command (`set` at a dotted path), so the
GUI changes the project the way every other client does (ADR 0015). Quantities show
as "5 mm" and are typed the same way; references to other objects show their names
and are read-only here.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

PATH = Qt.ItemDataRole.UserRole
READ_ONLY = {"id", "kind"}


def is_quantity(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"value", "unit"}


def format_value(value: Any) -> str:
    if is_quantity(value):
        unit = value["unit"]
        return f"{value['value']:g}" if unit == "dimensionless" else f"{value['value']:g} {unit}"
    if isinstance(value, str):
        return value
    return json.dumps(value)


def parse_value(text: str, old: Any) -> Any:
    """The typed text, as the type the field held: "5 mm" for a quantity, JSON otherwise."""
    text = text.strip()
    if is_quantity(old):
        number, _, unit = text.partition(" ")
        return {"value": float(number), "unit": unit.strip() or old["unit"]}
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

    def show_object(self, object_id: str, data: dict[str, Any], path: str, names: dict) -> None:
        """Show `data` (the object's JSON form) from `path` down; `names` maps ids to names."""
        self._filling = True
        self.clear()
        self.object_id = object_id
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
            sub = f"{path}.{key}" if path else str(key)
            if isinstance(value, (dict, list)) and not is_quantity(value):
                label = f"[{len(value)}]" if isinstance(value, list) else ""
                item = QTreeWidgetItem(parent, [str(key), label])
                self._fill(item, value, sub, names)
                continue
            reference = isinstance(value, str) and value in names and key not in READ_ONLY
            shown = names[value] if reference else None
            item = QTreeWidgetItem(parent, [str(key), shown or format_value(value)])
            if key not in READ_ONLY and not reference:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setData(1, PATH, (sub, value))

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 1 or item.data(1, PATH) is None:
            return
        path, old = item.data(1, PATH)
        try:
            value = parse_value(item.text(1), old)
        except ValueError:
            self._filling = True
            item.setText(1, format_value(old))
            self._filling = False
            return
        self.edited.emit(self.object_id, path, value)
