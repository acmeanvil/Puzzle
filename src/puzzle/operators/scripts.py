"""Python script operators (ADR 0062 phase 4): code the user writes, run apart.

A `script` operator's code sees its inputs by port name (a, b, c, d: a number in SI, a
list, a table as {column: [values]}, points as [[x, y, z], ...], a text) and sets `out`:
a number, a list, a table (a dict of equal-length lists), points or a text, as its
`returns` says. It runs

- in a **separate process**, the pixi environment `scripts` (numpy, scipy, polars and
  what `puzzle scripts add PACKAGE` installs), never Puzzle's own Python;
- with a **time limit** (`timeout_s`), killed past it;
- in an **empty working folder** of its own, its environment cleared, the project's
  folder never named, and sandboxed:
  - macOS: `sandbox-exec`: no network, writes only in its own folder (and /dev), the
    project folder, Puzzle's user folder (keys, hosts, trusted projects) and ~/.ssh not
    read either (`profile`);
  - Linux: bubblewrap (`bwrap`, where installed): the system read-only, the project
    folder and Puzzle's user folder empty tmpfs, its own folder the one it writes, no
    network;
  - Windows: an AppContainer (`sandbox_win`): the environment and its own folder
    granted, nothing of the user's, no network, in a job object;
  where none can be had (Linux without bwrap) it is refused, code `unsandboxed`, saying
  what to install, unless the user's preference `scripts.unsandboxed` lets it run
  (ADR 0090, BE-22: it used to run there silently);
- only in a **trusted project** (`puzzle.trust`).

The project's folder is a required argument of `run` (`project_root`, None only for a
project that has none on disk), so no caller can leave it out by accident: about ten
callers did, and on macOS and Linux those scripts could read the project.

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
                          encoding="utf-8", errors="replace", timeout=60,
                          env=_environment())  # fmt: skip
    return json.loads(done.stdout or "[]")


def _environment() -> dict[str, str]:
    """A cleared environment: nothing of Puzzle's (its paths, keys, the project)."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def kind() -> str:
    """Which sandbox scripts run in here: sandbox-exec, bwrap, appcontainer or none."""
    import shutil

    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    if os.name == "nt":
        return "appcontainer"
    return "none"


def _quoted(path) -> str:
    return str(Path(path).resolve()).replace("\\", "").replace('"', "")


def _private() -> list[Path]:
    """Folders a script never reads: Puzzle's user folder (agent.toml, hosts.toml,
    trusted.json) and the user's SSH keys."""
    from puzzle.materials import user_data_dir

    return [user_data_dir(), Path.home() / ".ssh"]


def profile(project_root, folder: Path | None) -> str:
    """macOS's sandbox profile for a script: everything else allowed (reading the system
    and the scripts environment), but no network, no writes outside its own folder and
    /dev, and the project folder and the private folders neither read nor written. The
    rules are read in order, the last that matches deciding."""
    rules = ["(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)"]
    if folder is not None:
        rules.append(f'(allow file-write* (subpath "{_quoted(folder)}"))')
    rules.append('(allow file-write* (subpath "/dev"))')
    hidden = [*([project_root] if project_root is not None else []), *_private()]
    for where in hidden:
        rules.append(f'(deny file-read* file-write* (subpath "{_quoted(where)}"))')
    return " ".join(rules)


def _sandbox(argv: list[str], project_root, folder: Path | None = None) -> list[str]:
    """The run under the platform's sandbox: macOS sandbox-exec (`profile`); Linux
    bubblewrap (everything read-only, the project folder and the private folders empty
    tmpfs, the work folder writable, no network). Windows' AppContainer isn't a command
    line: `run` starts it. Without one, the argv as it is (`run` refuses it first unless
    the user allowed that)."""
    how = kind()
    if how == "sandbox-exec":
        return ["/usr/bin/sandbox-exec", "-p", profile(project_root, folder), *argv]
    if how == "bwrap":
        import shutil

        out = [
            shutil.which("bwrap") or "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
        ]
        if project_root is not None:
            out += ["--tmpfs", str(Path(project_root).resolve())]
        for where in _private():
            if where.is_dir():
                out += ["--tmpfs", str(where.resolve())]
        if folder is not None:
            out += ["--bind", str(folder), str(folder), "--chdir", str(folder)]
        return [*out, *argv]
    return argv


