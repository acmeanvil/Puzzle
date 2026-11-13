"""Convert Lacewing Lab's fastener tables into Puzzle's fastener library (ADR 0050):
`src/puzzle/fasteners/fasteners.json`, SI throughout.

    python scripts/import_fasteners.py <Tension_Meter>/packages/engineering_core/lib

Lacewing Lab generated these tables into Dart from the standards and handbooks named in
each table's `reference` (ISO 4014/4017, 4032, 4035, 4762, 7380, 10642, 7089-7094, DIN
7984, ASME B18.2.1, B18.2.2, B18.2.6, B18.3, B18.22.1, B18.22M, ASTM F436, read from the
standards, Machinery's Handbook 26th ed., the Lindstrom Metric manual and Unbrako's guide);
only the published dimensions are carried over, each table with its reference and notes.
Also: ISO 273's medium clearance holes (`fastening/bolt_torque.dart`) and Shigley's Tables
8-1 and 8-2, the metric and Unified sizes with their tabulated stress and minor areas
(`fastening/threads.dart`).

Lengths become metres; counts (threads per inch), angles (degrees), masses (as printed:
kg per 1000 pieces metric, lb per 100 inch) and text are kept as printed. Each row also
gets its nominal diameter `d` (m) from its designation (M10, 1 1/8, #10), or null.
"""

from __future__ import annotations

import json
import re
import sys
from fractions import Fraction
from pathlib import Path

IN = 0.0254
MM = 1e-3
OUT = Path(__file__).resolve().parents[1] / "src" / "puzzle" / "fasteners" / "fasteners.json"

# --- a small reader for Dart constant expressions ----------------------------------------

TOKEN = re.compile(
    r"""(?P<ws>\s+)|(?P<comment>//[^\n]*)|(?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")
    |(?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|(?P<ident>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)
    |(?P<punct>[()\[\]{},:<>?])""",
    re.X,
)


def tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if m is None:
            raise ValueError(f"can't read {text[pos : pos + 40]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        out.append((kind, m.group()))
    return out


def unquote(s: str) -> str:
    body = s[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n"}.get(m.group(1), m.group(1)), body)


class Reader:
    """Values: strings (adjacent ones joined), numbers, null, identifiers, calls
    `Name(args)` (a dict of named and positional arguments), lists and records."""

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, want=None):
        tok = self.t[self.i]
        if want is not None and tok[1] != want:
            raise ValueError(f"expected {want}, got {tok}")
        self.i += 1
        return tok

    def value(self):
        kind, text = self.peek()
        if kind == "str":
            parts = []
            while self.peek()[0] == "str":
                parts.append(unquote(self.take()[1]))
            return "".join(parts)
        if kind == "num":
            self.take()
            return float(text) if re.search(r"[.eE]", text) else int(text)
        if text == "[":
            return self.items("[", "]")
        if text == "(":
            return tuple(self.items("(", ")"))
        if text == "<":  # a type argument before a list literal: <DimTable>[...]
            depth = 0
            while True:
                t = self.take()[1]
                depth += {"<": 1, ">": -1}.get(t, 0)
                if depth == 0:
                    break
            return self.value()
        if kind == "ident":
            self.take()
            if text == "null":
                return None
            if text in ("true", "false"):
                return text == "true"
            if self.peek()[1] == "(":
                return {"$call": text, **self.arguments()}
            return {"$ident": text}
        raise ValueError(f"unexpected {self.peek()}")

    def items(self, open_, close):
        self.take(open_)
        out = []
        while self.peek()[1] != close:
            out.append(self.value())
            if self.peek()[1] == ",":
                self.take()
        self.take(close)
        return out

    def arguments(self):
        self.take("(")
        named, positional = {}, []
        while self.peek()[1] != ")":
            if self.peek()[0] == "ident" and self.peek(1)[1] == ":":
                name = self.take()[1]
                self.take(":")
                named[name] = self.value()
            else:
                positional.append(self.value())
            if self.peek()[1] == ",":
                self.take()
        self.take(")")
        return {"args": positional, **named}


def constant(path: Path, name: str):
    """The value of `const name = ...;` (or `final`) in a Dart file."""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"(?:const|final)\s+{re.escape(name)}\s*=", text)
    if m is None:
        raise ValueError(f"no {name} in {path}")
    r = Reader(tokens(text[m.end() :].split(";\n", 1)[0]))
    return r.value()


# --- the size tables ----------------------------------------------------------------------


def enum(v, default):
    return v["$ident"].split(".", 1)[1] if isinstance(v, dict) else default


def nominal(label: str, unit: str) -> float | None:
    """A designation's nominal diameter (m): M10, M1.6, 1/4, 1 1/8, #10."""
    label = label.strip()
    if m := re.fullmatch(r"M(\d+(?:\.\d+)?)", label):
        return float(m.group(1)) * MM
    if m := re.fullmatch(r"#(\d+)", label):
        return (0.060 + 0.013 * int(m.group(1))) * IN
    if unit == "inch" and (m := re.fullmatch(r"(?:(\d+) )?(\d+)/(\d+)|(\d+)", label)):
        if m.group(4):
            return int(m.group(4)) * IN
        return float(int(m.group(1) or 0) + Fraction(int(m.group(2)), int(m.group(3)))) * IN
    return None


