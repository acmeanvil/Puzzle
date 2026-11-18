"""VDI 2230 check… (ADR 0050, ADR 0072 section 10): a concentric single-bolt joint by
VDI 2230-1:2015 in one form, what MCP `bolt_vdi2230` does without the agent. The bolt
and its joint (size, grade, head, nut or tapped hole, clamp length, shank), the moduli,
the friction, the embedding inputs and the working load go in; `vdi2230.check`'s steps
come out as a table as the values change, the governing check marked, saved as CSV or
text. Opened from a bolt type or a bolt (the tree, the bolt table) it starts from their
values. Nothing in the project changes.
"""

from __future__ import annotations

import csv
import re

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.agent_prefill import add_ask_button
from puzzle.gui.live_form import LiveForm, quantity, shown_length
from puzzle.gui.quick_form import Ask
from puzzle.gui.theme import role

GRADES = [("8.8", "8.8"), ("10.9", "10.9"), ("12.9", "12.9"), ("Given Rp0.2", "rp02")]
HEADS = [("Hex head", "hex"), ("Socket head", "socket")]
ENDS = [("A nut", "nut"), ("A tapped hole", "tapped")]
HEADERS = ["Step", "Quantity", "Value"]


def _si(text: str, unit: str, what: str, positive: bool = False) -> float:
    from puzzle.model.schema import Quantity

    try:
        value = Quantity(**quantity(text, unit)).to_si()
    except ValueError as exc:
        raise ValueError(f"{what}: {exc}") from None
    if positive and not value > 0:  # VDI 2230 divides by it (PANEL-12)
        raise ValueError(f"{what}: must be more than zero")
    return value


def _optional(text: str, unit: str, what: str, positive: bool = False) -> float | None:
    return _si(text, unit, what, positive) if (text or "").strip() else None


def _number(text: str, what: str, low: float | None = None, whole: bool = False,
            above: bool = False) -> float:  # fmt: skip
    """A plain number: finite, at least (or, `above`, more than) `low`, whole if asked."""
    import math

    try:
        value = float(text.strip().replace(",", ".") if text.count(",") == 1 else text)
    except (ValueError, AttributeError):
        raise ValueError(f"{what}: '{text}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{what}: must be finite")
    if low is not None and (value <= low if above else value < low):
        raise ValueError(f"{what}: must be {'more than' if above else 'at least'} {low:g}")
    if whole and value != int(value):
        raise ValueError(f"{what}: a whole number")
    return value


def arguments(values: dict) -> dict:
    """The form's values as `vdi2230.check`'s (and MCP `bolt_vdi2230`'s) arguments, SI."""
    length = shown_length(1.0).split(" ", 1)[1]
    grade = values.get("grade", "10.9")
    out = {
        "size": values["size"],
        "clamp_length": _si(values.get("clamp_length", ""), length, "clamp length", True),
        "grade": None if grade == "rp02" else grade,
        "head": values.get("head", "hex"),
        "tapped": values.get("end") == "tapped",
        "shank_length": _optional(values.get("shank_length"), length, "shank") or 0.0,
        "e_bolt": _si(values.get("e_bolt", ""), "GPa", "the bolt's modulus", True),
        "e_plate": _si(values.get("e_plate", ""), "GPa", "the plates' modulus", True),
        "e_nut": _optional(values.get("e_nut"), "GPa", "the nut's modulus", True),
        "rp02": _si(values.get("rp02", ""), "MPa", "Rp0.2", True) if grade == "rp02" else None,
        "mu_thread": _number(values.get("mu_thread", ""), "thread friction", 0.0),
        "mu_head": _number(values.get("mu_head", ""), "head friction", 0.0),
        "n": _number(values.get("n", ""), "load introduction n", 0.0),
        "axial_load": _optional(values.get("axial_load"), "N", "axial load"),
        "rz": _si(values.get("rz", ""), "um", "Rz"),
        "interfaces": int(_number(values.get("interfaces", ""), "interfaces", 0.0, whole=True)),
        "tightening_factor": (_number(values["tightening_factor"], "tightening factor", 0.0,
                                      above=True)
                              if (values.get("tightening_factor") or "").strip() else None),
    }  # fmt: skip
    return out


