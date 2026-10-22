"""Reattaching selections in bulk (G3, ADR 0014 decision 4).

One row per member that needs a decision: re-found by its anchor (shown with its new
name, to confirm) or detached (its ranked candidates, nearest first, or remove it).
"Accept all" takes every proposal; a selection whose new members a simple rule picks
exactly offers to keep that rule, so the next regeneration resolves it unaided.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from puzzle.geometry.naming import describe_rule

REMOVE = "(remove from the selection)"


def needs_decision(reports: list[dict]) -> list[tuple[dict, dict]]:
    """(selection report, member) pairs that are re-found or detached."""
    return [(r, m) for r in reports for m in r["members"] if m["status"] in ("refound", "detached")]


def proposal(member: dict) -> str | None:
    """What accepting proposes: the re-found name, else the nearest candidate."""
    if member["now"]:
        return member["now"]
    return member["candidates"][0]["name"] if member["candidates"] else None


class ReattachDialog(QDialog):
    """Rows of members with a choice each; `choices()` gives what `reattach_ops` takes."""

    def __init__(self, geometry: str, reports: list[dict], rules: dict[str, dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Reattach selections of {geometry}")
        self.resize(760, 420)
        self.rows = needs_decision(reports)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Members the regeneration could not keep by name. Re-found ones moved "
                "only in name; detached ones list the nearest entities first."
            )
        )
        self.table = QTableWidget(len(self.rows), 4)
        self.table.setHorizontalHeaderLabels(["Selection", "Was", "Status", "Now"])
        self.choosers: list[QComboBox] = []
        for i, (report, member) in enumerate(self.rows):
            self.table.setItem(i, 0, QTableWidgetItem(report["selection"]))
            self.table.setItem(i, 1, QTableWidgetItem(member["name"]))
            status = {"refound": "found again", "detached": "not found"}[member["status"]]
            self.table.setItem(i, 2, QTableWidgetItem(status))
            box = QComboBox()
            names = [member["now"]] if member["now"] else []
            names += [c["name"] for c in member["candidates"] if c["name"] not in names]
            for n in names:
                d = next((c["distance"] for c in member["candidates"] if c["name"] == n), None)
                box.addItem(n if d is None else f"{n}   (off by {d:.3g})", n)
            box.addItem(REMOVE, None)
            self.table.setCellWidget(i, 3, box)
            self.choosers.append(box)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        self.rule_boxes: dict[str, QCheckBox] = {}
        for selection, rule in rules.items():
            box = QCheckBox(f"Keep '{selection}' as a rule: {describe_rule(rule)}")
            box.setChecked(True)
            box.setProperty("rule", rule)
            layout.addWidget(box)
            self.rule_boxes[selection] = box
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Accept")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choices(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for (report, member), box in zip(self.rows, self.choosers, strict=True):
            choice = out.setdefault(report["selection"], {"replace": {}})
            choice["replace"][member["name"]] = box.currentData()
        for selection, box in self.rule_boxes.items():
            if box.isChecked():
                out.setdefault(selection, {})["rule"] = box.property("rule")
        return out


def accept_all_choices(reports: list[dict], rules: dict[str, dict]) -> dict[str, dict]:
    """What "Accept" gives with every row left at its proposal and every rule kept."""
    out: dict[str, dict] = {}
    for report, member in needs_decision(reports):
        out.setdefault(report["selection"], {"replace": {}})["replace"][member["name"]] = proposal(
            member
        )
    for selection, rule in rules.items():
        out.setdefault(selection, {})["rule"] = rule
    return out