def kind_of(figure: str) -> str:
    return {"hexNut": "nut", "washer": "washer"}.get(figure, "screw")


def table(call: dict) -> dict:
    unit = enum(call["unit"], "mm")
    scale = IN if unit == "inch" else MM
    columns = []
    for c in call["columns"]:
        symbol, name = c["args"][:2]
        columns.append(
            {
                "symbol": symbol,
                "name": name,
                "role": enum(c.get("role"), None),
                "limit": enum(c.get("limit"), "nominal"),
                "kind": enum(c.get("kind"), "length"),
                "unit": c.get("unit"),
            }
        )
    rows = []
    for r in call["rows"]:
        size, values = r["args"][:2]
        assert len(values) == len(columns), (call["id"], size, len(values), len(columns))
        si = [
            v * scale if (c["kind"] == "length" and isinstance(v, int | float)) else v
            for v, c in zip(values, columns, strict=True)
        ]
        rows.append({"size": size, "d": nominal(size, unit), "values": si, "note": r.get("note")})
    figure = enum(call["figure"], None)
    return {
        "id": call["id"],
        "title": call["title"],
        "standard": call["standard"],
        "reference": call["reference"],
        "kind": kind_of(figure),
        "figure": figure,
        "printed_in": unit,
        "nut": call.get("nutTableId"),
        "columns": columns,
        "rows": rows,
        "notes": call.get("notes", []),
    }


def tidy(v):
    """Floats to 10 significant digits (the unit conversions leave noise past them)."""
    if isinstance(v, float):
        return float(f"{v:.10g}")
    if isinstance(v, list | tuple):
        return [tidy(x) for x in v]
    if isinstance(v, dict):
        return {k: tidy(x) for k, x in v.items()}
    return v


def dump(out: dict) -> str:
    """JSON with one line per column, row and thread size, so diffs stay readable."""

    def one(v) -> str:
        return json.dumps(tidy(v), ensure_ascii=False)

    def lines(items, pad: str) -> str:
        return "[\n" + ",\n".join(pad + one(x) for x in items) + "]"

    tables = []
    for t in out["tables"]:
        head = one({k: v for k, v in t.items() if k not in ("columns", "rows")})[:-1]
        tables.append(
            f'  {head},\n   "columns": {lines(t["columns"], "    ")},\n'
            f'   "rows": {lines(t["rows"], "    ")}}}'
        )
    threads = one({k: v for k, v in out["threads"].items() if k != "sizes"})[:-1]
    return (
        f'{{\n "about": {one(out["about"])},\n "tables": [\n' + ",\n".join(tables) + "],\n"
        f' "clearance_holes": {one(out["clearance_holes"])},\n'
        f' "threads": {threads},\n  "sizes": {lines(out["threads"]["sizes"], "   ")}}}\n}}\n'
    )


def main(lib: Path) -> None:
    std = lib / "standards"
    tables = []
    for file, name in (
        ("inch_fasteners_data.dart", "inchFastenerTables"),
        ("metric_fasteners_data.dart", "metricFastenerTables"),
        ("washer_data.dart", "washerTables"),
    ):
        for call in constant(std / file, name):
            tables.append(table(call))
    holes = constant(lib / "fastening" / "bolt_torque.dart", "iso273Medium")
    metric = constant(lib / "fastening" / "threads.dart", "_metric")
    unified = constant(lib / "fastening" / "threads.dart", "_unified")
    threads = []
    for d, pc, atc, arc, pf, atf, arf in metric:
        for series, p, at, ar in (("coarse", pc, atc, arc), ("fine", pf, atf, arf)):
            if p is not None:
                threads.append(
                    {
                        "series": "iso_" + series,
                        "label": f"M{d:g}x{p:g}",
                        "d": d * MM,
                        "pitch": p * MM,
                        "stress_area": at * MM**2,
                        "minor_area": ar * MM**2,
                    }
                )
    for size, d, nc, atc, arc, nf, atf, arf in unified:
        for series, n, at, ar in (("unc", nc, atc, arc), ("unf", nf, atf, arf)):
            if n is not None:
                label = "#" + size if d < 0.25 else size  # numbered sizes: #0 to #12
                threads.append(
                    {
                        "series": series,
                        "label": f"{label}-{n} {series.upper()}",
                        "d": d * IN,
                        "pitch": IN / n,
                        "stress_area": at * IN**2,
                        "minor_area": ar * IN**2,
                    }
                )
    out = {
        "about": "Generated by scripts/import_fasteners.py from Lacewing Lab's tables; SI "
        "(lengths m, areas m^2). Do not edit by hand.",
        "tables": tables,
        "clearance_holes": {
            "standard": "ISO 273:1979 (DIN EN 20273), medium series",
            "reference": "ISO 273:1979 medium series, as VDI 2230-1:2015 Table A1 uses it",
            "medium": [[d * MM, dh * MM] for d, dh in holes],
        },
        "threads": {
            "reference": "Budynas & Nisbett, Shigley's Mechanical Engineering Design, 10th ed., "
            "Tables 8-1 and 8-2 (5-44 UNF At printed 0.00880, taken as a misprint for 0.00831)",
            "sizes": threads,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(dump(out), encoding="utf-8", newline="\n")
    print(
        f"{len(tables)} tables, {sum(len(t['rows']) for t in tables)} rows, "
        f"{len(threads)} thread sizes -> {OUT}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
