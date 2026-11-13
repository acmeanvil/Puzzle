"""Import the Aluminum Association's standard shapes from the Aluminum Design Manual 2015,
Part V (ADR 0079 step 5), into Puzzle's section library: `src/puzzle/sections/aa.json`,
SI throughout, standard `aa`.

    python scripts/import_aluminum_sections.py "<path>/Aluminum-Design-Manual-2015.pdf"

Needs pypdf (on the path, or `--lib` a folder holding it). Tables read:

- Table 8, Aluminum Association standard I-beams: d, b_f, t_f, t_w, R, A, I_x, S_x, Z_x,
  r_x, I_y, S_y, Z_y, r_y, C_w, J;
- Table 22, pipes: OD, ID, t, A, I, S, Z, r, J;
- Table 23, square tubes: d, t, A, I, S, r, J, Z;
- Table 24, rectangular tubes: d, b, t, A, I_x, S_x, Z_x, r_x, I_y, S_y, Z_y, r_y, J.

Each row's area is checked against its dimensions (within 6 %: fillets and corner
radii), so a misread row is refused rather than shipped.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

IN = 0.0254
OUT = Path(__file__).resolve().parents[1] / "src" / "puzzle" / "sections" / "aa.json"
SOURCE = ("Aluminum Design Manual 2015 (The Aluminum Association), Part V: Table 8 standard "
          "I-beams, Table 22 pipes, Table 23 square tubes, Table 24 rectangular tubes")  # fmt: skip
NUMBER = re.compile(r"^-?\d*\.?\d+$")


def _pages(pdf: Path) -> list[str]:
    import pypdf

    reader = pypdf.PdfReader(str(pdf))
    return [(p.extract_text() or "") for p in reader.pages]


def _tables(pages: list[str], title: str) -> list[str]:
    """Every line of the pages whose text names `title`."""
    lines: list[str] = []
    for text in pages:
        if title in text:
            lines += text.splitlines()
    return lines


def _mend(tokens: list[str], expected: int) -> list[str] | None:
    """A number split by a stray space ("0.15 0" for 0.150) joined back, while there
    are more tokens than expected."""
    tokens = list(tokens)
    while len(tokens) > expected:
        for k in range(len(tokens) - 1):
            a, b = tokens[k], tokens[k + 1]
            if "." in a and re.fullmatch(r"\d", b):
                tokens[k : k + 2] = [a + b]
                break
        else:
            return None
    return tokens if len(tokens) == expected else None


def _si(row: dict, units: dict[str, int]) -> dict:
    return {k: (v * IN ** units[k] if k in units else v) for k, v in row.items()}


def i_beams(pages) -> list[dict]:
    keys = ["d", "bf", "tf", "tw", "r", "A", "Ix", "Sx", "Zx", "rx", "Iy", "Sy", "Zy", "ry", "Cw", "J"]
    units = {"d": 1, "bf": 1, "tf": 1, "tw": 1, "r": 1, "A": 2, "Ix": 4, "Sx": 3, "Zx": 3,
             "rx": 1, "Iy": 4, "Sy": 3, "Zy": 3, "ry": 1, "Cw": 6, "J": 4}  # fmt: skip
    out, seen = [], set()
    for line in _tables(pages, "ALUMINUM ASSOCIATION STANDARD I BEAMS"):
        tokens = line.split()
        if len(tokens) < 20 or tokens[0] != "I" or tokens[2] != "x":
            continue
        name = f"I{tokens[1]}X{tokens[3]}"
        rest = tokens[4:]
        if len(rest) == len(keys) + 1 and rest[0] == "1":  # a footnote mark
            rest = rest[1:]
        rest = [t.replace(" ", "") for t in rest]
        joined = " ".join(rest)
        joined = re.sub(r"(\d+) \.(\d+)", r"\1.\2", joined)  # "12 .000"
        mended = _mend(joined.split(), len(keys))
        if mended is None or name in seen:
            continue
        row = dict(zip(keys, map(float, mended), strict=True))
        area = 2 * row["bf"] * row["tf"] + (row["d"] - 2 * row["tf"]) * row["tw"]
        if abs(area / row["A"] - 1) > 0.06:
            raise SystemExit(f"{name}: area {row['A']} against {area:.3f} from its sizes")
        seen.add(name)
        out.append({"designation": name, "family": "I", "shape": "I", **_si(row, units)})
    return out


def square_tubes(pages) -> list[dict]:
    keys = ["d", "t", "W", "A", "I", "S", "r", "J", "Z", "bt"]
    out, seen = [], set()
    for line in _tables(pages, "SQUARE TUBES"):
        tokens = line.split()
        if not tokens or tokens[0] != "RT":
            continue
        values = tokens[-len(keys):]
        if not all(NUMBER.match(v) for v in values):
            continue
        row = dict(zip(keys, map(float, values), strict=True))
        name = f"RT{_dim(row['d'])}X{_dim(row['d'])}X{row['t']:g}"
        area = row["d"] ** 2 - (row["d"] - 2 * row["t"]) ** 2
        if name in seen or abs(area / row["A"] - 1) > 0.06:
            continue
        seen.add(name)
        si = {"Ht": row["d"] * IN, "B": row["d"] * IN, "tdes": row["t"] * IN, "A": row["A"] * IN**2,
              "Ix": row["I"] * IN**4, "Iy": row["I"] * IN**4, "Sx": row["S"] * IN**3,
              "Sy": row["S"] * IN**3, "Zx": row["Z"] * IN**3, "Zy": row["Z"] * IN**3,
              "rx": row["r"] * IN, "ry": row["r"] * IN, "J": row["J"] * IN**4}  # fmt: skip
        out.append({"designation": name, "family": "RT", "shape": "RHS", **si})
    return out


def rectangular_tubes(pages) -> list[dict]:
    keys = ["d", "b", "t", "W", "A", "Ix", "Sx", "Zx", "rx", "Iy", "Sy", "Zy", "ry", "J"]
    out, seen = [], set()
    for line in _tables(pages, "RECTANGULAR TUBES"):
        tokens = line.split()
        if not tokens or tokens[0] != "RT":
            continue
        values = tokens[-len(keys):]
        if not all(NUMBER.match(v) for v in values):
            continue
        row = dict(zip(keys, map(float, values), strict=True))
        name = f"RT{_dim(row['d'])}X{_dim(row['b'])}X{row['t']:g}"
        area = row["d"] * row["b"] - (row["d"] - 2 * row["t"]) * (row["b"] - 2 * row["t"])
        if name in seen or abs(area / row["A"] - 1) > 0.06:
            continue
        seen.add(name)
        si = {"Ht": row["d"] * IN, "B": row["b"] * IN, "tdes": row["t"] * IN, "A": row["A"] * IN**2,
              "Ix": row["Ix"] * IN**4, "Iy": row["Iy"] * IN**4, "Sx": row["Sx"] * IN**3,
              "Sy": row["Sy"] * IN**3, "Zx": row["Zx"] * IN**3, "Zy": row["Zy"] * IN**3,
              "rx": row["rx"] * IN, "ry": row["ry"] * IN, "J": row["J"] * IN**4}  # fmt: skip
        out.append({"designation": name, "family": "RT", "shape": "RHS", **si})
    return out


def pipes(pages) -> list[dict]:
    keys = ["OD", "ID", "t", "A", "W", "I", "S", "Z", "r", "J", "Rbt"]
    out, seen, size = [], set(), None
    for line in _tables(pages, "PIPES"):
        tokens = line.split()
        if len(tokens) < len(keys) + 1:
            continue
        values = tokens[-len(keys):]
        head = tokens[: -len(keys)]
        if not all(NUMBER.match(v) for v in values) or not head:
            continue
        schedule = head[-1]
        if len(head) > 1:
            size = " ".join(head[:-1])
        if size is None or not re.fullmatch(r"\d+|XX|STD|XS", schedule):
            continue
        row = dict(zip(keys, map(float, values), strict=True))
        area = math.pi / 4 * (row["OD"] ** 2 - row["ID"] ** 2)
        name = f"PIPE{size.replace(' ', '-')}SCH{schedule}"
        if name in seen or abs(area / row["A"] - 1) > 0.06:
            continue
        seen.add(name)
        si = {"OD": row["OD"] * IN, "ID": row["ID"] * IN, "tdes": row["t"] * IN,
              "A": row["A"] * IN**2, "Ix": row["I"] * IN**4, "Iy": row["I"] * IN**4,
              "Sx": row["S"] * IN**3, "Sy": row["S"] * IN**3, "Zx": row["Z"] * IN**3,
              "Zy": row["Z"] * IN**3, "rx": row["r"] * IN, "ry": row["r"] * IN,
              "J": row["J"] * IN**4}  # fmt: skip
        out.append({"designation": name, "family": "PIPE", "shape": "CHS", **si})
    return out


def _dim(v: float) -> str:
    return f"{v:g}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--lib", type=Path, help="a folder holding pypdf")
    args = parser.parse_args()
    if args.lib:
        sys.path.insert(0, str(args.lib))
    pages = _pages(args.pdf)
    sections = i_beams(pages) + square_tubes(pages) + rectangular_tubes(pages) + pipes(pages)
    for s in sections:
        s["mass"] = s["A"] * 2700.0  # 6061's density, as Part V's weights
    counts = {f: sum(s["family"] == f for s in sections) for f in ("I", "RT", "PIPE")}
    OUT.write_text(json.dumps({"source": SOURCE, "sections": sections}, indent=1) + "\n",
                   encoding="utf-8", newline="\n")  # fmt: skip
    print(f"wrote {OUT}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
