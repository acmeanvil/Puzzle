"""Project versions (ADR 0059): snapshots of a project's own files to roll back to.

A version is `versions/<n>.json` (the manifest: when, who, why, known good, each
file's hash, a summary of the project then) plus `versions/blobs/<hash>` (each file's
content, compressed, stored once). Results, working files, the history and the
versions themselves are left out. The session makes versions implicitly (opened,
before a run, after many edits, before a restore, copied) and the user explicitly;
`restore_files` writes one back (the session wraps it as an undoable command).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from puzzle.errors import PuzzleError
from puzzle.project.layout import HISTORY, PROJECT_FILE, RESULTS_DIR

VERSIONS_DIR = "versions"
BLOBS = "blobs"
CACHE = "cache.json"
LEFT_OUT = (RESULTS_DIR, ".puzzle", VERSIONS_DIR, HISTORY)
TRIGGERS = {  # why an automatic version was made, as the browser says it
    "opened": "Opened, changed outside Puzzle",
    "first": "First version",
    "before_run": "Before running {study}",
    "edits": "After {count} changes",
    "before_restore": "Before restoring version {version}",
    "copied": "Copied from {source}",
    "user": "Saved by you",
}
KEEP_AUTO = 30  # automatic versions kept in full; older ones thinned to one a day
DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass
class Version:
    """One version's manifest."""

    number: int
    time: str  # UTC, ISO 8601
    how: str  # "user" or "auto"
    trigger: str  # a TRIGGERS key
    actor: dict[str, str] = field(default_factory=dict)
    label: str = ""
    note: str = ""
    good: bool = False
    good_reason: str = ""
    history_seq: int = 0  # the last history entry it follows
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    summary: dict[str, Any] = field(default_factory=dict)
    words: dict[str, Any] = field(default_factory=dict)  # the trigger's details

    @property
    def what(self) -> str:
        """The label, or why it was made."""
        if self.label:
            return self.label
        try:
            return TRIGGERS.get(self.trigger, self.trigger).format(**self.words)
        except (KeyError, IndexError):
            return TRIGGERS.get(self.trigger, self.trigger)

    @property
    def by(self) -> str:
        kind = self.actor.get("kind", "")
        if self.how == "auto":
            return "Puzzle"
        if kind == "agent":
            return f"Agent ({self.actor.get('name') or 'agent'})"
        return "You" if kind in ("user", "") else self.actor.get("name") or kind

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.time)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out |= {"what": self.what, "by": self.by}
        out.pop("files")
        out["file_count"] = len(self.files)
        return out


# --- reading ---------------------------------------------------------------------


def folder(root: Path) -> Path:
    return Path(root) / VERSIONS_DIR


def safe_path(rel: str) -> str:
    """A manifest's file path that stays inside the project, else an error: a manifest
    may have come in an archive (ADR 0061), and a restore writes where it says."""
    p = PurePosixPath(rel) if isinstance(rel, str) else None
    if (p is None or not p.parts or rel.startswith(("/", "\\")) or "\\" in rel
            or p.is_absolute() or ":" in p.parts[0] or ".." in p.parts
            or p.parts[0] in LEFT_OUT):  # fmt: skip
        raise PuzzleError(f"a version lists the file '{rel}', which is not inside the project",
                          code="invalid", path=str(rel))  # fmt: skip
    return rel


def check_manifest(data: dict, name: str = "") -> dict:
    """A manifest's files checked: safe paths, and blob names that are SHA-256 digests."""
    files = data.get("files", {}) if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise PuzzleError(f"version manifest {name} is not a version", code="invalid", path=name)
    for rel, digest in files.items():
        safe_path(rel)
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise PuzzleError(f"version manifest {name} lists '{rel}' with a bad hash",
                              code="invalid", path=name)  # fmt: skip
    return data


def versions(root: Path) -> list[Version]:
    """Every version, oldest first."""
    out = []
    for path in folder(root).glob("*.json"):
        if path.stem.isdigit():
            out.append(Version(**check_manifest(json.loads(path.read_text()), path.name)))
    return sorted(out, key=lambda v: v.number)


def get(root: Path, number: int | str) -> Version:
    """Version `number`, or "good" (the latest known good) or "latest"."""
    found = versions(root)
    if number == "good":
        good = [v for v in found if v.good]
        if not good:
            raise PuzzleError("no version is marked known good yet", code="not_found")
        return good[-1]
    if number == "latest":
        if not found:
            raise PuzzleError("the project has no versions yet", code="not_found")
        return found[-1]
    for v in found:
        if v.number == int(number):
            return v
    raise PuzzleError(f"no version {number}", code="not_found")


