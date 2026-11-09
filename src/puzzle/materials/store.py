"""Writing your materials (ADR 0020): the builder's store, not a project.

Your materials live in `custom_library_path()` (JSON, `{"materials": {key: entry}}`),
read after the shipped generics and your imported library, so an entry there with a
library key overrides that material: editing a generic saves your copy, and removing
your copy brings the library's back. Writes are atomic; each change is logged with what
was there before (`custom-history.jsonl`), so `undo` restores it. A library material
removed (`remove_from_library`) is the marker `{"removed": true}` under its key here, so
it stays removed when the library is imported again; `restore` takes the marker away.

Agents do not write here directly (ADR 0020: they may only propose library changes).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from puzzle.errors import PuzzleError
from puzzle.model.schema import Material


def entry_of(material: Material, parent: str | None = None) -> dict:
    """A project or builder material as a library entry (what `library_material` reads)."""
    data = material.model_dump(mode="json")
    entry = {
        "name": data["name"],
        "description": data["description"],
        "info": data["info"],
        "properties": data["properties"],
        "tables": {
            k: {
                "unit": t["unit"],
                "reference_temperature": t["reference_temperature"],
                "points": [[p["temperature"], p["value"], p["source"]] for p in t["points"]],
            }
            for k, t in data["tables"].items()
        },
        "sources": data["sources"],
        "models": {k: v for k, v in data["models"].items() if v is not None},
        "solver_keywords": data["solver_keywords"],
        "fatigue": {k: v for k, v in data["fatigue"].items() if v},
    }
    if parent:
        entry["parent"] = parent
    return entry


def key_for(name: str) -> str:
    """A library key from a name: "Al 6061-T6" -> "al_6061_t6"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "material"


class LibraryStore:
    def __init__(self, path: Path | None = None) -> None:
        from puzzle.materials import custom_library_path

        self.path = Path(path) if path else custom_library_path()
        self.history = self.path.with_name(self.path.stem + "-history.jsonl")

    def entries(self) -> dict[str, dict]:
        """Everything in the file, removal markers too."""
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8")).get("materials", {})

    def mine(self) -> dict[str, dict]:
        """Your materials: the file without its removal markers."""
        return {k: e for k, e in self.entries().items() if not e.get("removed")}

    def removed(self) -> list[str]:
        """Library materials you removed."""
        return sorted(k for k, e in self.entries().items() if e.get("removed"))

    def save(self, key: str, entry: dict) -> None:
        """Add or replace your material `key` (checked by building it first)."""
        from puzzle.materials import reload_library

        self._check(key, entry)
        entry = {**entry, "modified": datetime.now(UTC).isoformat(timespec="seconds")}
        self._write(key, entry)
        reload_library()

    def save_many(self, entries: dict[str, dict]) -> dict[str, str]:
        """Several materials added or replaced at once (an imported file): each checked
        against the others, the valid ones written together as one undo. Returns the
        ones skipped, key -> why."""
        from puzzle.materials import reload_library

        invalid: dict[str, str] = {}
        good = dict(entries)
        while True:  # a skipped entry can take a variant that inherits from it along
            for key, entry in good.items():
                try:
                    self._check(key, entry, good)
                except PuzzleError as exc:
                    invalid[key] = exc.message
            if not invalid.keys() & good.keys():
                break
            good = {k: v for k, v in good.items() if k not in invalid}
        if not good:
            return invalid
        current = self.entries()
        before = {key: current.get(key) for key in good}
        now = datetime.now(UTC).isoformat(timespec="seconds")
        current.update({key: {**entry, "modified": now} for key, entry in good.items()})
        self._dump(current)
        self._log({"batch": before})
        reload_library()
        return invalid

    def remove(self, key: str) -> None:
        """Your material `key` removed (a library material by that key shows again)."""
        from puzzle.materials import reload_library

        if key not in self.mine():
            raise PuzzleError(f"no material '{key}' of yours", code="not_found")
        self._write(key, None)
        reload_library()

    def remove_from_library(self, keys: list[str]) -> list[str]:
        """Library materials removed, whoever made them (imported, generic or yours), as
        one undo. Refused when a material left would lose its family. Returns the keys."""
        from puzzle.materials import _library, _load, library_paths, reload_library

        lib = _library()
        keys = sorted(set(keys))
        if missing := [k for k in keys if k not in lib]:
            raise PuzzleError(f"no material {', '.join(missing)} in the library",
                              code="not_found")  # fmt: skip
        orphans = sorted(k for k, e in lib.items() if k not in keys and e.get("parent") in keys)
        if orphans:
            raise PuzzleError(
                f"{', '.join(orphans)} inherit from a material being removed: remove them "
                "too, or first save them as materials of their own",
                code="inconsistent",
            )
        current = self.entries()
        before = {key: current.get(key) for key in keys}
        shipped = _load(tuple(p for p in library_paths() if p != self.path))
        for key in keys:  # only yours: just gone; from a library: marked removed
            if key in shipped:
                current[key] = {"removed": True}
            else:
                current.pop(key, None)
        self._dump(current)
        self._log({"batch": before})
        reload_library()
        return keys

    def restore(self, keys: list[str]) -> list[str]:
        """Removed library materials brought back (their markers taken away), one undo."""
        from puzzle.materials import reload_library

        current = self.entries()
        keys = sorted(k for k in set(keys) if current.get(k, {}).get("removed"))
        if not keys:
            return []
        before = {key: current.pop(key) for key in keys}
        self._dump(current)
        self._log({"batch": before})
        reload_library()
        return keys

    def undo(self) -> str | None:
        """The last change taken back; returns the key it touched (None: nothing)."""
        from puzzle.materials import reload_library

        if not self.history.exists():
            return None
        lines = self.history.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        last = json.loads(lines[-1])
        entries = self.entries()
        befores = last.get("batch") or {last["key"]: last["before"]}
        for key, before in befores.items():
            if before is None:
                entries.pop(key, None)
            else:
                entries[key] = before
        self._dump(entries)
        self.history.write_text("".join(line + "\n" for line in lines[:-1]), encoding="utf-8")
        reload_library()
        return last.get("key") or ", ".join(sorted(befores))

    # --- inside --------------------------------------------------------------------------

    def _check(self, key: str, entry: dict, others: dict | None = None) -> None:
        """Build the material as the library would, so a bad entry never lands."""
        from puzzle.materials import _library, material_from_entry, resolve

        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise PuzzleError("a material key is lower-case letters, digits and _", code="invalid")
        lib = {**_library(), **self.mine(), **(others or {}), key: entry}
        try:
            material_from_entry(resolve(lib, key))
        except KeyError as exc:
            raise PuzzleError(str(exc).strip("'\""), code="not_found") from None
        except Exception as exc:  # noqa: BLE001 - a schema error from the entry
            raise PuzzleError(f"material '{key}' is not valid: {exc}", code="invalid") from None

    def _write(self, key: str, entry: dict | None) -> None:
        entries = self.entries()
        before = entries.get(key)
        if entry is None:
            entries.pop(key, None)
        else:
            entries[key] = entry
        self._dump(entries)
        self._log({"key": key, "before": before})

    def _log(self, change: dict) -> None:
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a", encoding="utf-8") as f:
            f.write(json.dumps(change) + "\n")

    def _dump(self, entries: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"materials": entries}, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)  # never a half-written file
