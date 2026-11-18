"""Project directory format: one TOML file per object, so projects diff and merge in git.

my-project/
    project.toml
    geometry/<uuid>.toml
    material/<uuid>.toml
    component/<uuid>.toml
    study/<uuid>.toml
    ...

Saving writes only the files whose text changed, as one transaction (ADR 0090
package 2), so a crash at any point leaves a project that opens, wholly as it was
before the change or wholly after it, with history.jsonl agreeing:

1. each new file's content goes to a temporary file beside it (flushed to disk);
2. `.puzzle/journal.json` is written whole (temp and replace): it lists those
   temporary files and their places, the files to remove and the history entries.
   This is the commit point: before it nothing has changed;
3. the history entries are appended (each once: an entry whose seq is there already
   is not appended again), then each temporary file replaces its file, then the
   removed objects' files go, and the journal last.

A crash after step 2 is finished by `recover` on the next open or change (the session
calls it under the lock), and until then `load_project` reads through the journal, so
every reader sees the change whole. A crash before it leaves only temporary files,
which no reader looks at and the next session sweeps away.
"""

from __future__ import annotations

import contextlib
import json
import os
import pickle
import secrets
import threading
import tomllib
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    OBJECT_TYPES,
    SCHEMA_VERSION,
    AgentSettings,
    Project,
    ProjectObject,
)
from puzzle.project import atomic
from puzzle.project.layout import JOURNAL, PROJECT_FILE

_BY_KIND: dict[str, type[ProjectObject]] = {cls.kind: cls for cls in OBJECT_TYPES}
RESTORE = "Restore a version (Project → Versions…, or puzzle versions / version-restore) or a copy."


@dataclass
class Staged:
    """Files written to temporary names beside their places, not yet in them (a
    version's files for a restore): committed with a change, or discarded."""

    moves: list[tuple[Path, str]] = field(default_factory=list)  # (temporary file, rel)
    removes: list[str] = field(default_factory=list)

    def moved(self) -> dict[str, Path]:
        return {rel: tmp for tmp, rel in self.moves}

    def check(self) -> None:
        lost = [rel for tmp, rel in self.moves if not tmp.exists()]
        if lost:
            raise PuzzleError(f"files staged for writing went missing: {', '.join(lost)}",
                              code="write_failed")  # fmt: skip

    def discard(self) -> None:
        for tmp, _ in self.moves:
            tmp.unlink(missing_ok=True)
        self.moves = []

    def changes(self) -> dict[str, list[str]]:
        return {"written": [rel for _, rel in self.moves], "removed": list(self.removes)}


def object_path(kind: str, oid: Any) -> str:
    return f"{kind}/{oid}.toml"


def header_text(project: Project) -> str:
    header = {
        "name": project.name,
        "schema_version": project.schema_version,
        "agent": project.agent.model_dump(mode="json"),
    }
    return tomli_w.dumps(header)


def object_text(obj: ProjectObject) -> str:
    return tomli_w.dumps(obj.model_dump(mode="json", exclude_none=True))


