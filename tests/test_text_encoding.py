"""Every text file Puzzle reads or writes names its encoding.

Without one, Python uses the locale's: UTF-8 on Linux and macOS, cp1252 on Windows,
where the bundled section tables and the agent's notes failed to load and 74 tests
failed (ADR 0068's first full Windows run). So `read_text`, `write_text` and `open` in
text mode say `encoding="utf-8"`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
NOT_FILES = {"os", "webbrowser", "urllib", "request", "gzip", "bz2", "lzma", "zipfile",
             "tarfile", "io", "codecs", "subprocess", "h5py", "Image", "QFile",
             "tempfile"}  # fmt: skip


def _mode(call: ast.Call, index: int):
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value.value if isinstance(kw.value, ast.Constant) else ...
    if len(call.args) > index:
        arg = call.args[index]
        return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ...
    return None


def _unnamed(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or any(
            kw.arg in ("encoding", None) for kw in node.keywords
        ):
            continue
        f, mode = node.func, None
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            continue
        if isinstance(f, ast.Name) and f.id == "open":
            mode = _mode(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr == "open" and (node.args or node.keywords):
            owner = f.value
            name = getattr(owner, "id", None) or getattr(owner, "attr", None)
            if name in NOT_FILES:
                continue
            mode = _mode(node, 0)
        else:
            continue
        if mode is None or (mode is not ... and "b" not in mode):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_text_io_names_its_encoding():
    files = [p for d in ("src", "scripts", "tests") for p in (ROOT / d).rglob("*.py")]
    found = [where for p in files for where in _unnamed(p)]
    assert not found, f"{len(found)} without encoding=: " + ", ".join(found[:20])