def _contained(exe: Path, folder: Path, timeout_s: float) -> str:
    """Windows: the runner in the AppContainer (`sandbox_win`), its output in files in
    its own folder (a container gets no pipes of Puzzle's); what it printed."""
    from puzzle.operators import sandbox_win

    env = _environment()
    # CreateProcess needs LOCALAPPDATA to start a container (it is redirected to the
    # container's own folder there); the user's other paths stay out
    env.update({"TEMP": str(folder), "TMP": str(folder),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", str(folder))})  # fmt: skip
    argv = [str(exe), "-I", "-c",
            "import sys; sys.stdout = open('stdout.txt', 'w'); sys.stderr = sys.stdout; "
            "exec(open('runner.py').read())"]  # fmt: skip
    try:
        _code, late = sandbox_win.run(argv, folder, env, timeout_s, readable=[exe.parent])
    except OSError as exc:
        raise PuzzleError(f"the script sandbox couldn't start: {exc}",
                          code="script_failed") from None  # fmt: skip
    if late:
        raise PuzzleError(
            f"the script ran past its {timeout_s:g} s limit and was stopped", code="timeout"
        )
    said = folder / "stdout.txt"
    return said.read_text(encoding="utf-8", errors="replace") if said.exists() else ""


def unsandboxed_allowed() -> bool:
    """The user's preference `scripts.unsandboxed`: run scripts where no sandbox is."""
    try:
        from puzzle.settings import current

        return bool(current().get("scripts.unsandboxed"))
    except Exception:  # noqa: BLE001 - no settings (a bare script): the safe answer
        return False


def refuse_unsandboxed() -> None:
    """No sandbox here: refused, saying what to install, unless the user allowed it."""
    if kind() != "none" or unsandboxed_allowed():
        return
    what = (
        "install bubblewrap (sudo apt install bubblewrap, sudo dnf install bubblewrap)"
        if sys.platform.startswith("linux")
        else "run Puzzle where a sandbox is"
    )
    raise PuzzleError(
        f"no sandbox for scripts on this computer, so the script does not run: {what}, or "
        "allow scripts without one (Settings > Solvers > Scripts where no sandbox is)",
        code="unsandboxed",
    )


def run(code: str, inputs: dict[str, Any], *, timeout_s: float = 30.0,
        project_root) -> Any:  # fmt: skip
    """The value the script leaves in `out` (JSON-plain), or a PuzzleError.
    `project_root` (required: None only for a project with no folder) is the folder the
    sandbox hides."""
    exe = python()
    if exe is None:
        raise PuzzleError("no scripts environment: install it with pixi install -e scripts",
                          code="unsupported")  # fmt: skip
    refuse_unsandboxed()
    with tempfile.TemporaryDirectory(prefix="puzzle-script-") as work:
        folder = Path(work)
        (folder / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
        (folder / "script.py").write_text(code, encoding="utf-8")
        (folder / "runner.py").write_text(RUNNER, encoding="utf-8")
        if kind() == "appcontainer":
            said = _contained(exe, folder, timeout_s)
        else:
            argv = _sandbox([str(exe), "-I", "runner.py"], project_root, folder)
            try:
                done = subprocess.run(argv, cwd=folder, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=timeout_s,
                                      env=_environment())  # fmt: skip
            except subprocess.TimeoutExpired:
                raise PuzzleError(f"the script ran past its {timeout_s:g} s limit and was "
                                  "stopped", code="timeout") from None  # fmt: skip
            said = done.stderr or done.stdout
        result = folder / "out.json"
        if not result.exists():
            tail = said.strip().splitlines()[-3:]
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
