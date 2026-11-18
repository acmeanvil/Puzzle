"""Markdown as Puzzle writes it, cut into prose and fenced code, with its TeX found:
the agent's transcript (ADR 0074) and the help's site and PDF (ADR 0083) both read it.
No Qt, so the help builds without the GUI (ADR 0090 package 16)."""

from __future__ import annotations

import re
from dataclasses import dataclass

FENCE = re.compile(r"^```([\w+.-]*)[^\n]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
DISPLAY_TEX = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
INLINE_TEX = re.compile(r"(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])")


@dataclass
class Segment:
    kind: str  # "text" or "code"
    text: str
    language: str = ""


def segments(markdown: str) -> list[Segment]:
    """A reply cut into its prose and its fenced code blocks, in order."""
    out, at = [], 0
    for m in FENCE.finditer(markdown):
        if markdown[at : m.start()].strip():
            out.append(Segment("text", markdown[at : m.start()]))
        out.append(Segment("code", m.group(2).rstrip("\n"), m.group(1)))
        at = m.end()
    if markdown[at:].strip():
        out.append(Segment("text", markdown[at:]))
    return out
