"""The files a project's objects read, by content (ADR 0090 package 1, BE-1).

An object's hash covers its settings; a geometry's `source`, a history step's `file`
or `dxf`, an imported field's file and a File operator's pattern are only names. So a
replaced CAD file left its studies "up to date" and their runs reused. `digests` gives
each file an object reads its SHA-256, which `graph.content_hashes` puts into that
object's hash. Hashes are cached by path, size and modification time (once a file is a
few seconds old: `SETTLED_NS`), so the tree's status on every refresh reads no file that
hasn't changed.

`copy_in` brings a file from outside into the project folder without ever replacing a
different file of the same name that something already reads.
"""

from __future__ import annotations

import glob
import hashlib
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

MISSING = "missing"  # a stable stand-in for a file that isn't there: its problem says so

_cache: dict[str, tuple[int, int, str]] = {}
_lock = threading.Lock()
# A file written twice within one tick of the file system's clock keeps its time, and at
# the same size looked unchanged (seen on Windows' CI: an edited field file kept its
# hash). So a hash is kept only once the file is older than any such tick (FAT's is 2 s);
# one changed more recently is read again each time.
SETTLED_NS = 3_000_000_000


def digest(path: Path) -> str:
    """SHA-256 of a file, or of every file under a folder (an OpenFOAM case), or
    MISSING. Cached by size and modification time."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return MISSING
    if path.is_dir():
        h = hashlib.sha256()
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(f.relative_to(path).as_posix().encode())
            h.update(digest(f).encode())
        return h.hexdigest()
    key = os.fspath(path)
    with _lock:
        hit = _cache.get(key)
    if hit is not None and hit[:2] == (st.st_size, st.st_mtime_ns):
        return hit[2]
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return MISSING
    if time.time_ns() - st.st_mtime_ns > SETTLED_NS:
        with _lock:
            _cache[key] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return h.hexdigest()


def _history_files(args: Any, key: str = "") -> list[str]:
    """`file` and `dxf` arguments anywhere in a history step's arguments (a profile
    given as {"dxf": ...} too)."""
    if isinstance(args, dict):
        return [f for k, v in args.items() for f in _history_files(v, k)]
    if isinstance(args, list):
        return [f for v in args for f in _history_files(v, key)]
    if key in ("file", "dxf") and isinstance(args, str) and args:
        return [args]
    return []


def referenced(obj) -> list[tuple[str, bool]]:
    """(name, is a pattern) for every file `obj` reads, as stored (relative to the
    project, or absolute)."""
    kind = getattr(obj, "kind", "")
    out: list[tuple[str, bool]] = []
    if kind == "geometry":
        if obj.source:
            out.append((obj.source, False))
        for step in obj.history:  # a suppressed step reads nothing, but may be restored
            out += [(f, False) for f in _history_files(step.args)]
    elif kind == "field":
        if obj.file:
            out.append((obj.file, False))
        out += [(t.file, False) for t in obj.times if t.file]
    elif kind == "operator" and obj.type == "file" and getattr(obj, "file", None):
        out.append((obj.file, True))
    return list(dict.fromkeys(out))


def _where(name: str, root: Path | None) -> Path | None:
    path = Path(name)
    if path.is_absolute():
        return path
    return None if root is None else Path(root) / path


def _source_of(obj, path: Path) -> Path:
    """What to hash for a field file: an EnSight case with the files beside it (its
    folder, as `fields.import_file` copies it), else the file itself."""
    if getattr(obj, "kind", "") == "field" and getattr(obj, "format", "") == "ensight":
        return path.parent
    return path


def digests(obj, root: Path | None) -> dict[str, str]:
    """Each file `obj` reads, by its stored name, to its content hash (MISSING when it
    isn't there). Relative names need `root`; without one (a project built in memory)
    they are MISSING too, as a saved project's would be with the file not there."""
    out: dict[str, str] = {}
    for name, pattern in referenced(obj):
        path = _where(name, root)
        if path is None:
            out[name] = MISSING
            continue
        if pattern:
            found = sorted(glob.glob(os.fspath(path)))
            h = hashlib.sha256()
            for f in found:
                h.update(Path(f).name.encode())
                h.update(digest(Path(f)).encode())
            out[name] = h.hexdigest() if found else MISSING
        else:
            out[name] = digest(_source_of(obj, path))
    return out


def missing(obj, root: Path | None) -> list[str]:
    """The files `obj` reads that aren't there (patterns aside: a File operator
    reports its own)."""
    return [name for name, pattern in referenced(obj) if not pattern
            for path in [_where(name, root)]
            if path is not None and not path.exists()]  # fmt: skip


def free_name(folder: Path, name: str) -> str:
    """`name`, or `<stem>-2<suffix>`, `-3`... : the first not taken in `folder`."""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 1
    while (Path(folder) / candidate).exists():
        n += 1
        candidate = f"{stem}-{n}{suffix}"
    return candidate


def copy_in(root: Path, path: Path, folder: str = "") -> str:
    """Copy `path` into the project (into `folder`, relative to `root`) and return its
    name there, relative to the project. A file already there with the same contents is
    used as it is; a different one keeps its name (something may read it) and the copy
    takes a free one (`bracket-2.step`). A file already inside `folder` is not
    copied."""
    root, path = Path(root), Path(path)
    target_dir = root / folder if folder else root
    try:
        path.resolve().relative_to(target_dir.resolve())
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        pass  # outside: copied in
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists() and digest(target) != digest(path):
        target = target_dir / free_name(target_dir, path.name)
    if not target.exists():
        shutil.copyfile(path, target)
    return target.relative_to(root).as_posix()
