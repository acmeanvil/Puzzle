"""Python script operators (ADR 0062 phase 4): code the user writes, run apart.

A `script` operator's code sees its inputs by port name (a, b, c, d: a number in SI, a
list, a table as {column: [values]}, points as [[x, y, z], ...], a text) and sets `out`:
a number, a list, a table (a dict of equal-length lists), points or a text, as its
`returns` says. It runs

- in a **separate process**, the pixi environment `scripts` (numpy, scipy, polars and
  what `puzzle scripts add PACKAGE` installs), never Puzzle's own Python;
- with a **time limit** (`timeout_s`), killed past it;
- in an **empty working folder** of its own, its environment cleared, the project's
  folder never named; on macOS under `sandbox-exec`, which denies reading or writing
  that folder and the network (elsewhere the folder is simply not given);
- only in a **trusted project** (`puzzle.trust`).

Its code's text and its inputs' values are in the hashes (the operator's content and
its upstream operators), so a pre-solve script stales what it feeds when either
changes. Its imports are the project's record of what it needs (`imports`); `missing`
says which the environment lacks.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from puzzle.errors import PuzzleError

ENVIRONMENT = "scripts"
STDLIB = set(getattr(sys, "stdlib_module_names", ()))
RUNNER = r"""
import json, sys, traceback
with open("inputs.json") as f:
    names = json.load(f)
code = open("script.py").read()
space = dict(names)
try:
    exec(compile(code, "script", "exec"), space)
except Exception:
    with open("out.json", "w") as f:
        json.dump({"error": traceback.format_exc(limit=4)}, f)
    sys.exit(0)
out = space.get("out")

def plain(v):
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, np.generic):
            return v.item()
    except ImportError:
        pass
    if hasattr(v, "to_dict") and hasattr(v, "columns"):  # a pandas or polars frame
        try:
            return {c: [plain(x) for x in v[c].to_list()] for c in v.columns}
        except AttributeError:
            return {c: [plain(x) for x in v[c].tolist()] for c in v.columns}
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v

with open("out.json", "w") as f:
    json.dump({"out": plain(out)}, f)
"""


def python() -> Path | None:
    """The scripts environment's Python, beside Puzzle's own environments."""
    from puzzle.jobs.runtime import environment

    if os.environ.get("PUZZLE_SCRIPTS_PYTHON"):
        return Path(os.environ["PUZZLE_SCRIPTS_PYTHON"])
    env = environment(ENVIRONMENT)
    exe = env / ("python.exe" if os.name == "nt" else "bin/python") if env else None
    return exe if exe is not None and exe.exists() else None


def imports(code: str) -> list[str]:
    """The top-level packages a script imports, the standard library left out."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PuzzleError(f"the script does not parse: {exc.msg} (line {exc.lineno})",
                          code="invalid") from None  # fmt: skip
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return sorted(found - STDLIB)


def missing(code: str) -> list[str]:
    """The packages a script imports that the scripts environment lacks."""
    wanted = imports(code)
    exe = python()
    if not wanted or exe is None:
        return wanted if exe is None else []
    probe = (
        "import importlib.util, json, sys; print(json.dumps("
        "[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]))"
    )
    done = subprocess.run([str(exe), "-c", probe, *wanted], capture_output=True, text=True,
                          timeout=60, env=_environment())  # fmt: skip
    return json.loads(done.stdout or "[]")


def _environment() -> dict[str, str]:
    """A cleared environment: nothing of Puzzle's (its paths, keys, the project)."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _sandbox(argv: list[str], project_root) -> list[str]:
    """macOS: the run under sandbox-exec, the project folder and the network denied."""
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        return argv
    rules = ["(version 1)", "(allow default)", "(deny network*)"]
    if project_root is not None:
        folder = str(Path(project_root).resolve()).replace('"', "")
        rules.append(f'(deny file-read* file-write* (subpath "{folder}"))')
    return ["/usr/bin/sandbox-exec", "-p", " ".join(rules), *argv]


def run(code: str, inputs: dict[str, Any], *, timeout_s: float = 30.0,
        project_root=None) -> Any:  # fmt: skip
    """The value the script leaves in `out` (JSON-plain), or a PuzzleError."""
    exe = python()
    if exe is None:
        raise PuzzleError("no scripts environment: install it with pixi install -e scripts",
                          code="unsupported")  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="puzzle-script-") as work:
        folder = Path(work)
        (folder / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
        (folder / "script.py").write_text(code, encoding="utf-8")
        (folder / "runner.py").write_text(RUNNER, encoding="utf-8")
        argv = _sandbox([str(exe), "-I", "runner.py"], project_root)
        try:
            done = subprocess.run(argv, cwd=folder, capture_output=True, text=True,
                                  timeout=timeout_s, env=_environment())  # fmt: skip
        except subprocess.TimeoutExpired:
            raise PuzzleError(f"the script ran past its {timeout_s:g} s limit and was stopped",
                              code="timeout") from None  # fmt: skip
        result = folder / "out.json"
        if not result.exists():
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            raise PuzzleError("the script stopped without a result: " + " / ".join(tail),
                              code="script_failed")  # fmt: skip
        got = json.loads(result.read_text(encoding="utf-8"))
    if "error" in got:
        last = got["error"].strip().splitlines()[-1]
        raise PuzzleError(f"the script failed: {last}", code="script_failed",
                          details={"traceback": got["error"]})  # fmt: skip
    return got.get("out")


def encode(value) -> Any:
    """An operator value as a script sees it."""
    from puzzle.operators import evaluate as ev
    from puzzle.operators.geometry_data import Points

    if isinstance(value, ev.Number):
        return value.value
    if isinstance(value, ev.ListValue):
        return [float(v) if value.numeric else v for v in value.values]
    if isinstance(value, Points):
        return value.xyz.tolist()
    if isinstance(value, ev.Table):
        return {c: value.frame[c].to_list() for c in value.columns}
    if isinstance(value, ev.Text):
        return value.value
    raise PuzzleError(f"a script takes numbers, lists, tables, points and texts, not "
                      f"{type(value).__name__}", code="invalid")  # fmt: skip


def decode(op, got):
    """The script's `out` as the operator value its `returns` says."""
    from puzzle.operators import evaluate as ev
    from puzzle.operators.geometry_data import Points

    want = op.returns
    try:
        if want == "number":
            return ev.Number(float(got), None, op.name)
        if want == "list":
            values = list(got)
            numeric = all(isinstance(v, (int, float)) for v in values)
            return ev.ListValue([float(v) for v in values] if numeric else values, None,
                                op.name)  # fmt: skip
        if want == "points":
            return Points(got, op.name)
        if want == "table":
            columns = list(got)
            rows = [list(r) for r in zip(*(got[c] for c in columns), strict=True)]
            return ev.Table(columns, rows, [None] * len(columns))
        return ev.Text(str(got))
    except (TypeError, ValueError) as exc:
        raise PuzzleError(f"'{op.name}': its script's out is not a {want} ({exc})",
                          code="invalid") from None  # fmt: skip
