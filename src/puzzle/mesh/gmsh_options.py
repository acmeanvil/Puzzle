"""Gmsh's own mesh options, with their defaults and descriptions (ADR 0019).

Gmsh prints every option (`gmsh -help_options`): `Name = default; // description`.
The meshing ones (Mesh.*, numbers and text; colours left out) are listed for the Mesh
tab's Gmsh options… dialog, searchable, so a recipe's `gmsh_options` can name any of
them without guessing. Read once per session.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LINE = re.compile(r"^(Mesh\.[A-Za-z0-9_.]+) = (.*?); // (.*)$")


@dataclass(frozen=True)
class GmshOption:
    name: str
    default: float | str
    description: str

    @property
    def text(self) -> bool:
        return isinstance(self.default, str)


def _gmsh() -> str | None:
    beside = Path(sys.executable).parent / ("gmsh.exe" if sys.platform == "win32" else "gmsh")
    return str(beside) if beside.exists() else shutil.which("gmsh")


@functools.cache
def mesh_options() -> tuple[GmshOption, ...]:
    """Gmsh's Mesh.* options; empty when the gmsh program can't be found."""
    exe = _gmsh()
    if exe is None:
        return ()
    try:
        out = subprocess.run([exe, "-help_options"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    options = []
    for line in out.stdout.splitlines():
        m = LINE.match(line.strip())
        if not m or m.group(2).startswith("{"):  # colours
            continue
        raw = m.group(2)
        value: float | str = raw.strip('"') if raw.startswith('"') else float(raw)
        options.append(GmshOption(m.group(1), value, m.group(3)))
    return tuple(options)


def search(words: str) -> list[GmshOption]:
    """The options whose name or description holds every word (any case)."""
    terms = words.lower().split()
    return [
        o
        for o in mesh_options()
        if all(t in o.name.lower() or t in o.description.lower() for t in terms)
    ]
