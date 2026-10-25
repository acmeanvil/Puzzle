"""Keyword overrides in a written .comm file (ADR 0023): Code_Aster's expert
pass-through, and how its typed settings are placed.

A path names a command and a keyword, optionally inside a factor keyword:

- `MECA_STATIQUE.SOLVEUR.METHODE = 'MUMPS'`: in every MECA_STATIQUE call, the
  SOLVEUR factor's METHODE (the factor is made if missing, the keyword replaced if
  there);
- `STAT_NON_LINE.INFO = 2`: a keyword of the command itself.

Values are Code_Aster syntax: a number, a quoted string, or a tuple of them; anything
else is refused, as is a path to a command the study doesn't use (it would be ignored,
and pass-through is never dropped silently).
"""

from __future__ import annotations

import ast
import re

from puzzle.adapters.common import InputError

PATH = re.compile(r"^[A-Z][A-Z0-9_]*(\.[A-Z][A-Z0-9_]*){1,2}$")


def check(path: str, value: str) -> None:
    if not PATH.match(path):
        raise InputError(
            f"'{path}': write COMMAND.KEYWORD or COMMAND.FACTOR.KEYWORD in capitals, "
            "e.g. STAT_NON_LINE.NEWTON.REAC_ITER",
            code="invalid",
        )
    try:
        literal = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        raise InputError(
            f"'{path}': {value!r} is not a Code_Aster value (a number, 'TEXT', or a tuple)",
            code="invalid",
        ) from None
    if not isinstance(literal, int | float | str | tuple):
        raise InputError(f"'{path}': {value!r} is not a Code_Aster value", code="invalid")


def apply(comm: str, overrides: dict[str, str]) -> str:
    """The .comm with each override placed in every call of its command."""
    for path, value in overrides.items():
        check(path, value)
        command, *keys = path.split(".")
        calls = list(_calls(comm, command))
        if not calls:
            raise InputError(
                f"'{path}': this study's command file has no {command}",
                code="unsupported",
                details={"commands": sorted(set(re.findall(r"\b([A-Z][A-Z_]{3,})\(", comm)))},
            )
        for start, end in reversed(calls):  # the argument text between the parentheses
            comm = comm[:start] + _set(comm[start:end], keys, value) + comm[end:]
    return comm


def _calls(text: str, command: str):
    for m in re.finditer(rf"\b{command}\(", text):
        start = m.end()
        yield start, _closing(text, start)


def _closing(text: str, start: int) -> int:
    """The index of the parenthesis closing the one just before `start`."""
    depth, quote = 1, None
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    raise InputError("unbalanced parentheses in the command file", code="invalid")


def _top_level(args: str) -> list[tuple[str, int, int]]:
    """(keyword, value start, value end) of each top-level `KEY=value` in `args`."""
    out, depth, quote, i = [], 0, None, 0
    while i < len(args):
        c = args[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0:
            m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", args[i:])
            if m and (i == 0 or args[i - 1] in ", \n("):
                vstart = i + m.end()
                vend = _value_end(args, vstart)
                out.append((m.group(1), vstart, vend))
                i = vend
                continue
        i += 1
    return out


def _value_end(args: str, start: int) -> int:
    depth, quote = 0, None
    for i in range(start, len(args)):
        c = args[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
    return len(args)


def _set(args: str, keys: list[str], value: str) -> str:
    """`args` (a call's argument text) with keys[0] (or keys[0]'s factor keys[1]) set."""
    found = {k: (s, e) for k, s, e in _top_level(args)}
    if len(keys) == 1:
        key = keys[0]
        if key in found:
            s, e = found[key]
            return args[:s] + value + args[e:]
        return args.rstrip() + f", {key}={value}"
    factor, key = keys
    if factor not in found:
        return args.rstrip() + f", {factor}=_F({key}={value})"
    s, e = found[factor]
    text = args[s:e]
    if text.strip().startswith("("):  # a tuple of _F(...): set it in each
        inner = re.sub(r"_F\(", "\0", text)
        parts = inner.split("\0")
        out = parts[0]
        for part in parts[1:]:
            close = _closing("(" + part, 1) - 1
            out += "_F(" + _set(part[:close], [key], value) + part[close:]
        return args[:s] + out + args[e:]
    m = re.match(r"\s*_F\(", text)
    if not m:
        raise InputError(f"{factor} is not a factor keyword (_F)", code="invalid")
    open_at = s + m.end()
    close_at = _closing(args, open_at)
    return args[:open_at] + _set(args[open_at:close_at], [key], value) + args[close_at:]