def _len(m: float) -> str:
    return shown_length(m)


def _force(n: float) -> str:
    return f"{n / 1e3:.4g} kN"


def _stress(pa: float) -> str:
    return f"{pa / 1e6:.4g} MPa"


def rows(out: dict) -> list[list[str]]:
    """`check`'s answer as table rows: step, quantity, value."""
    r = out["resilience_m_per_N"]
    table = [
        ["Bolt", "Size", out["size"]],
        ["Bolt", "Rp0.2", _stress(out["rp02_Pa"])],
        ["Bolt", "Stress area As", f"{out['thread']['stress_area_m2'] * 1e6:.4g} mm²"],
        ["Joint", "Bearing diameter dW", _len(out["bearing_m"])],
        ["Joint", "Hole dh", _len(out["hole_m"])],
        ["Joint", "Outside diameter DA", _len(out["outer_diameter_m"])],
        ["R3 resilience", "Bolt δS", f"{r['bolt']:.4g} m/N"],
        ["R3 resilience", "Parts δP", f"{r['parts']:.4g} m/N"],
        ["R3 resilience", "Bolt stiffness", f"{out['stiffness_N_per_m']['bolt']:.4g} N/m"],
        ["R3 resilience", "Parts' stiffness", f"{out['stiffness_N_per_m']['parts']:.4g} N/m"],
        ["R3 resilience", "Cone tan φ", f"{out['cone']['tan_phi']:.4g}"],
        ["R3 load factor", "Φ_K", f"{out['load_factor']['phi_k']:.4g}"],
        ["R3 load factor", f"Φ_n (n = {out['load_factor']['n']:g})",
         f"{out['load_factor']['phi_n']:.4g}"],
        ["R4 embedding", "Embedding fZ", f"{out['embedding_m'] * 1e6:.4g} µm"],
        ["R4 embedding", "Preload loss FZ", _force(out["preload_loss_N"])],
        ["R7 assembly", "Assembly preload FMzul", _force(out["assembly_preload_N"])],
        ["R7 assembly", "Assembly stress", _stress(out["assembly_stress_Pa"])],
        ["R13 torque", "Tightening torque MA", f"{out['tightening_torque_Nm']:.4g} N·m"],
        ["R13 torque", "Nut factor K", f"{out['nut_factor_K']:.4g}"],
    ]  # fmt: skip
    if "least_preload_N" in out:
        table.append(["R2 preload", "Least preload FM,min", _force(out["least_preload_N"])])
    if "residual_clamp_N" in out:
        table.append(["R5 clamp load", "Residual clamp load", _force(out["residual_clamp_N"])])
    w = out.get("working")
    if w:
        safety = w["fatigue_safety"]
        table += [
            ["R8 working", "Bolt's extra load FSA", _force(w["bolt_extra_load_N"])],
            ["R8 working", "Plates' relief FPA", _force(w["plate_relief_N"])],
            ["R8 working", "Bolt's largest load", _force(w["bolt_max_load_N"])],
            ["R8 working", "Equivalent stress", _stress(w["equivalent_stress_Pa"])],
            ["R9 fatigue", "Stress amplitude σa", _stress(w["stress_amplitude_Pa"])],
            ["R9 fatigue", "Endurance limit σASV", _stress(w["endurance_limit_Pa"])],
            ["R9 fatigue", "Safety SD", f"{safety:.3g}" if safety is not None else "no cycling"],
        ]  # fmt: skip
    return table


def checks(out: dict) -> list[tuple[str, float]]:
    """Each check as (name, utilisation: ≤ 1 passes); the largest governs."""
    found = [("Assembly stress / Rp0.2", out["assembly_stress_Pa"] / out["rp02_Pa"])]
    w = out.get("working")
    if w:
        found.append(("Working equivalent stress / Rp0.2", w["of_rp02"]))
        if w["fatigue_safety"]:
            found.append(("Stress amplitude / endurance limit", 1 / w["fatigue_safety"]))
    if "residual_clamp_N" in out and "least_preload_N" in out:
        # the clamp load left against what the least preload brings: > 1 opens the joint
        least = out["least_preload_N"]
        found.append(
            (
                "Load taken from the clamp / least preload",
                (least - out["residual_clamp_N"]) / least if least else float("inf"),
            )
        )
    return found