BY_KINDS = {"you": "You", "puzzle": "Puzzle", "agent": "Agents"}  # who made it, as shown
TYPES = {  # a version's type, by its trigger, as the browser's filter names it
    "user": "Saved by you",
    "first": "First version",
    "opened": "Changed outside Puzzle",
    "before_run": "Before a run",
    "edits": "After changes",
    "before_restore": "Before a restore",
    "copied": "Copied",
}


def who(v: Version) -> str:
    """ "you", "puzzle" or "agent": who made the version."""
    if v.how == "auto":
        return "puzzle"
    return "agent" if v.actor.get("kind") == "agent" else "you"


def select(
    found: list[Version],
    *,
    by: str | None = None,
    types: set[str] | list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    good: bool | None = None,
    text: str = "",
) -> list[Version]:
    """The versions that pass every filter given: who made them ("you", "puzzle",
    "agent"), their types (TYPES keys), a time range (inclusive; aware datetimes, or
    naive ones read as local time), known good or not, and words found in the label,
    note, what, study names or changes."""

    def aware(t: datetime | None) -> datetime | None:
        if t is None:
            return None
        return t if t.tzinfo is not None else t.astimezone()

    since, until = aware(since), aware(until)
    words = text.casefold().split()
    out = []
    for v in found:
        if by and who(v) != by:
            continue
        if types and v.trigger not in types:
            continue
        if since is not None and v.when < since:
            continue
        if until is not None and v.when > until:
            continue
        if good is not None and v.good != good:
            continue
        if words:
            hay = " ".join([v.what, v.label, v.note, v.good_reason,
                            *(st.get("name", "") for st in v.summary.get("studies", [])),
                            *(c for cs in v.summary.get("changes", {}).values() for c in cs),
                            ]).casefold()  # fmt: skip
            if not all(w in hay for w in words):
                continue
        out.append(v)
    return out


def latest(root: Path) -> Version | None:
    found = versions(root)
    return found[-1] if found else None


# --- the project's files -------------------------------------------------------------


def tracked(root: Path) -> list[Path]:
    """The project's own files (relative), what a version holds."""
    root = Path(root)
    out = []
    for dirpath, dirs, files in os.walk(root):
        here = Path(dirpath).relative_to(root)
        if here == Path("."):
            dirs[:] = [d for d in dirs if d not in LEFT_OUT]
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            rel = here / f
            if here == Path(".") and f in LEFT_OUT:
                continue
            if f.startswith(".") or f.endswith((".pyc", "~")):
                continue
            out.append(rel)
    return sorted(out)


def hashes(root: Path) -> dict[str, str]:
    """Each tracked file's sha256, from the cache when its size and time are unchanged."""
    root = Path(root)
    cache_path = folder(root) / CACHE
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    out, fresh = {}, {}
    for rel in tracked(root):
        path = root / rel
        st = path.stat()
        key = rel.as_posix()
        seen = cache.get(key)
        if seen and seen[0] == st.st_size and seen[1] == st.st_mtime_ns:
            digest = seen[2]
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out[key] = digest
        fresh[key] = [st.st_size, st.st_mtime_ns, digest]
    if fresh != cache and folder(root).exists():
        cache_path.write_text(json.dumps(fresh))
    return out


def changed_since(root: Path, version: Version | None) -> bool:
    return version is None or hashes(root) != version.files


# --- making one ---------------------------------------------------------------------


def save(
    root: Path,
    *,
    how: str = "user",
    trigger: str = "user",
    actor: dict[str, str] | None = None,
    label: str = "",
    note: str = "",
    good: bool = False,
    good_reason: str = "",
    history_seq: int = 0,
    project=None,
    words: dict[str, Any] | None = None,
    only_if_changed: bool = False,
) -> Version | None:
    """A new version of the project as it is on disk; None when `only_if_changed`
    and nothing changed since the latest. Callers hold the project lock."""
    root = Path(root)
    previous = latest(root)
    files = hashes(root)
    if only_if_changed and previous is not None and files == previous.files:
        return None
    blobs = folder(root) / BLOBS
    blobs.mkdir(parents=True, exist_ok=True)
    for rel, digest in files.items():
        blob = blobs / digest
        if not blob.exists():
            tmp = blob.with_suffix(".tmp")
            tmp.write_bytes(zlib.compress((root / rel).read_bytes(), 6))
            tmp.replace(blob)
    if project is None:
        from puzzle.project.store import load_project

        project = load_project(root)
    v = Version(
        number=(previous.number + 1) if previous else 1,
        time=datetime.now(UTC).isoformat(timespec="seconds"),
        how=how,
        trigger=trigger,
        actor=actor or {},
        label=label.strip(),
        note=note.strip(),
        good=good,
        good_reason=good_reason,
        history_seq=history_seq,
        files=files,
        summary=summarize(root, project, previous, files),
        words=words or {},
    )
    _write(root, v)
    if how == "auto":
        prune(root)
    return v


