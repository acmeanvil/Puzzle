"""Frame member checks in the GUI (ADR 0079 step 3).

- `edit_design`: Model → Components → Member design… (one form, one Edit): which members
  (every one, a part, a group, a member), LRFD or ASD, the unbraced lengths and K
  factors, a deflection limit. The same as MCP `member_design`.
- `MemberTable`: the Members panel (Window → Members, Results → Member checks): for a
  run, one row per member with its section, governing ratio, check, clause and
  combination, worst first, failing ratios marked; a selected member's checks below.
  Exports to CSV. Evaluated from the stored run on demand (`frames.design`).
"""

from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.quick_form import Ask
from puzzle.gui.theme import role

METHODS = [("LRFD (load and resistance factors)", "LRFD"), ("ASD (allowable strength)", "ASD")]
HEADERS = ["Member", "Section", "Ratio", "Governing check", "Clause", "Combination", "At"]
DETAIL = ["Check", "Limit state", "Clause", "Demand", "Available", "Ratio", "At"]


def _targets(project, comp) -> list[tuple[str, str]]:
    from puzzle.frames import frame_lines

    out = [("Every member", "")]
    for part, ln in frame_lines(project, comp).items():
        out.append((f"Part {part}", part))
        out += [(f"Group {g} ({part})", f"{part}/{g}") for g in ln.groups()]
        out += [(f"Member {m.name} ({part})", f"{part}/{m.name}") for m in ln.members]
    return out


def _quantity(text: str):
    from puzzle.model.schema import Quantity

    number, _, unit = text.strip().partition(" ")
    return Quantity(value=float(number), unit=unit.strip() or "m")


def _check_form(values: dict) -> str | None:
    for key in ("unbraced_length", "length_x", "length_y"):
        text = (values.get(key) or "").strip()
        if text:
            try:
                _quantity(text)
            except ValueError:
                return f"'{text}': give a number and a unit, e.g. 10 ft"
    for key in ("k_x", "k_y", "deflection_limit"):
        text = (values.get(key) or "").strip()
        if text:
            try:
                if float(text) <= 0:
                    raise ValueError
            except ValueError:
                return f"'{text}' must be a positive number"
    return None


