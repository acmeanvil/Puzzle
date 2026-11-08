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


def _undecoded(path: Path) -> list[str]:
    """subprocess calls reading text with the locale's code page: a solver's byte that
    cp1252 can't map (0x9d) killed the thread reading CalculiX's and Code_Aster's output
    on Windows, and the solve with it."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "attr", None) or getattr(f, "id", None)
        if name not in ("run", "Popen", "check_output", "check_call"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        textual = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True
                      for k in ("text", "universal_newlines"))  # fmt: skip
        if textual and "encoding" not in kw and "errors" not in kw:
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_program_output_names_its_encoding():
    files = [p for d in ("src", "scripts") for p in (ROOT / d).rglob("*.py")]
    found = [where for p in files for where in _undecoded(p)]
    assert not found, f"{len(found)} reading program output without encoding=: " + ", ".join(found)


def test_a_project_file_with_a_byte_order_mark_opens(tmp_path):
    """Windows editors and PowerShell write UTF-8 with a BOM; tomllib refused it at
    line 1, column 1, so a project wouldn't reopen (the first Windows tests' I1)."""
    import pytest

    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Project
    from puzzle.project.store import load_project, save_project

    save_project(Project(name="Vessel"), tmp_path)
    header = tmp_path / "project.toml"
    header.write_bytes(b"\xef\xbb\xbf" + header.read_bytes())
    assert load_project(tmp_path).name == "Vessel"
    header.write_bytes(b"\x00\x01garbage")
    with pytest.raises(PuzzleError, match="project.toml .* isn't a readable project file"):
        load_project(tmp_path)


def test_a_console_without_a_character_gets_a_question_mark(monkeypatch):
    """Windows' cp1252 console can't print → or ², and a print there raised; the CLI
    replaces what the console lacks instead."""
    import io
    import sys

    from puzzle import cli

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(sys, "stdout", console)
    cli._tolerant_output()
    print("Zienkiewicz–Zhu → ²")
    console.flush()
    assert console.buffer.getvalue() == b"Zienkiewicz\x96Zhu ? \xb2\n"