def _write(root: Path, v: Version) -> None:
    path = folder(root) / f"{v.number}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(v), indent=1))
    tmp.replace(path)


def mark(root: Path, number: int, good: bool, reason: str = "") -> Version:
    """Mark a version known good (or not)."""
    v = get(root, number)
    v.good, v.good_reason = good, (reason if good else "")
    _write(root, v)
    return v


def edit(root: Path, number: int, label: str | None = None, note: str | None = None) -> Version:
    v = get(root, number)
    if label is not None:
        v.label = label.strip()
    if note is not None:
        v.note = note.strip()
    _write(root, v)
    return v


def delete(root: Path, number: int) -> None:
    """Remove a version (its blobs go when nothing else holds them)."""
    get(root, number)
    (folder(root) / f"{number}.json").unlink()
    collect(root)


def prune(root: Path, keep: int | None = None) -> list[int]:
    """Thin automatic versions: the latest `keep` in full, older ones to the newest of
    each day. User and known-good versions are always kept."""
    if keep is None:
        keep = _keep_setting()
    found = versions(root)
    auto = [v for v in found if v.how == "auto" and not v.good]
    older = auto[:-keep] if keep and len(auto) > keep else []
    days: dict[str, Version] = {}
    for v in older:
        days[v.when.date().isoformat()] = v  # the day's newest wins
    gone = [v.number for v in older if days[v.when.date().isoformat()] is not v]
    for n in gone:
        (folder(root) / f"{n}.json").unlink()
    if gone:
        collect(root)
    return gone


def _keep_setting() -> int:
    try:
        from puzzle.settings import current

        return int(current().get("versions.keep_auto"))
    except Exception:  # noqa: BLE001 - settings unavailable (scripts): the default
        return KEEP_AUTO


def collect(root: Path) -> int:
    """Remove blobs no version holds; how many went."""
    held = {d for v in versions(root) for d in v.files.values()}
    gone = 0
    for blob in (folder(root) / BLOBS).glob("*"):
        if blob.suffix != ".tmp" and blob.name not in held:
            blob.unlink()
            gone += 1
    return gone


# --- restoring ----------------------------------------------------------------------


def content(root: Path, digest: str) -> bytes:
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise PuzzleError(f"'{digest}' is not a file's hash", code="invalid")
    return zlib.decompress((folder(root) / BLOBS / digest).read_bytes())


def _inside(base: Path, rel: str) -> Path:
    """Where `rel` goes under `base`, refused when it (or a link on the way) leads out."""
    path = Path(base) / safe_path(rel)
    if not path.resolve().is_relative_to(Path(base).resolve()):
        raise PuzzleError(f"a version lists the file '{rel}', which leads outside the project",
                          code="invalid", path=rel)  # fmt: skip
    return path


def materialize(root: Path, v: Version, dest: Path) -> None:
    """Write version `v`'s files under `dest` (a restore, or a copy from a version)."""
    check_manifest({"files": v.files}, str(v.number))
    for rel, digest in v.files.items():
        path = _inside(dest, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content(root, digest))


def restore_files(root: Path, v: Version) -> dict[str, list[str]]:
    """Make the project's own files those of version `v`: write the ones that differ,
    remove the ones it didn't have. Results and the history stay. Callers hold the
    lock and make a "before restoring" version first."""
    root = Path(root)
    check_manifest({"files": v.files}, str(v.number))
    for rel in v.files:  # every path checked before anything is written
        _inside(root, rel)
    now = hashes(root)
    written = [rel for rel, d in v.files.items() if now.get(rel) != d]
    removed = [rel for rel in now if rel not in v.files]
    for rel in written:
        path = _inside(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content(root, v.files[rel]))
    for rel in removed:
        (root / rel).unlink(missing_ok=True)
        _drop_empty(root, (root / rel).parent)
    hashes(root)  # refresh the cache
    return {"written": written, "removed": removed}


def _drop_empty(root: Path, path: Path) -> None:
    while path != root and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


# --- the summary ---------------------------------------------------------------------