def edit_design(window, comp):
    """One form setting a member design entry, as one Edit of the component."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import MemberDesign

    targets = _targets(window.session.project, comp)
    if len(targets) == 1:
        window.toasts.show(
            "This component has no frame: make one with a frame geometry step.", state="info"
        )
        return None
    got = window.ask_form(
        f"Member design of {comp.name}",
        [
            Ask("selection", "Members", choices=targets),
            Ask("method", "Method", choices=METHODS),
            Ask(
                "unbraced_length",
                "Unbraced length L_b",
                kind="text",
                default="",
                help="lateral-torsional buckling; blank: the member's length",
            ),
            Ask(
                "length_x",
                "Buckling length, strong axis",
                kind="text",
                default="",
                help="blank: the member's length",
            ),
            Ask(
                "length_y", "Buckling length, weak axis", kind="text", default="", help="blank: L_b"
            ),
            Ask("k_x", "K, strong axis", kind="text", default="1"),
            Ask("k_y", "K, weak axis", kind="text", default="1"),
            Ask(
                "deflection_limit",
                "Deflection limit L/…",
                kind="text",
                default="",
                help="e.g. 360; blank: not checked",
            ),
        ],  # fmt: skip
        intro="How members are checked. The code follows the material: carbon and low-alloy "
        "steel by AISC 360-16. The most specific entry for a member wins.",
        check=_check_form,
    )
    if got is None:
        return None
    values = {"selection": got["selection"], "method": got["method"]}
    for key in ("unbraced_length", "length_x", "length_y"):
        if got[key].strip():
            values[key] = _quantity(got[key])
    for key in ("k_x", "k_y"):
        if got[key].strip():
            values[key] = float(got[key])
    if got["deflection_limit"].strip():
        values["deflection_limit"] = float(got["deflection_limit"])
    entry = MemberDesign(**values)
    entries = [d for d in comp.member_design if d.selection != entry.selection] + [entry]
    value = [d.model_dump(mode="json") for d in entries]
    return window.submit(Edit(object=str(comp.id), ops=[EditOp(op="set", path="member_design",
                                                                value=value)]))  # fmt: skip


class MemberTable(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.found: dict = {"members": [], "not_checked": []}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.run = QComboBox()
        self.run.currentIndexChanged.connect(lambda _i: self.show_results())
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(lambda: self.export())
        bar.addWidget(QLabel("Run"))
        bar.addWidget(self.run, 1)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)
        self.note = role(QLabel(""), "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = self._table(HEADERS)
        self.table.itemSelectionChanged.connect(self._details)
        self.details = self._table(DETAIL)
        split.addWidget(self.table)
        split.addWidget(self.details)
        layout.addWidget(split, 1)

    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return t

    def refresh(self) -> None:
        """Completed runs of studies whose component is a frame, newest first."""
        from puzzle.frames import frame_lines
        from puzzle.model.schema import Component, Study

        session = self.window.session
        if session is None:
            return
        chosen = self.run.currentData()
        self.run.blockSignals(True)
        self.run.clear()
        try:
            runs = [r for r in self.window._store().runs() if r.status == "completed"]
        except Exception:  # no results yet
            runs = []
        studies = {str(s.id): s for s in session.project.of_type(Study)}
        for r in sorted(runs, key=lambda r: r.recorded_at, reverse=True):
            study = studies.get(r.study_id)
            comp = session.project.objects.get(study.component) if study else None
            if isinstance(comp, Component) and frame_lines(session.project, comp):
                when = r.recorded_at.strftime("%Y-%m-%d %H:%M")
                self.run.addItem(f"{r.study_name} ({r.solver}, {when})", r.run_id)
        found = self.run.findData(chosen)
        self.run.setCurrentIndex(found if found >= 0 else 0)
        self.run.blockSignals(False)
        self.show_results()

    def show_results(self) -> None:
        from puzzle.frames.design import member_checks
        from puzzle.model.schema import Component, Study

        run_id = self.run.currentData()
        self.found = {"members": [], "not_checked": []}
        if run_id is None:
            self.note.setText("Run a frame study to check its members here.")
        else:
            store, project = self.window._store(), self.window.session.project
            record = next(r for r in store.runs() if r.run_id == run_id)
            try:
                study = next(o for o in project.of_type(Study) if str(o.id) == record.study_id)
                comp = project.get(study.component, Component)
                self.found = member_checks(project, comp, store.read_mesh(record.mesh_id),
                                           store.read_results(run_id))  # fmt: skip
                skipped = [f"{m['member']}: {m['reason']}" for m in self.found["not_checked"]]
                failing = sum(not m["passes"] for m in self.found["members"])
                self.note.setText(
                    f"AISC 360-16, worst first: {failing} of {len(self.found['members'])} "
                    "members over 1." + (f" Not checked: {'; '.join(skipped)}." if skipped
                                         else ""))  # fmt: skip
            except Exception as exc:  # the model changed since the run, or no frame
                self.note.setText(f"These members can't be checked from this run: {exc}")
        rows = self.found["members"]
        self.table.setRowCount(len(rows))
        for r, m in enumerate(rows):
            g = m["governing"] or {}
            at = g.get("at_m")
            cells = [m["member"], m["section"], f"{m['ratio']:.3f}", g.get("check", ""),
                     g.get("clause", ""), g.get("label", ""),
                     "" if at is None else self._length(at)]  # fmt: skip
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if not m["passes"]:
                    bold = QFont(item.font())
                    bold.setBold(True)
                    item.setFont(bold)
                    item.setToolTip("over its available strength")
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        if rows:
            self.table.selectRow(0)
        self._details()

    @staticmethod
    def _length(metres: float) -> str:
        from puzzle.settings import length_unit

        unit, per_metre = length_unit()
        return f"{metres * per_metre:.4g} {unit}"

    def _details(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        members = self.found["members"]
        if len(rows) != 1 or not members:
            self.details.setRowCount(0)
            return
        m = members[next(iter(rows))]
        k = (m["governing"] or {}).get("step", 1)
        checks = m["steps"][k - 1]["checks"] if m["steps"] else []
        self.details.setRowCount(len(checks))
        for r, c in enumerate(checks):
            unit = "" if c["check"] == "combined forces" else self._unit(c["check"])
            cells = [c["check"], c["limit"], c["clause"], self._value(c["demand"], unit),
                     self._value(c["available"], unit), f"{c['ratio']:.3f}",
                     "" if c["at_m"] is None else self._length(c["at_m"])]  # fmt: skip
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c.get("notes"):
                    item.setToolTip("; ".join(c["notes"]))
                self.details.setItem(r, col, item)
        self.details.resizeColumnsToContents()

    @staticmethod
    def _unit(check: str) -> str:
        if check.startswith(("flexure", "torsion")):
            return "N·m"
        if check == "deflection":
            return "m"
        return "N"

    @staticmethod
    def _value(v: float, unit: str) -> str:
        from puzzle.model.units import convert, pretty
        from puzzle.settings import current

        if not unit:
            return f"{v:.3f}"
        units = current().display_units()
        shown = {"N": units.get("force", "N"), "m": units.get("length", "m")}.get(unit)
        if shown and shown != unit:
            return f"{convert(v, unit, shown):.4g} {pretty(shown)}"
        return f"{v:.4g} {unit}"

    def export(self, path: str | None = None) -> str | None:
        path = path or self.window.ask_save_path("Export member checks", "member-checks.csv",
                                                 "CSV files (*.csv)")  # fmt: skip
        if not path:
            return None
        with open(path, "w", newline="", encoding="utf-8") as f:
            out = csv.writer(f)
            out.writerow(["Member", "Part", "Section", "Step", "Check", "Limit state", "Clause",
                          "Demand (N, N·m, m)", "Available", "Ratio", "At (m)"])  # fmt: skip
            for m in self.found["members"]:
                for st in m["steps"]:
                    for c in st["checks"]:
                        out.writerow([m["member"], m["part"], m["section"], st["label"],
                                      c["check"], c["limit"], c["clause"], c["demand"],
                                      c["available"], c["ratio"], c["at_m"]])  # fmt: skip
        return path
