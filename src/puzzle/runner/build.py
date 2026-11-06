"""The runner as one archive for a remote host: this package and PSI/J's pure-Python tree.

PSI/J loads its scheduler plugins and templates from files, so the runner can't run
from inside a zip: the archive is unpacked on the host into a folder named for its hash
(`~/.puzzle-runner/runner-<hash>/`) and run as `python3 <folder>/main.py`. Without the
pixi `runner` environment the archive holds the runner alone, which runs jobs without a
scheduler but refuses Slurm, PBS and LSF.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

#: What the runner takes from the `runner` environment's site-packages.
VENDOR = ("psij", "psij-descriptors", "pystache", "typeguard", "packaging", "filelock",
          "typing_compat.py", "typing_extensions.py", "importlib_metadata", "zipp")  # fmt: skip
MAIN = '''"""Puzzle's job runner (ADR 0067), unpacked by Puzzle; run as python3 main.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from puzzle.runner.__main__ import main  # noqa: E402

sys.exit(main())
'''


def vendor_dir() -> Path | None:
    """The `runner` environment's site-packages, if it is here (PUZZLE_RUNNER_VENDOR,
    else beside this Python's environment, else in the source checkout's .pixi)."""
    if os.environ.get("PUZZLE_RUNNER_VENDOR"):
        return Path(os.environ["PUZZLE_RUNNER_VENDOR"])
    from puzzle.jobs.runtime import environment

    here = Path(__file__).resolve()
    envs = [environment("runner"), here.parents[3] / ".pixi" / "envs" / "runner"]
    for env in [e for e in envs if e is not None]:
        libs = [*sorted(env.glob("lib/python3.*/site-packages")), env / "Lib" / "site-packages"]
        for lib in libs:
            if (lib / "psij").is_dir():
                return lib
    return None


def _files() -> list[tuple[str, bytes]]:
    pkg = Path(__file__).resolve().parent
    stub = b'"""Only the job runner, unpacked for a remote host."""\n'
    files = [("main.py", MAIN.encode()), ("puzzle/__init__.py", stub)]
    for path in sorted(pkg.glob("*.py")):
        if path.name != "build.py":
            files.append((f"puzzle/runner/{path.name}", path.read_bytes()))
    vendor = vendor_dir()
    if vendor is not None:
        for name in VENDOR:
            top = vendor / name
            if top.is_file():
                files.append((name, top.read_bytes()))
                continue
            for path in sorted(top.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    files.append((path.relative_to(vendor).as_posix(), path.read_bytes()))
    return files


def bundle() -> tuple[str, bytes]:
    """(hash, a .tar.gz of the runner): the same bytes for the same sources, so the hash
    says whether a host already has this runner."""
    files = _files()
    h = hashlib.sha256()
    for name, data in files:
        h.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as tar:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return h.hexdigest()[:16], out.getvalue()


def has_schedulers() -> bool:
    return vendor_dir() is not None
