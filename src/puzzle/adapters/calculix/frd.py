"""Read CalculiX ASCII .frd result files into solver-neutral results.

Each result block is introduced by `1PSTEP` (with the analysis step number), an
optional `1PMODE` (mode number), and a `100CL` line whose value is the step time,
or the frequency in Hz for a mode. Data lines are fixed width: ` -1`, a 10-wide
node id, then 12-wide values (continued on ` -2` lines when long).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.results.model import Field, Results, StepResult

# CalculiX block name -> (Puzzle field name, components).
FIELDS = {
    "DISP": ("displacement", ["x", "y", "z"]),
    "STRESS": ("stress", ["xx", "yy", "zz", "xy", "yz", "zx"]),
    "TOSTRAIN": ("strain", ["xx", "yy", "zz", "xy", "yz", "zx"]),
    "NDTEMP": ("temperature", [""]),
    "FLUX": ("heat_flux", ["x", "y", "z"]),
    "FORC": ("reaction_force", ["x", "y", "z"]),
    "PE": ("plastic_strain_equivalent", [""]),  # PEEQ, from *EL FILE ... PEEQ
}


def read_frd(path: Path, solver: str = "calculix") -> Results:
    results = Results(solver=solver)
    steps: dict[tuple[int, int | None, float], StepResult] = {}
    analysis_step, mode = 0, None
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        tag = line.split()[0] if line.strip() else ""
        if tag == "1PSTEP":
            analysis_step, mode = int(line.split()[3]), None
        elif tag == "1PMODE":
            mode = int(line.split()[1])
        elif tag == "100CL":
            value = float(line.split()[2])
            i, block = _block(lines, i + 1)
            if block is None:
                continue
            key = (analysis_step, mode, value)
            if key not in steps:
                steps[key] = StepResult(index=analysis_step, kind="", value=value, mode=mode)
                results.steps.append(steps[key])
            steps[key].fields[block.name] = block
            continue
        i += 1
    return results


def _block(lines: list[str], i: int) -> tuple[int, Field | None]:
    """Parse one `-4` result block starting at line i; return the next line index and the field."""
    header = lines[i].split()
    name = header[1]
    i += 1
    while lines[i].startswith(" -5"):
        i += 1
    ids, rows = [], []
    while not lines[i].startswith(" -3"):
        line = lines[i]
        if line.startswith(" -1"):
            ids.append(int(line[3:13]))
            rows.append(_values(line[13:]))
        elif line.startswith(" -2"):
            rows[-1].extend(_values(line[13:]))
        i += 1
    if name not in FIELDS:
        return i + 1, None
    field_name, components = FIELDS[name]
    values = np.array([r[: len(components)] for r in rows], dtype=float).reshape(
        len(rows), len(components)
    )
    return i + 1, Field(
        name=field_name,
        location="node",
        components=components,
        ids=np.array(ids, dtype=np.int64),
        values=values,
    )


def _values(text: str) -> list[float]:
    text = text.rstrip()
    return [float(text[j : j + 12]) for j in range(0, len(text), 12)]
