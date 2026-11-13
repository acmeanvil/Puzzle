"""Read the comma-separated result tables Code_Aster writes with IMPR_TABLE."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from puzzle.results.model import Field, Results, StepResult

# Code_Aster component -> Puzzle component, per field.
COMPONENTS = {
    "displacement": {"DX": "x", "DY": "y", "DZ": "z"},
    "displacement_imag": {"DX": "x", "DY": "y", "DZ": "z"},
    "stress": {"SIXX": "xx", "SIYY": "yy", "SIZZ": "zz", "SIXY": "xy", "SIYZ": "yz", "SIXZ": "zx"},
    "temperature": {"TEMP": ""},
    "reaction_force": {"DX": "x", "DY": "y", "DZ": "z"},
    # EPSP: tensor (not engineering) shear components.
    "plastic_strain": {
        "EPXX": "xx",
        "EPYY": "yy",
        "EPZZ": "zz",
        "EPXY": "xy",
        "EPYZ": "yz",
        "EPXZ": "zx",
    },
    "plastic_strain_equivalent": {"V1": ""},  # VMIS_ISOT_TRAC: V1 = p
    # Beams (ADR 0027): section forces in the beam's own axes (x along it, y the
    # section's first axis): axial, two shears, torsion, two bending moments.
    "beam_forces": {"N": "N", "VY": "Vy", "VZ": "Vz", "MT": "T", "MFY": "My", "MFZ": "Mz"},
}


# CONT_NOEU (ADR 0056) -> raw fields that post/contact.py turns into the neutral ones:
# nodal forces (N), not pressures; CONT 0 none, 1 sticking, 2 sliding.
CONTACT = {
    "contact_normal_force": {"RN": ""},
    "contact_normal_vector": {"RNX": "x", "RNY": "y", "RNZ": "z"},
    "contact_force": {"RX": "x", "RY": "y", "RZ": "z"},
    "contact_status_code_aster": {"CONT": ""},
    "contact_gap_code_aster": {"JEU": ""},
    "contact_sliding_code_aster": {"GLI": ""},
}


MULTIPLIERS = {
    "contact_lags_c": {"LAGS_C": ""},
    "contact_lags_f": {"LAGS_F1": "1", "LAGS_F2": "2"},
}


def read_table(path: Path) -> list[dict[str, str]]:
    """Rows of an IMPR_TABLE file (comment lines start with #)."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(lines)]


def nodal_fields(rows: list[dict[str, str]], name: str, comps=None) -> dict[int, Field]:
    """One field per NUME_ORDRE (step or mode), keyed by that number."""
    comps = comps or COMPONENTS[name]
    # A 2D model's tables (ADR 0048) have no DZ, SIYZ or SIXZ column: those are zero.
    # A column printed but empty at a node (a discrete's own node: "-") skips the node.
    printed = [c for c in comps if rows and c in rows[0]]
    by_order: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        if not printed or any(row.get(c, "-") in ("-", "") for c in printed):
            continue
        by_order.setdefault(int(row["NUME_ORDRE"]), []).append(row)
    out = {}
    for order, group in by_order.items():
        ids = np.array([int(r["NOEUD"]) for r in group], dtype=np.int64)
        values = np.array([[float(r[c]) if c in printed else 0.0 for c in comps] for r in group])
        sort = np.argsort(ids)
        out[order] = Field(name, "node", list(comps.values()), ids[sort], values[sort])
    return out


def read_results(workdir: Path) -> Results:
    results = Results(solver="code_aster")
    tables = {p.stem: read_table(p) for p in workdir.glob("*.csv")}
    if "buckling" in tables:  # ADR 0028: CHAR_CRIT is the load factor
        factors = {int(r["NUME_ORDRE"]): float(r["CHAR_CRIT"]) for r in tables["buckling"]}
        shapes = nodal_fields(tables.get("displacement", []), "displacement")
        for mode, order in enumerate(sorted(factors), start=1):
            fields = {"displacement": shapes[order]} if order in shapes else {}
            results.steps.append(StepResult(1, "buckling", factors[order], mode, fields))
        return results
    if "frequency" in tables:
        frequencies = {int(r["NUME_ORDRE"]): float(r["FREQ"]) for r in tables["frequency"]}
        shapes = nodal_fields(tables.get("displacement", []), "displacement")
        for order in sorted(frequencies):
            fields = {"displacement": shapes[order]} if order in shapes else {}
            results.steps.append(StepResult(1, "modal", frequencies[order], order, fields))
        return results
    # One result step per stored instant (NUME_ORDRE); a static solve has one.
    steps: dict[int, StepResult] = {}
    for name in COMPONENTS:
        if name not in tables:
            continue
        times = {int(r["NUME_ORDRE"]): float(r.get("INST") or 1.0) for r in tables[name]}
        for order, field in nodal_fields(tables[name], name).items():
            step = steps.setdefault(order, StepResult(1, "", times[order]))
            step.fields[name] = field
    if "contact" in tables:
        times = {int(r["NUME_ORDRE"]): float(r.get("INST") or 1.0) for r in tables["contact"]}
        # The continuous contact's multipliers ride in DEPL: LAGS_C is the pressure
        # (compression negative), LAGS_F1/F2 the friction semi-multipliers.
        found = [(tables["contact"], CONTACT)]
        if tables.get("displacement") and "LAGS_C" in tables["displacement"][0]:
            found.append((tables["displacement"], MULTIPLIERS))
        for rows, kinds in found:
            for name, comps in kinds.items():
                for order, field in nodal_fields(rows, name, comps).items():
                    step = steps.setdefault(order, StepResult(1, "", times.get(order, 1.0)))
                    step.fields[name] = field
    results.steps = [steps[o] for o in sorted(steps)]
    # A stepped structural solve also stores its unloaded start (t = 0): nothing to show,
    # and CalculiX doesn't report it, so it goes. A transient heat start is the initial
    # temperature and stays.
    if len(results.steps) > 1 and "temperature" not in results.steps[0].fields:
        results.steps = [s for s in results.steps if s.value != 0.0] or results.steps
    return results
