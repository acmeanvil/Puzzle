"""Read the comma-separated result tables Code_Aster writes with IMPR_TABLE."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from puzzle.results.model import Field, Results, StepResult

# Code_Aster component -> Puzzle component, per field.
COMPONENTS = {
    "displacement": {"DX": "x", "DY": "y", "DZ": "z"},
    "stress": {"SIXX": "xx", "SIYY": "yy", "SIZZ": "zz", "SIXY": "xy", "SIYZ": "yz", "SIXZ": "zx"},
    "temperature": {"TEMP": ""},
    "reaction_force": {"DX": "x", "DY": "y", "DZ": "z"},
}


def read_table(path: Path) -> list[dict[str, str]]:
    """Rows of an IMPR_TABLE file (comment lines start with #)."""
    lines = [
        line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]
    return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(lines)]


def nodal_fields(rows: list[dict[str, str]], name: str) -> dict[int, Field]:
    """One field per NUME_ORDRE (step or mode), keyed by that number."""
    comps = COMPONENTS[name]
    by_order: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        by_order.setdefault(int(row["NUME_ORDRE"]), []).append(row)
    out = {}
    for order, group in by_order.items():
        ids = np.array([int(r["NOEUD"]) for r in group], dtype=np.int64)
        values = np.array([[float(r[c]) for c in comps] for r in group])
        sort = np.argsort(ids)
        out[order] = Field(name, "node", list(comps.values()), ids[sort], values[sort])
    return out


def read_results(workdir: Path) -> Results:
    results = Results(solver="code_aster")
    tables = {p.stem: read_table(p) for p in workdir.glob("*.csv")}
    if "frequency" in tables:
        frequencies = {int(r["NUME_ORDRE"]): float(r["FREQ"]) for r in tables["frequency"]}
        shapes = nodal_fields(tables.get("displacement", []), "displacement")
        for order in sorted(frequencies):
            fields = {"displacement": shapes[order]} if order in shapes else {}
            results.steps.append(StepResult(1, "modal", frequencies[order], order, fields))
        return results
    # One result step per stored instant (NUME_ORDRE); a static solve has one.
    steps: dict[int, StepResult] = {}
    for name in ("displacement", "stress", "temperature", "reaction_force"):
        if name not in tables:
            continue
        times = {int(r["NUME_ORDRE"]): float(r.get("INST") or 1.0) for r in tables[name]}
        for order, field in nodal_fields(tables[name], name).items():
            step = steps.setdefault(order, StepResult(1, "", times[order]))
            step.fields[name] = field
    results.steps = [steps[o] for o in sorted(steps)]
    return results
