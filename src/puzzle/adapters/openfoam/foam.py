"""OpenFOAM dictionary files: writing case dictionaries, reading ASCII fields."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

HEADER = """FoamFile
{{
    version 2.0;
    format ascii;
    class {cls};
    object {name};
}}
"""


def _value(v) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, float):
        return f"{v:.10g}"
    if isinstance(v, (list, tuple)):
        return "(" + " ".join(_value(x) for x in v) + ")"
    return str(v)


def render(entries: dict, indent: int = 0) -> str:
    """A dictionary body: nested dicts become sub-dictionaries, the rest `key value;`.

    Strings are written as they are, so `"uniform (0 0 0)"` or `"Gauss linear"` pass
    through; lists become OpenFOAM lists.
    """
    pad = " " * indent
    out = []
    for key, v in entries.items():
        if isinstance(v, dict):
            out.append(f"{pad}{key}\n{pad}{{\n{render(v, indent + 4)}{pad}}}\n")
        else:
            out.append(f"{pad}{key} {_value(v)};\n")
    return "".join(out)


def write_dict(path: Path, entries: dict, cls: str = "dictionary") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER.format(cls=cls, name=path.name) + "\n" + render(entries))


def read_internal_field(path: Path, count: int) -> np.ndarray:
    """A field file's internalField, as (count, components); uniform values repeat.

    Needs `writeFormat ascii` and no compression, which the adapter's controlDict sets.
    """
    text = path.read_text()
    at = text.index("internalField")
    head = text[at : at + 200]
    if head.split()[1].startswith("uniform"):
        inner = head[head.index("uniform") + len("uniform") : head.index(";")]
        value = np.array(inner.replace("(", " ").replace(")", " ").split(), dtype=float)
        return np.tile(value, (count, 1))
    match = re.compile(r"nonuniform\s+List<\w+>\s*(\d+)\s*\(").search(text, at)
    n = int(match.group(1))
    body = text[match.end() : text.index("\n)", match.end())]
    values = np.array(body.replace("(", " ").replace(")", " ").split(), dtype=float)
    if n != count:
        raise ValueError(f"{path.name} holds {n} cell values, the mesh has {count} cells")
    return values.reshape(n, -1)


def latest_time(case: Path) -> Path | None:
    """The newest time directory after 0 (a solved state), or None."""
    times = []
    for d in case.iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        if d.is_dir() and t > 0:
            times.append((t, d))
    return max(times)[1] if times else None
