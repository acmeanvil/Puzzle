"""Material library files to move between installs (Materials → Export/Import).

A library file is the JSON every library already is, `{"materials": {key: entry}}`,
with a `format` tag and a `scope` saying what was exported. `export` writes your
materials, your imported library and yours, or everything including Puzzle's generics;
the families an exported entry inherits from (`parent`) go with it, so the file loads
on its own anywhere.

`import_file` reads one (or a bare `library.json`/`custom.json`) into your materials
(`custom.json`, through `LibraryStore`: every entry checked, one undo), or puts it in
place of your imported library (`library.json`, the old one kept as `library.json.bak`).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from puzzle.errors import PuzzleError

FORMAT = "puzzle-materials"
VERSION = 1
SCOPES = {
    "yours": "Your materials (made or edited in the builder)",
    "user": "Your imported library and your materials",
    "all": "Everything, with Puzzle's generic materials",
}
CONFLICTS = {
    "keep": "Keep the ones you have",
    "replace": "Replace them with the file's",
}
TARGETS = {
    "yours": "Add them to your materials",
    "library": "Use the file as your imported library",
}


def entries_for(scope: str) -> dict[str, dict]:
    """The library entries a scope covers, with the families they inherit from."""
    from puzzle.materials import _library, library_paths
    from puzzle.materials.store import LibraryStore

    if scope not in SCOPES:
        raise PuzzleError(f"no scope '{scope}': {', '.join(SCOPES)}", code="invalid")
    full = _library()
    if scope == "all":
        return dict(full)
    if scope == "yours":
        chosen = LibraryStore().mine()
    else:
        chosen = {}
        for path in library_paths():
            chosen.update(_read(path))
        chosen = {k: e for k, e in chosen.items() if k in full}  # without ones removed
    out = dict(chosen)
    for entry in chosen.values():  # each family an entry needs, up its chain
        parent = entry.get("parent")
        while parent and parent not in out and parent in full:
            out[parent] = full[parent]
            parent = full[parent].get("parent")
    return out


def export(path: Path, scope: str = "user") -> int:
    """Write the scope's materials to `path`; returns how many."""
    entries = entries_for(scope)
    if not entries:
        raise PuzzleError(f"no materials to export ({SCOPES[scope].lower()})", code="not_found")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format": FORMAT,
        "version": VERSION,
        "scope": scope,
        "exported": datetime.now(UTC).isoformat(timespec="seconds"),
        "materials": entries,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return len(entries)


@dataclass
class Imported:
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # already there, left as they were
    invalid: dict[str, str] = field(default_factory=dict)  # key -> why
    backup: Path | None = None

    def summary(self) -> str:
        parts = [f"{len(self.added)} added"]
        if self.replaced:
            parts.append(f"{len(self.replaced)} replaced")
        if self.kept:
            parts.append(f"{len(self.kept)} already there, kept")
        if self.invalid:
            parts.append(f"{len(self.invalid)} not valid, skipped")
        return ", ".join(parts)


def import_file(path: Path, target: str = "yours", conflicts: str = "keep") -> Imported:
    """Read a library file into your materials, or use it as your imported library."""
    from puzzle.materials import _library, reload_library, user_library_path
    from puzzle.materials.store import LibraryStore

    if target not in TARGETS:
        raise PuzzleError(f"no target '{target}': {', '.join(TARGETS)}", code="invalid")
    if conflicts not in CONFLICTS:
        raise PuzzleError(f"no choice '{conflicts}': {', '.join(CONFLICTS)}", code="invalid")
    entries = _read(Path(path))
    if target == "library":
        dest = user_library_path()
        done = Imported(added=sorted(entries))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            done.backup = dest.with_name(dest.name + ".bak")
            shutil.copy2(dest, done.backup)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(
            json.dumps({"materials": entries}, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(dest)
        reload_library()
        return done
    have = _library()
    done = Imported()
    chosen: dict[str, dict] = {}
    for key, entry in entries.items():
        if key in have and conflicts == "keep":
            done.kept.append(key)
            continue
        chosen[key] = entry
    store = LibraryStore()
    done.invalid = store.save_many(chosen)
    for key in chosen:
        if key not in done.invalid:
            (done.replaced if key in have else done.added).append(key)
    return done


def _read(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PuzzleError(f"no file {path}", code="not_found") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PuzzleError(f"{path.name} is not a material library: {exc}", code="invalid") from None
    if data.get("format", FORMAT) != FORMAT or not isinstance(data.get("materials"), dict):
        raise PuzzleError(f"{path.name} is not a material library", code="invalid")
    if data.get("version", VERSION) > VERSION:
        raise PuzzleError(
            f"{path.name} was written by a newer Puzzle (format {data['version']})",
            code="invalid",
        )
    return data["materials"]