def governing(out: dict) -> tuple[str, float]:
    return max(checks(out), key=lambda c: c[1])


class Results(QWidget):
    """The check's table and the governing check's line."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.notes = role(QLabel(""), "muted")
        self.notes.setWordWrap(True)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(260)
        layout.addWidget(self.verdict)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.notes)
        self.result: dict | None = None
        self.shown: list[list[str]] = []

    def show_result(self, out: dict | None, why: str = "") -> None:
        self.result = out
        if out is None:
            self.verdict.setText(why)
            self.notes.setText("")
            self.table.setRowCount(0)
            self.shown = []
            return
        name, worst = governing(out)
        self.shown = rows(out) + [["Checks", n, f"{u:.3f}" + ("  (governs)" if n == name else "")]
                                  for n, u in checks(out)]  # fmt: skip
        self.verdict.setText(f"Governing: {name} = {worst:.3f} "
                             f"({'passes' if worst <= 1 else 'fails'}).")  # fmt: skip
        self.notes.setText("Defaults used: " + "; ".join(out["notes"]) if out["notes"] else "")
        self.table.setRowCount(len(self.shown))
        for r, row in enumerate(self.shown):
            for c, text in enumerate(row):
                item = QTableWidgetItem(text)
                if row[0] == "Checks" and row[1] == name:
                    bold = QFont(item.font())
                    bold.setBold(True)
                    item.setFont(bold)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def save(self, path: str) -> str:
        """The table as CSV (a .csv path) or aligned text (anything else)."""
        if path.lower().endswith(".csv"):
            with open(path, "w", newline="", encoding="utf-8") as f:
                out = csv.writer(f)
                out.writerow(HEADERS)
                out.writerows(self.shown)
            return path
        widths = [max(len(r[i]) for r in [HEADERS, *self.shown]) for i in range(len(HEADERS))]
        lines = ["VDI 2230-1:2015, concentric single-bolt joint", self.verdict.text(), ""]
        lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
                  for r in [HEADERS, *self.shown]]  # fmt: skip
        if self.notes.text():
            lines += ["", self.notes.text()]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path


def _grade_of(material) -> tuple[str, str]:
    """(grade, Rp0.2 text) from a bolt type's grade material."""
    found = re.search(r"\b(8\.8|10\.9|12\.9)\b", getattr(material, "name", "") or "")
    if found:
        return found.group(1), ""
    q = material.properties.get("yield_strength") if material is not None else None
    if q is not None:
        return "rp02", f"{q.to_si() / 1e6:g} MPa"
    return "10.9", ""


def starting_values(project, bolt_type=None, length: float | None = None) -> dict:
    """What the form starts from: a bolt type's size, grade, head, end and head friction;
    `length` (m) the clamp length, e.g. a bolt's between its ends."""
    from puzzle import bolts

    out: dict = {}
    if bolt_type is not None:
        if bolt_type.size and any(s.name == bolt_type.size for s in bolts.SIZES):
            out["size"] = bolt_type.size
        grade, rp02 = _grade_of(project.objects.get(bolt_type.material))
        out["grade"] = grade
        if rp02:
            out["rp02"] = rp02
        out["head"] = bolt_type.head
        out["end"] = "tapped" if bolt_type.joint in ("tapped", "stud_tapped") else "nut"
        out["mu_head"] = f"{bolt_type.head_friction:g}"
    if length:
        out["clamp_length"] = shown_length(length)
    return out


