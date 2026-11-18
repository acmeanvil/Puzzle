"""The core never imports the GUI (ADR 0003: "`puzzle.gui` imports core; never the
reverse"), so it runs headless and from scripts without PySide6 (ADR-E-16, ADR 0090
package 16).

Three entry points into Qt features are allowed, each inside a function and named here
with its reason; anything else under `src/puzzle/` outside `gui/` that imports
`puzzle.gui` or PySide6 fails the first test.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "puzzle"

# (module, imported name) -> why it is allowed. Each is a lazy import in a function.
ALLOWED = {
    ("cli.py", "puzzle.gui.app"): "`puzzle gui` starts the GUI",
    ("cli.py", "puzzle.gui.solution_panel"): "`puzzle progress --plot` draws the Solution "
    "panel off screen; refused in words without PySide6",
    ("help/site.py", "PySide6"): "the PDF manual is printed by Qt; refused in words "
    "without PySide6 (the site builds without it)",
}


def _imports(path: Path) -> list[tuple[str, int, bool]]:
    """(module, line, at module level) for each import of the GUI or Qt in a file."""
    rel = path.relative_to(SRC)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {id(n) for n in tree.body}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = list(rel.parts[:-1])
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                module = ".".join(["puzzle", *base, *([module] if module else [])])
            names = [module]
        else:
            continue
        for name in names:
            if name.startswith(("puzzle.gui", "PySide6")):
                out.append((name, node.lineno, id(node) in top))
    return out


def _key(name: str) -> str:
    return "PySide6" if name.startswith("PySide6") else name


def test_no_core_module_imports_the_gui():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("gui/"):
            continue
        for name, line, at_top in _imports(path):
            if (rel, _key(name)) in ALLOWED and not at_top:
                continue
            found.append(f"{rel}:{line}: {name}")
    assert found == [], "move what the core needs out of puzzle.gui:\n" + "\n".join(found)


def test_every_allowed_import_is_still_there():
    """An allowance nobody needs any more is taken off the list."""
    seen = set()
    for module, _ in ALLOWED:
        for name, _, _ in _imports(SRC / module):
            seen.add((module, _key(name)))
    assert set(ALLOWED) <= seen


def test_the_core_imports_and_builds_the_help_with_pyside6_blocked():
    """With PySide6 unimportable, every non-GUI module imports, and the generated help
    reference (which reads the Properties descriptions) and the site's page bodies are
    made: they had failed with ModuleNotFoundError (ADR-E-16)."""
    code = r"""
import importlib, pkgutil, sys
sys.modules["PySide6"] = None
import puzzle
bad = []
for m in pkgutil.walk_packages(puzzle.__path__, "puzzle."):
    if m.name.startswith("puzzle.gui") or m.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(m.name)
    except ModuleNotFoundError as exc:
        if "PySide6" in str(exc) or "puzzle.gui" in str(exc):
            bad.append(f"{m.name}: {exc}")
    except Exception:
        pass  # optional packages of their own (OCP, solvers' envs): not this test's
from puzzle.help import reference, site
reference.fields()
from puzzle import help as manual
html = site.page_html(manual.parse("x", "---\ntitle: X\n---\n\nSpeed $v = d/t$.\n"))
assert "<img" in html, html
from puzzle.model.units import to_display
to_display([1.0], "length")
print("BAD", bad)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", timeout=600)  # fmt: skip
    assert out.returncode == 0, out.stderr[-3000:]
    assert out.stdout.strip().splitlines()[-1] == "BAD []"
