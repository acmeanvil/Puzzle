"""Convert Lacewing Lab's structural section tables into Puzzle's section library
(ADR 0055): `src/puzzle/sections/aisc.json` and `en.json`, SI throughout.

    python scripts/import_sections.py <Tension_Meter>/App/packages/engineering_core/lib/sections

The sources are the AISC Shapes Database v16.0 (US units, as AISC tabulates them) and
ArcelorMittal's Sections and Merchant Bars sales programme (EN 10365:2017 and EN
10056-1:2017 dimensions, EN 1993-1-1 properties), which Lacewing Lab generated into Dart
from the originals. Each row keeps its designation and family; the properties are
renamed to one set of symbols: `Ix` is the strong axis's second moment (EN Iy), `Iy` the
weak axis's (EN Iz), `J` the torsion constant (EN It), `Cw` the warping constant (EN Iw),
`Avz` the shear area along the web where tabulated, `Iu`/`Iv` an angle's principal
second moments and `alpha` the angle of its u axis to the x axis.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

IN = 0.0254
OUT = Path(__file__).resolve().parents[1] / "src" / "puzzle" / "sections"

# AISC SteelShape.fields, in the generator's order, with each value's unit.
AISC_FIELDS = [
    ("W", "weight"), ("A", "area"), ("d", "length"), ("Ht", "length"), ("OD", "length"),
    ("bf", "length"), ("B", "length"), ("b", "length"), ("ID", "length"), ("tw", "length"),
    ("tf", "length"), ("t", "length"), ("tnom", "length"), ("tdes", "length"),
    ("kdes", "length"), ("x", "length"), ("y", "length"), ("eo", "length"),
    ("bf/2tf", "ratio"), ("b/t", "ratio"), ("b/tdes", "ratio"), ("h/tw", "ratio"),
    ("h/tdes", "ratio"), ("D/t", "ratio"), ("Ix", "l4"), ("Zx", "l3"), ("Sx", "l3"),
    ("rx", "length"), ("Iy", "l4"), ("Zy", "l3"), ("Sy", "l3"), ("ry", "length"),
    ("Iz", "l4"), ("rz", "length"), ("Sz", "l3"), ("J", "l4"), ("Cw", "l6"), ("C", "l3"),
    ("ro", "length"), ("H", "ratio"), ("tan(α)", "ratio"), ("rts", "length"),
    ("ho", "length"),
]  # fmt: skip
AISC_FACTOR = {"length": IN, "area": IN**2, "l3": IN**3, "l4": IN**4, "l6": IN**6,
               "ratio": 1.0, "weight": 0.45359237 / 0.3048}  # lb/ft -> kg/m  # fmt: skip
AISC_SHAPE = {"W": "I", "M": "I", "S": "I", "HP": "I", "C": "C", "MC": "C", "L": "L",
              "2L": "2L", "WT": "T", "MT": "T", "ST": "T", "HSS": "HSS", "PIPE": "CHS"}  # fmt: skip

MM, CM = 1e-3, 1e-2
# EN symbol -> (Puzzle's symbol, factor to SI); the rest are dropped.
EN_FIELDS = {
    "G": ("mass", 1.0), "h": ("d", MM), "b": ("bf", MM), "tw": ("tw", MM), "tf": ("tf", MM),
    "t": ("t", MM), "r": ("r", MM), "A": ("A", CM**2), "zs": ("y", CM), "ys": ("x", CM),
    "ym": ("eo", CM), "Iy": ("Ix", CM**4), "Wel.y": ("Sx", CM**3), "Wpl.y": ("Zx", CM**3),
    "Avz": ("Avz", CM**2), "Iz": ("Iy", CM**4), "Wel.z": ("Sy", CM**3), "Wpl.z": ("Zy", CM**3),
    "It": ("J", CM**4), "Iw": ("Cw", 1e3 * CM**6), "Iu": ("Iu", CM**4), "Iv": ("Iv", CM**4),
    "alpha": ("alpha", 1.0),
}  # fmt: skip
EN_SHAPE = {"UPE": "C", "UPN": "C", "PFC": "C", "L": "L", "LU": "L"}  # the rest are I


def _number(text: str) -> float | None:
    text = text.strip()
    return None if text == "null" else float(text)


def aisc(folder: Path) -> list[dict]:
    src = (folder / "aisc_shapes_data.dart").read_text()
    rows = re.findall(r"\('([^']+)', '([^']+)', \[([^\]]*)\]\)", src)
    out = []
    for family, label, values in rows:
        numbers = [_number(v) for v in values.split(",")]
        if len(numbers) != len(AISC_FIELDS):
            raise ValueError(f"{label}: {len(numbers)} values, expected {len(AISC_FIELDS)}")
        row = {"designation": label, "family": family, "shape": AISC_SHAPE.get(family, family)}
        for (symbol, unit), value in zip(AISC_FIELDS, numbers, strict=True):
            if value is not None and unit != "ratio" or symbol == "tan(α)" and value is not None:
                row["mass" if symbol == "W" else symbol] = value * AISC_FACTOR[unit]
        if family == "HSS":  # round HSS have an OD; rectangular ones a height and width
            row["shape"] = "CHS" if "OD" in row else "RHS"
        if "tan(α)" in row:
            row["alpha"] = math.degrees(math.atan(row.pop("tan(α)")))
        out.append(row)
    return out


def en(folder: Path) -> list[dict]:
    src = (folder / "en_shapes_data.dart").read_text()
    rows = re.findall(r"\('([^']+)', '([^']+)', '([^']+)', (\d+), \{([^}]*)\}\)", src)
    out = []
    for family, _table, designation, _page, values in rows:
        row = {"designation": designation, "family": family, "shape": EN_SHAPE.get(family, "I")}
        for key, value in re.findall(r"'([^']+)': ([-\d.eE]+)", values):
            if key in EN_FIELDS:
                symbol, factor = EN_FIELDS[key]
                row[symbol] = float(value) * factor
        out.append(row)
    return out


def main() -> None:
    folder = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows, source in (
        ("aisc", aisc(folder), "AISC Shapes Database v16.0 (Aug. 2023), consistent with the "
         "AISC Steel Construction Manual, 16th ed."),
        ("en", en(folder), "ArcelorMittal Europe, Sections and Merchant Bars, sales programme "
         "V2026-1 (dimensions to EN 10365:2017 and EN 10056-1:2017; properties to EN "
         "1993-1-1:2005)"),
    ):  # fmt: skip
        data = {"source": source, "units": "SI (m, m², m⁴, m⁶, kg/m; alpha in degrees)",
                "generated_by": "scripts/import_sections.py from Lacewing Lab's tables",
                "sections": rows}  # fmt: skip
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=0, ensure_ascii=False) + "\n")
        print(f"{name}: {len(rows)} sections")


if __name__ == "__main__":
    main()