def summarize(root: Path, project, previous: Version | None, files: dict[str, str]) -> dict:
    """The project's condition, for the browser: counts, each study's state, problems,
    the last command, and what changed since the previous version."""
    from puzzle.model.schema import Component, Geometry, Material, MeshRecipe, Study

    out: dict[str, Any] = {
        "name": project.name,
        "counts": {
            "geometry": len(project.of_type(Geometry)),
            "components": len(project.of_type(Component)),
            "materials": len(project.of_type(Material)),
            "meshes": len(project.of_type(MeshRecipe)),
            "studies": len(project.of_type(Study)),
        },
    }
    try:
        problems = project.problems()
    except Exception:  # noqa: BLE001 - a summary never stops a version
        problems = []
    out["problems"] = len(problems)
    out["studies"] = _study_states(root, project)
    out["last_command"] = _last_command(root)
    out["changes"] = _changes(root, project, previous, files)
    out["names"] = names(project)
    return out


def _study_states(root: Path, project) -> list[dict[str, str]]:
    from puzzle.model.schema import Study
    from puzzle.project.status import WORDS, status

    try:
        from puzzle.results.store import ResultStore

        runs = (
            ResultStore(Path(root) / RESULTS_DIR).runs()
            if (Path(root) / RESULTS_DIR).exists()
            else []
        )
    except Exception:  # noqa: BLE001 - an old or broken store: states without runs
        runs = []
    try:
        states = status(project, runs)
    except Exception:  # noqa: BLE001
        states = {}
    out = []
    for s in project.of_type(Study):
        st = states.get(str(s.id))
        out.append({"name": s.name, "solver": s.solver,
                    "state": WORDS.get(st.state, st.state) if st else ""})  # fmt: skip
    return out


def _last_command(root: Path) -> str:
    path = Path(root) / HISTORY
    if not path.exists():
        return ""
    for line in reversed(path.read_text().splitlines()):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "command":
            c = e.get("command") or {}
            return describe_command(c)
    return ""


def describe_command(c: dict) -> str:
    """A command in a few words ("edit Bar", "run Static")."""
    name = c.get("command", "")
    target = c.get("object") or c.get("study") or c.get("name") or c.get("kind") or ""
    words = {"edit": "Edited", "create": "Created", "delete": "Deleted", "run_study": "Ran",
             "undo": "Undid a change", "restore_version": "Restored version",
             "add_library_material": "Added material", "copy_component": "Copied",
             "set_autonomy": "Set the agent's autonomy"}  # fmt: skip
    head = words.get(name, name.replace("_", " ").capitalize())
    if name == "restore_version":
        return f"{head} {c.get('version', '')}".strip()
    if isinstance(target, str) and target and name != "undo":
        return f"{head} {target}"
    return head


def _changes(root: Path, project, previous: Version | None, files: dict[str, str]) -> dict:
    """Objects added, changed and removed since `previous` (by name), and other files."""
    if previous is None:
        return {}
    now = names(project)
    old_names = previous.summary.get("names", {})
    out: dict[str, list[str]] = {"added": [], "changed": [], "removed": []}
    for rel in sorted(set(files) | set(previous.files)):
        a, b = previous.files.get(rel), files.get(rel)
        if a == b:
            continue
        label = now.get(rel) or old_names.get(rel) or rel
        if a is None:
            out["added"].append(label)
        elif b is None:
            out["removed"].append(label)
        else:
            out["changed"].append(label)
    return {k: v for k, v in out.items() if v}


KIND_WORDS = {"geometry": "Geometry", "component": "Component", "material": "Material",
              "mesh": "Mesh", "study": "Study", "field": "Field", "result": "Result",
              "axes": "Coordinate system", "probe": "Result tool"}  # fmt: skip


def _kind_word(kind: str) -> str:
    return KIND_WORDS.get(kind, kind.capitalize())


def names(project) -> dict[str, str]:
    """Each object file's readable name, kept in the summary so a later version can
    name what it removed."""
    return {
        f"{o.kind}/{o.id}.toml": f"{_kind_word(o.kind)} {o.name}" for o in project.objects.values()
    }


def due(root: Path, every_edits: int, every_minutes: float) -> tuple[bool, int]:
    """Whether an "edits" version is due: commands since the latest version, and time."""
    v = latest(root)
    path = Path(root) / HISTORY
    seqs = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (
                e.get("type") == "command"
                and (e.get("command") or {}).get("command") != "run_study"
            ):
                seqs.append(e.get("seq", 0))
    since = [s for s in seqs if v is None or s > v.history_seq]
    if not since:
        return False, 0
    if len(since) >= every_edits:
        return True, len(since)
    if v is not None and datetime.now(UTC) - v.when >= timedelta(minutes=every_minutes):
        return True, len(since)
    return False, len(since)


__all__ = [
    "PROJECT_FILE",
    "Version",
    "changed_since",
    "collect",
    "delete",
    "due",
    "get",
    "latest",
    "mark",
    "materialize",
    "names",
    "prune",
    "restore_files",
    "save",
    "versions",
]
