"""Keyword overrides in a written .sif (ADR 0023): Elmer's expert pass-through, and how
its typed settings are placed.

A path is `Section/Keyword`: `Solver 1/Linear System Max Iterations`, `Simulation/Max
Output Level`; `Solver */...` means every solver section. The keyword's line is
replaced (with any continuation lines under it) or added before the section's `End`.
Values are written as given, so a keyword Elmer doesn't list needs its type, as in
the SIF itself (`Logical True`, `Real 1e-8`). A section the file doesn't have is
refused: a pass-through is never dropped.
"""

from __future__ import annotations

import re

from puzzle.adapters.common import InputError

PATH = re.compile(r"^([A-Za-z][A-Za-z ]*?(?: \d+| \*)?)/([A-Za-z][A-Za-z0-9 ()]*)$")


def check(path: str, value: str) -> None:
    if not PATH.match(path):
        raise InputError(
            f"'{path}': write Section/Keyword, e.g. Solver 1/Linear System Max Iterations "
            "(Solver */... for every solver)",
            code="invalid",
        )
    if not str(value).strip() or "\n" in str(value):
        raise InputError(f"'{path}': give one value on one line", code="invalid")


def sections(sif: str) -> list[tuple[str, int, int]]:
    """(name, first line, End line) of each section."""
    lines = sif.split("\n")
    out, start, name = [], None, None
    for i, line in enumerate(lines):
        if start is None and line and not line.startswith((" ", "!")) and line.strip() != "End":
            start, name = i, line.strip()
        elif start is not None and line.strip() == "End":
            out.append((name, start, i))
            start = None
    return out


def apply(sif: str, overrides: dict[str, str], skip=lambda section_lines: False) -> str:
    """The .sif with every override placed. `skip(lines)` leaves a section alone for
    `Solver *` paths (the magnetic A-V solve keeps its direct method)."""
    for path, value in overrides.items():
        check(path, value)
        section, keyword = PATH.match(path).groups()
        lines = sif.split("\n")
        targets = [
            (s, a, b)
            for s, a, b in sections(sif)
            if s.lower() == section.lower()
            or (section.endswith("*") and s.lower().startswith(section[:-1].lower())
                and not skip(lines[a:b]))
        ]  # fmt: skip
        if not targets:
            raise InputError(
                f"'{path}': this study's input has no section {section}",
                code="unsupported",
                details={"sections": [s for s, _, _ in sections(sif)]},
            )
        for _name, first, end in reversed(targets):
            body = lines[first + 1 : end]
            at = next(
                (k for k, line in enumerate(body)
                 if line.split("=")[0].strip().lower() == keyword.lower()),
                None,
            )  # fmt: skip
            new = f"  {keyword} = {value}"
            if at is None:
                body.append(new)
            else:
                stop = at + 1
                while stop < len(body) and body[stop].startswith("    "):  # continuations
                    stop += 1
                body[at:stop] = [new]
            lines[first + 1 : end] = body
        sif = "\n".join(lines)
    return sif