def vdi_check(window, bolt_type=None, length: float | None = None):
    """The VDI 2230 check… form; returns the last check it showed (None if none)."""
    from puzzle import bolts, vdi2230

    start = starting_values(window.session.project, bolt_type, length)
    view = Results()

    def show(v):
        try:
            view.show_result(vdi2230.check(**arguments(v)))
        except (ValueError, KeyError, ArithmeticError) as exc:  # never a popup (PANEL-12)
            view.show_result(None, str(exc).strip("'\"") or "These values give no answer")

    sizes = [(s.name, s.name) for s in bolts.SIZES]
    d = lambda key, default: start.get(key, default)  # noqa: E731
    asks = [
        Ask("size", "Size", choices=sizes, default=d("size", "M12")),
        Ask("grade", "Grade", choices=GRADES, default=d("grade", "10.9")),
        Ask("rp02", "Rp0.2", kind="text", default=d("rp02", "940 MPa"),
            shown=lambda v: v.get("grade") == "rp02", help="The bolt's 0.2 % proof stress"),
        Ask("head", "Head", choices=HEADS, default=d("head", "hex")),
        Ask("end", "Other end", choices=ENDS, default=d("end", "nut")),
        Ask("clamp_length", "Clamp length lK", kind="text",
            default=d("clamp_length", shown_length(0.03)),
            help="The plates' total thickness between head and nut (or the tapped part)"),
        Ask("shank_length", "Shank", kind="text", default="",
            help="The unthreaded shank inside the clamp length; empty: threaded throughout"),
        Ask("e_bolt", "Bolt's modulus", kind="text", default="205 GPa"),
        Ask("e_plate", "Plates' modulus", kind="text", default="205 GPa"),
        Ask("e_nut", "Nut's modulus", kind="text", default="",
            help="Empty: the bolt's (a tapped part's: the plates')"),
        Ask("mu_thread", "Thread friction μG", kind="text", default="0.12"),
        Ask("mu_head", "Head friction μK", kind="text", default=d("mu_head", "0.12")),
        Ask("interfaces", "Interfaces", kind="text", default="1",
            help="Interfaces between the clamped plates, for embedding (Table 5)"),
        Ask("rz", "Roughness Rz", kind="text", default="16 µm"),
        Ask("n", "Load introduction n", kind="text", default="1",
            help="Where the working load enters the plates (Table 2): 1 under the head, "
            "about 0.5 mid-plate"),
        Ask("axial_load", "Axial working load FA", kind="text", default="",
            help="With its unit (N, kN); empty: assembly only"),
        Ask("tightening_factor", "Tightening factor αA", kind="text", default="",
            help="Table A8: 1.6 to 2.5 for a torque wrench; empty: no least preload"),
    ]  # fmt: skip
    dialog = LiveForm(window, "VDI 2230 check", asks, changed=show, below=view,
                      intro="A concentric single-bolt joint by VDI 2230-1:2015. Nothing in "
                      "the project changes.", help_key="form:vdi2230")  # fmt: skip
    dialog.view = view
    dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).hide()
    for text, default, filters in (
        ("Save as CSV…", "vdi2230.csv", "CSV files (*.csv)"),
        ("Save as text…", "vdi2230.txt", "Text files (*.txt)"),
    ):
        b = dialog.buttons.addButton(text, QDialogButtonBox.ButtonRole.ActionRole)
        b.clicked.connect(lambda _=False, t=text, f=default, x=filters: _save(window, view, t,
                                                                             f, x))  # fmt: skip

    def prompt(v):
        load = f", axial working load {v['axial_load']}" if v.get("axial_load") else ""
        return (f"Check a {v.get('size')} grade {v.get('grade')} {v.get('head')}-head bolt "
                f"into {'a tapped hole' if v.get('end') == 'tapped' else 'a nut'}, clamp "
                f"length {v.get('clamp_length')}{load}, by VDI 2230 (MCP bolt_vdi2230), and "
                "explain which check governs.")  # fmt: skip

    add_ask_button(dialog, window, prompt)
    window.run_dialog(dialog)
    return view.result


def _save(window, view: Results, title: str, default: str, filters: str) -> str | None:
    if view.result is None:
        return None
    path = window.ask_save_path(title.rstrip("…"), default, filters)
    return view.save(path) if path else None


def vdi_for_bolt(window, comp, index: int):
    """The form from a bolt: its type's values and its length between its ends (on the
    component's last mesh) as the clamp length."""
    from puzzle.gui.bolt_table import bolt_rows

    project = window.session.project
    bolt = comp.connectors[index]
    row = next((r for r in bolt_rows(project, getattr(window, "meshes", {}))
                if r.component.id == comp.id and r.index == index), None)  # fmt: skip
    btype = project.objects.get(bolt.type) if bolt.type else None
    return vdi_check(window, btype, row.length if row is not None else None)
