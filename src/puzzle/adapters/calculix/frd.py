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
    "DISPI": ("displacement_imag", ["x", "y", "z"]),  # steady-state dynamics (ADR 0041)
    "STRESS": ("stress", ["xx", "yy", "zz", "xy", "yz", "zx"]),
    "TOSTRAIN": ("strain", ["xx", "yy", "zz", "xy", "yz", "zx"]),
    # ME, total less thermal strain: the plastic strain tensor's source (ADR 0090
    # COMMON-2), dropped once that is made (calculix.read_results)
    "MESTRAIN": ("mechanical_strain", ["xx", "yy", "zz", "xy", "yz", "zx"]),
    "NDTEMP": ("temperature", [""]),
    "FLUX": ("heat_flux", ["x", "y", "z"]),
    "FORC": ("reaction_force", ["x", "y", "z"]),
    "PE": ("plastic_strain_equivalent", [""]),  # PEEQ, from *EL FILE ... PEEQ
    # *CONTACT FILE CDIS, CSTR (ADR 0056): COPEN, CSLIP1, CSLIP2, CPRESS, CSHEAR1, CSHEAR2
    # on every node, zero off the contact; post/contact.py keeps the slave nodes.
    "CONTACT": ("contact_calculix", ["open", "slip1", "slip2", "pressure", "shear1", "shear2"]),
}


def read_frd(path: Path, solver: str = "calculix") -> Results:
    results = Results(solver=solver)
    steps: dict[tuple[int, int | None, float], StepResult] = {}
    analysis_step, mode = 0, None
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        tag = _tag(line)
        if tag == "1PSTEP":
            analysis_step, mode = _int(line, 48, 60, 3), None
        elif tag == "1PMODE":
            mode = _int(line, 10, 36, 1)
        elif tag == "100CL":
            value = _value(line)
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


# The header lines are fixed width, read by column (ADR 0090 CCX-1): from the 9,900th
# output increment the `100CL` counter (100 + increment, 5 wide) touches its tag
# (`100CL10000`), and a negative value touches the counter, so splitting on spaces
# lost every block from there on, without a word.
def _tag(line: str) -> str:
    head = line[:10].strip()
    return "100CL" if head.startswith("100CL") else head


def _value(line: str) -> float:
    """A `100CL` line's value: the time, the frequency or the load factor, cols 13-24."""
    try:
        return float(line[12:24])
    except ValueError:
        return float(line.split()[2])


def _int(line: str, a: int, b: int, token: int) -> int:
    try:
        return int(line[a:b])
    except ValueError:
        return int(line.split()[token])


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