def _on_disk(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None


def save_project(
    project: Project,
    root: Path,
    *,
    only: Iterable[Any] | None = None,
    gone: Iterable[tuple[str, Any]] = (),
    entries: list[dict[str, Any]] | None = None,
    check: Callable[[], None] | None = None,
    staged: Staged | None = None,
) -> list[int]:
    """Write the project to `root` as one transaction (see the module's docstring):
    project.toml and the object files whose text changed, and the files of objects
    that no longer exist. With `only` (object ids), just those objects are compared
    and `gone` ((kind, id) pairs) names the files to remove; without it every object
    is compared and any object file not in the project is removed. `entries` are
    history entries appended in the same transaction (their seqs are returned);
    `check` runs just before the commit point (the session checks it still holds
    the lock); `staged` files (a restore's) go in the same transaction."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    writes: dict[str, str] = {}
    head = header_text(project)
    if _on_disk(root / PROJECT_FILE) != head:
        writes[PROJECT_FILE] = head
    removes: list[str] = []
    if only is None:
        keep: set[str] = set()
        for obj in project.objects.values():
            rel = object_path(obj.kind, obj.id)
            keep.add(rel)
            text = object_text(obj)
            if _on_disk(root / rel) != text:
                writes[rel] = text
        for kind in _BY_KIND:
            for path in (root / kind).glob("*.toml"):
                rel = f"{kind}/{path.name}"
                if rel not in keep:
                    removes.append(rel)
    else:
        for oid in only:
            obj = project.objects.get(oid)
            if obj is None:
                continue
            rel = object_path(obj.kind, obj.id)
            text = object_text(obj)
            if _on_disk(root / rel) != text:
                writes[rel] = text
        for kind, oid in gone:
            rel = object_path(kind, oid)
            if (root / rel).exists() and rel not in writes:
                removes.append(rel)
    if staged is not None:
        removes = list(dict.fromkeys([*removes, *(r for r in staged.removes if r not in writes)]))
    seqs = commit(root, writes, removes, entries or [], check=check,
                  staged=staged.moves if staged is not None else ())  # fmt: skip
    if project.trusted:  # made (or trusted) here: its folder stays trusted (ADR 0062)
        from puzzle import trust

        trust.trust(root)
    return seqs


def create_project(project: Project, root: Path) -> None:
    """Save a new project into `root`, refused when the folder holds a project already
    (or object folders of one) instead of replacing it (GUI-19)."""
    root = Path(root)
    if (root / PROJECT_FILE).exists():
        try:
            name = _toml(root / PROJECT_FILE).get("name", "")
        except PuzzleError:
            name = ""
        what = f"the project '{name}'" if name else "a Puzzle project"
        raise PuzzleError(
            f"{root} already holds {what}: open it, or choose another folder",
            code="exists",
            path=str(root),
        )
    held = [k for k in _BY_KIND if any((root / k).glob("*.toml"))]
    if held:
        raise PuzzleError(
            f"{root} holds project files ({', '.join(held)}/…) without a project.toml: "
            "choose another folder",
            code="exists",
            path=str(root),
        )
    save_project(project, root)


# --- the transaction ---------------------------------------------------------------


def commit(
    root: Path,
    writes: dict[str, str | bytes],
    removes: list[str],
    entries: list[dict[str, Any]],
    *,
    check: Callable[[], None] | None = None,
    staged: Iterable[tuple[Path, str]] = (),
) -> list[int]:
    """Write files, remove files and append history entries all or none (the
    module's docstring). `staged` are files already written to temporary names
    ((temporary file, rel) pairs); a file in `writes` wins over a staged one. Each
    entry gets the next seq and the time unless it has them. Returns the seqs."""
    from datetime import UTC, datetime

    from puzzle.project import history

    root = Path(root)
    if entries:
        base = history.last_seq(root)
        now = datetime.now(UTC).isoformat()
        entries = [{"seq": base + k + 1, "time": now, **e} for k, e in enumerate(entries)]
    seqs = [e["seq"] for e in entries]
    given = []
    for tmp, rel in staged:
        if rel in writes:
            Path(tmp).unlink(missing_ok=True)
        else:
            given.append((Path(tmp), rel))
    if not writes and not removes and not given:
        if check is not None:
            check()
        for e in entries:
            history.append(root, e)
        return seqs
    token = secrets.token_hex(6)
    staged = list(given)
    try:
        for rel, data in writes.items():
            raw = data.encode("utf-8") if isinstance(data, str) else data
            staged.append((atomic.write_temp(root / rel, raw, token=token), rel))
        if check is not None:
            check()
        journal = {
            "token": token,
            "moves": [[tmp.relative_to(root).as_posix(), rel] for tmp, rel in staged],
            "removes": list(removes),
            "history": entries,
        }
        path = root / JOURNAL
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(journal), encoding="utf-8")  # the commit point
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise
    try:
        _finish(root, journal)
    except OSError as exc:
        raise PuzzleError(
            f"the change was recorded but not all of its files could be written ({exc}); "
            "Puzzle finishes it the next time the project is opened or changed",
            code="write_failed",
            path=str(root),
        ) from exc
    return seqs


def _finish(root: Path, journal: dict[str, Any]) -> None:
    """Carry out a committed journal; safe to repeat after a crash part-way."""
    from puzzle.project import history

    todo = journal.get("history") or []
    if todo:
        last = history.last_seq(root)
        for e in todo:  # the journal is on disk already: no flush of its own
            if e.get("seq", 0) > last:
                history.append(root, e, durable=False)
    for tmp_rel, rel in journal.get("moves", []):
        tmp = root / tmp_rel
        if tmp.exists():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            atomic.replace(tmp, root / rel)
        _forget(root / rel)  # read again: a clock coarser than two writes can't tell
    for rel in journal.get("removes", []):
        (root / rel).unlink(missing_ok=True)
        _forget(root / rel)
    (root / JOURNAL).unlink(missing_ok=True)


def _journal(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / JOURNAL).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):  # never half written (replaced whole): set it aside
        bad = root / (JOURNAL + ".bad")
        with contextlib.suppress(OSError):
            atomic.replace(root / JOURNAL, bad)
        return None
    return data if isinstance(data, dict) else None


def pending(root: Path) -> bool:
    """Whether a committed change is still being written (or was cut short)."""
    return (Path(root) / JOURNAL).exists()


def recover(root: Path) -> bool:
    """Finish a change a crash cut short (callers hold the project lock); True when
    there was one."""
    root = Path(root)
    journal = _journal(root)
    if journal is None:
        return False
    _finish(root, journal)
    return True


def sweep(root: Path) -> int:
    """Remove temporary files a crash left before a commit point (callers hold the
    lock and have recovered first, so no journal names them)."""
    gone = 0
    for folder in [Path(root), *(Path(root) / k for k in _BY_KIND)]:
        for tmp in folder.glob(f".*{atomic.SUFFIX}"):
            tmp.unlink(missing_ok=True)
            gone += 1
    return gone


# --- reading -----------------------------------------------------------------------


def _toml(path: Path, text: str | None = None) -> dict:
    """A project file's contents. A byte-order mark (Windows editors and PowerShell add
    one) is allowed; a file that still doesn't read says which it is and how it starts,
    not just where the parser stopped."""
    if text is None:
        text = path.read_text(encoding="utf-8-sig")
    text = text.removeprefix("﻿")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        start = text.encode("utf-8")[:16]
        raise PuzzleError(
            f"{path.name} in {path.parent} isn't a readable project file ({exc}); it starts "
            f"with {start!r}. {RESTORE}",
            code="invalid",
            path=str(path),
        ) from exc


def _validate(cls: type[ProjectObject], data: dict, path: Path) -> ProjectObject:
    """An object from its file, or an error naming the file (and the field) and how to
    recover: never a bare pydantic error."""
    from pydantic import ValidationError

    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        extra = [".".join(str(p) for p in e["loc"]) for e in errors
                 if e.get("type") == "extra_forbidden"]  # fmt: skip
        where = f"{path.parent.name}/{path.name}"
        if not data:
            message = f"{where} is empty (a save cut short?). {RESTORE}"
            code = "invalid"
        elif extra:
            message = (f"{where} has a field this Puzzle doesn't know ({', '.join(extra)}): "
                       "it was probably saved by a newer Puzzle. Open it there, or update "
                       f"Puzzle; or {RESTORE[0].lower()}{RESTORE[1:]}")  # fmt: skip
            code = "unknown_field"
        else:
            first = errors[0]
            field = ".".join(str(p) for p in first["loc"]) or "(the object)"
            message = f"{where} doesn't fit the schema at {field}: {first['msg']}. {RESTORE}"
            code = "invalid"
        raise PuzzleError(message, code=code, path=str(path),
                          details={"file": where, "fields": extra or None,
                                   "errors": len(errors)}) from None  # fmt: skip


def load_project(root: Path) -> Project:
    root = Path(root)
    journal = _journal(root)  # a change cut short is read as finished
    moved: dict[str, Path] = {}
    removed: set[str] = set()
    if journal is not None:
        for tmp_rel, rel in journal.get("moves", []):
            if (root / tmp_rel).exists():
                moved[rel] = root / tmp_rel
        removed = set(journal.get("removes", []))
    return _load(root, moved, removed)


def load_staged(root: Path, staged: Staged) -> Project:
    """The project as it would be with `staged` written (a restore checked before it
    is committed: it must open)."""
    return _load(Path(root), staged.moved(), set(staged.removes))


# Parsed object files, by path, while their size and times hold (BE-11): parsing TOML
# was most of a load, and a session loads the project on every change. Each file's
# contents are kept pickled, so every load gets objects of its own to change.
_PARSED: OrderedDict[str, tuple[tuple[int, int, int, int], bytes]] = OrderedDict()
_PARSED_MAX = 20_000  # files; a few kB each pickled
_parsed_lock = threading.Lock()


def _stamp(st: os.stat_result) -> tuple[int, int, int, int]:
    # Size, modification and change (Windows: creation) times and the inode (POSIX;
    # 0 on Windows, whose times are precise): an atomic replace makes a new file. A
    # clock coarser than two writes (Linux file times move by the jiffy) can still
    # give one stamp twice, so this process's own writes drop their entries too.
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, 0 if os.name == "nt" else st.st_ino)


def _forget(path: Path) -> None:
    with _parsed_lock:
        _PARSED.pop(str(path), None)


def _parsed(path: Path, st: os.stat_result | None = None) -> dict:
    """A project file's contents, from the cache while the file is unchanged."""
    if st is None:
        st = path.stat()
    key, stamp = str(path), _stamp(st)
    with _parsed_lock:
        hit = _PARSED.get(key)
        if hit is not None and hit[0] == stamp:
            _PARSED.move_to_end(key)
            blob = hit[1]
        else:
            blob = None
    if blob is not None:
        return pickle.loads(blob)
    data = _toml(path)
    with _parsed_lock:
        _PARSED[key] = (stamp, pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
        while len(_PARSED) > _PARSED_MAX:
            _PARSED.popitem(last=False)
    return data


def _object_files(folder: Path) -> dict[str, os.stat_result]:
    """The .toml files in an object folder with their stat (free from the listing on
    Windows)."""
    try:
        entries = os.scandir(folder)
    except (FileNotFoundError, NotADirectoryError):
        return {}
    out = {}
    with entries:
        for e in entries:
            if e.name.endswith(".toml") and e.is_file():
                try:
                    out[e.name] = e.stat()
                except FileNotFoundError:  # removed while listing
                    continue
    return out


def disk_stamp(root: Path) -> tuple:
    """Every project file's name, size and times: equal stamps mean no project file
    was written in between (a session keeps its project across changes then)."""
    root = Path(root)
    out: list = []
    try:
        out.append((PROJECT_FILE, _stamp((root / PROJECT_FILE).stat())))
    except FileNotFoundError:
        out.append((PROJECT_FILE, None))
    out.append((JOURNAL, (root / JOURNAL).exists()))
    for kind in _BY_KIND:
        listed = _object_files(root / kind)
        out += sorted((f"{kind}/{name}", _stamp(st)) for name, st in listed.items())
    return tuple(out)


def reread(project: Project, root: Path, ids: Iterable[Any]) -> None:
    """Put the objects `ids` back as their files now read (just written): what a load
    would give, so a project kept in memory never differs from one read afresh."""
    root = Path(root)
    for oid in ids:
        obj = project.objects.get(oid)
        if obj is None:
            continue
        path = root / object_path(obj.kind, obj.id)
        project.objects[oid] = _validate(type(obj), _parsed(path), path)


def _load(root: Path, moved: dict[str, Path], removed: set[str]) -> Project:
    from puzzle.project import migrations

    stats: dict[str, os.stat_result] = {}

    def read(rel: str) -> dict:
        path = root / rel
        if rel in moved:
            return _toml(path, moved[rel].read_text(encoding="utf-8-sig"))
        return _parsed(path, stats.get(rel))

    header = read(PROJECT_FILE)
    found = header.get("schema_version", 1)
    if not isinstance(found, int):
        raise PuzzleError(f"project.toml in {root} has schema_version {found!r}, not a "
                          f"number. {RESTORE}", code="invalid",
                          path=str(root / PROJECT_FILE))  # fmt: skip
    if found > SCHEMA_VERSION:
        raise PuzzleError(
            f"this project was saved by a newer Puzzle (schema {found}; this build reads up "
            f"to {SCHEMA_VERSION}): open it with that Puzzle, or update this one",
            code="newer_schema",
            path=str(root),
            details={"found": found, "reads": SCHEMA_VERSION},
        )
    if "name" not in header:
        raise PuzzleError(f"project.toml in {root} has no name. {RESTORE}", code="invalid",
                          path=str(root / PROJECT_FILE))  # fmt: skip
    raws: list[dict[str, Any]] = []
    for kind in _BY_KIND:
        listed = _object_files(root / kind)
        stats.update({f"{kind}/{name}": st for name, st in listed.items()})
        rels = {f"{kind}/{name}" for name in listed}
        rels |= {rel for rel in moved if rel.startswith(f"{kind}/")}
        for rel in sorted(rels - removed):
            raws.append({"kind": kind, "data": read(rel), "path": root / rel})
    if found < SCHEMA_VERSION:
        migrations.upgrade(header, raws, found)
    try:
        agent = AgentSettings.model_validate(header.get("agent", {}))
    except Exception as exc:  # noqa: BLE001 - a pydantic error, said plainly
        raise PuzzleError(f"project.toml in {root}: the agent settings don't read ({exc}). "
                          f"{RESTORE}", code="invalid",
                          path=str(root / PROJECT_FILE)) from None  # fmt: skip
    project = Project(name=header["name"], schema_version=SCHEMA_VERSION, agent=agent)
    for raw in raws:
        cls = _BY_KIND.get(raw["kind"])
        if cls is None:
            raise PuzzleError(f"no object kind '{raw['kind']}' after migrating {raw['path']}",
                              code="invalid", path=str(raw["path"]))  # fmt: skip
        project.add(_validate(cls, raw["data"], raw["path"]))
    if found < SCHEMA_VERSION:
        project.migrated_from = found
    from puzzle import trust

    project.trusted = trust.is_trusted(root)
    project.root = root
    return project
