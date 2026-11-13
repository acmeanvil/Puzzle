"""Every error code the source raises is registered in `errors.CODES`: `PuzzleError`
asserts on an unknown one, so an unregistered code turned a plain "choose what to read"
message on a new Model value operator into an AssertionError dialog."""

import ast
from pathlib import Path

from puzzle.errors import CODES

SRC = Path(__file__).parents[1] / "src" / "puzzle"


def _raised_codes(tree: ast.AST):
    """`code="..."` given to an error class (`PuzzleError`, `InputError`, ...), not the
    codes of solver messages that progress events carry."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
        if not name.endswith("Error"):
            continue
        for kw in node.keywords:
            if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                yield kw.value.value, node.lineno


def test_every_code_raised_is_registered():
    unknown = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        unknown += [f"{path.relative_to(SRC)}:{line} {code}"
                    for code, line in _raised_codes(tree) if code not in CODES]  # fmt: skip
    assert not unknown, f"codes missing from errors.CODES: {unknown}"
